# -*- coding: utf-8 -*-
"""Тарифы и проведение оплаты.

Боевой PAYMENT_PROVIDER_TOKEN уходит в Telegram Payments (ЮKassa).
Токен-заглушка из .env.example и пустой токен включают dry-run:
доступ записывается без счёта и без списания. Звёзды в dry-run тоже
не создают invoice в валюте XTR.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.config import PLACEHOLDER_PROVIDER_TOKEN
from bot.services.lives import ensure_aware


@dataclass(frozen=True)
class Tariff:
    code: str
    title: str
    description: str
    amount_rub_kopecks: int
    stars: int
    days: int | None


TARIFFS: dict[str, Tariff] = {
    "month": Tariff(
        code="month",
        title="Безлимит на 30 дней",
        description="Все режимы без ограничения жизней на 30 дней.",
        amount_rub_kopecks=49000,
        stars=250,
        days=30,
    ),
    "forever": Tariff(
        code="forever",
        title="Курс навсегда",
        description="Постоянный безлимит жизней во всех режимах.",
        amount_rub_kopecks=149000,
        stars=750,
        days=None,
    ),
}


def is_dry_run(provider_token: str | None) -> bool:
    token = (provider_token or "").strip()
    return token == "" or token == PLACEHOLDER_PROVIDER_TOKEN


def rubles(kopecks: int) -> str:
    return f"{kopecks // 100} ₽"


def apply_tariff(
    premium_forever: bool,
    premium_until: datetime | None,
    tariff: Tariff,
    now: datetime,
) -> tuple[bool, datetime | None]:
    """Вернуть (premium_forever, premium_until) после успешной оплаты."""
    if tariff.days is None:
        return True, None
    if premium_forever:
        return True, None
    current = ensure_aware(now) or now
    until = ensure_aware(premium_until)
    base = until if until and until > current else current
    return False, base + timedelta(days=tariff.days)


def tariff_label(premium_forever: bool, premium_until: datetime | None, now: datetime) -> str:
    if premium_forever:
        return "Курс навсегда"
    until = ensure_aware(premium_until)
    current = ensure_aware(now)
    if until and current and until > current:
        return "Безлимит до " + until.strftime("%d.%m.%Y")
    return "Бесплатный"
