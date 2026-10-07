#!/usr/bin/env python3
"""
MineSlot Signal Bot — Telegram ↔ 1win postback MVP (aiogram 3.x + aiohttp).

Запуск: python bot.py

Активация пользователя = ТОЛЬКО server-to-server postback от 1win
(событие registration). Клик по ссылке / открытие сайта НЕ активируют.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import random
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# Конфигурация
# ──────────────────────────────────────────────

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bot.db"
SIGNALS_DIR = BASE_DIR / "signals"
LOG_PATH = BASE_DIR / "bot.log"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")

# Реферальная ссылка 1win (кнопка ведёт сразу сюда + &sub1=CLICK_ID)
# Поддерживаем оба имени: REFERRAL_URL (предпочтительно) и LANDING_URL
REFERRAL_URL = (
    os.getenv("REFERRAL_URL", "").strip()
    or os.getenv("LANDING_URL", "").strip()
    or "https://r1wtvmb.life/casino/list?open=register&p=fiyw"
)
LANDING_URL = REFERRAL_URL  # алиас для совместимости

# Публичный URL сервера БЕЗ /postback в конце.
# Можно без домена — по IP VPS:
#   http://1.2.3.4:8080
# Или с доменом:
#   https://myserver.com
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

POSTBACK_SECRET = os.getenv("POSTBACK_SECRET", "").strip()

# ── ЕДИНСТВЕННОЕ МЕСТО ДЛЯ СМЕНЫ ФОРМАТА POSTBACK 1WIN ──────────────
# Если в кабинете 1win макросы/имена параметров другие — меняйте здесь
# (или через .env: POSTBACK_CLICK_ID_PARAM / POSTBACK_EVENT_PARAM /
#  POSTBACK_SECRET_PARAM / POSTBACK_SUCCESS_EVENT).
POSTBACK_CLICK_ID_PARAM = os.getenv("POSTBACK_CLICK_ID_PARAM", "click_id").strip()
POSTBACK_EVENT_PARAM = os.getenv("POSTBACK_EVENT_PARAM", "event").strip()
POSTBACK_SECRET_PARAM = os.getenv("POSTBACK_SECRET_PARAM", "secret").strip()
POSTBACK_SUCCESS_EVENT = os.getenv("POSTBACK_SUCCESS_EVENT", "registration").strip().lower()
# ────────────────────────────────────────────────────────────────────

POSTBACK_ALLOWED_IPS = {
    ip.strip()
    for ip in os.getenv("POSTBACK_ALLOWED_IPS", "").split(",")
    if ip.strip()
}

HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0").strip()
HTTP_PORT = int(os.getenv("HTTP_PORT", "8080") or "8080")

ACTIVATION_NOTIFICATION_DELAY = int(
    os.getenv("ACTIVATION_NOTIFICATION_DELAY", "120") or "120"
)
SIGNAL_LIFETIME = int(os.getenv("SIGNAL_LIFETIME", "240") or "240")


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# true = разрешить http://IP:порт без покупки домена (порт должен быть открыт в firewall VPS)
ALLOW_HTTP_IP = _env_bool("ALLOW_HTTP_IP", "true")

ADMIN_NOTIFY_ON_NEW_USER = _env_bool("ADMIN_NOTIFY_ON_NEW_USER", "true")
ADMIN_NOTIFY_ON_ACTIVATION = _env_bool("ADMIN_NOTIFY_ON_ACTIVATION", "true")
ADMIN_NOTIFY_ON_SIGNAL = _env_bool("ADMIN_NOTIFY_ON_SIGNAL", "false")

# true = можно пользоваться ботом БЕЗ регистрации (для теста на Mac / админа)
# На боевом сервере поставьте false
DEV_SKIP_ACTIVATION = _env_bool("DEV_SKIP_ACTIVATION", "false")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PRIVATE_HOST_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0")

# ──────────────────────────────────────────────
# Логирование
# ──────────────────────────────────────────────

logger = logging.getLogger("mineslot_bot")
logger.setLevel(logging.INFO)
logger.handlers.clear()

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_file_handler = RotatingFileHandler(
    LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)
logger.addHandler(_console_handler)

# ──────────────────────────────────────────────
# Утилиты
# ──────────────────────────────────────────────


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def str_to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def format_local_date(dt: Optional[datetime] = None) -> str:
    dt = dt or utcnow()
    return dt.astimezone(timezone.utc).strftime("%d.%m.%Y")


def _is_public_ip(host: str) -> bool:
    """Публичный IP = не localhost / не LAN / не link-local."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_public_base_url(url: str) -> Optional[str]:
    """
    Возвращает текст ошибки или None, если URL ок.

    Разрешено:
      - https://domain.com
      - http://1.2.3.4:8080   (публичный IP VPS, без домена)
      - https://1.2.3.4:8443
    Запрещено:
      - localhost / 127.0.0.1 / частные сети (192.168/10.x)
    """
    if not url:
        return (
            "Укажите PUBLIC_BASE_URL — адрес вашего VPS, куда 1win будет слать postback.\n"
            "Пример без домена: http://ВАШ_IP:8080\n"
            "Пример с доменом:  https://myserver.com"
        )

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()

    if scheme not in {"http", "https"}:
        return "PUBLIC_BASE_URL должен начинаться с http:// или https://"
    if not host:
        return "PUBLIC_BASE_URL указан некорректно."

    if host in PRIVATE_HOST_MARKERS or host.endswith(".local"):
        return "Нельзя использовать localhost. Нужен публичный IP VPS или домен."

    is_ip = False
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False

    if is_ip and not _is_public_ip(host):
        return (
            "IP из частной сети (192.168.x.x / 10.x.x.x) с интернета недоступен. "
            "Укажите публичный IP вашего VPS."
        )

    if scheme == "http":
        if is_ip and ALLOW_HTTP_IP:
            return None  # VPS по IP без домена — ок
        if not is_ip and not ALLOW_HTTP_IP:
            return (
                "Для домена лучше HTTPS. Либо поставьте ALLOW_HTTP_IP=true, "
                "либо укажите https://..."
            )
        if not is_ip and ALLOW_HTTP_IP:
            return None
        return (
            "HTTP без публичного IP запрещён. Пример: http://ВАШ_IP:8080 "
            "или купите домен и используйте https://"
        )

    return None


