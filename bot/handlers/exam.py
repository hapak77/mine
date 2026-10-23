# -*- coding: utf-8 -*-
import random
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.database.models import Question, User
from bot.keyboards.common import BTN_EXAM, choice_keyboard
from bot.services.exam_logic import (
    BASE_MINUTES,
    apply_answer,
    paper_from_state,
    paper_to_state,
    start_paper,
    time_is_over,
)
from bot.services.training import ordered_questions, questions_are_sample, record_mistake
from bot.ui import answer_texts, render_question, respond

router = Router(name="exam")
router.message.filter(F.chat.type == "private")


class ExamState(StatesGroup):
    running = State()


@router.message(F.text == BTN_EXAM)
@router.message(Command("exam"))
async def begin(message: Message, session, state: FSMContext) -> None:
    questions = await ordered_questions(session)
    if not questions:
        await message.answer("База вопросов пуста — экзамен не из чего собрать.")
        return
    paper = start_paper(questions, time.time(), random.Random())
    sample = await questions_are_sample(session)
    await state.set_state(ExamState.running)
    await state.set_data({**paper_to_state(paper), "sample": sample})
    await message.answer(_intro(len(paper.questions), sample))
    await _send_current(message, session, paper)


@router.callback_query(ExamState.running, F.data.startswith("e:"))
async def exam_answer(callback: CallbackQuery, session, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    paper = paper_from_state(data)
    now = time.time()
    if time_is_over(paper.deadline_ts, now):
        await state.clear()
        await respond(callback, "Время вышло. Экзамен не сдан.")
        await callback.answer()
        return
    if paper.cursor >= len(paper.questions):
        await state.clear()
        await callback.answer("Экзамен уже завершён")
        return
    current = paper.questions[paper.cursor]
    question = await session.get(Question, current.id)
    if question is None:
        await state.clear()
        await callback.answer("Вопрос исчез из базы", show_alert=True)
        return
    choice = int(callback.data.split(":", 1)[1])
    answers = answer_texts(question)
    if choice < 0 or choice >= len(answers):
        await callback.answer("Нет такого варианта")
        return
    correct = choice == question.correct_index
    pool = list(await session.scalars(select(Question)))
    paper, added, fail = apply_answer(paper, current.id, correct, pool, random.Random())
    await state.update_data(**paper_to_state(paper))

    if correct:
        await respond(callback, "✅ Верно.")
    else:
        await record_mistake(session, db_user.id, question.id)
        right = answers[question.correct_index] if 0 <= question.correct_index < len(answers) else ""
        lines = ["❌ Неверно."]
        if right:
            lines.append(f"Верный ответ: {right}")
        if question.comment:
            lines.append(question.comment)
        if question.mnemonic:
            lines.append(f"💡 Лайфхак: {question.mnemonic}")
        if added:
            lines.append(
                f"Добавлено {len(added)} вопросов из блока «{question.module_title}» и 5 минут."
            )
        elif fail is None and not current.extra:
            lines.append("В этом блоке не осталось вопросов для дополнительной пятёрки.")
        await respond(callback, "\n".join(lines))

    if fail == "block":
        await state.clear()
        await respond(callback, "Вторая ошибка в одном блоке. Экзамен не сдан.")
        await callback.answer()
        return
    if fail == "extra":
        await state.clear()
        await respond(callback, "Ошибка в дополнительном вопросе. Экзамен не сдан.")
        await callback.answer()
        return
    if paper.finished:
        await state.clear()
        base_errors = sum(paper.base_errors.values())
        extras = len(paper.questions) - paper.base_count
        await respond(
            callback,
            "Экзамен сдан.\n"
            f"Ошибок в основных вопросах: {base_errors}.\n"
            f"Дополнительных вопросов: {extras}.",
        )
        await callback.answer()
        return
    await _send_current(callback, session, paper)
    await callback.answer()


@router.callback_query(F.data.startswith("e:"))
async def stale(callback: CallbackQuery) -> None:
    await callback.answer("Экзамен уже закрыт.", show_alert=True)


def _intro(count: int, sample: bool) -> str:
    text = (
        f"Симулятор экзамена: {count} вопросов, {BASE_MINUTES} минут.\n"
        "Ошибка в основном вопросе добавляет до 5 вопросов из того же блока и 5 минут.\n"
        "Вторая ошибка в том же блоке или ошибка в дополнительном вопросе — экзамен не сдан.\n"
        "Жизни за экзамен не списываются."
    )
    if count < 20:
        text += "\nВ базе меньше 20 вопросов, поэтому билет короче."
    if sample:
        text = "Сейчас в базе учебная выборка, не официальные билеты.\n" + text
    return text


async def _send_current(event, session, paper) -> None:
    current = paper.questions[paper.cursor]
    question = await session.get(Question, current.id)
    if question is None:
        await respond(event, "Вопрос не найден.")
        return
    remaining = max(0, int(paper.deadline_ts - time.time()))
    minutes, seconds = divmod(remaining, 60)
    kind = "Дополнительный вопрос" if current.extra else "Вопрос"
    title = f"{kind} {paper.cursor + 1} из {len(paper.questions)}"
    meta = f"{question.module_title} · осталось {minutes:02d}:{seconds:02d}"
    await respond(
        event,
        render_question(title=title, meta=meta, text=question.text, answers=answer_texts(question)),
        reply_markup=choice_keyboard("e", len(answer_texts(question))),
    )
