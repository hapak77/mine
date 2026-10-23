# -*- coding: utf-8 -*-
import json
import random
import unittest
from pathlib import Path
from types import SimpleNamespace

from bot.database.bank import parse_question_bank
from bot.services.exam_logic import (
    BASE_MINUTES,
    EXTRA_MINUTES,
    ExamPaper,
    PaperQuestion,
    apply_answer,
    pick_extra_questions,
    start_paper,
    time_is_over,
)


def _bank(blocks: dict[str, int]) -> list[SimpleNamespace]:
    pool = []
    number = 1
    for block, count in blocks.items():
        for _ in range(count):
            pool.append(SimpleNamespace(id=number, block=block))
            number += 1
    return pool


class ExamLogicTests(unittest.TestCase):
    def test_extra_five_come_from_the_same_block(self):
        pool = _bank({"A": 8, "B": 8})
        used = {1}
        extras = pick_extra_questions("A", pool, used, limit=5, rng=random.Random(1))
        self.assertEqual(len(extras), 5)
        self.assertTrue(all(item.block == "A" for item in extras))
        self.assertNotIn(1, {item.id for item in extras})

    def test_extras_do_not_reuse_the_paper(self):
        pool = _bank({"A": 7})
        used = {1, 2, 3}
        extras = pick_extra_questions("A", pool, used, limit=5, rng=random.Random(2))
        self.assertEqual(len(extras), 4)
        self.assertTrue(used.isdisjoint({item.id for item in extras}))

    def test_short_block_returns_fewer(self):
        pool = _bank({"A": 3, "B": 10})
        extras = pick_extra_questions("A", pool, set(), limit=5, rng=random.Random(3))
        self.assertEqual(len(extras), 3)

    def test_initial_paper_reserves_extras(self):
        pool = _bank({str(block): 10 for block in range(5)})
        paper = start_paper(pool, now_ts=1_000, rng=random.Random(4))
        self.assertEqual(len(paper.questions), 20)
        self.assertEqual(paper.deadline_ts, 1_000 + BASE_MINUTES * 60)
        used = paper.used_ids
        for block in {str(i) for i in range(5)}:
            left = [item for item in pool if item.block == block and item.id not in used]
            self.assertGreaterEqual(len(left), 5)

    def test_mistake_appends_five_and_extends_time(self):
        pool = _bank({"A": 12, "B": 12})
        paper = ExamPaper(
            questions=[PaperQuestion(1, "A"), PaperQuestion(2, "B")],
            base_count=2,
            deadline_ts=5_000,
        )
        paper, added, fail = apply_answer(paper, 1, False, pool, random.Random(5))
        self.assertIsNone(fail)
        self.assertEqual(len(added), 5)
        self.assertTrue(all(item.block == "A" and item.extra for item in added))
        self.assertEqual(paper.deadline_ts, 5_000 + EXTRA_MINUTES * 60)
        self.assertEqual(len(paper.questions), 7)

    def test_second_mistake_in_block_fails_without_another_bundle(self):
        pool = _bank({"A": 20})
        paper = ExamPaper(
            questions=[PaperQuestion(1, "A"), PaperQuestion(2, "A")],
            base_count=2,
            deadline_ts=100,
        )
        paper, _, fail = apply_answer(paper, 1, False, pool, random.Random(6))
        self.assertIsNone(fail)
        paper, added, fail = apply_answer(paper, 2, False, pool, random.Random(6))
        self.assertEqual(fail, "block")
        self.assertEqual(added, [])

    def test_mistake_on_extra_fails(self):
        pool = _bank({"A": 10})
        paper = ExamPaper(
            questions=[PaperQuestion(1, "A", extra=True)],
            base_count=0,
            deadline_ts=100,
        )
        paper, added, fail = apply_answer(paper, 1, False, pool, random.Random(7))
        self.assertEqual(fail, "extra")
        self.assertEqual(added, [])

    def test_correct_answer_does_not_extend_the_paper(self):
        pool = _bank({"A": 10})
        paper = ExamPaper(questions=[PaperQuestion(1, "A")], base_count=1, deadline_ts=50)
        paper, added, fail = apply_answer(paper, 1, True, pool, random.Random(8))
        self.assertIsNone(fail)
        self.assertEqual(added, [])
        self.assertTrue(paper.finished)
        self.assertEqual(paper.deadline_ts, 50)

    def test_time_limit(self):
        self.assertFalse(time_is_over(100, 100))
        self.assertTrue(time_is_over(100, 101))

    def test_sample_bank_can_exercise_extra_five(self):
        payload = json.loads(Path("data/parsed_tickets.json").read_text(encoding="utf-8"))
        bank = parse_question_bank(payload)
        self.assertTrue(payload["sample"])
        self.assertTrue(bank.sample)
        modules = {question.module for question in bank.questions}
        self.assertEqual(modules, {1, 2, 3, 4, 5})
        self.assertGreaterEqual(sum(1 for question in bank.questions if question.qualification), 3)
        by_block: dict[str, list] = {}
        for question in bank.questions:
            by_block.setdefault(question.block, []).append(question)
            self.assertGreaterEqual(len(question.answers), 2)
            self.assertTrue(0 <= question.correct_index < len(question.answers))
        self.assertGreaterEqual(len(by_block), 5)
        for items in by_block.values():
            self.assertGreaterEqual(len(items), 6)

        pool = [
            SimpleNamespace(id=index, block=question.block)
            for index, question in enumerate(bank.questions)
        ]
        paper = start_paper(pool, now_ts=0, rng=random.Random(9))
        self.assertEqual(len(paper.questions), 20)
        first = paper.questions[0]
        paper, added, fail = apply_answer(paper, first.id, False, pool, random.Random(10))
        self.assertIsNone(fail)
        self.assertEqual(len(added), 5)
        self.assertTrue(all(item.block == first.block for item in added))


if __name__ == "__main__":
    unittest.main()
