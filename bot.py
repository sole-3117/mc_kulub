import asyncio
import html
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import ChatMemberUpdatedFilter, Command, IS_MEMBER, IS_NOT_MEMBER
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ErrorEvent,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# ==========================================
# 1. MUHIT O'ZGARUVCHILARI VA LOGGING
# ==========================================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

if not BOT_TOKEN or not OWNER_ID:
    raise ValueError("XATO: .env faylida BOT_TOKEN va OWNER_ID ko'rsatilishi shart!")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)
scheduler = AsyncIOScheduler()

DB_PATH = "mc_club.db"


# ==========================================
# 2. XAVFSIZ MA'LUMOTLAR BAZASI (SQLite WAL)
# ==========================================
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    with get_db() as conn:
        c = conn.cursor()
        # Dinamik sozlamalar jadvali (group_id, channel_id, default_doc)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Admin va mentorlar jadvali
        c.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT
            )
        """)
        # Asosiy egani adminlar ro'yxatiga qo'shish
        c.execute("INSERT OR IGNORE INTO admins (user_id, added_at) VALUES (?, datetime('now'))", (OWNER_ID,))

        # A'zolar jadvali
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                strikes INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)
        # Topshiriqlar jadvali
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                doc_url TEXT,
                deadline TEXT,
                is_closed INTEGER DEFAULT 0
            )
        """)
        # Topshirilgan vazifalar
        c.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                user_id INTEGER,
                task_id INTEGER,
                status TEXT DEFAULT 'pending',
                PRIMARY KEY (user_id, task_id)
            )
        """)
        # Sababli kechikishlar (Uzr)
        c.execute("""
            CREATE TABLE IF NOT EXISTS excuses (
                user_id INTEGER,
                task_id INTEGER,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                PRIMARY KEY (user_id, task_id)
            )
        """)
        conn.commit()


# Bazadan sozlamalarni o'qish va yozish funksiyalari
def get_setting(key: str, default: str = None) -> str:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )
        conn.commit()


def get_group_id() -> int:
    val = get_setting("group_id")
    return int(val) if val else 0


def get_channel_id() -> str:
    return get_setting("channel_id", "")


def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return c.fetchone() is not None


def get_all_admins() -> list[int]:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins")
        return [row[0] for row in c.fetchall()]


def get_active_task():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, title, doc_url, deadline FROM tasks WHERE is_closed = 0 ORDER BY id DESC LIMIT 1")
        return c.fetchone()


# ==========================================
# 3. YORDAMCHI XAVFSIZ FUNKSIYALAR
# ==========================================
FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)

MUTE_PERMISSIONS = ChatPermissions(can_send_messages=False)


async def safe_send(chat_id: int | str, text: str, reply_markup=None) -> types.Message | None:
    """Xatolik chiqarmasdan xavfsiz xabar yuborish"""
    if not chat_id:
        return None
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await safe_send(chat_id, text, reply_markup)
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        logger.warning(f"Xabar yuborib bo'lmadi ({chat_id}): {e}")
        return None
    except Exception as e:
        logger.error(f"Kutilmagan xato safe_send ({chat_id}): {e}")
        return None


# Global xatoliklarni ushlash (Serverda bot to'xtab qolmasligi uchun)
@dp.error()
async def global_error_handler(event: ErrorEvent):
    logger.exception(f"Global xatolik yuz berdi: {event.exception}")
    return True


# ==========================================
# 4. BOT ICHIDAN BOSHQARUV PANELI (/panel)
# ==========================================
class SetupStates(StatesGroup):
    waiting_channel = State()
    waiting_doc = State()
    waiting_new_admin = State()
    waiting_del_admin = State()
    waiting_reset_strike = State()


def get_panel_kb() -> InlineKeyboardMarkup:
    group_status = "✅ Ulangan" if get_group_id() else "❌ Ulanmagan"
    channel_status = get_channel_id() or "❌ Ulanmagan"
    doc_status = get_setting("default_doc", "Kiritilmagan")

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Yangi vazifa e'lon qilish", callback_data="panel:new_task")],
        [
            InlineKeyboardButton(text=f"👥 Guruh ({group_status})", callback_data="panel:info_group"),
            InlineKeyboardButton(text=f"📢 Kanal ({channel_status})", callback_data="panel:set_channel")
        ],
        [InlineKeyboardButton(text="📚 Asosiy qo'llanma linkini o'zgartirish", callback_data="panel:set_doc")],
        [
            InlineKeyboardButton(text="👮‍♂️ Admin qo'shish", callback_data="panel:add_admin"),
            InlineKeyboardButton(text="🗑 Admin o'chirish", callback_data="panel:del_admin")
        ],
        [
            InlineKeyboardButton(text="📊 Statistika", callback_data="panel:stats"),
            InlineKeyboardButton(text="♻️ Strike tozalash", callback_data="panel:reset_strike")
        ]
    ])


@router.message(Command("start", "panel"))
async def cmd_panel(message: types.Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private":
        return
    if not is_admin(message.from_user.id):
        return await message.answer(
            "🤖 <b>MC Club Nazoratchi Boti</b>\n\n"
            "Vazifa topshirish uchun xabaringizga <b>#vazifa</b> xeshtegini qo'shib yuboring.\n"
            "Sababli kechikish uchun: <code>/uzr sabab</code>",
            parse_mode="HTML"
        )

    group_id = get_group_id()
    channel_id = get_channel_id() or "Yo'q"
    doc_url = html.escape(get_setting("default_doc", "O'rnatilmagan"))
    admins_list = ", ".join(str(a) for a in get_all_admins())

    text = (
        "⚙️ <b>MC Club Boshqaruv Paneli</b>\n\n"
        f"🔹 <b>Ulangan guruh ID:</b> <code>{group_id}</code>\n"
        f"🔹 <b>Ulangan kanal:</b> <code>{html.escape(channel_id)}</code>\n"
        f"🔹 <b>Asosiy qo'llanma:</b> {doc_url}\n"
        f"🔹 <b>Adminlar ID:</b> <code>{admins_list}</code>\n\n"
        "💡 <i>Guruhni ulash uchun botni guruhga Admin qilib qo'shing va guruh ichida <code>/set_group</code> buyrug'ini yozing.</i>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_panel_kb(), disable_web_page_preview=True)


# Guruh ichida yozib guruhni avtomatik bog'lash
@router.message(Command("set_group"))
async def cmd_set_group(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply("❌ Bu buyruqni faqat guruh ichida yozing!")

    set_setting("group_id", str(message.chat.id))
    await message.reply(
        f"✅ <b>Guruh muvaffaqiyatli ulandi!</b>\nGuruh ID: <code>{message.chat.id}</code>",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("panel:"))
async def handle_panel_callbacks(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("❌ Ruxsat yo'q!", show_alert=True)

    action = call.data.split(":")[1]

    if action == "info_group":
        await call.answer(
            "Guruhni ulash uchun botni guruhga Admin qiling va guruh ichida /set_group buyrug'ini yozing!",
            show_alert=True
        )

    elif action == "set_channel":
        await call.message.answer(
            "📢 Kanal ID raqamini (masalan: <code>-100123456789</code>) yoki <code>@kanal_username</code> yuboring.\n"
            "<i>Eslatma: Bot kanalda Admin bo'lishi kerak! O'chirish uchun 0 yuboring.</i>",
            parse_mode="HTML"
        )
        await state.set_state(SetupStates.waiting_channel)
        await call.answer()

    elif action == "set_doc":
        await call.message.answer("📚 Asosiy qo'llanma uchun yangi URL havolani yuboring:")
        await state.set_state(SetupStates.waiting_doc)
        await call.answer()

    elif action == "add_admin":
        if call.from_user.id != OWNER_ID:
            return await call.answer("Faqat asosiy ega (Owner) yangi admin qo'sha oladi!", show_alert=True)
        await call.message.answer("👮‍♂️ Yangi mentor/adminning Telegram ID raqamini yuboring:")
        await state.set_state(SetupStates.waiting_new_admin)
        await call.answer()

    elif action == "del_admin":
        if call.from_user.id != OWNER_ID:
            return await call.answer("Faqat asosiy ega (Owner) admin o'chira oladi!", show_alert=True)
        await call.message.answer("🗑 O'chiriladigan adminning Telegram ID raqamini yuboring:")
        await state.set_state(SetupStates.waiting_del_admin)
        await call.answer()

    elif action == "stats":
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
            active_cnt = c.fetchone()[0]
            c.execute("SELECT user_id, username, full_name, strikes FROM users WHERE is_active = 1 AND strikes > 0 ORDER BY strikes DESC")
            striked_users = c.fetchall()

        striked_text = "\n".join(
            f"• {html.escape(u[2])} (@{html.escape(u[1] or 'yoq')}) — <b>{u[3]}/3</b> [ID: <code>{u[0]}</code>]"
            for u in striked_users
        ) or "Hozircha hech kimda Strike yo'q 🎉"

        await call.message.answer(
            f"📊 <b>Klub Statistikasi:</b>\n\n"
            f"👥 Faol a'zolar soni: <b>{active_cnt} ta</b>\n\n"
            f"⚠️ <b>Strike olganlar:</b>\n{striked_text}",
            parse_mode="HTML"
        )
        await call.answer()

    elif action == "reset_strike":
        await call.message.answer("♻️ Strike'lari tozalanadigan a'zoning Telegram ID raqamini yuboring:")
        await state.set_state(SetupStates.waiting_reset_strike)
        await call.answer()

    elif action == "new_task":
        if not get_group_id():
            return await call.answer("❌ Avval guruhni ulang (/set_group)!", show_alert=True)
        await call.message.answer("📝 Yangi mashg'ulot matni va vazifa shartini yuboring:")
        await state.set_state(TaskCreation.waiting_for_title)
        await call.answer()


@router.message(SetupStates.waiting_channel)
async def save_channel(message: types.Message, state: FSMContext):
    val = message.text.strip()
    set_setting("channel_id", "" if val == "0" else val)
    await state.clear()
    await message.answer("✅ Kanal sozlamasi saqlandi! /panel")


@router.message(SetupStates.waiting_doc)
async def save_doc(message: types.Message, state: FSMContext):
    set_setting("default_doc", message.text.strip())
    await state.clear()
    await message.answer("✅ Asosiy qo'llanma linki yangilandi! /panel")


@router.message(SetupStates.waiting_new_admin)
async def save_new_admin(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        return await message.answer("❌ Iltimos, faqat raqamdan iborat ID yuboring!")
    new_id = int(message.text.strip())
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO admins (user_id, added_at) VALUES (?, datetime('now'))", (new_id,))
        conn.commit()
    await state.clear()
    await message.answer(f"✅ <code>{new_id}</code> adminlar ro'yxatiga qo'shildi! /panel", parse_mode="HTML")


@router.message(SetupStates.waiting_del_admin)
async def delete_admin(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        return await message.answer("❌ Faqat raqam yuboring!")
    del_id = int(message.text.strip())
    if del_id == OWNER_ID:
        return await message.answer("❌ Asosiy egani o'chirib bo'lmaydi!")
    with get_db() as conn:
        conn.execute("DELETE FROM admins WHERE user_id = ?", (del_id,))
        conn.commit()
    await state.clear()
    await message.answer(f"🗑 <code>{del_id}</code> adminlikdan olindi! /panel", parse_mode="HTML")


@router.message(SetupStates.waiting_reset_strike)
async def reset_user_strike(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        return await message.answer("❌ Faqat raqamdan iborat Telegram ID yuboring!")
    uid = int(message.text.strip())
    with get_db() as conn:
        conn.execute("UPDATE users SET strikes = 0, is_active = 1 WHERE user_id = ?", (uid,))
        conn.commit()
    await state.clear()
    await message.answer(f"♻️ Foydalanuvchi (<code>{uid}</code>) strike'lari 0 qilindi! /panel", parse_mode="HTML")


# ==========================================
# 5. SHARTLI KIRISH (Onboarding & Rozilik filtri)
# ==========================================
@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: types.ChatMemberUpdated):
    group_id = get_group_id()
    if not group_id or event.chat.id != group_id:
        return
    user = event.new_chat_member.user
    if user.is_bot or is_admin(user.id):
        return

    try:
        await bot.restrict_chat_member(chat_id=group_id, user_id=user.id, permissions=MUTE_PERMISSIONS)
    except Exception as e:
        logger.warning(f"Yangi a'zoni cheklashda xatolik ({user.id}): {e}")

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Qoidalarga roziman va topshiriqlarni bajaraman",
            callback_data=f"accept_rules:{user.id}"
        )
    ]])

    safe_name = html.escape(f"@{user.username}" if user.username else user.full_name)
    await safe_send(
        chat_id=group_id,
        text=(
            f"Xush kelibsiz, <b>{safe_name}</b>!\n\n"
            f"📌 <b>MC Club ichki qoidalari:</b>\n"
            f"1. Berilgan topshiriqlarni dedlayngacha bajarish shart.\n"
            f"2. Topshiriq bajarmaslik 1 ta Strike (ogohlantirish) berilishiga sabab bo'ladi.\n"
            f"3. 3 ta Strike olgan a'zo guruhdan avtomatik chetlatiladi.\n"
            f"4. Savol berishdan oldin qo'llanma bilan tanishib chiqing.\n\n"
            f"Guruhda yozish huquqini olish uchun quyidagi tugmani bosing:"
        ),
        reply_markup=kb
    )


@router.message(Command("reg"))
async def manual_register(message: types.Message):
    user = message.from_user
    if user.is_bot:
        return
    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, full_name, strikes, is_active)
            VALUES (?, ?, ?, 0, 1)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name, is_active=1
        """, (user.id, user.username, user.full_name))
        conn.commit()
    await message.reply("✅ Siz MC Club nazorat bazasiga muvaffaqiyatli ro'yxatga olindingiz!")


