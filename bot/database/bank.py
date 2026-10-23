# -*- coding: utf-8 -*-
"""Разбор банка вопросов.

Канонический файл — объект с флагом sample и массивом questions.
Тот же загрузчик принимает список вопросов и вложение tickets[].questions,
чтобы полную базу можно было положить вместо учебной без правок кода.
"""

from dataclasses import dataclass

DEFAULT_MODULES = {
    1: "Перекрестки",
    2: "Регулировщик",
    3: "Обгон/Стоянка",
    4: "Знаки",
    5: "Медицина",
}


class BankError(ValueError):
    pass


@dataclass
class ParsedQuestion:
    external_id: str
    module: int
    module_title: str
    block: str
    ticket: int | None
    number: int | None
    text: str
    answers: list[str]
    correct_index: int
    comment: str
    mnemonic: str
    image: str | None
    qualification: bool
    position: int


@dataclass
class ParsedBank:
    sample: bool
    questions: list[ParsedQuestion]


def parse_question_bank(payload) -> ParsedBank:
    items, meta = _question_items(payload)
    catalog = _catalog(meta)
    dynamic = max([pair[0] for pair in catalog.values()] or [0])
    seen: set[str] = set()
    questions: list[ParsedQuestion] = []
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise BankError(f"question #{position} is not an object")
        module, module_title = _module(item, catalog, dynamic)
        dynamic = max(dynamic, module)
        external_id = str(item.get("id") or item.get("external_id") or f"auto-{position}").strip()
        if not external_id:
            raise BankError(f"question #{position} has an empty id")
        if external_id in seen:
            raise BankError(f"duplicate question id {external_id}")
        seen.add(external_id)
        answers, correct_index = _answers_and_correct(item, external_id)
        text = item.get("text") or item.get("question") or item.get("title")
        if not isinstance(text, str) or not text.strip():
            raise BankError(f"question {external_id} has no text")
        block = item.get("block", item.get("block_id", item.get("topic_block")))
        if block is None or str(block).strip() == "":
            block = str(module)
        image = item.get("image") or item.get("image_url") or None
        if isinstance(image, str):
            image = image.strip() or None
        else:
            image = None
        questions.append(
            ParsedQuestion(
                external_id=external_id,
                module=module,
                module_title=module_title,
                block=str(block).strip(),
                ticket=_opt_int(item.get("ticket", item.get("ticket_number"))),
                number=_opt_int(item.get("number", item.get("question_number", item.get("id_in_ticket")))),
                text=text.strip(),
                answers=answers,
                correct_index=correct_index,
                comment=str(item.get("comment") or item.get("explanation") or item.get("answer_tip") or "").strip(),
                mnemonic=str(item.get("mnemonic") or item.get("lifehack") or item.get("hint") or "").strip(),
                image=image,
                qualification=_as_bool(item.get("qualification", item.get("is_qualification", item.get("hard", False)))),
                position=position,
            )
        )
    if not questions:
        raise BankError("question bank is empty")
    return ParsedBank(sample=_as_bool(meta.get("sample")) if isinstance(meta, dict) else False, questions=questions)


def _question_items(payload) -> tuple[list, dict]:
    if isinstance(payload, list):
        return payload, {}
    if not isinstance(payload, dict):
        raise BankError("question bank must be a JSON object or a list")
    questions = payload.get("questions")
    if isinstance(questions, list):
        return questions, payload
    tickets = payload.get("tickets")
    if isinstance(tickets, list):
        flat: list[dict] = []
        for ticket in tickets:
            if not isinstance(ticket, dict):
                continue
            ticket_id = ticket.get("id", ticket.get("number", ticket.get("ticket_number")))
            for question in ticket.get("questions") or []:
                if not isinstance(question, dict):
                    continue
                item = dict(question)
                item.setdefault("ticket", ticket_id)
                flat.append(item)
        if flat:
            return flat, payload
    raise BankError("no questions in bank: expected questions[] or tickets[].questions[]")


