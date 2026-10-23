# -*- coding: utf-8 -*-
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.services.lives import regen_push_text
from bot.services.users import get_or_create_user

logger = logging.getLogger(__name__)


class UserTrackerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        chat = data.get("event_chat")
        if tg_user is None or getattr(tg_user, "is_bot", False):
            return await handler(event, data)
        if chat is not None and getattr(chat, "type", "private") != "private":
            return await handler(event, data)
        session = data.get("session")
        if session is None:
            return await handler(event, data)

        user, created, gained = await get_or_create_user(session, tg_user)
        data["db_user"] = user
        data["user_created"] = created
        if gained > 0 and data.get("bot") is not None:
            try:
                await data["bot"].send_message(user.telegram_id, regen_push_text(gained, user.lives))
            except Exception:
                logger.exception("Не удалось отправить пуш о восстановлении жизни")
        return await handler(event, data)
