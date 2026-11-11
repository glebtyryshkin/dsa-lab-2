"""Текст уведомления и накопление статистики."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


text = load("notification_text", ROOT / "notifier" / "app" / "text.py")
stats = load("store_stats", ROOT / "analytics" / "app" / "stats.py")
notification_text = text.notification_text
points_word = text.points_word
apply_event = stats.apply_event
empty_store = stats.empty_store


class TextTest(unittest.TestCase):
    def test_plural(self):
        self.assertEqual(points_word(1), "балл")
        self.assertEqual(points_word(2), "балла")
        self.assertEqual(points_word(5), "баллов")
        self.assertEqual(points_word(11), "баллов")
        self.assertEqual(points_word(21), "балл")
        self.assertEqual(points_word(124), "балла")

    def test_phrases(self):
        applied = {
            "status": "applied",
            "type": "purchase",
            "points_delta": 124,
            "balance_after": 1530,
            "reject_reason": None,
        }
        self.assertEqual(
            notification_text(applied),
            "Начислено 124 балла, баланс 1 530",
        )
        debit = dict(applied, type="return", points_delta=-30, balance_after=-30)
        self.assertEqual(notification_text(debit), "Списано 30 баллов, баланс -30")
        rejected = dict(applied, status="rejected", points_delta=0, reject_reason="limit_exceeded")
        self.assertEqual(notification_text(rejected), "Операция отклонена: limit_exceeded")


class StatsTest(unittest.TestCase):
    def test_store_totals_ignore_rejected(self):
        stores = {}
        apply_event(stores, {
            "store_id": "S12", "status": "applied", "type": "purchase", "points_delta": 124,
        })
        apply_event(stores, {
            "store_id": "S12", "status": "applied", "type": "return", "points_delta": -124,
        })
        apply_event(stores, {
            "store_id": "S12", "status": "applied", "type": "redeem", "points_delta": -30,
        })
        apply_event(stores, {
            "store_id": "S12", "status": "rejected", "type": "redeem", "points_delta": 0,
        })
        self.assertEqual(stores["S12"], {
            "store_id": "S12",
            "applied_purchase": 1,
            "applied_return": 1,
            "applied_redeem": 1,
            "rejected": 1,
            "points_accrued": 124,
            "points_returned": 124,
            "points_spent": 30,
        })
        self.assertEqual(empty_store("S07")["points_accrued"], 0)


if __name__ == "__main__":
    unittest.main()
