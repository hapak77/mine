# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timezone

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from bot.database.engine import SessionLocal
from bot.database.models import User
from bot.services.lives import FULL_LIVES, accrue_lives, is_unlimited, regen_push_text

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler(bot: Bot) -> None:
    global _scheduler
    if _scheduler is not None:
        return
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _restore_lives,
        "interval",
        minutes=1,
        args=[bot],
        id="lives-regen",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Lives scheduler started (every minute, +1 life per 3 hours).")


async def _restore_lives(bot: Bot) -> None:
    now = datetime.now(timezone.utc)
    pending: list[tuple[int, str]] = []
    async with SessionLocal() as session:
        user_ids = list(
            await session.scalars(
                select(User.id).where(
                    User.lives < FULL_LIVES,
                    User.regen_anchor.is_not(None),
                    User.premium_forever.is_(False),
                )
            )
        )
        for user_id in user_ids:
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None or is_unlimited(user.premium_forever, user.premium_until, now):
                continue
            lives, anchor, gained = accrue_lives(user.lives, user.regen_anchor, now)
            if gained <= 0:
                continue
            user.lives = lives
            user.regen_anchor = anchor
            pending.append((user.telegram_id, regen_push_text(gained, user.lives)))
        await session.commit()

    for telegram_id, text in pending:
        try:
            await bot.send_message(telegram_id, text)
        except Exception:
            logger.exception("Не удалось отправить пуш о жизни пользователю %s", telegram_id)
