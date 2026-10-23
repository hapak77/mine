# -*- coding: utf-8 -*-
import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy import select

from bot.config import settings
from bot.database.bank import ParsedBank, parse_question_bank
from bot.database.engine import SessionLocal
from bot.database.models import Question, SeedState

logger = logging.getLogger(__name__)


def load_question_bank(path: str | None = None) -> ParsedBank:
    file_path = Path(path or settings.QUESTIONS_DATA_PATH)
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    return parse_question_bank(payload)


async def seed_questions_if_needed() -> int:
    """Загрузить банк, если файл изменился. Повторный старт с тем же файлом ничего не пишет."""
    file_path = Path(settings.QUESTIONS_DATA_PATH)
    raw = file_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    bank = parse_question_bank(json.loads(raw.decode("utf-8")))

    async with SessionLocal() as session:
        state = await session.get(SeedState, "questions_sha256")
        if state is not None and state.value == digest:
            logger.info("Question bank is already loaded (%s).", digest[:12])
            return 0

        existing = {
            row.external_id: row
            for row in await session.scalars(select(Question))
        }
        seen: set[str] = set()
        for item in bank.questions:
            seen.add(item.external_id)
            row = existing.get(item.external_id)
            if row is None:
                row = Question(external_id=item.external_id)
                session.add(row)
            row.module = item.module
            row.module_title = item.module_title
            row.block = item.block
            row.ticket = item.ticket
            row.number = item.number
            row.position = item.position
            row.text = item.text
            row.answers = item.answers
            row.correct_index = item.correct_index
            row.comment = item.comment
            row.mnemonic = item.mnemonic
            row.image = item.image
            row.qualification = item.qualification

        for external_id, row in existing.items():
            if external_id not in seen:
                await session.delete(row)

        sample_value = "true" if bank.sample else "false"
        sample_row = await session.get(SeedState, "questions_sample")
        if sample_row is None:
            session.add(SeedState(key="questions_sample", value=sample_value))
        else:
            sample_row.value = sample_value
        if state is None:
            session.add(SeedState(key="questions_sha256", value=digest))
        else:
            state.value = digest
        await session.commit()

    if bank.sample:
        logger.warning(
            "Loaded a SAMPLE question bank (%s questions). Replace data/parsed_tickets.json with the full bank.",
            len(bank.questions),
        )
    else:
        logger.info("Loaded question bank: %s questions.", len(bank.questions))
    return len(bank.questions)
