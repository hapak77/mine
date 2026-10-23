# -*- coding: utf-8 -*-
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import func, or_, select

from bot.database.models import Payment, User
from bot.services.billing import tariff_label
from bot.services.lives import MSK, ensure_aware


@dataclass
class Stats:
    total: int
    active_7d: int
    paid_users: int
    conversion: float
    revenue_rub: float
    stars: int
    dry_run_payments: int


def render_stats(stats: Stats) -> str:
    return (
        "📊 Статистика\n"
        f"Регистрации: {stats.total}\n"
        f"Активны за 7 дней: {stats.active_7d}\n"
        f"Оплатили (без dry-run): {stats.paid_users}\n"
        f"Конверсия в оплату: {stats.conversion:.1f}%\n"
        f"Выручка ЮKassa: {stats.revenue_rub:.2f} ₽\n"
        f"Оплачено звёздами: {stats.stars} ⭐\n"
        f"Платежи dry-run (не входят в выручку): {stats.dry_run_payments}"
    )


async def collect_stats(session) -> Stats:
    now = datetime.now(timezone.utc)
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    active = await session.scalar(
        select(func.count()).select_from(User).where(User.last_active_at >= now - timedelta(days=7))
    ) or 0
    paid_users = await session.scalar(
        select(func.count(func.distinct(Payment.user_id))).where(
            Payment.status == "succeeded",
            Payment.provider != "dry_run",
        )
    ) or 0
    revenue_kopecks = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "succeeded",
            Payment.provider == "yookassa",
            Payment.currency == "RUB",
        )
    ) or 0
    stars = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "succeeded",
            Payment.provider == "stars",
            Payment.currency == "XTR",
        )
    ) or 0
    dry_run_payments = await session.scalar(
        select(func.count()).select_from(Payment).where(Payment.provider == "dry_run")
    ) or 0
    conversion = (paid_users / total * 100) if total else 0.0
    return Stats(
        total=int(total),
        active_7d=int(active),
        paid_users=int(paid_users),
        conversion=float(conversion),
        revenue_rub=float(revenue_kopecks) / 100,
        stars=int(stars),
        dry_run_payments=int(dry_run_payments),
    )


def _stamp(value: datetime | None) -> str:
    aware = ensure_aware(value)
    if aware is None:
        return ""
    return aware.astimezone(MSK).strftime("%Y-%m-%d %H:%M")


def _money(amount: int, currency: str) -> float | int:
    if currency == "RUB":
        return amount / 100
    return amount


async def build_report(session) -> bytes:
    now = datetime.now(timezone.utc)
    users = list(await session.scalars(select(User).order_by(User.id)))
    payments = list(await session.scalars(select(Payment).order_by(Payment.id)))
    by_id = {user.id: user for user in users}

    book = Workbook()
    sheet = book.active
    sheet.title = "Пользователи"
    headers = [
        "Telegram ID",
        "Username",
        "Имя",
        "Тариф",
        "Жизни",
        "Премиум до",
        "Регистрация",
        "Последняя активность",
        "Реферальный код",
        "Пригласивший",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for user in users:
        inviter = by_id.get(user.referred_by_id) if user.referred_by_id else None
        sheet.append([
            user.telegram_id,
            user.username or "",
            user.full_name,
            tariff_label(user.premium_forever, user.premium_until, now),
            user.lives,
            _stamp(user.premium_until),
            _stamp(user.created_at),
            _stamp(user.last_active_at),
            user.referral_code,
            inviter.telegram_id if inviter else "",
        ])

    pays = book.create_sheet("Платежи")
    pay_headers = [
        "ID",
        "Telegram ID",
        "Провайдер",
        "Тариф",
        "Сумма",
        "Валюта",
        "Статус",
        "Дата",
        "Payload",
    ]
    pays.append(pay_headers)
    for cell in pays[1]:
        cell.font = Font(bold=True)
    for payment in payments:
        owner = by_id.get(payment.user_id)
        pays.append([
            payment.id,
            owner.telegram_id if owner else "",
            payment.provider,
            payment.tariff_code,
            _money(payment.amount, payment.currency),
            payment.currency,
            payment.status,
            _stamp(payment.created_at),
            payment.payload or "",
        ])

    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def free_users_clause(now: datetime):
    return (
        User.premium_forever.is_(False),
        or_(User.premium_until.is_(None), User.premium_until <= now),
    )