@router.callback_query(F.data.startswith("accept_rules:"))
async def accept_rules_callback(call: types.CallbackQuery):
    target_user_id = int(call.data.split(":")[1])
    if call.from_user.id != target_user_id:
        return await call.answer("❌ Bu tugma faqat yangi qo'shilgan a'zo uchun!", show_alert=True)

    user = call.from_user
    group_id = get_group_id()

    try:
        await bot.restrict_chat_member(chat_id=group_id, user_id=user.id, permissions=FULL_PERMISSIONS)
    except Exception as e:
        logger.warning(f"Unmute xatoligi ({user.id}): {e}")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, full_name, strikes, is_active)
            VALUES (?, ?, ?, 0, 1)
            ON CONFLICT(user_id) DO UPDATE SET is_active=1, username=excluded.username, full_name=excluded.full_name
        """, (user.id, user.username, user.full_name))
        conn.commit()

    safe_name = html.escape(user.full_name)
    try:
        await call.message.edit_text(
            f"✅ <b>{safe_name}</b> qoidalarni qabul qildi va ro'yxatga olindi. Omad!",
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass


# ==========================================
# 6. YANGI TOPSHIRIQ QO'SHISH (Guruh + Kanalga e'lon)
# ==========================================
class TaskCreation(StatesGroup):
    waiting_for_title = State()
    waiting_for_doc = State()
    waiting_for_deadline = State()


@router.message(Command("new_task"))
async def cmd_new_task(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not get_group_id():
        return await message.answer("❌ Avval guruh ichida <code>/set_group</code> buyrug'ini yozib guruhni ulang!", parse_mode="HTML")
    await message.answer("📝 Yangi mashg'ulot matni va vazifa shartini yuboring:")
    await state.set_state(TaskCreation.waiting_for_title)


@router.message(TaskCreation.waiting_for_title)
async def process_title(message: types.Message, state: FSMContext):
    if not message.text:
        return await message.answer("❌ Iltimos, matn ko'rinishida yuboring:")
    await state.update_data(title=message.text)
    await message.answer("🔗 Qo'llanma (doc) uchun havolani yuboring:")
    await state.set_state(TaskCreation.waiting_for_doc)


@router.message(TaskCreation.waiting_for_doc)
async def process_doc(message: types.Message, state: FSMContext):
    if not message.text:
        return await message.answer("❌ Havolani matn ko'rinishida yuboring:")
    await state.update_data(doc_url=message.text.strip())
    await message.answer(
        "⏰ Dedlayn vaqtini kiriting.\n"
        "Format: <code>YYYY-MM-DD HH:MM</code> (Masalan: <code>2026-11-20 21:00</code>)",
        parse_mode="HTML"
    )
    await state.set_state(TaskCreation.waiting_for_deadline)


@router.message(TaskCreation.waiting_for_deadline)
async def process_deadline(message: types.Message, state: FSMContext):
    if not message.text:
        return
    try:
        deadline_dt = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
        if deadline_dt <= datetime.now():
            return await message.answer("❌ Dedlayn vaqti hozirgi vaqtdan keyin bo'lishi kerak!")
    except ValueError:
        return await message.answer("❌ Format xato! Namuna: <code>2026-11-20 21:00</code>", parse_mode="HTML")

    data = await state.get_data()
    deadline_str = deadline_dt.strftime("%Y-%m-%d %H:%M")

    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE tasks SET is_closed = 1 WHERE is_closed = 0")
        c.execute(
            "INSERT INTO tasks (title, doc_url, deadline, is_closed) VALUES (?, ?, ?, 0)",
            (data["title"], data["doc_url"], deadline_str)
        )
        task_id = c.lastrowid
        conn.commit()

    scheduler.add_job(
        check_deadline,
        trigger="date",
        run_date=deadline_dt,
        args=[task_id],
        id=f"task_{task_id}",
        replace_existing=True
    )

    announcement = (
        f"📢 <b>YANGI MASHG'ULOT (#{task_id})</b>\n\n"
        f"📋 <b>Vazifa:</b> {html.escape(data['title'])}\n"
        f"📚 <b>Qo'llanma:</b> {html.escape(data['doc_url'])}\n"
        f"⏳ <b>Dedlayn:</b> {deadline_str}\n\n"
        f"📤 <i>Topshirish uchun vazifangizni (GitHub link, fayl yoki skrinshot) "
        f"<b>#vazifa</b> xeshtegi bilan guruhga yoki botning o'ziga yuboring.</i>"
    )

    # Guruhga va (agar ulangan bo'lsa) Kanalga e'lon yuborish
    await safe_send(get_group_id(), announcement)
    channel_id = get_channel_id()
    if channel_id:
        await safe_send(channel_id, announcement)

    await message.answer(f"✅ #{task_id}-mashg'ulot e'lon qilindi va dedlayn taymeri o'rnatildi!")
    await state.clear()


# ==========================================
# 7. TOPSHIRIQ QABULI VA TASDIQLASH
# ==========================================
@router.message((F.text & F.text.contains("#vazifa")) | (F.caption & F.caption.contains("#vazifa")))
async def handle_submission(message: types.Message):
    task = get_active_task()
    if not task:
        return await message.reply("ℹ️ Hozirda faol topshiriq yo'q.")

    task_id = task[0]
    user = message.from_user

    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, full_name, strikes, is_active)
            VALUES (?, ?, ?, 0, 1)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name, is_active=1
        """, (user.id, user.username, user.full_name))
        conn.execute("""
            INSERT INTO submissions (user_id, task_id, status)
            VALUES (?, ?, 'pending')
            ON CONFLICT(user_id, task_id) DO UPDATE SET status='pending'
        """, (user.id, task_id))
        conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Qabul qilindi", callback_data=f"sub_ok:{task_id}:{user.id}"),
        InlineKeyboardButton(text="❌ Qayta ishlang", callback_data=f"sub_no:{task_id}:{user.id}")
    ]])

    safe_name = html.escape(user.full_name)
    safe_user = html.escape(user.username or "yoq")

    if message.chat.id == get_group_id():
        await message.reply(
            f"📥 <b>{safe_name}</b> #{task_id}-mashg'ulotni tekshiruvga yubordi.\nMentorlar baholashi kutilmoqda:",
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        # Lichkadan yuborilsa, asosiy adminga uzatiladi
        try:
            await message.forward(chat_id=OWNER_ID)
        except Exception:
            pass
        await safe_send(
            chat_id=OWNER_ID,
            text=f"📥 <b>{safe_name}</b> (@{safe_user}) #{task_id}-vazifani bot orqali yubordi:",
            reply_markup=kb
        )
        await message.reply("✅ Vazifangiz mentorlarga yuborildi. Tekshiruv natijasini kuting!")


@router.callback_query(F.data.startswith(("sub_ok:", "sub_no:")))
async def review_submission(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("❌ Faqat mentorlar vazifani baholay oladi!", show_alert=True)

    action, task_id_str, user_id_str = call.data.split(":")
    task_id, user_id = int(task_id_str), int(user_id_str)
    status = "approved" if action == "sub_ok" else "rejected"

    with get_db() as conn:
        conn.execute("UPDATE submissions SET status = ? WHERE user_id = ? AND task_id = ?", (status, user_id, task_id))
        conn.commit()

    mentor_name = html.escape(call.from_user.full_name)
    base_text = html.escape(call.message.text or call.message.caption or "Vazifa")

    if status == "approved":
        new_text = f"{base_text}\n\n✅ <b>Qabul qilindi!</b> (Mentor: {mentor_name})"
        await safe_send(user_id, f"🎉 #{task_id}-mashg'ulot bo'yicha vazifangiz <b>qabul qilindi!</b>")
    else:
        new_text = f"{base_text}\n\n❌ <b>Qaytarildi: Qayta ishlang!</b> (Mentor: {mentor_name})"
        await safe_send(user_id, f"⚠️ #{task_id}-mashg'ulot bo'yicha vazifangiz <b>qaytarildi</b>. Xatolarni tuzatib qayta yuboring!")

    try:
        await call.message.edit_text(new_text, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await call.answer("Baholandi!")


# ==========================================
# 8. SABABLI KECHIKISH (/uzr tizimi)
# ==========================================
@router.message(Command("uzr"))
async def request_excuse(message: types.Message):
    task = get_active_task()
    if not task:
        return await message.reply("ℹ️ Hozirda faol topshiriq yo'q.")

    task_id, _, _, deadline_str = task
    deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")

    if datetime.now() > deadline_dt - timedelta(hours=12):
        return await message.reply(
            "❌ Uzr so'rash muddati o'tgan! Qoidaga ko'ra dedlayndan kamida 12 soat oldin yozish kerak."
        )

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply(
            "✍️ Iltimos, sababini ham qo'shib yozing.\nNamuna: <code>/uzr Kompyuterim buzilib qoldi</code>",
            parse_mode="HTML"
        )

    reason = parts[1].strip()
    user = message.from_user

    with get_db() as conn:
        conn.execute("""
            INSERT INTO excuses (user_id, task_id, reason, status)
            VALUES (?, ?, ?, 'pending')
            ON CONFLICT(user_id, task_id) DO UPDATE SET reason=excluded.reason, status='pending'
        """, (user.id, task_id, reason))
        conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Qabul qilish", callback_data=f"exc_ok:{task_id}:{user.id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"exc_no:{task_id}:{user.id}")
    ]])

    await safe_send(
        chat_id=OWNER_ID,
        text=(
            f"🙏 <b>Sababli kechikish so'rovi (#{task_id}-vazifa)</b>\n"
            f"👤 A'zo: {html.escape(user.full_name)} (@{html.escape(user.username or 'yoq')})\n"
            f"💬 Sabab: <i>{html.escape(reason)}</i>"
        ),
        reply_markup=kb
    )
    await message.reply("✅ So'rovingiz adminga yuborildi. Tasdiqlansa, bu safar Strike yozilmaydi.")


