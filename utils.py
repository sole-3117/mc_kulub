import html
import logging
from datetime import datetime

from telegram import Bot, Message
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

import config

log = logging.getLogger(__name__)

DT_FORMAT = "%d.%m.%Y %H:%M"
STAFF_STATUSES = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


def esc(text: str | None) -> str:
    return html.escape(text or "")


def mention(user_id: int, username: str | None, full_name: str) -> str:
    if username:
        return f"@{username}"
    return f'<a href="tg://user?id={user_id}">{esc(full_name)}</a>'


def mention_user(user) -> str:
    return mention(user.id, user.username, user.full_name)


def mention_member(row) -> str:
    return mention(row["user_id"], row["username"], row["full_name"])


def fmt_dt(ts: int) -> str:
    return datetime.fromtimestamp(ts, config.TZ).strftime(DT_FORMAT)


def parse_dt(text: str) -> int:
    """'25.11.2026 23:59' (Toshkent vaqti) -> UTC timestamp. Noto'g'ri format bo'lsa ValueError."""
    dt = datetime.strptime(text.strip(), DT_FORMAT).replace(tzinfo=config.TZ)
    return int(dt.timestamp())


async def is_mentor(bot: Bot, user_id: int) -> bool:
    if user_id in config.MENTOR_IDS:
        return True
    try:
        member = await bot.get_chat_member(config.GROUP_ID, user_id)
    except TelegramError:
        return False
    return member.status in STAFF_STATUSES


async def is_mentor_message(bot: Bot, msg: Message) -> bool:
    # guruhda "anonim admin" sifatida yozgan admin
    if msg.sender_chat and msg.sender_chat.id == config.GROUP_ID:
        return True
    return msg.from_user is not None and await is_mentor(bot, msg.from_user.id)


async def dm(bot: Bot, user_id: int, text: str) -> None:
    """Shaxsiy xabar yuboradi; foydalanuvchi botni ishga tushirmagan bo'lsa jim o'tadi."""
    try:
        await bot.send_message(user_id, text)
    except TelegramError as e:
        log.info("DM yuborilmadi (%s): %s", user_id, e)
