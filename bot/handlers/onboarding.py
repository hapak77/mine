# -*- coding: utf-8 -*-
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, CallbackQuery, ErrorEvent, FSInputFile, Message
from sqlalchemy import select

from bot.config import settings
from bot.database.models import Question, User
from bot.keyboards.common import choice_keyboard, main_menu, qual_start_keyboard
from bot.services.lives import format_lives
from bot.services.training import record_mistake
from bot.services.users import apply_referral
from bot.ui import answer_texts, render_question, respond

logger = logging.getLogger(__name__)
router = Router(name="onboarding")
router.message.filter(F.chat.type == "private")


class QualState(StatesGroup):
    answering = State()


WELCOME = (
    "Привет! Я тренажёр «ПДД без зубрежки».\n"
    "Здесь готовятся к теории ГИБДД (категории A, B, M): пять модулей, "
    "марафон, работа над ошибками и экзамен на 20 минут.\n\n"
    "Бесплатно даётся 5 жизней. Ошибка в тренажёре снимает одну. "
    "На нуле решение блокируется, новая жизнь приходит раз в 3 часа.\n"
    "Ниже — PDF-шпаргалка. После неё можно пройти 3 коротких вопроса: "
    "на ошибке сразу покажу комментарий и лайфхак. Жизни за них не списываются."
)


@router.startup()
async def configure_commands(bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Старт и шпаргалка"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="lives", description="Жизни"),
        BotCommand(command="referral", description="Пригласить друга"),
        BotCommand(command="exam", description="Симулятор экзамена"),
        BotCommand(command="admin", description="Админка"),
    ])


@router.error()
async def on_error(event: ErrorEvent) -> bool:
    logger.error("Сбой в обработчике", exc_info=event.exception)
    update = event.update
    try:
        if update.message is not None:
            await update.message.answer("Не получилось обработать сообщение. Попробуйте ещё раз.")
        elif update.callback_query is not None:
            await update.callback_query.answer("Не получилось обработать действие.", show_alert=True)
    except Exception:
        logger.exception("Не удалось сообщить пользователю об ошибке")
    return True


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session,
    db_user: User,
    user_created: bool,
    state: FSMContext,
) -> None:
    await state.clear()
    if user_created and command.args:
        inviter = await apply_referral(session, db_user, command.args)
        if inviter is not None:
            await message.answer(
                "Вы открыли бота по приглашению. "
                "Другу восстановили жизни до 5 и начислили ещё +5."
            )
            try:
                await message.bot.send_message(
                    inviter.telegram_id,
                    "Друг запустил бота по вашей ссылке.\n"
                    "Жизни восстановлены минимум до 5, и сверху начислено +5.\n"
                    f"Сейчас: {format_lives(inviter.lives)}.",
                )
            except Exception:
                logger.exception("Не удалось уведомить пригласившего")
        else:
            await message.answer("Реферальная ссылка не сработала: друг не найден или это вы сами.")
    elif command.args and not user_created:
        await message.answer("Реферальная ссылка засчитывается только при первом запуске бота.")

    if settings.WELCOME_VIDEO_NOTE_ID:
        try:
            await message.answer_video_note(settings.WELCOME_VIDEO_NOTE_ID)
        except Exception:
            logger.exception("Не удалось отправить приветственный кружок")

    await message.answer(WELCOME)
    await _send_guide(message)

    if not db_user.onboarding_done:
        await message.answer(
            "Входной тест — 3 вопроса. Это ещё не тренажёр, жизни не тратятся.",
            reply_markup=qual_start_keyboard(),
        )
    else:
        await message.answer("Главное меню.", reply_markup=main_menu())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Главное меню.", reply_markup=main_menu())


