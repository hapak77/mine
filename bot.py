#!/usr/bin/env python3
"""
MineSlot Signal Bot — MVP на aiogram 3.x + aiohttp.
Запуск: python bot.py
"""

from __future__ import annotations

import asyncio
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
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

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

LANDING_URL = os.getenv("LANDING_URL", "").strip()
POSTBACK_URL = os.getenv("POSTBACK_URL", "").strip()  # справочно: URL, который вы дадите партнёрке

POSTBACK_SECRET = os.getenv("POSTBACK_SECRET", "").strip()
POSTBACK_SUCCESS_STATUS = os.getenv("POSTBACK_SUCCESS_STATUS", "reg").strip().lower()
POSTBACK_ALLOWED_IPS = {
    ip.strip()
    for ip in os.getenv("POSTBACK_ALLOWED_IPS", "").split(",")
    if ip.strip()
}

HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0").strip()
HTTP_PORT = int(os.getenv("HTTP_PORT", "8080") or "8080")

ACTIVATION_NOTIFICATION_DELAY = int(os.getenv("ACTIVATION_NOTIFICATION_DELAY", "120") or "120")
SIGNAL_LIFETIME = int(os.getenv("SIGNAL_LIFETIME", "240") or "240")
ADMIN_NOTIFY_ON_SIGNAL = os.getenv("ADMIN_NOTIFY_ON_SIGNAL", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

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


def build_landing_url(click_id: str) -> str:
    """
    Собирает ссылку на прокладку с click_id.

    Типичная схема партнёрок: LANDING_URL?subid={click_id}
    Telegram ID намеренно НЕ передаём напрямую — только уникальный click_id (UUID).
    """
    if not LANDING_URL:
        return ""

    parsed = urlparse(LANDING_URL)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["subid"] = [click_id]
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
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    last_activity TEXT,
                    signals_count INTEGER NOT NULL DEFAULT 0,
                    activation_notified INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    image TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS postback_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    click_id TEXT,
                    raw_query TEXT,
                    status TEXT,
                    ip TEXT,
                    accepted INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_users_click_id ON users(click_id);
                CREATE INDEX IF NOT EXISTS idx_signals_telegram_id ON signals(telegram_id);
                """
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
                is_activated, created_at, last_activity, signals_count, activation_notified
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0, 0)
            """,
            (telegram_id, username, first_name, click_id, start_param, now, now),
        )
        user = await self.get_user(telegram_id)
        assert user is not None
        return user

    async def touch_activity(self, telegram_id: int) -> None:
        await self.execute(
            "UPDATE users SET last_activity = ? WHERE telegram_id = ?",
            (dt_to_str(utcnow()), telegram_id),
        )

    async def activate_user(self, click_id: str) -> Optional[sqlite3.Row]:
        """Идемпотентная активация. Возвращает пользователя или None."""
        user = await self.get_user_by_click_id(click_id)
        if user is None:
            return None
        if user["is_activated"]:
            return user
        now = dt_to_str(utcnow())
        await self.execute(
            """
            UPDATE users
            SET is_activated = 1, activated_at = ?, last_activity = ?
            WHERE click_id = ? AND is_activated = 0
            """,
            (now, now, click_id),
        )
        return await self.get_user_by_click_id(click_id)

    async def mark_activation_notified(self, telegram_id: int) -> bool:
        """Помечает, что уведомление об активации отправлено. True = пометили впервые."""
        async with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    """
                    UPDATE users
                    SET activation_notified = 1
                    WHERE telegram_id = ? AND activation_notified = 0
                    """,
                    (telegram_id,),
                )
                conn.commit()
                return cur.rowcount > 0

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

    async def get_last_signal(self, telegram_id: int) -> Optional[sqlite3.Row]:
        return await self.fetchone(
            """
            SELECT * FROM signals
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id,),
        )

    async def log_postback(
        self,
        click_id: Optional[str],
        raw_query: str,
        status: Optional[str],
        ip: Optional[str],
        accepted: bool,
        reason: str,
    ) -> None:
        await self.execute(
            """
            INSERT INTO postback_log (click_id, raw_query, status, ip, accepted, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                click_id,
                raw_query,
                status,
                ip,
                1 if accepted else 0,
                reason,
                dt_to_str(utcnow()),
            ),
        )

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

