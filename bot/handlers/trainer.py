# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.database.models import Question, User
from bot.keyboards.common import (
    BTN_MARATHON,
    BTN_MISTAKES,
    BTN_TOPICS,
    blocked_keyboard,
    choice_keyboard,
    modules_keyboard,
    restart_keyboard,
)
from bot.services.lives import apply_trainer_mistake, can_train, format_lives, format_msk, is_unlimited, next_life_at
from bot.services.training import (
    get_progress,
    list_modules,
    mistake_questions,
    ordered_questions,
    questions_are_sample,
    record_mistake,
    resolve_mistake,
    set_progress,
)
from bot.ui import answer_texts, render_question, respond

logger = logging.getLogger(__name__)
router = Router(name="trainer")
router.message.filter(F.chat.type == "private")


class TrainState(StatesGroup):
    solving = State()


@router.message(F.text == BTN_TOPICS)
async def topics(message: Message, session, state: FSMContext) -> None:
    await state.clear()
    modules = await list_modules(session)
    if not modules:
        await message.answer("В базе пока нет вопросов.")
        return
    note = await _sample_note(session)
    text = "Выберите модуль."
    if note:
        text = note + "\n\n" + text
    await message.answer(text, reply_markup=modules_keyboard(modules))


@router.callback_query(F.data.startswith("mod:"))
async def start_module(callback: CallbackQuery, session, db_user: User, state: FSMContext) -> None:
    module = int(callback.data.split(":", 1)[1])
    await _begin(
        callback,
        session,
        db_user,
        state,
        mode="module",
        module=module,
        progress_key=f"module:{module}",
    )
    await callback.answer()


@router.message(F.text == BTN_MARATHON)
async def marathon(message: Message, session, db_user: User, state: FSMContext) -> None:
    await _begin(message, session, db_user, state, mode="marathon", module=None, progress_key="marathon")


@router.message(F.text == BTN_MISTAKES)
async def mistakes(message: Message, session, db_user: User, state: FSMContext) -> None:
    if not await _ensure_can_train(message, db_user):
        return
    questions = await mistake_questions(session, db_user.id)
    if not questions:
        await message.answer("Пока ошибок нет — пул пуст. Решайте модули или марафон.")
        return
    sample = await questions_are_sample(session)
    await state.set_state(TrainState.solving)
    await state.set_data({
        "mode": "mistakes",
        "module": None,
        "progress_key": "",
        "ids": [question.id for question in questions],
        "index": 0,
        "sample": sample,
    })
    note = "Это вопросы, на которых вы уже ошибались."
    if await questions_are_sample(session):
        note = "Сейчас в базе учебная выборка, не официальные билеты.\n" + note
    await message.answer(note)
    await _send_current(message, session, db_user, await state.get_data())


@router.callback_query(F.data.startswith("re:"))
async def restart(callback: CallbackQuery, session, db_user: User, state: FSMContext) -> None:
    kind = callback.data.split(":", 1)[1]
    if kind == "marathon":
        await set_progress(session, db_user.id, "marathon", 0)
        await _begin(callback, session, db_user, state, mode="marathon", module=None, progress_key="marathon")
    elif kind.startswith("module:"):
        module = int(kind.split(":", 1)[1])
        await set_progress(session, db_user.id, f"module:{module}", 0)
        await _begin(callback, session, db_user, state, mode="module", module=module, progress_key=f"module:{module}")
    await callback.answer()


