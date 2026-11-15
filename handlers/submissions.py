import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import dm, esc, fmt_dt, is_mentor, mention, mention_user

log = logging.getLogger(__name__)

HASHTAG = re.compile(r"#topshiriq_?(\d+)?", re.IGNORECASE)


async def _post_review_card(bot, sub_id: int, task, user, src_chat_id: int, src_msg_id: int) -> None:
    """Mentorlar uchun ✅/❌ tugmali karta yuboradi."""
    if config.REVIEW_CHAT_ID == src_chat_id:
        reply_to = src_msg_id
    else:
        copied = await bot.copy_message(config.REVIEW_CHAT_ID, src_chat_id, src_msg_id)
        reply_to = copied.message_id

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Qabul qilindi", callback_data=f"rev:{sub_id}:ok"),
            InlineKeyboardButton("❌ Qayta ishlang", callback_data=f"rev:{sub_id}:no"),
        ]]
    )
    await bot.send_message(
        config.REVIEW_CHAT_ID,
        f"📥 {mention_user(user)} — #{task['id']} «{esc(task['title'])}» topshirdi.",
        reply_markup=keyboard,
        reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True),
    )


async def _submit(bot, user, task, src_chat_id: int, src_msg_id: int) -> None:
    sub_id = db.upsert_submission(task["id"], user.id, src_chat_id, src_msg_id)
    await _post_review_card(bot, sub_id, task, user, src_chat_id, src_msg_id)


# ------------------------------------------------------ shaxsiy chatdan

async def private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user = update.effective_message, update.effective_user
    member = db.get_member(user.id)
    if not member or member["status"] != "active":
        await msg.reply_text(
            "Avval guruhda qoidalarga rozilik bildiring, shundan so'ng vazifa topshira olasiz."
        )
        return

    tasks = db.submittable_tasks()
    if not tasks:
        await msg.reply_text("Hozir topshirish mumkin bo'lgan mashg'ulot yo'q.")
        return

    if len(tasks) == 1:
        await _submit(context.bot, user, tasks[0], msg.chat_id, msg.message_id)
        await msg.reply_text(
            f"✅ #{tasks[0]['id']} «{esc(tasks[0]['title'])}» uchun yuborildi. Mentor ko'rib chiqadi."
        )
        return

    context.user_data["pending_sub"] = (msg.chat_id, msg.message_id)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"#{t['id']} {t['title'][:40]}", callback_data=f"pick:{t['id']}")]
         for t in tasks]
    )
    await msg.reply_text("Qaysi mashg'ulot uchun topshiryapsiz?", reply_markup=keyboard)


async def on_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, user = update.callback_query, update.callback_query.from_user
    task_id = int(query.data.split(":")[1])
    pending = context.user_data.pop("pending_sub", None)
    if not pending:
        await query.answer("Xabar topilmadi, vazifani qayta yuboring.", show_alert=True)
        return

    task = db.get_task(task_id)
    member = db.get_member(user.id)
    if not member or member["status"] != "active":
        await query.answer("Avval guruhda ro'yxatdan o'ting.", show_alert=True)
        return
    if not task or task["closed"] or task["deadline"] <= db.now():
        await query.answer("Bu mashg'ulot yopilgan.", show_alert=True)
        return

    await _submit(context.bot, user, task, *pending)
    await query.answer()
    await query.edit_message_text(
        f"✅ #{task['id']} «{esc(task['title'])}» uchun yuborildi. Mentor ko'rib chiqadi."
    )


# ----------------------------------------------------------- guruhdan

async def group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user = update.effective_message, update.effective_user
    if user is None or user.is_bot or msg.sender_chat:
        return

    task = None
    if msg.reply_to_message:
        task = db.task_by_announce(msg.reply_to_message.message_id)
    tag = HASHTAG.search(msg.text or msg.caption or "")
    if task is None and tag is None:
        return

    member = db.get_member(user.id)
    if not member or member["status"] != "active":
        return

    if task is None:
        if tag.group(1):
            task = db.get_task(int(tag.group(1)))
        else:
            open_now = db.submittable_tasks()
            if len(open_now) != 1:
                await msg.reply_text(
                    "Qaysi mashg'ulot uchun? Raqam bilan yozing: <code>#topshiriq3</code>"
                    if open_now else "Hozir topshirish mumkin bo'lgan mashg'ulot yo'q."
                )
                return
            task = open_now[0]

    if task is None or task["closed"] or task["deadline"] <= db.now():
        await msg.reply_text("Bu mashg'ulot topilmadi yoki dedlayni tugagan.")
        return

    await _submit(context.bot, user, task, msg.chat_id, msg.message_id)
    try:
        await msg.set_reaction("👍")
    except TelegramError:
        pass


# --------------------------------------------------- mentor baholashi

async def on_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, mentor = update.callback_query, update.callback_query.from_user
    _, sub_id, action = query.data.split(":")
    if not await is_mentor(context.bot, mentor.id):
        await query.answer("Faqat mentorlar baholay oladi.", show_alert=True)
        return

    sub = db.get_submission(int(sub_id))
    if sub is None:
        await query.answer("Topshiriq topilmadi (mashg'ulot bekor qilingan bo'lishi mumkin).", show_alert=True)
        return

    accepted = action == "ok"
    db.set_submission_status(sub["id"], "accepted" if accepted else "rejected", mentor.id)
    task, member = db.get_task(sub["task_id"]), db.get_member(sub["user_id"])
    label = "✅ Qabul qilindi" if accepted else "❌ Qayta ishlang"
    name = mention(member["user_id"], member["username"], member["full_name"]) if member else str(sub["user_id"])

    await query.answer()
    await query.edit_message_text(
        f"{label}\n👤 {name} — #{task['id']} «{esc(task['title'])}»\n🧑‍🏫 {mention_user(mentor)}"
    )

    if accepted:
        text = f"✅ #{task['id']} «{esc(task['title'])}» topshirig'ingiz qabul qilindi."
    else:
        text = (
            f"❌ #{task['id']} «{esc(task['title'])}» topshirig'ini qayta ishlang.\n"
            f"Dedlayn: {fmt_dt(task['deadline'])}. Yangi variantni shu yerga yuboring."
        )
    await dm(context.bot, sub["user_id"], text)
