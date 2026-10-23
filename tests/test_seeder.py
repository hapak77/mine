# -*- coding: utf-8 -*-
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "000000:TEST")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("USE_WEBHOOK", "false")
os.environ.setdefault("REDIS_HOST", "redis")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "redis_secure_password")

from bot.config import settings
from bot.database.bank import BankError, parse_question_bank
from bot.database.seeder import load_question_bank
from bot.services.billing import apply_tariff, is_dry_run
from bot.services.billing import TARIFFS
from datetime import datetime, timedelta, timezone


class SeederTests(unittest.TestCase):
    def test_loader_accepts_sample_file(self):
        bank = load_question_bank("data/parsed_tickets.json")
        self.assertTrue(bank.sample)
        self.assertGreaterEqual(len(bank.questions), 40)
        self.assertEqual({question.module for question in bank.questions}, {1, 2, 3, 4, 5})

    def test_aliases_and_nested_tickets(self):
        payload = {
            "sample": False,
            "tickets": [
                {
                    "id": 7,
                    "questions": [
                        {
                            "id": "t7q1",
                            "topic": "Знаки",
                            "question": "Что означает образец знака?",
                            "options": [
                                {"text": "Первый", "is_correct": False},
                                {"text": "Второй", "is_correct": True},
                            ],
                            "explanation": "Комментарий",
                            "hint": "Подсказка",
                        }
                    ],
                }
            ],
        }
        bank = parse_question_bank(payload)
        question = bank.questions[0]
        self.assertFalse(bank.sample)
        self.assertEqual(question.correct_index, 1)
        self.assertEqual(question.ticket, 7)
        self.assertEqual(question.comment, "Комментарий")
        self.assertEqual(question.mnemonic, "Подсказка")
        self.assertEqual(question.module_title, "Знаки")

    def test_correct_answer_is_one_based(self):
        bank = parse_question_bank([
            {
                "id": "n1",
                "module": 4,
                "text": "Образец",
                "answers": ["а", "б", "в"],
                "correct_answer": 2,
            }
        ])
        self.assertEqual(bank.questions[0].correct_index, 1)
        self.assertEqual(bank.questions[0].block, "4")

    def test_duplicate_id_is_rejected(self):
        with self.assertRaises(BankError):
            parse_question_bank([
                {"id": "x", "text": "а", "answers": ["1", "2"], "correct": 0},
                {"id": "x", "text": "б", "answers": ["1", "2"], "correct": 1},
            ])

    def test_redis_url_comes_from_redis_settings(self):
        self.assertTrue(settings.redis_url.startswith("redis://"))
        self.assertIn(settings.REDIS_HOST, settings.redis_url)
        self.assertIn(str(settings.REDIS_PORT), settings.redis_url)
        if settings.REDIS_PASSWORD:
            self.assertIn("@", settings.redis_url)

    def test_env_example_lists_payment_placeholder_as_dry_run(self):
        example = Path(".env.example").read_text(encoding="utf-8")
        self.assertIn("PAYMENT_PROVIDER_TOKEN=381764678:TEST:12345", example)
        self.assertIn("ENABLE_TELEGRAM_STARS=true", example)
        self.assertIn("QUESTIONS_DATA_PATH=data/parsed_tickets.json", example)
        self.assertIn("LEAD_MAGNET_PATH=data/master_pdd_guide.pdf", example)
        self.assertTrue(is_dry_run("381764678:TEST:12345"))
        self.assertTrue(is_dry_run(""))
        self.assertFalse(is_dry_run("381764678:TEST:real-token"))

    def test_month_tariff_extends_and_forever_sticks(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        forever, until = apply_tariff(False, None, TARIFFS["month"], now)
        self.assertFalse(forever)
        self.assertEqual(until, now + timedelta(days=30))
        forever, until = apply_tariff(False, until, TARIFFS["month"], now + timedelta(days=10))
        self.assertEqual(until, now + timedelta(days=60))
        forever, until = apply_tariff(forever, until, TARIFFS["forever"], now)
        self.assertTrue(forever)
        self.assertIsNone(until)
        raw = json.loads(Path("data/parsed_tickets.json").read_text(encoding="utf-8"))
        self.assertTrue(raw["sample"])


if __name__ == "__main__":
    unittest.main()