@router.callback_query(TrainState.solving, F.data.startswith("t:"))
async def answer(callback: CallbackQuery, session, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    ids: list[int] = list(data.get("ids") or [])
    index = int(data.get("index") or 0)
    if index >= len(ids):
        await state.clear()
        await callback.answer("Сессия уже закрыта")
        return
    question = await session.get(Question, ids[index])
    if question is None:
        await state.clear()
        await callback.answer("Вопрос больше не в базе", show_alert=True)
        return
    choice = int(callback.data.split(":", 1)[1])
    answers = answer_texts(question)
    if choice < 0 or choice >= len(answers):
        await callback.answer("Нет такого варианта")
        return

    now = datetime.now(timezone.utc)
    unlimited = is_unlimited(db_user.premium_forever, db_user.premium_until, now)
    if choice == question.correct_index:
        await resolve_mistake(session, db_user.id, question.id)
        await respond(callback, "✅ Верно.")
        blocked = False
    else:
        await record_mistake(session, db_user.id, question.id)
        lives, anchor, blocked = apply_trainer_mistake(
            db_user.lives,
            db_user.regen_anchor,
            now,
            unlimited=unlimited,
        )
        db_user.lives = lives
        db_user.regen_anchor = anchor
        await _fail_note(callback)
        right = answers[question.correct_index] if 0 <= question.correct_index < len(answers) else ""
        lines = ["❌ Неверно."]
        if right:
            lines.append(f"Верный ответ: {right}")
        if question.comment:
            lines.append(question.comment)
        if question.mnemonic:
            lines.append(f"💡 Лайфхак: {question.mnemonic}")
        if not unlimited:
            lines.append(f"Жизни: {format_lives(db_user.lives)}")
        await respond(callback, "\n".join(lines))

    index += 1
    progress_key = data.get("progress_key") or ""
    if progress_key:
        await set_progress(session, db_user.id, progress_key, index)
    data["index"] = index
    await state.update_data(index=index)

    if blocked:
        await state.clear()
        await respond(callback, _blocked_text(db_user, now), reply_markup=blocked_keyboard())
        await callback.answer()
        return
    if index >= len(ids):
        await state.clear()
        keyboard = _done_keyboard(data)
        if keyboard is None:
            await respond(callback, _done_text(data))
        else:
            await respond(callback, _done_text(data), reply_markup=keyboard)
        await callback.answer()
        return
    await _send_current(callback, session, db_user, data)
    await callback.answer()


@router.callback_query(F.data.startswith("t:"))
async def stale(callback: CallbackQuery) -> None:
    await callback.answer("Этот вопрос уже закрыт. Откройте режим заново.", show_alert=True)


async def _begin(event, session, db_user: User, state: FSMContext, *, mode: str, module: int | None, progress_key: str) -> None:
    if not await _ensure_can_train(event, db_user):
        return
    questions = await ordered_questions(session, module)
    if not questions:
        await respond(event, "В этом разделе пока нет вопросов.")
        return
    saved = await get_progress(session, db_user.id, progress_key)
    if saved >= len(questions):
        title = questions[0].module_title if mode == "module" else "Марафон"
        await respond(
            event,
            f"{title} уже пройден.",
            reply_markup=restart_keyboard("marathon" if mode == "marathon" else f"module:{module}"),
        )
        return
    ids = [question.id for question in questions]
    sample = await questions_are_sample(session)
    await state.set_state(TrainState.solving)
    await state.set_data({
        "mode": mode,
        "module": module,
        "progress_key": progress_key,
        "ids": ids,
        "index": saved,
        "sample": sample,
    })
    intro = _intro(mode, questions[0].module_title if mode == "module" else "", saved, len(ids), sample)
    await respond(event, intro)
    await _send_current(event, session, db_user, await state.get_data())


async def _send_current(event, session, db_user: User, data: dict) -> None:
    ids: list[int] = list(data.get("ids") or [])
    index = int(data.get("index") or 0)
    question = await session.get(Question, ids[index])
    if question is None:
        await respond(event, "Вопрос не найден.")
        return
    now = datetime.now(timezone.utc)
    unlimited = is_unlimited(db_user.premium_forever, db_user.premium_until, now)
    meta = f"{question.module_title} · {format_lives(db_user.lives, unlimited=unlimited)} · {index + 1} из {len(ids)}"
    await respond(
        event,
        render_question(
            title=_mode_title(data),
            meta=meta,
            text=question.text,
            answers=answer_texts(question),
        ),
        reply_markup=choice_keyboard("t", len(answer_texts(question))),
    )


async def _ensure_can_train(event, db_user: User) -> bool:
    now = datetime.now(timezone.utc)
    unlimited = is_unlimited(db_user.premium_forever, db_user.premium_until, now)
    if can_train(db_user.lives, unlimited=unlimited):
        return True
    await respond(event, _blocked_text(db_user, now), reply_markup=blocked_keyboard())
    return False


def _blocked_text(db_user: User, now: datetime) -> str:
    nxt = next_life_at(db_user.lives, db_user.regen_anchor)
    when = format_msk(nxt) if nxt else "скоро"
    return (
        "Жизни закончились, тренажёр на паузе.\n"
        f"Следующая жизнь: {when} (+1 каждые 3 часа, бот пришлёт сообщение).\n"
        "Либо отправьте ссылку другу: когда он впервые откроет бота, "
        "жизни восстановятся до 5 и вам начислят ещё +5."
    )


def _intro(mode: str, module_title: str, index: int, total: int, sample: bool) -> str:
    if mode == "marathon":
        text = f"Марафон: вопрос {index + 1} из {total}. Прогресс сохранится."
    else:
        text = f"Модуль «{module_title}»: вопрос {index + 1} из {total}."
    if sample:
        text = "Сейчас в базе учебная выборка, не официальные билеты.\n" + text
    return text


def _mode_title(data: dict) -> str:
    mode = data.get("mode")
    if mode == "mistakes":
        return "Работа над ошибками"
    if mode == "marathon":
        return "Марафон"
    return "По темам"


def _done_text(data: dict) -> str:
    mode = data.get("mode")
    if mode == "mistakes":
        return "В этом проходе ошибки разобраны. Если что-то снова решите неверно, вопрос вернётся в пул."
    if mode == "marathon":
        return "Марафон пройден. Можно начать сначала — прогресс обнулится."
    return "Модуль пройден. Можно пройти его ещё раз."


def _done_keyboard(data: dict):
    mode = data.get("mode")
    if mode == "marathon":
        return restart_keyboard("marathon")
    if mode == "module":
        return restart_keyboard(f"module:{data.get('module')}")
    return None


async def _sample_note(session) -> str:
    if await questions_are_sample(session):
        return "Сейчас в базе учебная выборка, не официальные билеты."
    return ""


async def _fail_note(callback: CallbackQuery) -> None:
    if not settings.DEMO_FAIL_VIDEO_NOTE_ID:
        return
    try:
        await callback.bot.send_video_note(callback.from_user.id, settings.DEMO_FAIL_VIDEO_NOTE_ID)
    except Exception:
        logger.exception("Не удалось отправить кружок с разбором ошибки")
