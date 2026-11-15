import logging
import warnings

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.error import TelegramError
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import db
from utils import esc, fmt_dt, is_mentor, is_mentor_message, parse_dt

log = logging.getLogger(__name__)

TITLE, DESC, LINK, DEADLINE, CONFIRM = range(5)


def announcement_text(task, bot_username: str) -> str:
    lines = [f"📌 <b>Mashg'ulot #{task['id']}: {esc(task['title'])}</b>", "", esc(task["description"]), ""]
    if task["link"]:
        lines.append(f"📖 Qo'llanma: {esc(task['link'])}")
    lines += [
        f"⏰ Dedlayn: <b>{fmt_dt(task['deadline'])}</b>",
        "",
        f"📥 Topshirish: @{bot_username} ga shaxsiy xabarda, shu e'longa reply qilib "
        f"yoki <code>#topshiriq{task['id']}</code> xeshtegi bilan.",
        f"🙏 Sababli kechikish: dedlayndan {config.EXCUSE_HOURS} soat oldin botga "
        f"<code>/uzr sabab</code> deb yozing.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------- /yangi (suhbat)

async def new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_mentor(context.bot, update.effective_user.id):
        await update.message.reply_text("Bu buyruq faqat mentorlar uchun.")
        return ConversationHandler.END
    context.user_data["new_task"] = {}
    await update.message.reply_text("📝 Mashg'ulot nomini yozing.\nBekor qilish: /cancel")
    return TITLE


async def got_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_task"]["title"] = update.message.text.strip()
    await update.message.reply_text("Vazifa matnini yozing:")
    return DESC


async def got_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_task"]["description"] = update.message.text.strip()
    await update.message.reply_text("Qo'llanma havolasini yuboring (yo'q bo'lsa: -)")
    return LINK


async def got_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    link = None if text == "-" else text
    if link and not link.startswith(("http://", "https://")):
        await update.message.reply_text("Havola http:// yoki https:// bilan boshlanishi kerak (yoki - yozing).")
        return LINK
    context.user_data["new_task"]["link"] = link
    await update.message.reply_text(
        "⏰ Dedlaynni <code>DD.MM.YYYY HH:MM</code> ko'rinishida yozing (Toshkent vaqti).\n"
        "Masalan: <code>25.11.2026 23:59</code>"
    )
    return DEADLINE


async def got_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        deadline = parse_dt(update.message.text)
    except ValueError:
        await update.message.reply_text("Format noto'g'ri. Masalan: <code>25.11.2026 23:59</code>")
        return DEADLINE
    if deadline <= db.now():
        await update.message.reply_text("Dedlayn kelajakda bo'lishi kerak. Qayta yozing:")
        return DEADLINE

    data = context.user_data["new_task"]
    data["deadline"] = deadline
    preview = (
        f"<b>{esc(data['title'])}</b>\n\n{esc(data['description'])}\n\n"
        f"📖 {esc(data['link']) if data['link'] else 'Havolasiz'}\n"
        f"⏰ {fmt_dt(deadline)}\n\nGuruhga e'lon qilaymi?"
    )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("📢 E'lon qilish", callback_data="task_pub"),
            InlineKeyboardButton("✖ Bekor qilish", callback_data="task_cancel"),
        ]]
    )
    await update.message.reply_text(preview, reply_markup=keyboard)
    return CONFIRM


async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = context.user_data.pop("new_task", None)
    if not data:
        await query.answer("Ma'lumot topilmadi, /yangi dan qayta boshlang.", show_alert=True)
        return ConversationHandler.END

    task_id = db.create_task(
        data["title"], data["description"], data["link"], data["deadline"], query.from_user.id
    )
    task = db.get_task(task_id)
    try:
        sent = await context.bot.send_message(
            config.GROUP_ID, announcement_text(task, context.bot.username)
        )
    except TelegramError as e:
        db.cancel_task(task_id)
        await query.answer()
        await query.edit_message_text(f"❌ Guruhga yuborib bo'lmadi: {esc(str(e))}")
        return ConversationHandler.END

    db.set_announce_msg(task_id, sent.message_id)
    await query.answer("E'lon qilindi")
    await query.edit_message_text(f"✅ Mashg'ulot #{task_id} guruhga e'lon qilindi.")
    return ConversationHandler.END


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_task", None)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Bekor qilindi.")
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_task", None)
    await update.message.reply_text("Bekor qilindi.")
    return ConversationHandler.END


def new_task_conversation() -> ConversationHandler:
    text = filters.TEXT & ~filters.COMMAND
    with warnings.catch_warnings():
        # tasdiqlash tugmalari suhbat ichida; per_message ogohlantirishi bu yerda ahamiyatsiz
        warnings.simplefilter("ignore", PTBUserWarning)
        return _build_conversation(text)


def _build_conversation(text) -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("yangi", new_start, filters=filters.ChatType.PRIVATE)],
        states={
            TITLE: [MessageHandler(text, got_title)],
            DESC: [MessageHandler(text, got_desc)],
            LINK: [MessageHandler(text, got_link)],
            DEADLINE: [MessageHandler(text, got_deadline)],
            CONFIRM: [
                CallbackQueryHandler(publish, pattern=r"^task_pub$"),
                CallbackQueryHandler(cancel_callback, pattern=r"^task_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )


# ------------------------------------------------- /vazifalar va /bekor

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await is_mentor_message(context.bot, msg):
        return
    rows = db.open_tasks()
    if not rows:
        await msg.reply_text("Ochiq mashg'ulotlar yo'q.")
        return
    lines = [
        f"#{t['id']} «{esc(t['title'])}» — {fmt_dt(t['deadline'])} "
        f"(topshirganlar: {db.submission_count(t['id'])})"
        for t in rows
    ]
    await msg.reply_text("\n".join(lines) + "\n\nBekor qilish: /bekor &lt;raqam&gt;")


async def cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await is_mentor_message(context.bot, msg):
        return
    if not context.args or not context.args[0].isdigit():
        await msg.reply_text("Format: /bekor &lt;mashg'ulot raqami&gt;")
        return
    task = db.cancel_task(int(context.args[0]))
    if task is None:
        await msg.reply_text("Bunday ochiq mashg'ulot topilmadi.")
        return
    await msg.reply_text(f"✅ Mashg'ulot #{task['id']} bekor qilindi.")
    if task["announce_msg_id"]:
        try:
            await context.bot.send_message(
                config.GROUP_ID,
                f"❌ Mashg'ulot #{task['id']} «{esc(task['title'])}» bekor qilindi.",
                reply_parameters=ReplyParameters(
                    message_id=task["announce_msg_id"], allow_sending_without_reply=True
                ),
            )
        except TelegramError as e:
            log.info("Bekor xabarini yuborib bo'lmadi: %s", e)
