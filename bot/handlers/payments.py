# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy import select

from bot.config import settings
from bot.database.models import Payment, User
from bot.keyboards.common import BTN_TARIFFS, main_menu, paywall_keyboard
from bot.services.billing import TARIFFS, apply_tariff, is_dry_run, rubles, tariff_label

logger = logging.getLogger(__name__)
router = Router(name="payments")
router.message.filter(F.chat.type == "private")


def paywall_text(intro: str | None = None) -> str:
    lines = []
    if intro:
        lines.append(intro)
        lines.append("")
    lines.append("Тарифы безлимита. На платном доступе жизни в тренажёре не тратятся.")
    for tariff in TARIFFS.values():
        period = "навсегда" if tariff.days is None else f"{tariff.days} дней"
        lines.append(
            f"• {tariff.title} ({period}) — {rubles(tariff.amount_rub_kopecks)}"
            + (f" или {tariff.stars} ⭐" if settings.ENABLE_TELEGRAM_STARS else "")
        )
    if is_dry_run(settings.PAYMENT_PROVIDER_TOKEN):
        lines.append("")
        lines.append(
            "Сейчас включён dry-run: кнопки ниже записывают тариф без счёта и без списания. "
            "Боевой PAYMENT_PROVIDER_TOKEN включает ЮKassa, звёзды уйдут настоящим invoice."
        )
    return "\n".join(lines)


async def show_paywall(event: Message | CallbackQuery, *, intro: str | None = None) -> None:
    await _reply(event, paywall_text(intro), reply_markup=paywall_keyboard())


@router.message(F.text == BTN_TARIFFS)
async def tariffs_button(message: Message) -> None:
    await show_paywall(message)


@router.callback_query(F.data == "pay:open")
async def pay_open(callback: CallbackQuery) -> None:
    await show_paywall(callback)
    await callback.answer()


@router.callback_query(F.data == "pay:later")
async def pay_later(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _reply(
        callback,
        "Бесплатный режим: 5 жизней. Ошибка в тренажёре снимает одну.",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dry:"))
async def dry_card(callback: CallbackQuery, session, db_user: User) -> None:
    await _dry(callback, session, db_user, currency="RUB")


@router.callback_query(F.data.startswith("drystar:"))
async def dry_stars(callback: CallbackQuery, session, db_user: User) -> None:
    if not settings.ENABLE_TELEGRAM_STARS:
        await callback.answer("Оплата звёздами выключена", show_alert=True)
        return
    await _dry(callback, session, db_user, currency="XTR")


@router.callback_query(F.data.startswith("card:"))
async def pay_card(callback: CallbackQuery) -> None:
    if is_dry_run(settings.PAYMENT_PROVIDER_TOKEN):
        await callback.answer("Сейчас dry-run, счёт ЮKassa не создаётся", show_alert=True)
        return
    tariff = _tariff_from(callback.data)
    if tariff is None:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=tariff.title[:32],
        description=tariff.description[:255],
        payload=f"tariff:{tariff.code}",
        provider_token=settings.PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label=tariff.title[:32], amount=tariff.amount_rub_kopecks)],
    )
    await callback.answer()


@router.callback_query(F.data.startswith("star:"))
async def pay_stars(callback: CallbackQuery) -> None:
    if not settings.ENABLE_TELEGRAM_STARS:
        await callback.answer("Оплата звёздами выключена", show_alert=True)
        return
    if is_dry_run(settings.PAYMENT_PROVIDER_TOKEN):
        await callback.answer("Сейчас dry-run, звёзды не списываются", show_alert=True)
        return
    tariff = _tariff_from(callback.data)
    if tariff is None:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=tariff.title[:32],
        description=tariff.description[:255],
        payload=f"tariff:{tariff.code}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=tariff.title[:32], amount=tariff.stars)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    code = (query.invoice_payload or "").removeprefix("tariff:")
    if code not in TARIFFS:
        await query.answer(ok=False, error_message="Неизвестный тариф")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, session, db_user: User) -> None:
    payment = message.successful_payment
    if payment is None:
        return
    code = (payment.invoice_payload or "").removeprefix("tariff:")
    tariff = TARIFFS.get(code)
    if tariff is None:
        await message.answer("Платёж пришёл с неизвестным тарифом. Напишите администратору.")
        return
    charge_id = payment.telegram_payment_charge_id
    if charge_id:
        existing = await session.scalar(select(Payment).where(Payment.telegram_charge_id == charge_id))
        if existing is not None:
            await message.answer("Этот платёж уже учтён.")
            return
    provider = "stars" if payment.currency == "XTR" else "yookassa"
    await _grant(
        session,
        db_user,
        tariff,
        provider=provider,
        amount=payment.total_amount,
        currency=payment.currency,
        charge_id=charge_id,
        provider_charge_id=payment.provider_payment_charge_id,
    )
    now = datetime.now(timezone.utc)
    await message.answer(
        "Оплата прошла. Тариф: "
        + tariff_label(db_user.premium_forever, db_user.premium_until, now)
        + ".",
        reply_markup=main_menu(),
    )


@router.message(Command("paysupport"))
async def paysupport(message: Message) -> None:
    await message.answer(
        "По вопросам оплаты напишите администратору бота. "
        "В режиме dry-run деньги и звёзды не списываются."
    )


async def _dry(callback: CallbackQuery, session, db_user: User, *, currency: str) -> None:
    if not is_dry_run(settings.PAYMENT_PROVIDER_TOKEN):
        await callback.answer("Dry-run выключен: используйте боевую оплату", show_alert=True)
        return
    tariff = _tariff_from(callback.data)
    if tariff is None:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return
    amount = tariff.stars if currency == "XTR" else tariff.amount_rub_kopecks
    await _grant(
        session,
        db_user,
        tariff,
        provider="dry_run",
        amount=amount,
        currency=currency,
        charge_id=None,
        provider_charge_id=None,
    )
    now = datetime.now(timezone.utc)
    await _reply(
        callback,
        "Dry-run: списания не было, тариф записан.\n"
        + tariff_label(db_user.premium_forever, db_user.premium_until, now),
        reply_markup=main_menu(),
    )
    await callback.answer("Dry-run: оплата не проводилась")


def _tariff_from(data: str):
    code = data.split(":", 1)[1]
    return TARIFFS.get(code)


async def _grant(session, user: User, tariff, *, provider: str, amount: int, currency: str, charge_id, provider_charge_id) -> None:
    now = datetime.now(timezone.utc)
    user.premium_forever, user.premium_until = apply_tariff(
        user.premium_forever,
        user.premium_until,
        tariff,
        now,
    )
    session.add(
        Payment(
            user_id=user.id,
            provider=provider,
            tariff_code=tariff.code,
            amount=amount,
            currency=currency,
            status="succeeded",
            telegram_charge_id=charge_id,
            provider_charge_id=provider_charge_id,
            payload=f"tariff:{tariff.code}",
            created_at=now,
        )
    )
    await session.flush()


async def _reply(event: Message | CallbackQuery, text: str, **kwargs):
    if isinstance(event, Message):
        return await event.answer(text, **kwargs)
    if isinstance(event.message, Message):
        return await event.message.answer(text, **kwargs)
    return await event.bot.send_message(event.from_user.id, text, **kwargs)
