import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _int_set(value: str) -> set[int]:
    return {int(x) for x in value.replace(" ", "").split(",") if x}


BOT_TOKEN = os.environ["BOT_TOKEN"]
GROUP_ID = int(os.getenv("GROUP_ID") or 0)
REVIEW_CHAT_ID = int(os.getenv("REVIEW_CHAT_ID") or GROUP_ID)
MENTOR_IDS = _int_set(os.getenv("MENTOR_IDS", ""))
GUIDE_URL = os.getenv("GUIDE_URL", "").strip()

MAX_STRIKES = int(os.getenv("MAX_STRIKES", "3"))
EXCUSE_HOURS = int(os.getenv("EXCUSE_HOURS", "12"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
DB_PATH = os.getenv("DB_PATH", "club.db")
