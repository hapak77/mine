# -*- coding: utf-8 -*-
import unittest
from datetime import datetime, timedelta, timezone

from bot.services.lives import (
    FULL_LIVES,
    accrue_lives,
    apply_referral_reward,
    apply_trainer_mistake,
    can_train,
    format_lives,
    is_unlimited,
    next_life_at,
    reward_inviter,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


class LivesTests(unittest.TestCase):
    def test_mistake_spends_one_and_starts_timer_below_five(self):
        lives, anchor, blocked = apply_trainer_mistake(5, None, NOW, unlimited=False)
        self.assertEqual(lives, 4)
        self.assertEqual(anchor, NOW)
        self.assertFalse(blocked)

    def test_second_mistake_does_not_reset_timer(self):
        anchor = NOW - timedelta(hours=1)
        lives, new_anchor, blocked = apply_trainer_mistake(4, anchor, NOW, unlimited=False)
        self.assertEqual(lives, 3)
        self.assertEqual(new_anchor, anchor)
        self.assertFalse(blocked)

    def test_last_life_blocks(self):
        lives, anchor, blocked = apply_trainer_mistake(1, NOW, NOW, unlimited=False)
        self.assertEqual(lives, 0)
        self.assertTrue(blocked)
        self.assertIsNotNone(anchor)

    def test_zero_stays_zero(self):
        lives, _, blocked = apply_trainer_mistake(0, NOW, NOW, unlimited=False)
        self.assertEqual((lives, blocked), (0, True))
        self.assertFalse(can_train(0, unlimited=False))

    def test_premium_does_not_spend_lives(self):
        lives, anchor, blocked = apply_trainer_mistake(2, NOW, NOW, unlimited=True)
        self.assertEqual((lives, anchor, blocked), (2, NOW, False))
        self.assertTrue(can_train(0, unlimited=True))

    def test_regen_one_life_every_three_hours(self):
        lives, anchor, gained = accrue_lives(4, NOW, NOW + timedelta(hours=3))
        self.assertEqual((lives, gained), (5, 1))
        self.assertIsNone(anchor)

    def test_regen_waits_full_interval(self):
        lives, anchor, gained = accrue_lives(2, NOW, NOW + timedelta(hours=2, minutes=59))
        self.assertEqual((lives, gained), (2, 0))
        self.assertEqual(anchor, NOW)

    def test_regen_caps_at_five(self):
        lives, anchor, gained = accrue_lives(2, NOW, NOW + timedelta(hours=12))
        self.assertEqual((lives, gained, anchor), (5, 3, None))

    def test_regen_does_not_grow_bonus_lives(self):
        lives, anchor, gained = accrue_lives(10, None, NOW + timedelta(hours=9))
        self.assertEqual((lives, gained, anchor), (10, 0, None))

    def test_partial_regen_moves_anchor(self):
        lives, anchor, gained = accrue_lives(1, NOW, NOW + timedelta(hours=6))
        self.assertEqual((lives, gained), (3, 2))
        self.assertEqual(anchor, NOW + timedelta(hours=6))
        nxt = next_life_at(lives, anchor)
        self.assertEqual(nxt, anchor + timedelta(hours=3))

    def test_referral_restores_five_and_grants_five(self):
        self.assertEqual(reward_inviter(0), 10)
        self.assertEqual(reward_inviter(3), 10)
        self.assertEqual(reward_inviter(FULL_LIVES), 10)
        self.assertEqual(reward_inviter(12), 17)
        lives, anchor = apply_referral_reward(0, NOW)
        self.assertEqual(lives, 10)
        self.assertIsNone(anchor)

    def test_unlimited_flag_and_expiry(self):
        self.assertTrue(is_unlimited(True, None, NOW))
        self.assertTrue(is_unlimited(False, NOW + timedelta(days=1), NOW))
        self.assertFalse(is_unlimited(False, NOW - timedelta(seconds=1), NOW))
        self.assertFalse(is_unlimited(False, None, NOW))

    def test_hearts_display(self):
        self.assertEqual(format_lives(5), "❤️❤️❤️❤️❤️")
        self.assertEqual(format_lives(0), "💔 0")
        self.assertEqual(format_lives(8), "❤️❤️❤️❤️❤️ +3")
        self.assertEqual(format_lives(0, unlimited=True), "♾ безлимит")


if __name__ == "__main__":
    unittest.main()