def postback_endpoint_url() -> str:
    return f"{PUBLIC_BASE_URL}/postback"


def postback_url_for_1win_cabinet() -> str:
    """Готовая строка для поля «Регистрация» в кабинете 1win."""
    base = postback_endpoint_url()
    parts = [
        f"{POSTBACK_CLICK_ID_PARAM}={{sub1}}",
        f"{POSTBACK_EVENT_PARAM}={POSTBACK_SUCCESS_EVENT}",
    ]
    if POSTBACK_SECRET:
        parts.append(f"{POSTBACK_SECRET_PARAM}={POSTBACK_SECRET}")
    return f"{base}?{'&'.join(parts)}"


def build_referral_url(click_id: str) -> str:
    """
    Кнопка ведёт сразу на вашу реферальную ссылку 1win:

      REFERRAL_URL&sub1=CLICK_ID

    Без прокладки. Telegram ID не передаём — только UUID click_id.
    """
    if not REFERRAL_URL:
        return ""

    parsed = urlparse(REFERRAL_URL)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["sub1"] = [click_id]
    new_query = urlencode({k: v[-1] for k, v in query.items()})
    return urlunparse(parsed._replace(query=new_query))


def list_signal_images() -> list[Path]:
    if not SIGNALS_DIR.exists():
        return []
    files = [
        p
        for p in SIGNALS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    files.sort(key=lambda p: p.name.lower())
    return files


def pick_signal_image(exclude: Optional[str] = None) -> Optional[Path]:
    images = list_signal_images()
    if not images:
        return None
    if exclude and len(images) > 1:
        candidates = [img for img in images if img.name != exclude]
        if candidates:
            return random.choice(candidates)
    return random.choice(images)


# ──────────────────────────────────────────────
# База данных (SQLite)
# ──────────────────────────────────────────────


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init(self) -> None:
        SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    click_id TEXT NOT NULL UNIQUE,
                    start_param TEXT,
                    is_activated INTEGER NOT NULL DEFAULT 0,
                    activation_notification_sent INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    last_activity TEXT,
                    signals_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    image TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS postbacks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    click_id TEXT,
                    event TEXT,
                    raw_data TEXT,
                    ip TEXT,
                    received_at TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0,
                    result TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_users_click_id ON users(click_id);
                CREATE INDEX IF NOT EXISTS idx_signals_telegram_id ON signals(telegram_id);
                CREATE INDEX IF NOT EXISTS idx_postbacks_click_id ON postbacks(click_id);
                """
            )
            # Мягкая миграция со старой схемы (activation_notified → activation_notification_sent)
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "activation_notification_sent" not in cols and "activation_notified" in cols:
                conn.execute(
                    "ALTER TABLE users RENAME COLUMN activation_notified "
                    "TO activation_notification_sent"
                )
            elif "activation_notification_sent" not in cols:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN activation_notification_sent "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            conn.commit()
        logger.info("Database initialized: %s", self.path)

    async def execute(self, query: str, params: tuple = ()) -> None:
        async with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(query, params)
                conn.commit()

    async def fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        async with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(query, params)
                return cur.fetchone()

    async def fetchall(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        async with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(query, params)
                return list(cur.fetchall())

    async def get_user(self, telegram_id: int) -> Optional[sqlite3.Row]:
        return await self.fetchone(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )

    async def get_user_by_click_id(self, click_id: str) -> Optional[sqlite3.Row]:
        return await self.fetchone(
            "SELECT * FROM users WHERE click_id = ?", (click_id,)
        )

    async def create_user(
        self,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
        start_param: Optional[str],
    ) -> sqlite3.Row:
        now = dt_to_str(utcnow())
        click_id = uuid.uuid4().hex
        await self.execute(
            """
            INSERT INTO users (
                telegram_id, username, first_name, click_id, start_param,
                is_activated, activation_notification_sent,
                created_at, last_activity, signals_count
            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, 0)
            """,
            (telegram_id, username, first_name, click_id, start_param, now, now),
        )
        user = await self.get_user(telegram_id)
        assert user is not None
        logger.info(
            "Generated click_id=%s for telegram_id=%s",
            click_id,
            telegram_id,
        )
        return user

    async def touch_activity(self, telegram_id: int) -> None:
        await self.execute(
            "UPDATE users SET last_activity = ? WHERE telegram_id = ?",
            (dt_to_str(utcnow()), telegram_id),
        )

    async def activate_user(self, click_id: str) -> tuple[Optional[sqlite3.Row], bool]:
        """
        Идемпотентная активация.
        Возвращает (user, newly_activated).
        """
        user = await self.get_user_by_click_id(click_id)
        if user is None:
            return None, False
        if user["is_activated"]:
            return user, False
        now = dt_to_str(utcnow())
        await self.execute(
            """
            UPDATE users
            SET is_activated = 1, activated_at = ?, last_activity = ?
            WHERE click_id = ? AND is_activated = 0
            """,
            (now, now, click_id),
        )
        return await self.get_user_by_click_id(click_id), True

    async def mark_activation_notification_sent(self, telegram_id: int) -> bool:
        """True = пометили впервые (можно слать сообщение)."""
        async with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    """
                    UPDATE users
                    SET activation_notification_sent = 1
                    WHERE telegram_id = ? AND activation_notification_sent = 0
                    """,
                    (telegram_id,),
                )
                conn.commit()
                return cur.rowcount > 0

    async def clear_activation_notification_sent(self, telegram_id: int) -> None:
        await self.execute(
            "UPDATE users SET activation_notification_sent = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )

    async def create_signal(self, telegram_id: int, image: str) -> sqlite3.Row:
        now = utcnow()
        expires = now + timedelta(seconds=SIGNAL_LIFETIME)
        async with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO signals (telegram_id, image, created_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (telegram_id, image, dt_to_str(now), dt_to_str(expires)),
                )
                conn.execute(
                    """
                    UPDATE users
                    SET signals_count = signals_count + 1, last_activity = ?
                    WHERE telegram_id = ?
                    """,
                    (dt_to_str(now), telegram_id),
                )
                conn.commit()
                signal_id = cur.lastrowid
                row = conn.execute(
                    "SELECT * FROM signals WHERE id = ?", (signal_id,)
                ).fetchone()
                assert row is not None
                return row

    async def get_signal(self, signal_id: int) -> Optional[sqlite3.Row]:
        return await self.fetchone("SELECT * FROM signals WHERE id = ?", (signal_id,))

    async def insert_postback(
        self,
        click_id: Optional[str],
        event: Optional[str],
        raw_data: str,
        ip: Optional[str],
        processed: bool,
        result: str,
    ) -> int:
        async with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO postbacks
                        (click_id, event, raw_data, ip, received_at, processed, result)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        click_id,
                        event,
                        raw_data,
                        ip,
                        dt_to_str(utcnow()),
                        1 if processed else 0,
                        result,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)

    async def stats(self) -> dict[str, int]:
        today = utcnow().strftime("%Y-%m-%d")
        total = await self.fetchone("SELECT COUNT(*) AS c FROM users")
        activated = await self.fetchone(
            "SELECT COUNT(*) AS c FROM users WHERE is_activated = 1"
        )
        not_activated = await self.fetchone(
            "SELECT COUNT(*) AS c FROM users WHERE is_activated = 0"
        )
        today_users = await self.fetchone(
            "SELECT COUNT(*) AS c FROM users WHERE created_at LIKE ?",
            (f"{today}%",),
        )
        today_activations = await self.fetchone(
            "SELECT COUNT(*) AS c FROM users WHERE activated_at LIKE ?",
            (f"{today}%",),
        )
        total_signals = await self.fetchone("SELECT COUNT(*) AS c FROM signals")
        today_signals = await self.fetchone(
            "SELECT COUNT(*) AS c FROM signals WHERE created_at LIKE ?",
            (f"{today}%",),
        )
        return {
            "total": int(total["c"] if total else 0),
            "activated": int(activated["c"] if activated else 0),
            "not_activated": int(not_activated["c"] if not_activated else 0),
            "today_users": int(today_users["c"] if today_users else 0),
            "today_activations": int(today_activations["c"] if today_activations else 0),
            "total_signals": int(total_signals["c"] if total_signals else 0),
            "today_signals": int(today_signals["c"] if today_signals else 0),
        }

    async def recent_users(self, limit: int = 20) -> list[sqlite3.Row]:
        return await self.fetchall(
            """
            SELECT telegram_id, username, first_name, click_id, is_activated, created_at
            FROM users
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )


db = Database(DB_PATH)

bot: Optional[Bot] = None
dp = Dispatcher()

# telegram_id → asyncio.Task отложенного уведомления
_pending_notifications: dict[int, asyncio.Task] = {}

# ──────────────────────────────────────────────
# Клавиатуры
# ──────────────────────────────────────────────


def build_landing_url(click_id: str) -> str:
    """Алиас: то же самое, что build_referral_url."""
    return build_referral_url(click_id)


def kb_register(click_id: str) -> InlineKeyboardMarkup:
    url = build_referral_url(click_id)
    if url:
        buttons = [[InlineKeyboardButton(text="🔓 Пройти регистрацию", url=url)]]
    else:
        buttons = [
            [
                InlineKeyboardButton(
                    text="🔓 Пройти регистрацию",
                    callback_data="register_no_url",
                )
            ]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎯 Получить прогноз", callback_data="get_signal")],
            [InlineKeyboardButton(text="ℹ️ Информация", callback_data="info")],
        ]
    )


def kb_signal(signal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Получить еще",
                    callback_data=f"more_signal:{signal_id}",
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")],
        ]
    )


# ──────────────────────────────────────────────
# Уведомления
# ──────────────────────────────────────────────


async def notify_admin(text: str) -> None:
    if not ADMIN_ID or bot is None:
        return
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode=ParseMode.HTML)
    except TelegramAPIError as exc:
        logger.warning("Failed to notify admin: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected admin notify error: %s", exc)