def _catalog(meta: dict) -> dict[str, tuple[int, str]]:
    catalog: dict[str, tuple[int, str]] = {}
    modules = meta.get("modules") if isinstance(meta, dict) else None
    if isinstance(modules, list) and modules:
        for entry in modules:
            if isinstance(entry, dict) and entry.get("id") is not None:
                module_id = int(entry["id"])
                title = str(entry.get("title") or entry.get("name") or DEFAULT_MODULES.get(module_id, str(module_id)))
            elif isinstance(entry, str):
                module_id = len({pair[0] for pair in catalog.values()}) + 1
                title = entry
            else:
                continue
            catalog[str(module_id)] = (module_id, title)
            catalog[title] = (module_id, title)
            if isinstance(entry, dict) and entry.get("code"):
                catalog[str(entry["code"])] = (module_id, title)
        if catalog:
            return catalog
    for module_id, title in DEFAULT_MODULES.items():
        catalog[str(module_id)] = (module_id, title)
        catalog[title] = (module_id, title)
    return catalog


def _module(item: dict, catalog: dict[str, tuple[int, str]], dynamic: int) -> tuple[int, str]:
    raw = item.get("module", item.get("module_id", item.get("topic")))
    if raw is None or raw == "":
        raw = 1
    key = str(raw).strip()
    if key in catalog:
        return catalog[key]
    number = _as_int(key)
    if number is not None:
        title = DEFAULT_MODULES.get(number, f"Модуль {number}")
        catalog[key] = (number, title)
        return number, title
    module_id = dynamic + 1
    catalog[key] = (module_id, key)
    return module_id, key


def _answers_and_correct(item: dict, external_id: str) -> tuple[list[str], int]:
    raw = item.get("answers", item.get("options", item.get("variants")))
    if not isinstance(raw, list) or len(raw) < 2:
        raise BankError(f"question {external_id} needs at least 2 answers")
    texts: list[str] = []
    flagged: int | None = None
    for answer in raw:
        if isinstance(answer, str):
            texts.append(answer.strip())
        elif isinstance(answer, dict):
            text = answer.get("text") or answer.get("answer_text") or answer.get("answer") or answer.get("value")
            if text is None or str(text).strip() == "":
                raise BankError(f"question {external_id} has an empty answer")
            if answer.get("is_correct") is True or answer.get("correct") is True:
                flagged = len(texts)
            texts.append(str(text).strip())
        else:
            raise BankError(f"question {external_id} has a bad answer")
    if flagged is not None:
        return texts, flagged
    if item.get("correct") is not None:
        return texts, _index_from_value(item.get("correct"), texts, one_based=False, external_id=external_id)
    if item.get("correct_index") is not None:
        return texts, _index_from_value(item.get("correct_index"), texts, one_based=False, external_id=external_id)
    if item.get("correct_answer") is not None:
        return texts, _index_from_value(item.get("correct_answer"), texts, one_based=True, external_id=external_id)
    raise BankError(f"question {external_id} has no correct answer")


def _index_from_value(value, texts: list[str], *, one_based: bool, external_id: str) -> int:
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) == 1 and stripped.upper() in "ABCDEFGH":
            index = ord(stripped.upper()) - ord("A")
            if index < len(texts):
                return index
        for index, text in enumerate(texts):
            if text == stripped:
                return index
        number = _as_int(stripped)
        if number is None:
            raise BankError(f"question {external_id}: cannot read correct answer {value!r}")
        value = number
    number = _as_int(value)
    if number is None:
        raise BankError(f"question {external_id}: correct answer is not an index")
    if one_based:
        if 1 <= number <= len(texts):
            return number - 1
        raise BankError(f"question {external_id}: correct_answer out of range")
    if 0 <= number < len(texts):
        return number
    raise BankError(f"question {external_id}: correct index out of range")


def _as_int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    return None


def _opt_int(value) -> int | None:
    if value is None or value == "":
        return None
    return _as_int(value)


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "да"}
    return bool(value)
