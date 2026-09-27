import asyncio
import hashlib
import hmac
import json
import re
import sqlite3
import time
from urllib.parse import parse_qsl
from pathlib import Path

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    MenuButtonWebApp,
    FSInputFile,
    ChatJoinRequest,
)



# =========================================================
# CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "eng"

DB_FILE = BASE_DIR / "soliqapp.db"


def _db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            referrer_id INTEGER,
            taps_today INTEGER DEFAULT 0,
            tap_date TEXT DEFAULT '',
            total_taps INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            bonus_taps INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referrals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL UNIQUE,
            created_at INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            task_type TEXT DEFAULT 'channel',
            channel TEXT DEFAULT '',
            channel_link TEXT DEFAULT '',
            reward INTEGER DEFAULT 0,
            target INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_tasks(
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            completed_at INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, task_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            card TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS join_requests(
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            requested_at INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        )
    """)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for name, definition in {
        "taps_today": "INTEGER DEFAULT 0",
        "tap_date": "TEXT DEFAULT ''",
        "total_taps": "INTEGER DEFAULT 0",
        "level": "INTEGER DEFAULT 1",
        "bonus_taps": "INTEGER DEFAULT 0",
    }.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")

    task_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    for name, definition in {
        "task_type": "TEXT DEFAULT 'channel'",
        "channel": "TEXT DEFAULT ''",
        "channel_link": "TEXT DEFAULT ''",
        "reward": "INTEGER DEFAULT 0",
        "target": "INTEGER DEFAULT 0",
        "active": "INTEGER DEFAULT 1",
        "created_at": "INTEGER DEFAULT 0",
        "channels_json": "TEXT DEFAULT '[]'",
        "links_json": "TEXT DEFAULT '[]'",
        "channel_count": "INTEGER DEFAULT 0",
    }.items():
        if name not in task_cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")

    # Eski bazalarda channel ustuni NOT NULL bo‘lishi mumkin.
    # Referral vazifalari kanalga muhtoj emas, shuning uchun bo‘sh qiymat ishlatiladi.
    # Referral vazifalar MINI APP TOMONIDAN avtomatik ko‘rsatiladi.
    # Admin ularni qo‘shmaydi/o‘zgartirmaydi. Har bir threshold faqat bir marta mukofotlanadi.
    referral_defaults = [
        (10, "10 ta do‘stingizni taklif qiling", 100_000),
        (20, "20 ta do‘stingizni taklif qiling", 200_000),
        (30, "30 ta do‘stingizni taklif qiling", 300_000),
    ]
    now = int(time.time())
    for target, title, reward in referral_defaults:
        exists = conn.execute(
            "SELECT 1 FROM tasks WHERE task_type='referral' AND target=? LIMIT 1",
            (target,)
        ).fetchone()
        if not exists:
            conn.execute("""
                INSERT INTO tasks(
                    title, task_type, channel, channel_link, reward, target, active, created_at
                ) VALUES (?, 'referral', '', '', ?, ?, 1, ?)
            """, (title, reward, target, now))

    conn.commit()
    conn.close()


def add_user(user_id, username="", first_name="", referrer_id=None):
    conn = _db()
    old = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    if old:
        conn.execute(
            "UPDATE users SET username=?,first_name=? WHERE user_id=?",
            (username or "", first_name or "", user_id),
        )
    else:
        conn.execute("""
            INSERT INTO users
            (user_id,username,first_name,balance,referrals,referrer_id,
             taps_today,tap_date,total_taps,level,bonus_taps,created_at)
            VALUES (?,?,?,0,0,?,0,'',0,1,0,?)
        """, (user_id, username or "", first_name or "", referrer_id, int(time.time())))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = _db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def get_all_user_ids():
    conn = _db()
    rows = conn.execute("SELECT user_id FROM users ORDER BY user_id ASC").fetchall()
    conn.close()
    return [int(row["user_id"]) for row in rows]


def add_referral(referrer_id, referred_id):
    if referrer_id == referred_id:
        return False
    conn = _db()
    if conn.execute("SELECT 1 FROM referrals WHERE referred_id=?", (referred_id,)).fetchone():
        conn.close()
        return False
    if not conn.execute("SELECT 1 FROM users WHERE user_id=?", (referrer_id,)).fetchone():
        conn.close()
        return False
    if not conn.execute("SELECT 1 FROM users WHERE user_id=?", (referred_id,)).fetchone():
        conn.close()
        return False
    conn.execute(
        "INSERT INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)",
        (referrer_id, referred_id, int(time.time())),
    )
    conn.execute(
        "UPDATE users SET referrals=referrals+1,bonus_taps=bonus_taps+100 WHERE user_id=?",
        (referrer_id,),
    )
    conn.commit()
    conn.close()
    return True



def save_join_request(chat_id, user_id):
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO join_requests(chat_id,user_id,requested_at) VALUES(?,?,?)",
        (int(chat_id), int(user_id), int(time.time())),
    )
    conn.commit()
    conn.close()


def has_join_request(chat_id, user_id):
    conn = _db()
    row = conn.execute(
        "SELECT 1 FROM join_requests WHERE chat_id=? AND user_id=? LIMIT 1",
        (int(chat_id), int(user_id)),
    ).fetchone()
    conn.close()
    return bool(row)



MIN_WITHDRAW = 600_000


def create_withdrawal(user_id, amount, card):
    user_id = int(user_id)
    amount = int(amount)
    card = str(card).strip().replace(" ", "")

    if amount < MIN_WITHDRAW:
        return {"ok": False, "error": f"Minimal yechib olish: {MIN_WITHDRAW:,} so‘m."}

    if not card.isdigit() or len(card) < 12 or len(card) > 19:
        return {"ok": False, "error": "Karta raqami noto‘g‘ri."}

    conn = _db()
    row = conn.execute(
        "SELECT balance FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not row:
        conn.close()
        return {"ok": False, "error": "Foydalanuvchi topilmadi."}

    balance = int(row["balance"] or 0)
    if balance < amount:
        conn.close()
        return {"ok": False, "error": "Balansingiz yetarli emas."}

    pending = conn.execute(
        "SELECT 1 FROM withdrawals WHERE user_id=? AND status='pending' LIMIT 1",
        (user_id,)
    ).fetchone()
    if pending:
        conn.close()
        return {"ok": False, "error": "Sizda hali ko‘rib chiqilmagan yechib olish so‘rovi bor."}

    now = int(time.time())
    conn.execute(
        "UPDATE users SET balance=balance-? WHERE user_id=?",
        (amount, user_id)
    )
    cur = conn.execute(
        "INSERT INTO withdrawals(user_id,amount,card,status,created_at) VALUES(?,?,?,'pending',?)",
        (user_id, amount, card, now)
    )
    withdrawal_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "ok": True,
        "id": int(withdrawal_id),
        "amount": amount,
        "card": card,
        "balance": balance - amount,
        "status": "pending",
    }


def get_pending_withdrawals():
    conn = _db()
    rows = conn.execute("""
        SELECT w.*, u.username, u.first_name
        FROM withdrawals w
        LEFT JOIN users u ON u.user_id=w.user_id
        WHERE w.status='pending'
        ORDER BY w.id DESC
    """).fetchall()
    conn.close()
    return rows


def set_withdrawal_status(withdrawal_id, status):
    conn = _db()
    row = conn.execute(
        "SELECT * FROM withdrawals WHERE id=?",
        (withdrawal_id,)
    ).fetchone()

    if not row:
        conn.close()
        return {"ok": False, "error": "So‘rov topilmadi."}

    if row["status"] != "pending":
        conn.close()
        return {"ok": False, "error": "Bu so‘rov allaqachon ko‘rib chiqilgan."}

    if status == "rejected":
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (int(row["amount"]), int(row["user_id"]))
        )

    conn.execute(
        "UPDATE withdrawals SET status=? WHERE id=?",
        (status, withdrawal_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "status": status, "user_id": int(row["user_id"]), "amount": int(row["amount"])}


def get_statistics():
    conn = _db()
    result = {
        "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "referrals": conn.execute("SELECT COALESCE(SUM(referrals),0) FROM users").fetchone()[0],
        "balance": conn.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone()[0],
        "withdrawals": conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone()[0],
    }
    conn.close()
    return result


def add_task(title, channels, channel_links, reward, task_type="channel", target=0):
    conn = _db()
    channels = channels or []
    channel_links = channel_links or []
    cur = conn.execute("""
        INSERT INTO tasks(title,task_type,channel,channel_link,channels_json,links_json,channel_count,reward,target,active,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,1,?)
    """, (
        title, task_type,
        channels[0] if channels else "",
        channel_links[0] if channel_links else "",
        json.dumps(channels, ensure_ascii=False),
        json.dumps(channel_links, ensure_ascii=False),
        len(channels), int(reward), int(target), int(time.time())
    ))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def task_channels(task):
    try:
        raw = task["channels_json"]
        if raw:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return [str(x) for x in data if str(x).strip()]
    except Exception:
        pass
    return [task["channel"]] if task["channel"] else []


def task_links(task):
    try:
        raw = task["links_json"]
        if raw:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x) for x in data]
    except Exception:
        pass
    return [task["channel_link"]] if task["channel_link"] else []

def get_tasks():
    conn = _db()
    rows = conn.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id ASC").fetchall()
    conn.close()
    return rows

def get_task(task_id):
    conn = _db()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return row


def deactivate_task(task_id):
    conn = _db()
    conn.execute("UPDATE tasks SET active=0 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()


def is_task_completed(user_id, task_id):
    conn = _db()
    row = conn.execute(
        "SELECT 1 FROM completed_tasks WHERE user_id=? AND task_id=?",
        (user_id, task_id),
    ).fetchone()
    conn.close()
    return bool(row)


def _level(balance):
    if balance >= 5_000_000: return 5
    if balance >= 1_500_000: return 4
    if balance >= 500_000: return 3
    if balance >= 100_000: return 2
    return 1


def complete_task(user_id, task_id):
    conn = _db()
    if conn.execute(
        "SELECT 1 FROM completed_tasks WHERE user_id=? AND task_id=?",
        (user_id, task_id),
    ).fetchone():
        conn.close()
        return {"ok": False, "error": "Vazifa allaqachon bajarilgan."}

    task = conn.execute(
        "SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)
    ).fetchone()
    if not task:
        conn.close()
        return {"ok": False, "error": "Vazifa topilmadi."}

    conn.execute(
        "INSERT INTO completed_tasks(user_id,task_id,completed_at) VALUES(?,?,?)",
        (user_id, task_id, int(time.time())),
    )
    conn.execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (int(task["reward"]), user_id),
    )
    balance = conn.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ).fetchone()["balance"]
    conn.execute("UPDATE users SET level=? WHERE user_id=?", (_level(int(balance)), user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "reward": int(task["reward"]), "balance": int(balance)}


def process_taps(user_id, count=1):
    user_id = int(user_id)
    count = max(1, min(int(count), 50))
    today = time.strftime("%Y-%m-%d")

    conn = _db()
    row = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()

    if not row:
        conn.close()
        return {"ok": False, "error": "Foydalanuvchi topilmadi."}

    taps_today = int(row["taps_today"] or 0)
    bonus_taps = int(row["bonus_taps"] or 0)

    if row["tap_date"] != today:
        taps_today = 0

    free_left = max(0, 100 - taps_today)
    available = free_left + bonus_taps
    actual = min(count, available)

    if actual <= 0:
        conn.close()
        return {
            "ok": False,
            "error": "Bugungi tap tugadi. Do‘st taklif qilib qo‘shimcha tap oling.",
            "taps_today": taps_today,
            "bonus_taps": bonus_taps,
            "available_taps": 0,
        }

    free_used = min(actual, free_left)
    bonus_used = actual - free_used
    taps_today += free_used
    bonus_taps -= bonus_used

    # 1 TAP = 100 SO‘M
    reward = actual * 100
    balance = int(row["balance"] or 0) + reward
    total_taps = int(row["total_taps"] or 0) + actual
    level = _level(balance)

    conn.execute("""
        UPDATE users
        SET balance=?, taps_today=?, tap_date=?,
            total_taps=?, level=?, bonus_taps=?
        WHERE user_id=?
    """, (
        balance, taps_today, today,
        total_taps, level, bonus_taps, user_id
    ))

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "count": actual,
        "reward": reward,
        "tap_reward": 100,
        "balance": balance,
        "level": level,
        "taps_today": taps_today,
        "bonus_taps": bonus_taps,
        "total_taps": total_taps,
        "available_taps": max(0, 100 - taps_today) + bonus_taps,
    }


def process_tap(user_id):
    return process_taps(user_id, 1)


def get_init_data(request):
    # Telegram WebApp initData odatda query orqali keladi.
    # Qo‘shimcha ravishda header ham qabul qilamiz — bu URL encoding/cache muammolarini kamaytiradi.
    value = request.query.get("initData", "")
    if not value:
        value = request.headers.get("X-Telegram-Init-Data", "")
    if not value:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("tma "):
            value = auth[4:]
    return value


def validate_init_data(init_data):
    if not init_data:
        raise ValueError("Telegram initData mavjud emas. Mini App Telegram ichidan ochilishi kerak.")

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception as exc:
        raise ValueError(f"Telegram initData o‘qilmadi: {exc}")

    received = pairs.pop("hash", None)
    if not received:
        raise ValueError("Telegram initData hash mavjud emas.")

    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode("utf-8"),
        hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret,
        check.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received):
        raise ValueError("Telegram initData noto‘g‘ri. Bot token va Mini App bir xil botga tegishli ekanini tekshiring.")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        raise ValueError("Telegram auth_date noto‘g‘ri.")

    # Telegram sessiyasi juda eski bo‘lsa qabul qilmaymiz.
    if auth_date <= 0:
        raise ValueError("Telegram auth_date topilmadi.")
    if time.time() - auth_date > 86400:
        raise ValueError("Telegram sessiyasi eskirgan. Mini Appni yopib, qayta oching.")

    user_json = pairs.get("user")
    if not user_json:
        raise ValueError("Telegram user ma’lumoti topilmadi.")

    try:
        user = json.loads(user_json)
    except json.JSONDecodeError:
        raise ValueError("Telegram user ma’lumoti noto‘g‘ri.")

    if not isinstance(user, dict) or not user.get("id"):
        raise ValueError("Telegram user ID topilmadi.")

    return user


def web_user(request):
    user = validate_init_data(get_init_data(request))
    add_user(
        int(user["id"]),
        user.get("username", ""),
        user.get("first_name", "")
    )
    return user


def load_config():
    config = {}

    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            "eng fayli topilmadi. "
            "soliqapp.py bilan bir papkada 'eng' fayli bo‘lishi kerak."
        )

    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)

            config[key.strip()] = value.strip()

    return config


CONFIG = load_config()

BOT_TOKEN = CONFIG.get("BOT_TOKEN", "").strip()
ADMIN_ID_TEXT = CONFIG.get("ADMIN_ID", "").strip()
WEBAPP_URL = CONFIG.get("WEBAPP_URL", "").strip()
BOT_USERNAME = CONFIG.get(
    "BOT_USERNAME",
    "tap_keshbek_bot"
).strip().lstrip("@")


if not BOT_TOKEN:
    raise ValueError(
        "eng faylida BOT_TOKEN topilmadi."
    )


if not ADMIN_ID_TEXT.isdigit():
    raise ValueError(
        "eng faylida ADMIN_ID noto‘g‘ri."
    )


ADMIN_ID = int(ADMIN_ID_TEXT)


if not WEBAPP_URL:
    print(
        "⚠️ WEBAPP_URL hali kiritilmagan. "
        "Mini App tugmasi ishlashi uchun HTTPS URL kerak."
    )


# =========================================================
# BOT
# =========================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()


# =========================================================
# FSM
# =========================================================

class AddTaskState(StatesGroup):
    title = State()
    channel_count = State()
    channel_username = State()
    channel_link = State()
    reward = State()


class AddChannelState(StatesGroup):
    title = State()
    username = State()
    link = State()
    reward = State()

class BroadcastState(StatesGroup):
    photo = State()
    caption = State()


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# =========================================================
# MAIN USER KEYBOARD
# =========================================================

def main_keyboard():

    buttons = []

    if WEBAPP_URL:

        buttons.append([
            InlineKeyboardButton(
                text="💰 Cashback ishlash",
                style="success",
                web_app=WebAppInfo(
                    url=WEBAPP_URL
                )
            )
        ])

    else:

        buttons.append([
            InlineKeyboardButton(
                text="💰 Cashback ishlash",
                style="success",
                callback_data="cashback"
            )
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# =========================================================
# ADMIN KEYBOARD
# =========================================================

def admin_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="📊 Statistika",
                    callback_data="admin_stats"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📢 Reklama yuborish",
                    callback_data="admin_broadcast"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📋 Vazifalar",
                    callback_data="admin_tasks"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📢 Kanal vazifasi qo‘shish",
                    callback_data="admin_channel"
                )
            ],

            [
                InlineKeyboardButton(
                    text="💳 Yechib olishlar",
                    callback_data="admin_withdrawals"
                )
            ],

            [
                InlineKeyboardButton(
                    text="👥 Foydalanuvchilar",
                    callback_data="admin_users"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⚙️ Sozlamalar",
                    callback_data="admin_settings"
                )

            ],
        ]
    )


# =========================================================
# JOIN REQUEST — ZAYAVKA KANALLARI
# =========================================================

@dp.chat_join_request()
async def chat_join_request_handler(request: ChatJoinRequest):
    try:
        if request.from_user and request.chat:
            save_join_request(request.chat.id, request.from_user.id)
            print(f"✅ Join request saqlandi: user={request.from_user.id}, chat={request.chat.id}")
    except Exception as error:
        print("Join request saqlash xatosi:", error)


# =========================================================
# /START
# =========================================================

@dp.message(CommandStart())
async def start_handler(message: Message):

    user = message.from_user

    if not user:
        return

    referrer_id = None

    args = message.text.split(
        maxsplit=1
    )

    if len(args) > 1:

        payload = args[1].strip()

        if payload.startswith("ref_"):

            ref_value = payload[4:]

            if ref_value.isdigit():

                referrer_id = int(
                    ref_value
                )

    existing_user = get_user(
        user.id
    )

    if not existing_user:

        add_user(
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
            referrer_id=referrer_id
        )

        # Referral mukofoti
        if (
            referrer_id
            and referrer_id != user.id
        ):

            try:

                add_referral(
                    referrer_id,
                    user.id
                )

            except Exception as error:

                print(
                    "Referral xatosi:",
                    error
                )

    else:

        # Ism/username yangilanishi
        try:

            add_user(
                user_id=user.id,
                username=user.username or "",
                first_name=user.first_name or ""
            )

        except Exception:
            pass

    await message.answer(

        "🐦 Xush kelibsiz!\n\n"

        "Soliq bilan cashback ishlang "
        "va bonuslarga ega bo‘ling! 💰\n\n"

        "👇 Cashback ishlash uchun "
        "quyidagi tugmani bosing:",

        reply_markup=main_keyboard()
    )


# =========================================================
# CASHBACK FALLBACK
# =========================================================

@dp.callback_query(
    F.data == "cashback"
)
async def cashback_callback(
    callback: CallbackQuery
):

    if not WEBAPP_URL:

        await callback.answer(
            "⚠️ Mini App URL hali sozlanmagan.",
            show_alert=True
        )

        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Cashback ishlash",
                    style="success",
                    web_app=WebAppInfo(
                        url=WEBAPP_URL
                    )
                )
            ]
        ]
    )

    await callback.message.answer(
        "🐦 Cashback ishlash uchun "
        "quyidagi tugmani bosing:",
        reply_markup=keyboard
    )

    await callback.answer()


# =========================================================
# ADMIN PANEL
# =========================================================

@dp.message(Command("admin"))
async def admin_command(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return

    await message.answer(
        "⚙️ ADMIN PANEL\n\n"
        "Kerakli bo‘limni tanlang:",
        reply_markup=admin_keyboard()
    )


# =========================================================
# STATISTICS
# =========================================================

@dp.callback_query(
    F.data == "admin_stats"
)
async def admin_stats(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    try:

        stats = get_statistics()

        if isinstance(stats, dict):

            users = stats.get(
                "users",
                0
            )

            referrals = stats.get(
                "referrals",
                0
            )

            balance = stats.get(
                "balance",
                0
            )

            withdrawals = stats.get(
                "withdrawals",
                0
            )

        else:

            users = 0
            referrals = 0
            balance = 0
            withdrawals = 0

        text = (
            "📊 STATISTIKA\n\n"
            f"👥 Foydalanuvchilar: {users}\n"
            f"👫 Takliflar: {referrals}\n"
            f"💰 Balanslar: {balance:,} so‘m\n"
            f"💳 Yechib olishlar: {withdrawals}"
        )

    except Exception as error:

        print(
            "Statistika xatosi:",
            error
        )

        text = (
            "📊 STATISTIKA\n\n"
            "Statistikani olishda xatolik yuz berdi."
        )

    await callback.message.edit_text(
        text,
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# =========================================================
# TASKS
# =========================================================

@dp.callback_query(
    F.data == "admin_tasks"
)
async def admin_tasks(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    try:

        tasks = get_tasks()

    except Exception as error:

        print(
            "Task olish xatosi:",
            error
        )

        tasks = []

    if not tasks:

        text = (
            "📋 VAZIFALAR\n\n"
            "Hozircha hech qanday vazifa yo‘q."
        )

    else:

        lines = [
            "📋 VAZIFALAR\n"
        ]

        for task in tasks:

            try:

                task_id = task["id"]
                title = task["title"]
                channels = task_channels(task)
                reward = task["reward"]
                channel_text = ", ".join(channels) if channels else "Referral vazifa"

                lines.append(
                    f"#{task_id} — {title}\n"
                    f"📢 Kanallar: {len(channels)}\n"
                    f"{channel_text}\n"
                    f"💰 {reward:,} so‘m\n"
                )

            except Exception:
                continue

        text = "\n".join(lines)

    rows = []
    for task in tasks:
        task_id = int(task["id"])
        task_type = str(task["task_type"])
        # Referral vazifalari tizimniki — admin ularni o‘chirmaydi.
        if task_type == "channel":
            rows.append([
                InlineKeyboardButton(
                    text=f"🗑 Olib tashlash #{task_id}",
                    callback_data=f"delete_task:{task_id}"
                )
            ])

    rows.append([
        InlineKeyboardButton(
            text="➕ Yangi kanal vazifasi",
            callback_data="admin_channel"
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Admin panel",
            callback_data="admin_back"
        )
    ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("delete_task:"))
async def delete_task_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Vazifa ID noto‘g‘ri.", show_alert=True)
        return

    task = get_task(task_id)
    if not task or not task["active"]:
        await callback.answer("❌ Vazifa topilmadi.", show_alert=True)
        return

    if task["task_type"] != "channel":
        await callback.answer("ℹ️ Referral vazifalari avtomatik tizimniki.", show_alert=True)
        return

    deactivate_task(task_id)
    await callback.answer("✅ Vazifa olib tashlandi.", show_alert=True)

    # Ro‘yxatni yangilaymiz
    tasks = get_tasks()
    if not tasks:
        await callback.message.edit_text(
            "📋 VAZIFALAR\n\nHozircha kanal vazifalari yo‘q.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Yangi kanal vazifasi", callback_data="admin_channel")],
                [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="admin_back")]
            ])
        )
        return

    lines = ["📋 VAZIFALAR\n"]
    rows = []
    for t in tasks:
        channels = task_channels(t)
        lines.append(
            f"#{t['id']} — {t['title']}\n"
            f"📢 Kanallar: {len(channels)}\n"
            f"💰 {int(t['reward']):,} so‘m\n"
        )
        if t["task_type"] == "channel":
            rows.append([
                InlineKeyboardButton(
                    text=f"🗑 Olib tashlash #{int(t['id'])}",
                    callback_data=f"delete_task:{int(t['id'])}"
                )
            ])
    rows.append([InlineKeyboardButton(text="➕ Yangi kanal vazifasi", callback_data="admin_channel")])
    rows.append([InlineKeyboardButton(text="⬅️ Admin panel", callback_data="admin_back")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text(
        "⚙️ ADMIN PANEL\n\nKerakli bo‘limni tanlang:",
        reply_markup=admin_keyboard()
    )
    await callback.answer()


# =========================================================
# ADD TASK — 1 DAN 5 TAGACHA KANAL
# =========================================================

@dp.callback_query(F.data == "admin_add_task")
async def admin_add_task(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await state.set_state(AddTaskState.title)
    await callback.message.answer(
        "➕ VAZIFA QO‘SHISH\n\n"
        "1/4 — Vazifa nomini yuboring.\n\n"
        "Masalan: 📢 Telegram kanallarga obuna bo‘ling"
    )
    await callback.answer()


@dp.message(AddTaskState.title)
async def task_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("❌ Vazifa nomini yuboring.")
        return
    await state.update_data(title=title)
    await state.set_state(AddTaskState.channel_count)
    await message.answer(
        "2/4 — Bu vazifada nechta kanal bo‘ladi?\n\n"
        "1, 2, 3, 4 yoki 5 dan birini yuboring.\n"
        "Masalan: 3"
    )


@dp.message(AddTaskState.channel_count)
async def task_channel_count(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if text not in {"1", "2", "3", "4", "5"}:
        await message.answer("❌ Faqat 1, 2, 3, 4 yoki 5 yuboring.")
        return
    count = int(text)
    await state.update_data(channel_count=count, channels=[], links=[], current_channel=1)
    await state.set_state(AddTaskState.channel_username)
    await message.answer(
        f"📢 1/{count}-kanal\n\n"
        "Kanal ID sini yuboring.\n"
        "Masalan: -1001234567890\n\n"
        "⚠️ ID aynan admin qo‘shgan kanalniki bo‘lsin."
    )


@dp.message(AddTaskState.channel_username)
async def task_channel_username(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    channel = (message.text or "").strip()
    if not re.fullmatch(r"-100\d{5,}", channel):
        await message.answer(
            "❌ Kanal ID noto‘g‘ri. Masalan: -1001234567890"
        )
        return
    await state.update_data(current_username=channel)
    await state.set_state(AddTaskState.channel_link)
    data = await state.get_data()
    await message.answer(
        f"🔗 {data.get('current_channel', 1)}/{data.get('channel_count', 1)}-kanal havolasini yuboring.\n\n"
        "Ochiq kanal: https://t.me/kanal_nomi\n"
        "Yopiq/zayavka kanal: Telegram invite havolasini yuboring."
    )


@dp.message(AddTaskState.channel_link)
async def task_channel_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    link = (message.text or "").strip()
    if not link.startswith("https://t.me/") and not link.startswith("http://t.me/"):
        await message.answer("❌ To‘g‘ri Telegram kanal havolasini yuboring.")
        return

    data = await state.get_data()
    channels = list(data.get("channels", []))
    links = list(data.get("links", []))
    channels.append(data.get("current_username", ""))
    links.append(link)
    current = int(data.get("current_channel", 1))
    count = int(data.get("channel_count", 1))

    if current < count:
        await state.update_data(channels=channels, links=links, current_channel=current + 1)
        await state.set_state(AddTaskState.channel_username)
        await message.answer(
            f"📢 {current + 1}/{count}-kanal\n\nKanal ID sini yuboring.\n"
            "Masalan: -1001234567890"
        )
        return

    await state.update_data(channels=channels, links=links)
    await state.set_state(AddTaskState.reward)
    await message.answer(
        "3/4 — 💰 Vazifa mukofotini yuboring.\n\n"
        "Masalan: 5000"
    )


@dp.message(AddTaskState.reward)
async def task_reward(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    reward_text = (message.text or "").strip().replace(" ", "")
    if not reward_text.isdigit() or int(reward_text) <= 0:
        await message.answer("❌ Mukofot faqat musbat raqam bo‘lishi kerak. Masalan: 5000")
        return
    data = await state.get_data()
    try:
        task_id = add_task(
            title=data.get("title", "Vazifa"),
            channels=data.get("channels", []),
            channel_links=data.get("links", []),
            reward=int(reward_text),
            task_type="channel",
        )
        channels = data.get("channels", [])
        await state.clear()
        await message.answer(
            "✅ VAZIFA SAQLANDI!\n\n"
            f"📋 {data.get('title', 'Vazifa')}\n"
            f"📢 Kanallar soni: {len(channels)}\n"
            + "\n".join(f"{i+1}. {c}" for i, c in enumerate(channels))
            + f"\n💰 {int(reward_text):,} so‘m\n\n🆔 Vazifa: #{task_id}",
            reply_markup=admin_keyboard()
        )
    except Exception as error:
        print("Task saqlash xatosi:", error)
        await state.clear()
        await message.answer("❌ Vazifani saqlashda xatolik yuz berdi.", reply_markup=admin_keyboard())


# =========================================================
# CHANNEL
# =========================================================

@dp.callback_query(F.data == "admin_channel")
async def admin_channel(callback: CallbackQuery, state: FSMContext):
    # Referral vazifalar bu bo‘limdan qo‘shilmaydi. Ular avtomatik.
    # Bu bo‘lim faqat admin qo‘shadigan kanal vazifalari uchun.
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await state.set_state(AddTaskState.title)
    await callback.message.answer(
        "📢 KANAL VAZIFASI QO‘SHISH\n\n"
        "1/4 — Vazifa nomini yuboring.\n"
        "Masalan: Telegram kanallarga obuna bo‘ling"
    )
    await callback.answer()


# =========================================================
# BROADCAST
# =========================================================

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return

    await state.clear()
    await state.set_state(BroadcastState.photo)

    await callback.message.answer(
        "📢 REKLAMA YUBORISH\n\n"
        "1/2 — Reklama uchun RASM yuboring.\n"
        "Rasmga matnni caption qilib yozishingiz mumkin.\n\n"
        "Masalan: rasm + reklama matni."
    )
    await callback.answer()


@dp.message(BroadcastState.photo)
async def broadcast_photo(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if not message.photo:
        await message.answer(
            "❌ Rasm yuboring. Reklama rasm bilan yuboriladi."
        )
        return

    await state.update_data(
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        caption=message.caption or ""
    )

    if message.caption:
        await send_broadcast_from_message(message, message.caption)
        await state.clear()
    else:
        await state.set_state(BroadcastState.caption)
        await message.answer(
            "2/2 — Endi reklama matnini yuboring."
        )


@dp.message(BroadcastState.caption)
async def broadcast_caption(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    caption = (message.text or "").strip()
    if not caption:
        await message.answer("❌ Reklama matnini yuboring.")
        return

    data = await state.get_data()
    source_chat_id = int(data["source_chat_id"])
    source_message_id = int(data["source_message_id"])

    users = get_all_user_ids()
    sent = 0
    failed = 0

    for user_id in users:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
                caption=caption,
            )
            sent += 1
        except Exception as error:
            print(f"Broadcast user {user_id} xato:", error)
            failed += 1

        await asyncio.sleep(0.04)

    await state.clear()

    await message.answer(
        f"✅ Rasmli reklama yuborildi.\n\n"
        f"👥 Jami: {len(users)}\n"
        f"✅ Yetkazildi: {sent}\n"
        f"❌ Yetkazilmadi: {failed}",
        reply_markup=admin_keyboard()
    )



async def send_broadcast_from_message(message: Message, caption: str):
    users = get_all_user_ids()
    sent = 0
    failed = 0

    for user_id in users:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)

    await message.answer(
        f"✅ Rasmli reklama yuborildi.\n\n"
        f"👥 Jami: {len(users)}\n"
        f"✅ Yetkazildi: {sent}\n"
        f"❌ Yetkazilmadi: {failed}",
        reply_markup=admin_keyboard()
    )



# =========================================================
# WITHDRAWALS
# =========================================================

@dp.callback_query(F.data == "admin_withdrawals")
async def admin_withdrawals(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    rows = get_pending_withdrawals()
    if not rows:
        await callback.message.edit_text(
            "💳 YECHIB OLISHLAR\n\nHozircha kutayotgan so‘rovlar yo‘q.",
            reply_markup=admin_keyboard()
        )
        await callback.answer()
        return

    lines = ["💳 YECHIB OLISHLAR\n"]
    buttons = []

    for row in rows:
        username = f"@{row['username']}" if row["username"] else "username yo‘q"
        card = str(row["card"])
        masked = ("*" * max(0, len(card) - 4)) + card[-4:]
        lines.append(
            f"🆔 #{int(row['id'])}\n"
            f"👤 {row['first_name'] or 'Foydalanuvchi'} ({username})\n"
            f"🔢 User ID: {int(row['user_id'])}\n"
            f"💰 {int(row['amount']):,} so‘m\n"
            f"💳 {masked}\n"
        )
        buttons.append([
            InlineKeyboardButton(
                text=f"✅ Tasdiqlash #{int(row['id'])}",
                callback_data=f"wd_ok:{int(row['id'])}"
            ),
            InlineKeyboardButton(
                text=f"❌ Rad etish #{int(row['id'])}",
                callback_data=f"wd_no:{int(row['id'])}"
            )
        ])

    buttons.append([InlineKeyboardButton(text="⬅️ Admin panel", callback_data="admin_back")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("wd_ok:"))
async def withdrawal_approve(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    wid = int(callback.data.split(":", 1)[1])
    result = set_withdrawal_status(wid, "approved")

    if not result["ok"]:
        await callback.answer(result["error"], show_alert=True)
        return

    try:
        await bot.send_message(
            result["user_id"],
            f"✅ Yechib olish so‘rovingiz tasdiqlandi.\n"
            f"💰 {result['amount']:,} so‘m"
        )
    except Exception:
        pass

    await callback.answer("✅ Tasdiqlandi.", show_alert=True)
    await admin_withdrawals(callback)


@dp.callback_query(F.data.startswith("wd_no:"))
async def withdrawal_reject(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    wid = int(callback.data.split(":", 1)[1])
    result = set_withdrawal_status(wid, "rejected")

    if not result["ok"]:
        await callback.answer(result["error"], show_alert=True)
        return

    try:
        await bot.send_message(
            result["user_id"],
            f"❌ Yechib olish so‘rovingiz rad etildi.\n"
            f"💰 {result['amount']:,} so‘m balansingizga qaytarildi."
        )
    except Exception:
        pass

    await callback.answer("❌ Rad etildi, summa qaytarildi.", show_alert=True)
    await admin_withdrawals(callback)



# =========================================================
# USERS
# =========================================================

@dp.callback_query(
    F.data == "admin_users"
)
async def admin_users(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    await callback.message.answer(
        "👥 FOYDALANUVCHILAR\n\n"
        "Foydalanuvchilar ma’lumotlari "
        "SQLite bazasida saqlanmoqda."
    )

    await callback.answer()


# =========================================================
# SETTINGS
# =========================================================

@dp.callback_query(
    F.data == "admin_settings"
)
async def admin_settings(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    await callback.message.answer(
        "⚙️ SOZLAMALAR\n\n"
        f"🤖 Bot: @{BOT_USERNAME}\n"
        f"🌐 Mini App: "
        f"{WEBAPP_URL if WEBAPP_URL else 'sozlanmagan'}"
    )

    await callback.answer()


# =========================================================
# WEB APP DATA
# =========================================================

@dp.message(
    F.web_app_data
)
async def web_app_data_handler(
    message: Message
):

    if not message.web_app_data:
        return

    print(
        "Mini App data:",
        message.web_app_data.data
    )


# =========================================================
# MENU BUTTON
# =========================================================

async def setup_menu_button():

    if not WEBAPP_URL:
        return

    try:

        await bot.set_chat_menu_button(

            menu_button=MenuButtonWebApp(

                text="💰 Cashback",

                web_app=WebAppInfo(
                    url=WEBAPP_URL
                )
            )
        )

        print(
            "✅ Telegram Menu Mini App tugmasi o‘rnatildi."
        )

    except Exception as error:

        print(
            "⚠️ Menu tugmasini o‘rnatib bo‘lmadi:",
            error
        )


# =========================================================
# MAIN
# =========================================================

async def api_user(request):
    try:
        u = web_user(request)
        row = get_user(int(u["id"]))
        return web.json_response({
            "ok": True,
            "user_id": int(u["id"]),
            "first_name": row["first_name"],
            "username": row["username"],
            "balance": int(row["balance"]),
            "level": int(row["level"]),
            "referrals": int(row["referrals"]),
            "bonus_taps": int(row["bonus_taps"]),
            "taps_today": int(row["taps_today"]),
            "total_taps": int(row["total_taps"]),
            "daily_limit": 100,
            "min_withdraw": MIN_WITHDRAW,
            "bonus_limit": int(row["bonus_taps"]),
            "available_taps": 100 + int(row["bonus_taps"]),
            "tap_reward": 100,
        })
    except Exception as e:
        print("/api/user XATO:", repr(e))
        return web.json_response({"ok": False, "error": str(e)}, status=401)


async def api_tap(request):
    try:
        u = web_user(request)

        try:
            count = int(request.query.get("count", "1"))
        except ValueError:
            count = 1

        result = process_taps(int(u["id"]), count)

        return web.json_response(
            result,
            status=200 if result.get("ok") else 400,
            headers={"Cache-Control": "no-store"},
        )

    except Exception as exc:
        print("/api/tap XATO:", repr(exc))
        return web.json_response({"ok": False, "error": str(exc)}, status=401)


async def api_tasks(request):
    try:
        u = web_user(request)
        uid = int(u["id"])
        result = []
        for t in get_tasks():
            channels = task_channels(t)
            links = task_links(t)
            result.append({
                "id": int(t["id"]),
                "title": t["title"],
                "type": t["task_type"],
                "channels": channels,
                "channel_links": links,
                "channel": channels[0] if channels else "",
                "channel_link": links[0] if links else "",
                "reward": int(t["reward"]),
                "target": int(t["target"] or 0),
                "completed": is_task_completed(uid, int(t["id"])),
            })
        return web.json_response({"ok": True, "tasks": result})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=401)


async def api_check_channel(request):
    try:
        u = web_user(request)
        uid = int(u["id"])
        task_id = int(request.query.get("task_id", "0"))
        task = get_task(task_id)
        if not task or not task["active"] or task["task_type"] != "channel":
            return web.json_response({"ok": False, "error": "Vazifa topilmadi."}, status=404)
        if is_task_completed(uid, task_id):
            return web.json_response({"ok": False, "error": "Vazifa allaqachon bajarilgan."}, status=400)

        channels = task_channels(task)
        if not channels:
            return web.json_response({"ok": False, "error": "Kanal sozlanmagan."}, status=400)

        statuses = []
        for channel in channels:
            # Oddiy kanal: haqiqiy a'zolikni tekshiramiz.
            # Zayavka (join request) kanalida esa foydalanuvchi hali member bo'lmaydi,
            # shuning uchun Telegram yuborgan ChatJoinRequestni ham tekshiramiz.
            ok = False
            try:
                member = await bot.get_chat_member(channel, uid)
                status = getattr(member, "status", "")
                is_member = bool(getattr(member, "is_member", False))
                ok = status in {"member", "administrator", "creator"} or (
                    status == "restricted" and is_member
                )
            except Exception as member_error:
                print(f"get_chat_member xatosi ({channel}):", member_error)

            if not ok:
                try:
                    # channel bu yerda Telegram chat ID (-100...)
                    ok = has_join_request(int(channel), uid)
                except (ValueError, TypeError):
                    ok = False

            statuses.append({"channel": channel, "ok": ok})
            if not ok:
                return web.json_response({
                    "ok": False,
                    "error": f"Avval kanalga obuna bo‘ling yoki zayavka yuboring: {channel}",
                    "statuses": statuses
                }, status=400)

        result = complete_task(uid, task_id)
        return web.json_response(result, status=200 if result["ok"] else 400)
    except Exception as error:
        print("Kanal tekshirish xatosi:", error)
        return web.json_response({
            "ok": False,
            "error": "Kanalni tekshirib bo‘lmadi. Bot barcha kanallarga administrator qilinganini tekshiring."
        }, status=400)


async def api_claim_referral(request):
    try:
        u = web_user(request)
        uid = int(u["id"])
        task_id = int(request.query.get("task_id", "0"))
        task = get_task(task_id)
        if not task or not task["active"] or task["task_type"] != "referral":
            return web.json_response({"ok": False, "error": "Referral vazifasi topilmadi."}, status=404)

        row = get_user(uid)
        left = int(task["target"]) - int(row["referrals"])
        if left > 0:
            return web.json_response({"ok": False, "error": f"Yana {left} ta do‘st kerak."}, status=400)

        result = complete_task(uid, task_id)
        return web.json_response(result, status=200 if result["ok"] else 400)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=401)


async def api_withdraw(request):
    try:
        u = web_user(request)

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        amount = int(payload.get("amount", 0))
        card = str(payload.get("card", "")).strip()

        result = create_withdrawal(int(u["id"]), amount, card)
        return web.json_response(
            result,
            status=200 if result.get("ok") else 400,
            headers={"Cache-Control": "no-store"},
        )
    except Exception as e:
        print("/api/withdraw XATO:", repr(e))
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_referral(request):
    try:
        u = web_user(request)
        row = get_user(int(u["id"]))
        return web.json_response({
            "ok": True,
            "referrals": int(row["referrals"]),
            "bonus_taps": int(row["bonus_taps"]),
            "available_taps": max(0, 100 - int(row["taps_today"] or 0)) + int(row["bonus_taps"]),
            "ref_link": f"https://t.me/{BOT_USERNAME}?start=ref_{int(u['id'])}",
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=401)


async def serve_mini_app():
    app = web.Application()

    async def index(request):
        index_file = BASE_DIR / "index.html"
        if not index_file.exists():
            return web.Response(text="index.html topilmadi", status=404)
        return web.FileResponse(index_file)

    app.router.add_get("/", index)
    app.router.add_get("/health", lambda request: web.json_response({"ok": True}))

    # API ROUTES
    # add_static("/") ishlatilmaydi: u /api/* so‘rovlarini 404 qilishi mumkin.
    app.router.add_get("/api/user", api_user)
    app.router.add_get("/api/tap", api_tap)
    app.router.add_get("/api/tasks", api_tasks)
    app.router.add_get("/api/check-channel", api_check_channel)
    app.router.add_get("/api/claim-referral-task", api_claim_referral)
    app.router.add_get("/api/referral", api_referral)
    app.router.add_post("/api/withdraw", api_withdraw)

    # Frontend fayllari
    async def style_css(request):
        file = BASE_DIR / "style.css"
        if not file.exists():
            return web.Response(text="style.css topilmadi", status=404)
        return web.FileResponse(file)

    async def app_js(request):
        file = BASE_DIR / "app.js"
        if not file.exists():
            return web.Response(text="app.js topilmadi", status=404)
        return web.FileResponse(file)

    async def logo_png(request):
        file = BASE_DIR / "logo.png"
        if not file.exists():
            return web.Response(text="logo.png topilmadi", status=404)
        return web.FileResponse(file)

    app.router.add_get("/style.css", style_css)
    app.router.add_get("/app.js", app_js)
    app.router.add_get("/logo.png", logo_png)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

    print("🌐 Mini App server: http://localhost:8080")
    print("🌐 Cloudflare tunnel URL:", WEBAPP_URL or "SOZLANMAGAN")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()

async def main():
    logging_text = (
        "\n"
        "========================================\n"
        "🐦 SOLIQ CASHBACK BOT\n"
        "========================================\n"
        f"🤖 Bot: @{BOT_USERNAME}\n"
        f"🌐 Mini App: {WEBAPP_URL if WEBAPP_URL else 'SOZLANMAGAN'}\n"
        f"👑 Admin ID: {ADMIN_ID}\n"
        "========================================\n"
    )

    print(logging_text)
    init_db()
    await setup_menu_button()

    print("🤖 Soliq Cashback bot ishga tushdi!")
    print("🌐 Mini App 8080 portda ishga tushmoqda...")

    # Bot polling va Mini App server bir vaqtda ishlaydi.
    await asyncio.gather(
        dp.start_polling(bot),
        serve_mini_app(),
    )


# =========================================================
# START PROGRAM
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )