import logging

from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import is_mentor, mention_user

log = logging.getLogger(__name__)

# Qoidalar matnini shu yerda o'zgartiring. {user} - yangi a'zo ismi o'rniga qo'yiladi.
RULES = (
    "👋 Xush kelibsiz, {user}!\n\n"
    "<b>MC Club qoidalari:</b>\n"
    "1. Har bir mashg'ulot dedlayn bilan e'lon qilinadi va uni bajarish majburiy.\n"
    "2. Vazifani botga shaxsiy xabarda, e'longa reply qilib yoki <code>#topshiriq</code> "
    "xeshtegi bilan yuboring.\n"
    "3. Dedlaynda topshirmasangiz — 1 ta ogohlantirish (strike). "
    "{max_strikes} ta strike to'plansa, guruhdan chiqarilasiz.\n"
    "4. Sababli kechikish bo'lsa, dedlayndan kamida {excuse_hours} soat oldin botga "
    "<code>/uzr sabab</code> deb yozing.\n"
    "5. Oddiy savollardan oldin qo'llanmani o'qing.\n"
    "{guide}\n"
    "Yozish huquqini olish uchun quyidagi tugmani bosing 👇"
)


def _rules_text(user) -> str:
    guide = f'\n📖 <a href="{config.GUIDE_URL}">Qo\'llanma</a>\n' if config.GUIDE_URL else ""
    return RULES.format(
        user=mention_user(user),
        max_strikes=config.MAX_STRIKES,
        excuse_hours=config.EXCUSE_HOURS,
        guide=guide,
    )


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    for user in msg.new_chat_members:
        if user.is_bot or await is_mentor(context.bot, user.id):
            continue
        try:
            await context.bot.restrict_chat_member(
                config.GROUP_ID, user.id, ChatPermissions.no_permissions()
            )
        except TelegramError as e:
            log.warning("Cheklab bo'lmadi (%s): %s", user.id, e)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "✅ Qoidalarga roziman va topshiriqlarni bajaraman",
                callback_data=f"agree:{user.id}",
            )]]
        )
        await msg.reply_text(_rules_text(user), reply_markup=keyboard)


async def on_agree(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    target_id = int(query.data.split(":")[1])
    if user.id != target_id:
        await query.answer("Bu tugma siz uchun emas.", show_alert=True)
        return

    try:
        chat = await context.bot.get_chat(config.GROUP_ID)
        permissions = chat.permissions or ChatPermissions(
            can_send_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )
        await context.bot.restrict_chat_member(config.GROUP_ID, user.id, permissions)
    except TelegramError as e:
        log.warning("Huquq ochib bo'lmadi (%s): %s", user.id, e)
        await query.answer("Xatolik yuz berdi, mentorga murojaat qiling.", show_alert=True)
        return

    db.register_member(user.id, user.username, user.full_name)
    await query.answer("Xush kelibsiz!")
    await query.edit_message_text(
        f"✅ {mention_user(user)} qoidalarga rozi bo'ldi. Omad!"
    )


async def on_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_message.left_chat_member
    row = db.get_member(user.id)
    if row and row["status"] == "active":
        db.set_member_status(user.id, "left")
