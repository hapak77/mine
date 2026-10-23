# -*- coding: utf-8 -*-
"""Жизни бесплатного пользователя.

5 сердец, −1 за ошибку в тренажёре, блок на нуле, +1 каждые 3 часа.
Успешный реферал восстанавливает запас до 5 и добавляет пригласившему ещё +5.
"""

from datetime import datetime, timedelta, timezone

FULL_LIVES = 5
REGEN_STEP = timedelta(hours=3)
REFERRAL_BONUS = 5
MSK = timezone(timedelta(hours=3))


def ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def lives_word(count: int) -> str:
    number = abs(int(count)) % 100
    if 11 <= number <= 14:
        return "жизней"
    last = number % 10
    if last == 1:
        return "жизнь"
    if 2 <= last <= 4:
        return "жизни"
    return "жизней"


def format_lives(lives: int, *, unlimited: bool = False) -> str:
    if unlimited:
        return "♾ безлимит"
    if lives <= 0:
        return "💔 0"
    if lives <= FULL_LIVES:
        return "❤️" * lives
    return "❤️" * FULL_LIVES + f" +{lives - FULL_LIVES}"


def format_msk(value: datetime | None) -> str:
    aware = ensure_aware(value)
    if aware is None:
        return ""
    return aware.astimezone(MSK).strftime("%d.%m %H:%M МСК")


def is_unlimited(
    premium_forever: bool,
    premium_until: datetime | None,
    now: datetime,
) -> bool:
    if premium_forever:
        return True
    until = ensure_aware(premium_until)
    current = ensure_aware(now)
    return until is not None and current is not None and until > current


def can_train(lives: int, *, unlimited: bool) -> bool:
    return unlimited or lives > 0


def apply_trainer_mistake(
    lives: int,
    anchor: datetime | None,
    now: datetime,
    *,
    unlimited: bool,
) -> tuple[int, datetime | None, bool]:
    """Списать жизнь за ошибку в тренажёре.

    Возвращает (жизни, якорь регенерации, заблокирован ли тренажёр).
    Платный безлимит жизни не тратит. На нуле баланс не уходит в минус.
    Таймер 3 часов запускается в момент падения ниже 5 и не сбрасывается
    повторными ошибками.
    """
    if unlimited:
        return lives, anchor, False
    if lives <= 0:
        return 0, anchor, True
    lives -= 1
    if lives >= FULL_LIVES:
        anchor = None
    elif anchor is None:
        anchor = now
    return lives, anchor, lives <= 0


def accrue_lives(
    lives: int,
    anchor: datetime | None,
    now: datetime,
) -> tuple[int, datetime | None, int]:
    """Начислить +1 жизнь за каждые полные 3 часа, но не выше 5.

    Бонусные жизни сверх пятёрки (реферал) таймер не увеличивает и не сжигает.
    """
    anchor = ensure_aware(anchor)
    current = ensure_aware(now)
    if lives >= FULL_LIVES or anchor is None or current is None:
        if lives >= FULL_LIVES:
            return lives, None, 0
        return lives, anchor, 0
    elapsed = (current - anchor).total_seconds()
    if elapsed < 0:
        return lives, anchor, 0
    steps = int(elapsed // REGEN_STEP.total_seconds())
    if steps <= 0:
        return lives, anchor, 0
    gain = min(steps, FULL_LIVES - lives)
    lives += gain
    if lives >= FULL_LIVES:
        return lives, None, gain
    return lives, anchor + REGEN_STEP * gain, gain


def next_life_at(lives: int, anchor: datetime | None) -> datetime | None:
    anchor = ensure_aware(anchor)
    if lives >= FULL_LIVES or anchor is None:
        return None
    return anchor + REGEN_STEP


def reward_inviter(lives: int) -> int:
    """Восстановить все 5 жизней и начислить +5 за приглашённого друга."""
    return max(lives, FULL_LIVES) + REFERRAL_BONUS


def apply_referral_reward(
    lives: int,
    anchor: datetime | None,
) -> tuple[int, datetime | None]:
    lives = reward_inviter(lives)
    if lives >= FULL_LIVES:
        anchor = None
    return lives, anchor


def regen_push_text(gained: int, lives: int) -> str:
    return (
        f"❤️ Восстановлено {gained} {lives_word(gained)}. "
        f"Сейчас: {format_lives(lives)}. Можно снова решать вопросы."
    )
