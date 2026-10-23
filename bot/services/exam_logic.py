# -*- coding: utf-8 -*-
"""Чистая логика симулятора экзамена ГИБДД.

20 случайных вопросов и 20 минут. Первая ошибка в тематическом блоке
добавляет до 5 вопросов из того же блока и 5 минут. Вторая ошибка в том же
блоке или любая ошибка в дополнительном вопросе завершает экзамен.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

BASE_QUESTIONS = 20
BASE_MINUTES = 20
EXTRA_PER_MISTAKE = 5
EXTRA_MINUTES = 5


@dataclass
class PaperQuestion:
    id: int
    block: str
    extra: bool = False


@dataclass
class ExamPaper:
    questions: list[PaperQuestion]
    base_count: int
    cursor: int = 0
    base_errors: dict[str, int] = field(default_factory=dict)
    extra_errors: int = 0
    deadline_ts: float = 0.0

    @property
    def used_ids(self) -> set[int]:
        return {item.id for item in self.questions}

    @property
    def finished(self) -> bool:
        return self.cursor >= len(self.questions)


def select_initial_questions(
    questions: list,
    *,
    count: int = BASE_QUESTIONS,
    reserve: int = EXTRA_PER_MISTAKE,
    rng: random.Random | None = None,
) -> list:
    """Набрать до `count` вопросов, оставив в блоке запас для +5.

    В большом банке из блока удерживается `reserve` вопросов, и в билет
    они не попадают, пока не случится ошибка. Если банк меньше, билет
    короче 20, но правило «+5 из того же блока» остаётся выполнимым.
    """
    rng = rng or random.Random()
    grouped: dict[str, list] = {}
    for question in questions:
        grouped.setdefault(str(question.block), []).append(question)

    names = list(grouped)
    rng.shuffle(names)
    pools: list[list] = []
    held: list = []
    for name in names:
        items = list(grouped[name])
        rng.shuffle(items)
        if reserve and len(items) > reserve:
            pools.append(items[:-reserve])
            held.extend(items[-reserve:])
        elif items:
            pools.append(items[:1])
            held.extend(items[1:])

    picked: list = []
    while len(picked) < count:
        progressed = False
        for pool in pools:
            if pool and len(picked) < count:
                picked.append(pool.pop(0))
                progressed = True
        if not progressed:
            break
    if len(picked) < count and held:
        rng.shuffle(held)
        picked.extend(held[: count - len(picked)])
    return picked


def pick_extra_questions(
    block: str,
    pool: list,
    used_ids: set,
    *,
    limit: int = EXTRA_PER_MISTAKE,
    rng: random.Random | None = None,
) -> list:
    """До `limit` вопросов того же блока, которых ещё нет в билете."""
    rng = rng or random.Random()
    candidates = [
        question
        for question in pool
        if str(question.block) == str(block) and question.id not in used_ids
    ]
    if len(candidates) <= limit:
        rng.shuffle(candidates)
        return candidates
    return rng.sample(candidates, limit)


def time_is_over(deadline_ts: float, now_ts: float) -> bool:
    return now_ts > deadline_ts


def start_paper(
    questions: list,
    now_ts: float,
    rng: random.Random | None = None,
    *,
    count: int = BASE_QUESTIONS,
    reserve: int = EXTRA_PER_MISTAKE,
    minutes: int = BASE_MINUTES,
) -> ExamPaper:
    rng = rng or random.Random()
    chosen = select_initial_questions(questions, count=count, reserve=reserve, rng=rng)
    return ExamPaper(
        questions=[
            PaperQuestion(id=question.id, block=str(question.block), extra=False)
            for question in chosen
        ],
        base_count=len(chosen),
        deadline_ts=now_ts + minutes * 60,
    )


def apply_answer(
    paper: ExamPaper,
    question_id: int,
    correct: bool,
    pool: list,
    rng: random.Random | None = None,
) -> tuple[ExamPaper, list[PaperQuestion], str | None]:
    """Применить ответ. Возвращает (билет, добавленные вопросы, причина провала).

    Причина: None, «block» (вторая ошибка в блоке) или «extra».
    """
    rng = rng or random.Random()
    if paper.cursor >= len(paper.questions):
        return paper, [], None
    current = paper.questions[paper.cursor]
    if current.id != question_id:
        raise ValueError("answer does not match the current exam question")

    added: list[PaperQuestion] = []
    fail: str | None = None
    if not correct:
        if current.extra:
            paper.extra_errors += 1
            fail = "extra"
        else:
            paper.base_errors[current.block] = paper.base_errors.get(current.block, 0) + 1
            if paper.base_errors[current.block] >= 2:
                # Вторая ошибка в блоке завершает экзамен, второй пачки +5 нет.
                fail = "block"
            else:
                extras = pick_extra_questions(
                    current.block,
                    pool,
                    paper.used_ids,
                    limit=EXTRA_PER_MISTAKE,
                    rng=rng,
                )
                for item in extras:
                    extra = PaperQuestion(id=item.id, block=str(item.block), extra=True)
                    paper.questions.append(extra)
                    added.append(extra)
                if added:
                    paper.deadline_ts += EXTRA_MINUTES * 60
    paper.cursor += 1
    return paper, added, fail


def paper_to_state(paper: ExamPaper) -> dict:
    return {
        "questions": [
            {"id": item.id, "block": item.block, "extra": item.extra}
            for item in paper.questions
        ],
        "base_count": paper.base_count,
        "cursor": paper.cursor,
        "base_errors": paper.base_errors,
        "extra_errors": paper.extra_errors,
        "deadline_ts": paper.deadline_ts,
    }


def paper_from_state(data: dict) -> ExamPaper:
    return ExamPaper(
        questions=[PaperQuestion(**item) for item in data["questions"]],
        base_count=int(data["base_count"]),
        cursor=int(data["cursor"]),
        base_errors={str(key): int(value) for key, value in data.get("base_errors", {}).items()},
        extra_errors=int(data.get("extra_errors", 0)),
        deadline_ts=float(data["deadline_ts"]),
    )
