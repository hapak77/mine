# -*- coding: utf-8 -*-
from datetime import datetime, timezone

from sqlalchemy import func, select

from bot.database.models import Question, SeedState, TrainingProgress, UserMistake


async def ordered_questions(session, module: int | None = None) -> list[Question]:
    statement = select(Question).order_by(Question.position, Question.id)
    if module is not None:
        statement = statement.where(Question.module == module)
    return list(await session.scalars(statement))


async def list_modules(session) -> list[tuple[int, str]]:
    rows = await session.execute(
        select(Question.module, func.min(Question.module_title))
        .group_by(Question.module)
        .order_by(Question.module)
    )
    return [(int(module), title or str(module)) for module, title in rows.all()]


async def get_progress(session, user_id: int, mode: str) -> int:
    row = await session.scalar(
        select(TrainingProgress).where(
            TrainingProgress.user_id == user_id,
            TrainingProgress.mode == mode,
        )
    )
    return row.next_index if row else 0


async def set_progress(session, user_id: int, mode: str, index: int) -> None:
    row = await session.scalar(
        select(TrainingProgress).where(
            TrainingProgress.user_id == user_id,
            TrainingProgress.mode == mode,
        )
    )
    if row is None:
        session.add(TrainingProgress(user_id=user_id, mode=mode, next_index=index))
    else:
        row.next_index = index


async def record_mistake(session, user_id: int, question_id: int) -> None:
    now = datetime.now(timezone.utc)
    row = await session.scalar(
        select(UserMistake).where(
            UserMistake.user_id == user_id,
            UserMistake.question_id == question_id,
        )
    )
    if row is None:
        session.add(
            UserMistake(
                user_id=user_id,
                question_id=question_id,
                wrong_count=1,
                resolved=False,
                last_wrong_at=now,
            )
        )
        return
    row.wrong_count += 1
    row.resolved = False
    row.last_wrong_at = now


async def resolve_mistake(session, user_id: int, question_id: int) -> None:
    row = await session.scalar(
        select(UserMistake).where(
            UserMistake.user_id == user_id,
            UserMistake.question_id == question_id,
        )
    )
    if row is not None:
        row.resolved = True


async def mistake_questions(session, user_id: int) -> list[Question]:
    statement = (
        select(Question)
        .join(UserMistake, UserMistake.question_id == Question.id)
        .where(UserMistake.user_id == user_id, UserMistake.resolved.is_(False))
        .order_by(UserMistake.last_wrong_at.desc(), Question.id)
    )
    return list(await session.scalars(statement))


async def questions_are_sample(session) -> bool:
    row = await session.get(SeedState, "questions_sample")
    return bool(row is not None and row.value == "true")
