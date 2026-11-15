import sqlite3
import time

import config

conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS members(
    user_id   INTEGER PRIMARY KEY,
    username  TEXT,
    full_name TEXT NOT NULL,
    agreed_at INTEGER NOT NULL,
    strikes   INTEGER NOT NULL DEFAULT 0,
    status    TEXT NOT NULL DEFAULT 'active'   -- active | left | kicked
);
CREATE TABLE IF NOT EXISTS tasks(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    link            TEXT,
    deadline        INTEGER NOT NULL,
    created_by      INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    announce_msg_id INTEGER,
    closed          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS submissions(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    src_chat_id  INTEGER NOT NULL,
    src_msg_id   INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | rejected
    submitted_at INTEGER NOT NULL,
    reviewed_by  INTEGER,
    UNIQUE(task_id, user_id)
);
CREATE TABLE IF NOT EXISTS excuses(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | denied
    created_at INTEGER NOT NULL,
    UNIQUE(task_id, user_id)
);
CREATE TABLE IF NOT EXISTS strike_log(
    task_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(task_id, user_id)
);
"""


def init() -> None:
    with conn:
        conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")


def now() -> int:
    return int(time.time())


# ---------------------------------------------------------------- members

def get_member(user_id: int):
    return conn.execute("SELECT * FROM members WHERE user_id=?", (user_id,)).fetchone()


def active_members():
    return conn.execute("SELECT * FROM members WHERE status='active'").fetchall()


def register_member(user_id: int, username: str | None, full_name: str) -> None:
    row = get_member(user_id)
    with conn:
        if row is None:
            conn.execute(
                "INSERT INTO members(user_id, username, full_name, agreed_at) VALUES(?,?,?,?)",
                (user_id, username, full_name, now()),
            )
        else:
            # guruhdan chiqarilgan a'zo qayta ro'yxatdan o'tsa, strikelari nolga tushadi
            strikes = 0 if row["status"] == "kicked" else row["strikes"]
            conn.execute(
                "UPDATE members SET username=?, full_name=?, agreed_at=?, strikes=?, status='active' "
                "WHERE user_id=?",
                (username, full_name, now(), strikes, user_id),
            )


def set_member_status(user_id: int, status: str) -> None:
    with conn:
        conn.execute("UPDATE members SET status=? WHERE user_id=?", (status, user_id))


def add_strike(task_id: int, user_id: int) -> int | None:
    """Strike yozadi va yangi sonini qaytaradi. Shu vazifa uchun allaqachon yozilgan bo'lsa - None."""
    try:
        with conn:
            conn.execute(
                "INSERT INTO strike_log(task_id, user_id, created_at) VALUES(?,?,?)",
                (task_id, user_id, now()),
            )
            conn.execute("UPDATE members SET strikes=strikes+1 WHERE user_id=?", (user_id,))
    except sqlite3.IntegrityError:
        return None
    return conn.execute("SELECT strikes FROM members WHERE user_id=?", (user_id,)).fetchone()[0]


# ------------------------------------------------------------------ tasks

def create_task(title, description, link, deadline, created_by) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO tasks(title, description, link, deadline, created_by, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (title, description, link, deadline, created_by, now()),
        )
    return cur.lastrowid


def get_task(task_id: int):
    return conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()


def task_by_announce(msg_id: int):
    return conn.execute("SELECT * FROM tasks WHERE announce_msg_id=?", (msg_id,)).fetchone()


def set_announce_msg(task_id: int, msg_id: int) -> None:
    with conn:
        conn.execute("UPDATE tasks SET announce_msg_id=? WHERE id=?", (msg_id, task_id))


def open_tasks():
    return conn.execute("SELECT * FROM tasks WHERE closed=0 ORDER BY deadline").fetchall()


def submittable_tasks():
    return conn.execute(
        "SELECT * FROM tasks WHERE closed=0 AND deadline>? ORDER BY deadline", (now(),)
    ).fetchall()


def due_tasks():
    return conn.execute(
        "SELECT * FROM tasks WHERE closed=0 AND deadline<=? ORDER BY deadline", (now(),)
    ).fetchall()


def close_task(task_id: int) -> bool:
    """Vazifani atomik tarzda yopadi. Faqat birinchi chaqiruv True qaytaradi."""
    with conn:
        cur = conn.execute("UPDATE tasks SET closed=1 WHERE id=? AND closed=0", (task_id,))
    return cur.rowcount == 1


def cancel_task(task_id: int):
    """Ochiq vazifani va unga bog'liq yozuvlarni o'chiradi. O'chirilgan vazifani qaytaradi."""
    task = get_task(task_id)
    if task is None or task["closed"]:
        return None
    with conn:
        conn.execute("DELETE FROM submissions WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM excuses WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return task


# ------------------------------------------------------------ submissions

def upsert_submission(task_id: int, user_id: int, chat_id: int, msg_id: int) -> int:
    with conn:
        conn.execute(
            "INSERT INTO submissions(task_id, user_id, src_chat_id, src_msg_id, submitted_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(task_id, user_id) DO UPDATE SET "
            "src_chat_id=excluded.src_chat_id, src_msg_id=excluded.src_msg_id, "
            "status='pending', submitted_at=excluded.submitted_at, reviewed_by=NULL",
            (task_id, user_id, chat_id, msg_id, now()),
        )
    return conn.execute(
        "SELECT id FROM submissions WHERE task_id=? AND user_id=?", (task_id, user_id)
    ).fetchone()[0]


def get_submission(sub_id: int):
    return conn.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()


def set_submission_status(sub_id: int, status: str, reviewer: int) -> None:
    with conn:
        conn.execute(
            "UPDATE submissions SET status=?, reviewed_by=? WHERE id=?", (status, reviewer, sub_id)
        )


def has_valid_submission(task_id: int, user_id: int) -> bool:
    """Topshirilgan (kutilmoqda yoki qabul qilingan) bo'lsa True. Rad etilgani hisoblanmaydi."""
    row = conn.execute(
        "SELECT 1 FROM submissions WHERE task_id=? AND user_id=? AND status IN ('pending','accepted')",
        (task_id, user_id),
    ).fetchone()
    return row is not None


def submission_count(task_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE task_id=?", (task_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------- excuses

def create_excuse(task_id: int, user_id: int, reason: str) -> int | None:
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO excuses(task_id, user_id, reason, created_at) VALUES(?,?,?,?)",
                (task_id, user_id, reason, now()),
            )
    except sqlite3.IntegrityError:
        return None
    return cur.lastrowid


def find_excuse(task_id: int, user_id: int):
    return conn.execute(
        "SELECT * FROM excuses WHERE task_id=? AND user_id=?", (task_id, user_id)
    ).fetchone()


def get_excuse(excuse_id: int):
    return conn.execute("SELECT * FROM excuses WHERE id=?", (excuse_id,)).fetchone()


def set_excuse_status(excuse_id: int, status: str) -> None:
    with conn:
        conn.execute("UPDATE excuses SET status=? WHERE id=?", (status, excuse_id))


def has_approved_excuse(task_id: int, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM excuses WHERE task_id=? AND user_id=? AND status='approved'",
        (task_id, user_id),
    ).fetchone()
    return row is not None
