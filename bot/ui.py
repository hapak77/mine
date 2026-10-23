# -*- coding: utf-8 -*-
from aiogram.types import CallbackQuery, Message


def clip(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def render_question(*, title: str, meta: str, text: str, answers: list[str]) -> str:
    lines = [title]
    if meta:
        lines.append(meta)
    lines.append("")
    lines.append(text)
    lines.append("")
    for index, answer in enumerate(answers, start=1):
        lines.append(f"{index}. {answer}")
    return clip("\n".join(lines))


def answer_texts(question) -> list[str]:
    raw = question.answers or []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


async def respond(event: Message | CallbackQuery, text: str, **kwargs):
    text = clip(text)
    if isinstance(event, Message):
        return await event.answer(text, **kwargs)
    message = event.message
    if isinstance(message, Message):
        return await message.answer(text, **kwargs)
    return await event.bot.send_message(event.from_user.id, text, **kwargs)
