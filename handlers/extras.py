import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import dm, esc, fmt_dt, is_mentor, is_mentor_message, mention_user

log = logging.getLogger(__name__)

STATUS_UZ = {"pending": "ko'rib chiqilmoqda", "approved": "tasdiqlangan", "denied": "rad etilgan"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "👋 Men MC Club nazoratchi botiman.\n\n"
        "• Vazifani shu yerga yuboring (havola, fayl yoki skrinshot).\n"
        f"• Sababli kechikish: <code>/uzr sabab</code> (dedlayndan kamida {config.EXCUSE_HOURS} soat oldin).\n\n"
        "Avval guruhda qoidalarga rozilik bildirgan bo'lishingiz kerak."
    )


async def show_ids(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    await msg.reply_text(
        f"Chat ID: <code>{msg.chat_id}</code>\nSizning ID: <code>{update.effective_user.id}</code>"
    )


# ---------------------------------------------------------- /read, /doc

async def read_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await is_mentor_message(context.bot, msg):
        return
    target = msg.reply_to_message
    if target is None:
        await msg.reply_text("Savol yozilgan xabarga reply qilib /read yozing.")
        return
    if not config.GUIDE_URL:
        await msg.reply_text("GUIDE_URL sozlanmagan (.env faylini tekshiring).")
        return

    await target.reply_text(
        "📖 Bu savolning javobi qo'llanmada bor. "
        f"Avval qo'llanmani o'qib chiqing: {esc(config.GUIDE_URL)}"
    )
    try:
        await msg.delete()
    except TelegramError:
        pass


# ----------------------------------------------------------------- /uzr

async def _create_excuse(bot, user, task, reason: str) -> str:
    """Uzr yozuvini yaratadi va mentorlarga karta yuboradi. Foydalanuvchiga javob matnini qaytaradi."""
    excuse_id = db.create_excuse(task["id"], user.id, reason)
    if excuse_id is None:
        old = db.find_excuse(task["id"], user.id)
        return f"Bu mashg'ulot uchun uzr allaqachon yuborilgan (holati: {STATUS_UZ[old['status']]})."

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"exc:{excuse_id}:ok"),
            InlineKeyboardButton("❌ Rad etish", callback_data=f"exc:{excuse_id}:no"),
        ]]
    )
    await bot.send_message(
        config.REVIEW_CHAT_ID,
        f"🙏 <b>Uzr so'rovi</b>\n👤 {mention_user(user)} — #{task['id']} «{esc(task['title'])}»\n"
        f"⏰ Dedlayn: {fmt_dt(task['deadline'])}\n📝 Sabab: {esc(reason)}",
        reply_markup=keyboard,
    )
    return "🙏 Uzringiz mentorlarga yuborildi. Natija haqida xabar beraman."


async def uzr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user = update.effective_message, update.effective_user
    member = db.get_member(user.id)
    if not member or member["status"] != "active":
        await msg.reply_text("Avval guruhda qoidalarga rozilik bildiring.")
        return

    reason = " ".join(context.args).strip()[:500]
    if not reason:
        await msg.reply_text("Sababni yozing: <code>/uzr sababingiz</code>")
        return

    open_tasks = [t for t in db.open_tasks() if t["created_at"] >= member["agreed_at"]]
    if not open_tasks:
        await msg.reply_text("Hozir ochiq mashg'ulot yo'q.")
        return
    limit = db.now() + config.EXCUSE_HOURS * 3600
    eligible = [t for t in open_tasks if t["deadline"] >= limit]
    if not eligible:
        await msg.reply_text(
            f"Uzr dedlayndan kamida {config.EXCUSE_HOURS} soat oldin yuborilishi kerak. Muddat o'tgan."
        )
        return

    if len(eligible) == 1:
        await msg.reply_text(await _create_excuse(context.bot, user, eligible[0], reason))
        return

    context.user_data["pending_excuse"] = reason
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"#{t['id']} {t['title'][:40]}", callback_data=f"uzr:{t['id']}")]
         for t in eligible]
    )
    await msg.reply_text("Qaysi mashg'ulot uchun uzr so'raysiz?", reply_markup=keyboard)


async def on_uzr_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, user = update.callback_query, update.callback_query.from_user
    reason = context.user_data.pop("pending_excuse", None)
    task = db.get_task(int(query.data.split(":")[1]))
    if not reason:
        await query.answer("Sabab topilmadi, /uzr ni qayta yuboring.", show_alert=True)
        return
    if not task or task["closed"] or task["deadline"] < db.now() + config.EXCUSE_HOURS * 3600:
        await query.answer("Bu mashg'ulot uchun uzr muddati o'tgan.", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(await _create_excuse(context.bot, user, task, reason))


async def on_excuse_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, mentor = update.callback_query, update.callback_query.from_user
    _, excuse_id, action = query.data.split(":")
    if not await is_mentor(context.bot, mentor.id):
        await query.answer("Faqat mentorlar.", show_alert=True)
        return

    excuse = db.get_excuse(int(excuse_id))
    if excuse is None:
        await query.answer("So'rov topilmadi (mashg'ulot bekor qilingan bo'lishi mumkin).", show_alert=True)
        return

    approved = action == "ok"
    db.set_excuse_status(excuse["id"], "approved" if approved else "denied")
    task = db.get_task(excuse["task_id"])
    label = "✅ Uzr tasdiqlandi" if approved else "❌ Uzr rad etildi"

    await query.answer()
    await query.edit_message_text(
        f"{label}\n👤 <a href=\"tg://user?id={excuse['user_id']}\">a'zo</a> — "
        f"#{task['id']} «{esc(task['title'])}»\n📝 {esc(excuse['reason'])}\n🧑‍🏫 {mention_user(mentor)}"
    )
    if approved:
        text = f"✅ #{task['id']} «{esc(task['title'])}» uchun uzringiz tasdiqlandi. Strike yozilmaydi."
    else:
        text = f"❌ #{task['id']} «{esc(task['title'])}» uchun uzringiz rad etildi. Dedlaynga ulguring: {fmt_dt(task['deadline'])}."
    await dm(context.bot, excuse["user_id"], text)
