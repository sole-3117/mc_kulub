import asyncio
import logging

from telegram import Bot
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import Application

import config
import db
from utils import STAFF_STATUSES, esc, mention_member

log = logging.getLogger(__name__)


async def check_deadlines(app: Application) -> None:
    """Har 30 soniyada chaqiriladi: dedlayni o'tgan ochiq vazifalarni qayta ishlaydi."""
    for task in db.due_tasks():
        try:
            await process_deadline(app.bot, task)
        except Exception:
            log.exception("Vazifa #%s ni qayta ishlashda xato", task["id"])


async def _should_skip(bot: Bot, user_id: int) -> bool:
    """Mentor/admin bo'lsa yoki guruhda qolmagan bo'lsa strike yozilmaydi."""
    if user_id in config.MENTOR_IDS:
        return True
    try:
        member = await bot.get_chat_member(config.GROUP_ID, user_id)
    except TelegramError as e:
        log.warning("A'zo holatini bilib bo'lmadi (%s): %s", user_id, e)
        return True
    if member.status in STAFF_STATUSES:
        return True
    if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        db.set_member_status(user_id, "left")
        return True
    return False


async def _send_chunks(bot: Bot, header: str, items: list[str]) -> None:
    text = header
    for item in items:
        if len(text) + len(item) > 3500:
            await bot.send_message(config.GROUP_ID, text)
            text = ""
        text += item
    if text:
        await bot.send_message(config.GROUP_ID, text)


async def process_deadline(bot: Bot, task) -> None:
    if not db.close_task(task["id"]):  # boshqa chaqiruv allaqachon qayta ishlagan
        return

    warned: list[str] = []
    kicked: list[str] = []
    kick_failed: list[str] = []

    for member in db.active_members():
        uid = member["user_id"]
        if member["agreed_at"] > task["created_at"]:  # vazifa e'lonidan keyin qo'shilgan
            continue
        if db.has_valid_submission(task["id"], uid) or db.has_approved_excuse(task["id"], uid):
            continue
        if await _should_skip(bot, uid):
            continue

        strikes = db.add_strike(task["id"], uid)
        if strikes is None:
            continue
        who = mention_member(member)

        if strikes >= config.MAX_STRIKES:
            try:
                await bot.ban_chat_member(config.GROUP_ID, uid)
                await bot.unban_chat_member(config.GROUP_ID, uid, only_if_banned=True)
                db.set_member_status(uid, "kicked")
                kicked.append(f"{who} ({strikes}/{config.MAX_STRIKES})")
            except TelegramError as e:
                log.warning("Chiqarib bo'lmadi (%s): %s", uid, e)
                kick_failed.append(f"{who} ({strikes}/{config.MAX_STRIKES})")
        else:
            warned.append(f"{who} ({strikes}/{config.MAX_STRIKES})")
        await asyncio.sleep(0.05)

    title = f"#{task['id']} «{esc(task['title'])}»"
    if not (warned or kicked or kick_failed):
        await bot.send_message(config.GROUP_ID, f"✅ {title} mashg'ulotini hamma topshirdi!")
        return

    if warned:
        await _send_chunks(
            bot, f"⚠️ {title} mashg'ulotini bajarmaganlar:\n", [", ".join(warned)]
        )
    if kicked:
        await bot.send_message(
            config.GROUP_ID, "🚫 Strike limiti to'ldi, guruhdan chiqarildi:\n" + ", ".join(kicked)
        )
    if kick_failed:
        await bot.send_message(
            config.GROUP_ID,
            "⚠️ Chiqarib bo'lmadi (botga adminlik huquqlarini bering): " + ", ".join(kick_failed),
        )