@router.callback_query(F.data.startswith(("exc_ok:", "exc_no:")))
async def review_excuse(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("❌ Faqat adminlar tasdiqlay oladi!", show_alert=True)

    action, task_id_str, user_id_str = call.data.split(":")
    task_id, user_id = int(task_id_str), int(user_id_str)
    status = "approved" if action == "exc_ok" else "rejected"

    with get_db() as conn:
        conn.execute("UPDATE excuses SET status = ? WHERE user_id = ? AND task_id = ?", (status, user_id, task_id))
        conn.commit()

    verdict = "✅ Uzr qabul qilindi" if status == "approved" else "❌ Uzr rad etildi"
    try:
        await call.message.edit_text(html.escape(call.message.text) + f"\n\n<b>{verdict}</b>", parse_mode="HTML")
    except TelegramBadRequest:
        pass

    await safe_send(user_id, f"ℹ️ #{task_id}-vazifa bo'yicha uzr so'rovingiz holati: <b>{verdict}</b>")
    await call.answer("Saqlandi!")


# ==========================================
# 9. "QO'LLANMANI O'QI" TEZKOR JAVOB (/read va /doc)
# ==========================================
@router.message(Command("read", "doc"))
async def quick_doc_reply(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if not message.reply_to_message:
        return await message.reply("ℹ️ Bu buyruqni savol berilgan xabarga <b>reply</b> qilib yozing.", parse_mode="HTML")

    task = get_active_task()
    doc_url = task[2] if task else get_setting("default_doc", "Qo'llanma linki hali kiritilmagan")

    await message.reply_to_message.reply(
        f"📖 Bu savolning javobi qo'llanmada bor. Avval qo'llanmani diqqat bilan o'qib chiqing:\n👉 {html.escape(doc_url)}",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    try:
        await message.delete()
    except Exception:
        pass


# ==========================================
# 10. DEDLAYN TEKSHIRUVI VA AVTO-KICK (3 STRIKE)
# ==========================================
async def check_deadline(task_id: int):
    group_id = get_group_id()
    admin_ids = set(get_all_admins())

    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE tasks SET is_closed = 1 WHERE id = ?", (task_id,))
        c.execute("SELECT user_id, username, full_name, strikes FROM users WHERE is_active = 1")
        active_users = c.fetchall()

        violators_report = []

        for user_id, username, full_name, strikes in active_users:
            if user_id in admin_ids:
                continue

            # Vazifa tasdiqlanganmi?
            c.execute("SELECT status FROM submissions WHERE user_id = ? AND task_id = ?", (user_id, task_id))
            sub = c.fetchone()
            if sub and sub[0] == "approved":
                continue

            # Uzri qabul qilinganmi?
            c.execute("SELECT status FROM excuses WHERE user_id = ? AND task_id = ?", (user_id, task_id))
            exc = c.fetchone()
            if exc and exc[0] == "approved":
                continue

            new_strikes = strikes + 1
            raw_mention = f"@{username}" if username else full_name
            safe_mention = html.escape(raw_mention)

            if new_strikes >= 3:
                c.execute("UPDATE users SET strikes = ?, is_active = 0 WHERE user_id = ?", (new_strikes, user_id))
                violators_report.append(f"🚫 {safe_mention} (3/3 — Guruhdan chetlatildi)")
                if group_id:
                    try:
                        await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                        await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
                    except Exception as e:
                        logger.warning(f"Kick xatoligi ({user_id}): {e}")
            else:
                c.execute("UPDATE users SET strikes = ? WHERE user_id = ?", (new_strikes, user_id))
                violators_report.append(f"⚠️ {safe_mention} ({new_strikes}/3)")

        conn.commit()

    if violators_report:
        report_text = (
            f"⏰ <b>#{task_id}-mashg'ulot dedlayni tugadi!</b>\n\n"
            f"Topshiriqni bajarmaganlar ro'yxati:\n" + "\n".join(violators_report)
        )
    else:
        report_text = f"🎉 <b>#{task_id}-mashg'ulot dedlayni tugadi!</b> Barcha faol a'zolar vazifani o'z vaqtida bajardi!"

    await safe_send(group_id, report_text)


# ==========================================
# 11. ISHGA TUSHIRISH VA TAYMERLARNI TIKLASH
# ==========================================
def restore_scheduled_tasks():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, deadline FROM tasks WHERE is_closed = 0")
        open_tasks = c.fetchall()

    for task_id, deadline_str in open_tasks:
        try:
            deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
            if deadline_dt > datetime.now():
                scheduler.add_job(
                    check_deadline,
                    trigger="date",
                    run_date=deadline_dt,
                    args=[task_id],
                    id=f"task_{task_id}",
                    replace_existing=True
                )
            else:
                scheduler.add_job(check_deadline, args=[task_id])
        except Exception as e:
            logger.error(f"Taymerni tiklashda xato (task {task_id}): {e}")


async def main():
    init_db()
    scheduler.start()
    restore_scheduled_tasks()
    logger.info("MC Club Bot serverda muvaffaqiyatli ishga tushdi!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