@router.callback_query(F.data == "nav:menu")
async def nav_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await respond(callback, "Главное меню.", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "qual:start")
async def qual_start(callback: CallbackQuery, session, db_user: User, state: FSMContext) -> None:
    ids = await _qualification_ids(session)
    if not ids:
        db_user.onboarding_done = True
        await respond(callback, "В базе пока нет вопросов. Меню уже доступно.", reply_markup=main_menu())
        await callback.answer()
        return
    await state.set_state(QualState.answering)
    await state.set_data({"ids": ids, "index": 0, "correct": 0})
    question = await session.get(Question, ids[0])
    await respond(
        callback,
        render_question(
            title=f"Входной вопрос 1 из {len(ids)}",
            meta=question.module_title if question else "",
            text=question.text if question else "",
            answers=answer_texts(question) if question else [],
        ),
        reply_markup=choice_keyboard("q", len(answer_texts(question)) if question else 0),
    )
    await callback.answer()


@router.callback_query(QualState.answering, F.data.startswith("q:"))
async def qual_answer(callback: CallbackQuery, session, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    ids: list[int] = data.get("ids") or []
    index = int(data.get("index") or 0)
    correct_count = int(data.get("correct") or 0)
    if index >= len(ids):
        await state.clear()
        await callback.answer("Тест уже закончен")
        return
    question = await session.get(Question, ids[index])
    if question is None:
        await state.clear()
        await callback.answer("Вопрос исчез из базы", show_alert=True)
        return
    choice = int(callback.data.split(":", 1)[1])
    answers = answer_texts(question)
    if choice < 0 or choice >= len(answers):
        await callback.answer("Нет такого варианта")
        return
    if choice == question.correct_index:
        correct_count += 1
        await respond(callback, "✅ Верно.")
    else:
        await record_mistake(session, db_user.id, question.id)
        await _send_fail_note(callback)
        right = answers[question.correct_index] if 0 <= question.correct_index < len(answers) else ""
        lines = ["❌ Неверно."]
        if right:
            lines.append(f"Верный ответ: {right}")
        if question.comment:
            lines.append(question.comment)
        if question.mnemonic:
            lines.append(f"💡 Лайфхак: {question.mnemonic}")
        await respond(callback, "\n".join(lines))
    index += 1
    if index >= len(ids):
        db_user.onboarding_done = True
        await state.clear()
        from bot.handlers.payments import show_paywall

        await show_paywall(
            callback,
            intro=f"Входной тест: {correct_count} из {len(ids)}.\nДальше можно заниматься бесплатно или открыть безлимит.",
        )
        await callback.answer()
        return
    await state.update_data(index=index, correct=correct_count)
    nxt = await session.get(Question, ids[index])
    await respond(
        callback,
        render_question(
            title=f"Входной вопрос {index + 1} из {len(ids)}",
            meta=nxt.module_title if nxt else "",
            text=nxt.text if nxt else "",
            answers=answer_texts(nxt) if nxt else [],
        ),
        reply_markup=choice_keyboard("q", len(answer_texts(nxt)) if nxt else 0),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("q:"))
async def qual_stale(callback: CallbackQuery) -> None:
    await callback.answer("Этот вопрос уже закрыт.", show_alert=True)


async def _qualification_ids(session) -> list[int]:
    primary = list(
        await session.scalars(
            select(Question)
            .where(Question.qualification.is_(True))
            .order_by(Question.position, Question.id)
        )
    )
    ids = [question.id for question in primary[:3]]
    if len(ids) >= 3:
        return ids
    others = await session.scalars(select(Question).order_by(Question.position, Question.id))
    for question in others:
        if question.id not in ids:
            ids.append(question.id)
        if len(ids) >= 3:
            break
    return ids


async def _send_guide(message: Message) -> None:
    path = Path(settings.LEAD_MAGNET_PATH)
    if not path.is_file():
        logger.error("PDF-шпаргалка не найдена: %s", path)
        await message.answer("Не удалось найти PDF-шпаргалку на сервере. Напишите администратору.")
        return
    await message.answer_document(
        FSInputFile(path),
        caption="Шпаргалка к теории. Сохраните файл.",
    )


async def _send_fail_note(callback: CallbackQuery) -> None:
    if not settings.DEMO_FAIL_VIDEO_NOTE_ID:
        return
    try:
        await callback.bot.send_video_note(callback.from_user.id, settings.DEMO_FAIL_VIDEO_NOTE_ID)
    except Exception:
        logger.exception("Не удалось отправить кружок с разбором ошибки")