async def send_activation_message(telegram_id: int) -> None:
    """Отправляет уведомление об активации ровно один раз."""
    if bot is None:
        logger.warning("Bot is not ready; skip activation notice for %s", telegram_id)
        return

    marked = await db.mark_activation_notification_sent(telegram_id)
    if not marked:
        logger.info("Activation notice already sent for %s", telegram_id)
        return

    user = await db.get_user(telegram_id)
    if user is None or not user["is_activated"]:
        return

    try:
        await bot.send_message(
            telegram_id,
            "✅ Регистрация завершена!\n\n"
            "Бот активирован.\n\n"
            "Теперь вы можете получать прогнозы.",
            reply_markup=kb_main_menu(),
        )
        logger.info("Activation notice sent to %s", telegram_id)
    except TelegramAPIError as exc:
        logger.warning("Failed to send activation notice to %s: %s", telegram_id, exc)
        await db.clear_activation_notification_sent(telegram_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected activation notice error: %s", exc)
        await db.clear_activation_notification_sent(telegram_id)


async def schedule_activation_notification(telegram_id: int) -> None:
    existing = _pending_notifications.get(telegram_id)
    if existing and not existing.done():
        return

    async def _delayed() -> None:
        try:
            if ACTIVATION_NOTIFICATION_DELAY > 0:
                await asyncio.sleep(ACTIVATION_NOTIFICATION_DELAY)
            await send_activation_message(telegram_id)
        except asyncio.CancelledError:
            logger.info("Activation notice cancelled for %s", telegram_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Delayed activation notice failed: %s", exc)
        finally:
            _pending_notifications.pop(telegram_id, None)

    _pending_notifications[telegram_id] = asyncio.create_task(_delayed())


# ──────────────────────────────────────────────
# Telegram handlers
# ──────────────────────────────────────────────


def is_admin(telegram_id: int) -> bool:
    return bool(ADMIN_ID) and telegram_id == ADMIN_ID


def can_use_bot(user: Optional[sqlite3.Row], telegram_id: int) -> bool:
    """Доступ: активирован postback'ом, либо админ, либо DEV_SKIP_ACTIVATION."""
    if user is not None and user["is_activated"]:
        return True
    if is_admin(telegram_id):
        return True
    if DEV_SKIP_ACTIVATION:
        return True
    return False


async def ensure_dev_access(user: sqlite3.Row) -> sqlite3.Row:
    """
    Для теста/админа: сразу активируем в БД, чтобы меню и прогнозы работали
    без реального postback от 1win.
    """
    if user["is_activated"]:
        return user
    if not (is_admin(int(user["telegram_id"])) or DEV_SKIP_ACTIVATION):
        return user
    activated, _newly = await db.activate_user(user["click_id"])
    if activated is not None:
        await db.mark_activation_notification_sent(int(user["telegram_id"]))
        logger.info(
            "Dev/admin access granted without postback: telegram_id=%s",
            user["telegram_id"],
        )
        return activated
    return user


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return

    tg = message.from_user
    start_param = (command.args or "").strip() or None

    try:
        user = await db.get_user(tg.id)
        is_new = False
        if user is None:
            user = await db.create_user(
                telegram_id=tg.id,
                username=tg.username,
                first_name=tg.first_name,
                start_param=start_param,
            )
            is_new = True
            logger.info(
                "New Telegram user id=%s username=@%s click_id=%s start=%s",
                tg.id,
                tg.username,
                user["click_id"],
                start_param,
            )
            if ADMIN_NOTIFY_ON_NEW_USER:
                await notify_admin(
                    "🆕 <b>Новый пользователь</b>\n\n"
                    f"ID: <code>{tg.id}</code>\n"
                    f"Username: @{tg.username or '—'}\n"
                    f"Имя: {tg.first_name or '—'}\n"
                    f"Click ID: <code>{user['click_id']}</code>\n"
                    f"Дата: {format_local_date()}\n"
                    f"Start: <code>{start_param or '—'}</code>"
                )
        else:
            await db.touch_activity(tg.id)
            await db.execute(
                """
                UPDATE users
                SET username = ?, first_name = ?,
                    start_param = COALESCE(?, start_param)
                WHERE telegram_id = ?
                """,
                (tg.username, tg.first_name, start_param, tg.id),
            )
            user = await db.get_user(tg.id)

        assert user is not None

        # Админ или DEV_SKIP_ACTIVATION — смотрим бота без регистрации 1win
        if can_use_bot(user, tg.id) and not user["is_activated"]:
            user = await ensure_dev_access(user)

        if can_use_bot(user, tg.id):
            if user["is_activated"] and not user["activation_notification_sent"]:
                await db.mark_activation_notification_sent(tg.id)
            note = ""
            if DEV_SKIP_ACTIVATION or is_admin(tg.id):
                note = "\n\n🧪 Тестовый доступ без регистрации."
            await message.answer(
                "✅ Доступ активирован!\n\n"
                "Теперь вы можете получать прогнозы на ближайшие игры."
                f"{note}",
                reply_markup=kb_main_menu(),
            )
        else:
            await message.answer(
                "👋 Добро пожаловать!\n\n"
                "Для активации бота необходимо пройти регистрацию.\n\n"
                "После успешной регистрации доступ будет активирован автоматически.",
                reply_markup=kb_register(user["click_id"]),
            )
            if is_new:
                logger.debug(
                    "Registration link for %s: %s",
                    tg.id,
                    build_referral_url(user["click_id"]),
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_start error: %s", exc)
        try:
            await message.answer("Произошла ошибка. Попробуйте позже.")
        except TelegramAPIError:
            pass


@dp.message(Command("unlock"))
async def cmd_unlock(message: Message) -> None:
    """Админ: открыть себе доступ без регистрации (/unlock)."""
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    user = await db.get_user(message.from_user.id)
    if user is None:
        await message.answer("Сначала нажмите /start")
        return
    user = await ensure_dev_access(user)
    await message.answer(
        "🧪 Тестовый доступ открыт без регистрации.",
        reply_markup=kb_main_menu(),
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if message.from_user is None or message.from_user.id != ADMIN_ID:
        return
    try:
        s = await db.stats()
        await message.answer(
            "📊 <b>Статистика</b>\n\n"
            f"Всего пользователей: <b>{s['total']}</b>\n"
            f"Активировано: <b>{s['activated']}</b>\n"
            f"Не активировано: <b>{s['not_activated']}</b>\n"
            f"Новых сегодня: <b>{s['today_users']}</b>\n"
            f"Активаций сегодня: <b>{s['today_activations']}</b>\n"
            f"Всего прогнозов: <b>{s['total_signals']}</b>\n"
            f"Прогнозов сегодня: <b>{s['today_signals']}</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_stats error: %s", exc)
        await message.answer("Ошибка получения статистики.")


@dp.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if message.from_user is None or message.from_user.id != ADMIN_ID:
        return
    try:
        rows = await db.recent_users(20)
        if not rows:
            await message.answer("Пользователей пока нет.")
            return
        lines = ["👥 <b>Последние пользователи</b>\n"]
        for row in rows:
            status = "✅" if row["is_activated"] else "⏳"
            uname = f"@{row['username']}" if row["username"] else "—"
            lines.append(
                f"{status} <code>{row['telegram_id']}</code> {uname}\n"
                f"   click_id=<code>{row['click_id']}</code>\n"
                f"   {row['created_at']}"
            )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_users error: %s", exc)
        await message.answer("Ошибка получения списка.")


@dp.callback_query(F.data == "register_no_url")
async def cb_register_no_url(callback: CallbackQuery) -> None:
    await callback.answer("REFERRAL_URL не задан. Укажите его в .env", show_alert=True)


@dp.callback_query(F.data == "info")
async def cb_info(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    user = await db.get_user(callback.from_user.id)
    if user is None or not can_use_bot(user, callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await db.touch_activity(callback.from_user.id)
    text = (
        "ℹ️ <b>Информация</b>\n\n"
        "Бот выдаёт случайный контент-прогноз на ближайшую игру.\n"
        f"Каждый прогноз действует {SIGNAL_LIFETIME // 60} минуты.\n\n"
        "Доступ открывается автоматически после подтверждённой регистрации."
    )
    try:
        if callback.message:
            await callback.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]
                    ]
                ),
            )
    except TelegramAPIError as exc:
        logger.warning("cb_info edit failed: %s", exc)
    await callback.answer()


@dp.callback_query(F.data == "back_main")
async def cb_back_main(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    user = await db.get_user(callback.from_user.id)
    if user is None or not can_use_bot(user, callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await db.touch_activity(callback.from_user.id)
    text = (
        "✅ Доступ активирован!\n\n"
        "Теперь вы можете получать прогнозы на ближайшие игры."
    )
    try:
        if callback.message:
            if callback.message.photo:
                try:
                    await callback.message.delete()
                except TelegramAPIError:
                    pass
                await bot.send_message(  # type: ignore[union-attr]
                    callback.from_user.id,
                    text,
                    reply_markup=kb_main_menu(),
                )
            else:
                await callback.message.edit_text(text, reply_markup=kb_main_menu())
    except TelegramAPIError as exc:
        logger.warning("cb_back_main failed: %s", exc)
        try:
            if bot is not None:
                await bot.send_message(
                    callback.from_user.id, text, reply_markup=kb_main_menu()
                )
        except TelegramAPIError:
            pass
    await callback.answer()


async def _send_signal(
    telegram_id: int, chat_id: int, exclude_image: Optional[str] = None
) -> bool:
    if bot is None:
        return False
    user = await db.get_user(telegram_id)
    if user is None or not can_use_bot(user, telegram_id):
        return False

    image_path = pick_signal_image(exclude=exclude_image)
    if image_path is None:
        await bot.send_message(
            chat_id,
            "⚠️ Прогнозы временно недоступны. Попробуйте позже.",
            reply_markup=kb_main_menu(),
        )
        return False

    try:
        signal = await db.create_signal(telegram_id, image_path.name)
        caption = (
            "🔮 <b>Прогноз на ближайшую игру</b>\n\n"
            f"⏱ Действует {SIGNAL_LIFETIME // 60} минуты."
        )
        await bot.send_photo(
            chat_id,
            photo=FSInputFile(image_path),
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_signal(signal["id"]),
        )
        if ADMIN_NOTIFY_ON_SIGNAL:
            await notify_admin(
                "🎯 <b>Получен прогноз</b>\n\n"
                f"ID: <code>{telegram_id}</code>\n"
                f"Username: @{user['username'] or '—'}\n"
                f"Image: <code>{image_path.name}</code>"
            )
        return True
    except TelegramAPIError as exc:
        logger.warning("send_signal telegram error for %s: %s", telegram_id, exc)
        try:
            await bot.send_message(
                chat_id,
                "Не удалось отправить прогноз. Попробуйте ещё раз.",
                reply_markup=kb_main_menu(),
            )
        except TelegramAPIError:
            pass
        return False
    except Exception as exc:  # noqa: BLE001
        logger.exception("send_signal error: %s", exc)
        try:
            await bot.send_message(
                chat_id,
                "Ошибка при выдаче прогноза. Попробуйте позже.",
                reply_markup=kb_main_menu(),
            )
        except TelegramAPIError:
            pass
        return False


@dp.callback_query(F.data == "get_signal")
async def cb_get_signal(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    user = await db.get_user(callback.from_user.id)
    if user is None or not can_use_bot(user, callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return

    await callback.answer()
    try:
        if callback.message:
            await callback.message.delete()
    except TelegramAPIError:
        pass

    await _send_signal(callback.from_user.id, callback.from_user.id)


@dp.callback_query(F.data.startswith("more_signal:"))
async def cb_more_signal(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        await callback.answer()
        return

    user = await db.get_user(callback.from_user.id)
    if user is None or not can_use_bot(user, callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return

    try:
        signal_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный запрос.", show_alert=True)
        return

    old_signal = await db.get_signal(signal_id)
    exclude_image = old_signal["image"] if old_signal else None

    if old_signal:
        expires_at = str_to_dt(old_signal["expires_at"])
        if expires_at and utcnow() > expires_at:
            await callback.answer("⏱ Предыдущий прогноз уже истек.", show_alert=False)
        else:
            await callback.answer()
    else:
        await callback.answer()

    try:
        if callback.message:
            await callback.message.delete()
    except TelegramAPIError:
        pass

    await _send_signal(
        callback.from_user.id,
        callback.from_user.id,
        exclude_image=exclude_image,
    )


@dp.callback_query()
async def cb_unknown(callback: CallbackQuery) -> None:
    await callback.answer("Неизвестная команда.", show_alert=False)


@dp.message()
async def fallback_message(message: Message) -> None:
    if message.from_user is None:
        return
    user = await db.get_user(message.from_user.id)
    if user is None:
        await message.answer("Нажмите /start для начала.")
        return
    if not can_use_bot(user, message.from_user.id):
        await message.answer(
            "Для активации бота необходимо пройти регистрацию.",
            reply_markup=kb_register(user["click_id"]),
        )
        return
    await message.answer(
        "Выберите действие в меню:",
        reply_markup=kb_main_menu(),
    )


# ──────────────────────────────────────────────
# Postback (1win)
# ──────────────────────────────────────────────
#
# ⚠️ МЕНЯТЬ ФОРМАТ ПАРАМЕТРОВ ЗДЕСЬ / в .env:
#   POSTBACK_CLICK_ID_PARAM, POSTBACK_EVENT_PARAM,
#   POSTBACK_SECRET_PARAM, POSTBACK_SUCCESS_EVENT
#
# Предполагаемый шаблон для кабинета 1win
# (поле «Ссылка на постбэк события — Регистрация»):
#
#   {PUBLIC_BASE_URL}/postback?click_id={sub1}&event=registration&secret=YOUR_SECRET
#
# {sub1} — макрос 1win, куда партнёрка подставит значение sub1 из клика.
# Точные имена макросов уточните в кабинете 1win — это НЕ финальная спецификация.


def parse_postback_params(query: dict[str, str]) -> dict[str, Any]:
    """Нормализует query-параметры postback под внутренний формат."""
    click_id = (query.get(POSTBACK_CLICK_ID_PARAM) or "").strip()
    # Запасные алиасы на случай другого имени в кабинете
    if not click_id:
        for alt in ("sub1", "subid", "sub_id", "clickid"):
            if query.get(alt):
                click_id = str(query[alt]).strip()
                break

    event = (query.get(POSTBACK_EVENT_PARAM) or "").strip().lower()
    if not event:
        for alt in ("status", "action", "type"):
            if query.get(alt):
                event = str(query[alt]).strip().lower()
                break

    secret = (query.get(POSTBACK_SECRET_PARAM) or "").strip()
    if not secret:
        for alt in ("token", "key", "sign"):
            if query.get(alt):
                secret = str(query[alt]).strip()
                break

    return {"click_id": click_id, "event": event, "secret": secret, "raw": query}


async def process_postback(
    *,
    click_id: str,
    event: str,
    secret: str,
    raw_data: str,
    ip: Optional[str],
) -> tuple[int, dict[str, Any]]:
    """
    Единая точка обработки postback.
    Возвращает (http_status, json_body).
    """
    # IP whitelist (опционально)
    if POSTBACK_ALLOWED_IPS and ip and ip not in POSTBACK_ALLOWED_IPS:
        await db.insert_postback(click_id, event, raw_data, ip, False, "ip_denied")
        logger.warning("Postback denied by IP: %s", ip)
        return 403, {"ok": False, "error": "forbidden"}

    # Secret — только если задан в .env
    if POSTBACK_SECRET and secret != POSTBACK_SECRET:
        await db.insert_postback(click_id, event, raw_data, ip, False, "bad_secret")
        logger.warning("Postback bad secret from %s", ip)
        return 401, {"ok": False, "error": "unauthorized"}

    if not click_id:
        await db.insert_postback(click_id, event, raw_data, ip, False, "missing_click_id")
        logger.warning("Postback missing click_id from %s", ip)
        return 400, {"ok": False, "error": "missing click_id"}

    # Активируем ТОЛЬКО событие registration (не deposit / revenue / income)
    if event != POSTBACK_SUCCESS_EVENT:
        await db.insert_postback(click_id, event, raw_data, ip, False, "ignored_event")
        logger.info(
            "Postback ignored: event=%s (need %s) click_id=%s",
            event,
            POSTBACK_SUCCESS_EVENT,
            click_id,
        )
        return 200, {
            "ok": True,
            "accepted": False,
            "reason": "ignored_event",
            "event": event,
        }

    user, newly = await db.activate_user(click_id)
    if user is None:
        await db.insert_postback(click_id, event, raw_data, ip, False, "unknown_click_id")
        logger.warning("Postback unknown click_id=%s", click_id)
        return 404, {"ok": False, "error": "user_not_found", "click_id": click_id}

    if newly:
        await db.insert_postback(click_id, event, raw_data, ip, True, "activated")
        logger.info(
            "User activated via postback: telegram_id=%s click_id=%s",
            user["telegram_id"],
            click_id,
        )
        if ADMIN_NOTIFY_ON_ACTIVATION:
            await notify_admin(
                "✅ <b>Регистрация подтверждена</b>\n\n"
                f"ID: <code>{user['telegram_id']}</code>\n"
                f"Username: @{user['username'] or '—'}\n"
                f"Click ID: <code>{user['click_id']}</code>"
            )
        await schedule_activation_notification(int(user["telegram_id"]))
        return 200, {
            "ok": True,
            "accepted": True,
            "activated": True,
            "telegram_id": user["telegram_id"],
        }

    # Повторный postback — идемпотентно
    await db.insert_postback(click_id, event, raw_data, ip, True, "already_activated")
    logger.info("Duplicate postback for click_id=%s (already activated)", click_id)
    return 200, {
        "ok": True,
        "accepted": True,
        "activated": False,
        "already_activated": True,
        "telegram_id": user["telegram_id"],
    }


async def handle_postback(request: web.Request) -> web.Response:
    peer = request.remote or ""
    query_raw: dict[str, str] = {k: str(v) for k, v in request.rel_url.query.items()}

    if request.method == "POST":
        try:
            if request.content_type and "json" in request.content_type:
                body = await request.json()
                if isinstance(body, dict):
                    query_raw.update({str(k): str(v) for k, v in body.items()})
            else:
                form = await request.post()
                query_raw.update({str(k): str(v) for k, v in form.items()})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Postback body parse error: %s", exc)

    parsed = parse_postback_params(query_raw)
    raw_data = json.dumps(query_raw, ensure_ascii=False)

    logger.info(
        "Postback received ip=%s click_id=%s event=%s raw=%s",
        peer,
        parsed["click_id"],
        parsed["event"],
        raw_data,
    )

    try:
        status, body = await process_postback(
            click_id=parsed["click_id"],
            event=parsed["event"],
            secret=parsed["secret"],
            raw_data=raw_data,
            ip=peer,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Postback processing error: %s", exc)
        return web.json_response({"ok": False, "error": "internal"}, status=500)

    return web.json_response(body, status=status)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "service": "mineslot-bot",
            "postback": postback_endpoint_url() if PUBLIC_BASE_URL else None,
        }
    )


def create_http_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/postback", handle_postback)
    app.router.add_post("/postback", handle_postback)
    app.router.add_get("/health", handle_health)
    return app


# ──────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────


async def main() -> None:
    global bot

    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан. Укажите его в .env")

    url_error = validate_public_base_url(PUBLIC_BASE_URL)
    if url_error:
        raise SystemExit(url_error)

    if not REFERRAL_URL:
        raise SystemExit("REFERRAL_URL не задан. Укажите реферальную ссылку 1win в .env")

    db.init()
    images = list_signal_images()
    logger.info("Found %d signal images in %s", len(images), SIGNALS_DIR)
    if not images:
        logger.warning(
            "Папка signals/ пуста — положите туда jpg/png/webp (хоть 1, хоть 8). "
            "Бот сам берёт случайную картинку."
        )

    cabinet_url = postback_url_for_1win_cabinet()
    logger.info("1win referral URL: %s", REFERRAL_URL)
    logger.info("PUBLIC_BASE_URL: %s", PUBLIC_BASE_URL)
    logger.info(">>> Вставьте в кабинет 1win (Регистрация): %s", cabinet_url)
    if urlparse(PUBLIC_BASE_URL).scheme == "http":
        logger.warning(
            "Используется HTTP по IP (без домена). Откройте порт %s в firewall VPS. "
            "Если 1win отвергнет HTTP — понадобится HTTPS/домен.",
            HTTP_PORT,
        )

    bot = Bot(token=BOT_TOKEN)
    http_app = create_http_app()
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await site.start()
    logger.info(
        "Postback endpoint listening: http://%s:%s/postback",
        HTTP_HOST,
        HTTP_PORT,
    )

    try:
        logger.info("Starting Telegram polling…")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Stopping…")
        for task in list(_pending_notifications.values()):
            task.cancel()
        await runner.cleanup()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        logger.info("Exit: %s", exc)
