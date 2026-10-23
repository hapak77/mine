# -*- coding: utf-8 -*-
import asyncio
import logging
import sys
import time
from pathlib import Path

from aiogram import Bot

from bot.config import settings


class AdminLogHandler(logging.Handler):
    """Пересылает ERROR и CRITICAL в админский чат, без повторов и без рекурсии."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(level=logging.ERROR)
        self.bot = bot
        self._sending = False
        self._recent: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if self._sending or not settings.ADMIN_CHAT_ID:
            return
        try:
            text = self.format(record)
        except Exception:
            return
        if len(text) > 3500:
            text = text[:3500] + "…"
        key = text[:240]
        now = time.monotonic()
        last = self._recent.get(key)
        if last is not None and now - last < 10:
            return
        self._recent[key] = now
        if len(self._recent) > 100:
            cutoff = now - 60
            self._recent = {item: ts for item, ts in self._recent.items() if ts >= cutoff}
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._send(text))

    async def _send(self, text: str) -> None:
        if self._sending:
            return
        self._sending = True
        try:
            await self.bot.send_message(
                settings.ADMIN_CHAT_ID,
                "❗️ Ошибка бота\n\n" + text,
            )
        except Exception:
            print("admin log forward failed", file=sys.stderr)
        finally:
            self._sending = False


def setup_logger(bot: Bot) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    has_stream = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root.handlers
    )
    if not has_stream:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)
    try:
        Path("logs").mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler("logs/bot.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass
    root.addHandler(AdminLogHandler(bot))
