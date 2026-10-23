# -*- coding: utf-8 -*-
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import func, select

from bot.config import settings
from bot.database.models import User
from bot.keyboards.common import admin_keyboard, audience_keyboard, confirm_keyboard, url_keyboard
from bot.services.analytics import build_report, collect_stats, free_users_clause, render_stats

logger = logging.getLogger(__name__)
router = Router(name="admin")
router.message.filter(F.chat.type == "private")


class CastState(StatesGroup):
    waiting_message = State()
    waiting_buttons = State()
    confirm = State()


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_ids


@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return
    await state.clear()
    await message.answer("Админка.", reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin:stats")
async def stats(callback: CallbackQuery, session) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await callback.message.answer(render_stats(await collect_stats(session)))
    await callback.answer()


@router.callback_query(F.data == "admin:excel")
async def excel(callback: CallbackQuery, session) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    payload = await build_report(session)
    await callback.message.answer_document(
        BufferedInputFile(payload, filename="pdd-analytics.xlsx"),
        caption="Пользователи, тарифы, жизни и платежи.",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:cast")
async def cast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await callback.message.answer("Кому отправить рассылку?", reply_markup=audience_keyboard())
    await callback.answer()


@router.callback_query(F.data.in_({"cast:all", "cast:free"}))
async def cast_audience(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    audience = "free" if callback.data == "cast:free" else "all"
    await state.set_state(CastState.waiting_message)
    await state.update_data(audience=audience)
    label = "только бесплатным" if audience == "free" else "всем"
    await callback.message.answer(f"Пришлите сообщение для рассылки ({label}). Его скопирую как есть.")
    await callback.answer()


@router.message(CastState.waiting_message)
async def cast_message(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.update_data(chat_id=message.chat.id, message_id=message.message_id)
    await state.set_state(CastState.waiting_buttons)
    await message.answer(
        "Кнопки к рассылке: каждая строка в виде «Текст | https://ссылка».\n"
        "Или /skip, если кнопки не нужны."
    )


@router.message(CastState.waiting_buttons, Command("skip"))
@router.message(CastState.waiting_buttons, F.text.lower() == "пропустить")
async def cast_skip_buttons(message: Message, session, state: FSMContext) -> None:
    await _ask_confirm(message, session, state, [])


@router.message(CastState.waiting_buttons)
async def cast_buttons(message: Message, session, state: FSMContext) -> None:
    pairs, error = _parse_buttons(message.text or "")
    if error:
        await message.answer(error)
        return
    await _ask_confirm(message, session, state, pairs)


@router.callback_query(F.data == "cast:cancel")
async def cast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await callback.message.answer("Рассылка отменена.", reply_markup=admin_keyboard())
    await callback.answer()


@router.callback_query(CastState.confirm, F.data == "cast:go")
async def cast_go(callback: CallbackQuery, session, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    audience = data.get("audience") or "all"
    pairs = [tuple(item) for item in data.get("buttons") or []]
    markup = url_keyboard(pairs)
    user_ids = await _audience_ids(session, audience)
    await callback.message.answer(f"Отправляю {len(user_ids)} пользователям…")
    await callback.answer()
    sent = 0
    failed = 0
    for telegram_id in user_ids:
        try:
            await callback.bot.copy_message(
                chat_id=telegram_id,
                from_chat_id=data["chat_id"],
                message_id=data["message_id"],
                reply_markup=markup,
            )
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await callback.bot.copy_message(
                    chat_id=telegram_id,
                    from_chat_id=data["chat_id"],
                    message_id=data["message_id"],
                    reply_markup=markup,
                )
                sent += 1
            except Exception:
                failed += 1
                logger.exception("Повтор рассылки не удался для %s", telegram_id)
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1
            logger.exception("Рассылка не ушла пользователю %s", telegram_id)
        await asyncio.sleep(0.05)
    await callback.message.answer(
        f"Рассылка закончена. Доставлено: {sent}. Не доставлено: {failed}.",
        reply_markup=admin_keyboard(),
    )


async def _ask_confirm(message: Message, session, state: FSMContext, pairs: list[tuple[str, str]]) -> None:
    data = await state.get_data()
    audience = data.get("audience") or "all"
    count = await _audience_count(session, audience)
    await state.update_data(buttons=pairs)
    await state.set_state(CastState.confirm)
    label = "только бесплатным" if audience == "free" else "всем"
    extra = f"Кнопок: {len(pairs)}." if pairs else "Без кнопок."
    await message.answer(
        f"Отправить {label}? Получателей: {count}. {extra}",
        reply_markup=confirm_keyboard(),
    )


async def _audience_ids(session, audience: str) -> list[int]:
    statement = select(User.telegram_id)
    if audience == "free":
        statement = statement.where(*free_users_clause(datetime.now(timezone.utc)))
    return list(await session.scalars(statement))


async def _audience_count(session, audience: str) -> int:
    statement = select(func.count()).select_from(User)
    if audience == "free":
        statement = statement.where(*free_users_clause(datetime.now(timezone.utc)))
    return int(await session.scalar(statement) or 0)


def _parse_buttons(text: str) -> tuple[list[tuple[str, str]], str | None]:
    pairs: list[tuple[str, str]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if "|" not in line:
            return [], f"Строка {line_no}: нужна пара «Текст | https://ссылка»."
        label, url = line.split("|", 1)
        label, url = label.strip(), url.strip()
        if not label or not url.startswith(("https://", "http://", "tg://")):
            return [], f"Строка {line_no}: подпись пустая или ссылка не похожа на URL."
        pairs.append((label, url))
    if not pairs:
        return [], "Не вижу ни одной кнопки. Пришлите строки или /skip."
    return pairs, None
