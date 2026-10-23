# -*- coding: utf-8 -*-
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.database.models import Referral, User
from bot.services.lives import (
    FULL_LIVES,
    REFERRAL_BONUS,
    accrue_lives,
    apply_referral_reward,
    is_unlimited,
)


async def get_or_create_user(session, tg_user) -> tuple[User, bool, int]:
    """Вернуть (пользователь, только что создан, сколько жизней начислено таймером)."""
    now = datetime.now(timezone.utc)
    user = await session.scalar(
        select(User).where(User.telegram_id == tg_user.id).with_for_update()
    )
    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name or "",
            lives=FULL_LIVES,
            regen_anchor=None,
            premium_forever=False,
            premium_until=None,
            onboarding_done=False,
            referral_code=f"u{tg_user.id}",
            created_at=now,
            last_active_at=now,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            user = await session.scalar(
                select(User).where(User.telegram_id == tg_user.id).with_for_update()
            )
            if user is None:
                raise
            return user, False, 0
        return user, True, 0

    user.username = tg_user.username
    user.full_name = tg_user.full_name or user.full_name
    user.last_active_at = now
    if is_unlimited(user.premium_forever, user.premium_until, now):
        return user, False, 0
    lives, anchor, gained = accrue_lives(user.lives, user.regen_anchor, now)
    user.lives = lives
    user.regen_anchor = anchor
    return user, False, gained


async def apply_referral(session, invitee: User, args: str | None) -> User | None:
    """Если новый пользователь пришёл по ссылке, наградить пригласившего.

    Пригласившему восстанавливают запас до 5 и сверху дают +5.
    Сам переход награды не даёт: друг должен впервые открыть бота.
    """
    if not args or not args.startswith("u"):
        return None
    tail = args[1:]
    if not tail.isdigit():
        return None
    inviter_tid = int(tail)
    if inviter_tid == invitee.telegram_id or invitee.referred_by_id is not None:
        return None
    already = await session.scalar(select(Referral).where(Referral.invitee_id == invitee.id))
    if already is not None:
        return None
    inviter = await session.scalar(
        select(User).where(User.telegram_id == inviter_tid).with_for_update()
    )
    if inviter is None or inviter.id == invitee.id:
        return None
    inviter.lives, inviter.regen_anchor = apply_referral_reward(inviter.lives, inviter.regen_anchor)
    invitee.referred_by_id = inviter.id
    session.add(
        Referral(
            inviter_id=inviter.id,
            invitee_id=invitee.id,
            lives_granted=REFERRAL_BONUS,
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()
    return inviter