# Глобальные объекты — bot инициализируется в main()
bot: Optional[Bot] = None
dp = Dispatcher()

# pending activation notifications: telegram_id -> asyncio.Task
_pending_notifications: dict[int, asyncio.Task] = {}

# ──────────────────────────────────────────────
# Клавиатуры
# ──────────────────────────────────────────────


def kb_register(click_id: str) -> InlineKeyboardMarkup:
    url = build_landing_url(click_id)
    buttons = []
    if url:
        buttons.append(
            [InlineKeyboardButton(text="🔓 Пройти регистрацию", url=url)]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🔓 Пройти регистрацию",
                    callback_data="register_no_url",
                )
            ]
        )
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
# Уведомления админу / пользователю
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
    """Отправляет уведомление об активации один раз."""
    if bot is None:
        logger.warning("Bot is not ready; skip activation notice for %s", telegram_id)
        return

    marked = await db.mark_activation_notified(telegram_id)
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
            "Бот активирован. Теперь вы можете получать прогнозы на ближайшие игры.",
            reply_markup=kb_main_menu(),
        )
        logger.info("Activation notice sent to %s", telegram_id)
    except TelegramAPIError as exc:
        logger.warning("Failed to send activation notice to %s: %s", telegram_id, exc)
        # Разрешаем повторную попытку при ошибке Telegram
        await db.execute(
            "UPDATE users SET activation_notified = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected activation notice error: %s", exc)
        await db.execute(
            "UPDATE users SET activation_notified = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )


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
# Handlers
# ──────────────────────────────────────────────


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
                "New user %s (@%s) click_id=%s start=%s",
                tg.id,
                tg.username,
                user["click_id"],
                start_param,
            )
            await notify_admin(
                "🆕 <b>Новый пользователь</b>\n\n"
                f"ID: <code>{tg.id}</code>\n"
                f"Username: @{tg.username or '—'}\n"
                f"Имя: {tg.first_name or '—'}\n"
                f"Дата: {format_local_date()}\n"
                f"Click ID: <code>{user['click_id']}</code>\n"
                f"Start: <code>{start_param or '—'}</code>"
            )
        else:
            await db.touch_activity(tg.id)
            # Обновляем профиль / start_param при повторном заходе с параметром
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

        if user["is_activated"]:
            # Если уведомление ещё не ушло — отправим сразу через меню, без дубля
            if not user["activation_notified"]:
                await db.mark_activation_notified(tg.id)
            await message.answer(
                "✅ Доступ активирован!\n\n"
                "Теперь вы можете получать прогнозы на ближайшие игры.",
                reply_markup=kb_main_menu(),
            )
        else:
            await message.answer(
                "👋 Добро пожаловать!\n\n"
                "Для получения прогнозов необходимо сначала пройти регистрацию.\n\n"
                "После регистрации доступ к боту будет активирован автоматически.",
                reply_markup=kb_register(user["click_id"]),
            )
            if is_new:
                logger.debug("Registration prompt shown to new user %s", tg.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_start error: %s", exc)
        await message.answer("Произошла ошибка. Попробуйте позже.")


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if message.from_user is None or message.from_user.id != ADMIN_ID:
        return
    try:
        s = await db.stats()
        await message.answer(
            "📊 <b>Статистика</b>\n\n"
            f"Всего пользователей: <b>{s['total']}</b>\n"
            f"Активированных: <b>{s['activated']}</b>\n"
            f"Неактивированных: <b>{s['not_activated']}</b>\n"
            f"Пользователей за сегодня: <b>{s['today_users']}</b>\n"
            f"Активаций за сегодня: <b>{s['today_activations']}</b>\n"
            f"Всего прогнозов: <b>{s['total_signals']}</b>\n"
            f"Прогнозов за сегодня: <b>{s['today_signals']}</b>",
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
                f"{status} <code>{row['telegram_id']}</code> {uname} "
                f"| {row['first_name'] or '—'} | {row['created_at']}"
            )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_users error: %s", exc)
        await message.answer("Ошибка получения списка.")


@dp.callback_query(F.data == "register_no_url")
async def cb_register_no_url(callback: CallbackQuery) -> None:
    await callback.answer(
        "LANDING_URL не задан. Укажите его в .env",
        show_alert=True,
    )


@dp.callback_query(F.data == "info")
async def cb_info(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    user = await db.get_user(callback.from_user.id)
    if user is None or not user["is_activated"]:
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await db.touch_activity(callback.from_user.id)
    text = (
        "ℹ️ <b>Информация</b>\n\n"
        "Бот выдаёт случайный прогноз на ближайшую игру.\n"
        f"Каждый прогноз действует {SIGNAL_LIFETIME // 60} минуты.\n\n"
        "После регистрации доступ открывается автоматически."
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
    if user is None or not user["is_activated"]:
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await db.touch_activity(callback.from_user.id)
    text = (
        "✅ Доступ активирован!\n\n"
        "Теперь вы можете получать прогнозы на ближайшие игры."
    )
    try:
        if callback.message:
            # Если сообщение с фото — удаляем и шлём новое текстовое меню
            if callback.message.photo:
                try:
                    await callback.message.delete()
                except TelegramAPIError:
                    pass
                await bot.send_message(
                    callback.from_user.id,
                    text,
                    reply_markup=kb_main_menu(),
                )
            else:
                await callback.message.edit_text(text, reply_markup=kb_main_menu())
    except TelegramAPIError as exc:
        logger.warning("cb_back_main failed: %s", exc)
        try:
            await bot.send_message(
                callback.from_user.id, text, reply_markup=kb_main_menu()
            )
        except TelegramAPIError:
            pass
    await callback.answer()


async def _send_signal(telegram_id: int, chat_id: int, exclude_image: Optional[str] = None) -> bool:
    if bot is None:
        return False
    user = await db.get_user(telegram_id)
    if user is None or not user["is_activated"]:
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
        await bot.send_message(
            chat_id,
            "Не удалось отправить прогноз. Попробуйте ещё раз.",
            reply_markup=kb_main_menu(),
        )
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
    if user is None or not user["is_activated"]:
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return

    await callback.answer()
    # Убираем старое меню, чтобы не засорять чат
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
    if user is None or not user["is_activated"]:
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return

    try:
        signal_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный запрос.", show_alert=True)
        return

    old_signal = await db.get_signal(signal_id)
    exclude_image = old_signal["image"] if old_signal else None

    # Если предыдущий прогноз истёк — мягко сообщаем, но всё равно выдаём новый
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
    if not user["is_activated"]:
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
# Postback
# ──────────────────────────────────────────────
#
# ⚠️ ЕДИНСТВЕННОЕ МЕСТО ДЛЯ АДАПТАЦИИ ПОД РЕАЛЬНЫЙ ФОРМАТ ПАРТНЁРКИ:
# функция parse_postback_params() ниже.
# После получения реального URL/формата postback — правьте ТОЛЬКО её.


def parse_postback_params(query: dict[str, str]) -> dict[str, Any]:
    """
    Нормализует входящие query-параметры postback.

    Ожидаемый (гибкий) формат по умолчанию:
      GET /postback?click_id=UUID&status=reg&secret=TOKEN
      или
      GET /postback?subid=UUID&event=reg&secret=TOKEN

    Адаптируйте маппинг полей под вашу партнёрку здесь.
    """
    click_id = (
        query.get("click_id")
        or query.get("subid")
        or query.get("sub_id")
        or query.get("clickid")
        or ""
    ).strip()

    status = (
        query.get("status")
        or query.get("event")
        or query.get("action")
        or ""
    ).strip().lower()

    secret = (
        query.get("secret")
        or query.get("token")
        or query.get("key")
        or ""
    ).strip()

    return {
        "click_id": click_id,
        "status": status,
        "secret": secret,
        "raw": query,
    }


async def handle_postback(request: web.Request) -> web.Response:
    peer = request.remote or ""
    query_raw = dict(request.rel_url.query)
    # Также принимаем POST form/json (на будущее)
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

    parsed = parse_postback_params({k: str(v) for k, v in query_raw.items()})
    click_id = parsed["click_id"]
    status = parsed["status"]
    secret = parsed["secret"]
    raw_qs = str(request.rel_url.query)

    logger.info(
        "Postback received ip=%s click_id=%s status=%s qs=%s",
        peer,
        click_id,
        status,
        raw_qs,
    )

    # IP whitelist (опционально)
    if POSTBACK_ALLOWED_IPS and peer not in POSTBACK_ALLOWED_IPS:
        await db.log_postback(click_id, raw_qs, status, peer, False, "ip_denied")
        logger.warning("Postback denied by IP: %s", peer)
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    # Secret
    if POSTBACK_SECRET and secret != POSTBACK_SECRET:
        await db.log_postback(click_id, raw_qs, status, peer, False, "bad_secret")
        logger.warning("Postback bad secret from %s", peer)
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    if not click_id:
        await db.log_postback(click_id, raw_qs, status, peer, False, "missing_click_id")
        return web.json_response({"ok": False, "error": "missing click_id"}, status=400)

    # Статус события
    if status and status != POSTBACK_SUCCESS_STATUS:
        await db.log_postback(click_id, raw_qs, status, peer, False, "ignored_status")
        return web.json_response({"ok": True, "accepted": False, "reason": "ignored_status"})

    # Если статус пустой — считаем успешным (некоторые партнёрки шлют только click_id)
    user_before = await db.get_user_by_click_id(click_id)
    if user_before is None:
        await db.log_postback(click_id, raw_qs, status, peer, False, "user_not_found")
        logger.warning("Postback: unknown click_id=%s", click_id)
        return web.json_response({"ok": False, "error": "user_not_found"}, status=404)

    already = bool(user_before["is_activated"])
    user = await db.activate_user(click_id)
    if user is None:
        await db.log_postback(click_id, raw_qs, status, peer, False, "activate_failed")
        return web.json_response({"ok": False, "error": "activate_failed"}, status=500)

    await db.log_postback(click_id, raw_qs, status, peer, True, "ok" if not already else "already_activated")

    if not already:
        await notify_admin(
            "✅ <b>Пользователь активирован</b>\n\n"
            f"ID: <code>{user['telegram_id']}</code>\n"
            f"Username: @{user['username'] or '—'}\n"
            f"Click ID: <code>{user['click_id']}</code>"
        )
        await schedule_activation_notification(int(user["telegram_id"]))
        logger.info("User activated via postback: %s", user["telegram_id"])
    else:
        logger.info("Duplicate postback for click_id=%s (already activated)", click_id)

    return web.json_response(
        {
            "ok": True,
            "accepted": True,
            "already_activated": already,
            "telegram_id": user["telegram_id"],
        }
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "mineslot-bot"})


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

    db.init()
    images = list_signal_images()
    logger.info("Found %d signal images in %s", len(images), SIGNALS_DIR)
    if not images:
        logger.warning("Папка signals/ пуста — прогнозы выдавать нечего")

    bot = Bot(token=BOT_TOKEN)
    http_app = create_http_app()
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await site.start()
    logger.info("HTTP postback server on http://%s:%s/postback", HTTP_HOST, HTTP_PORT)

    try:
        logger.info("Starting Telegram polling…")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in list(_pending_notifications.values()):
            task.cancel()
        await runner.cleanup()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Exit")
