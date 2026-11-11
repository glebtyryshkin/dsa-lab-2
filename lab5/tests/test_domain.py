"""Проверка правил начисления без базы и брокера."""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from app.domain import ExistingPurchase, accrued_points, decide  # noqa: E402


def purchase(amount, balance=0, existing=None):
    return decide(
        "purchase", "C1", Decimal(amount), None, balance, existing, False
    )


class DomainTest(unittest.TestCase):
    def test_five_percent_truncates(self):
        self.assertEqual(accrued_points(Decimal("2490.00")), 124)
        self.assertEqual(accrued_points(Decimal("19.99")), 0)
        self.assertEqual(accrued_points(Decimal("20")), 1)

    def test_purchase_and_duplicate(self):
        first = purchase("2490.00", 1406)
        self.assertEqual((first.status, first.points_delta, first.balance_after), ("applied", 124, 1530))
        existing = ExistingPurchase("C1", 124)
        again = purchase("100", 1530, existing)
        self.assertEqual(again.reject_reason, "duplicate_purchase")
        self.assertEqual(again.balance_after, 1530)
        self.assertEqual(again.points_delta, 0)

    def test_return_reasons_in_table_order(self):
        missing = decide("return", "C1", None, None, 10, None, False)
        self.assertEqual(missing.reject_reason, "purchase_not_found")

        other = ExistingPurchase("C2", 124)
        mismatch = decide("return", "C1", None, None, 10, other, True)
        self.assertEqual(mismatch.reject_reason, "customer_mismatch")

        own = ExistingPurchase("C1", 124)
        returned = decide("return", "C1", None, None, 10, own, True)
        self.assertEqual(returned.reject_reason, "already_returned")

        ok = decide("return", "C1", None, None, 94, own, False)
        self.assertEqual((ok.status, ok.points_delta, ok.balance_after), ("applied", -124, -30))

    def test_redeem_limit_before_balance(self):
        # 30 % от 2490 = 747, 800 больше и лимита, и баланса. Берётся лимит.
        both = decide("redeem", "C1", Decimal("2490"), 800, 94, None, False)
        self.assertEqual(both.reject_reason, "limit_exceeded")

        # 200 не больше 30 % от 1000, но больше баланса.
        poor = decide("redeem", "C1", Decimal("1000"), 200, 94, None, False)
        self.assertEqual(poor.reject_reason, "insufficient_balance")

        exact = decide("redeem", "C1", Decimal("100"), 30, 30, None, False)
        self.assertEqual((exact.status, exact.points_delta, exact.balance_after), ("applied", -30, 0))

    def test_domain_has_no_infrastructure_imports(self):
        source = (Path(__file__).resolve().parents[1] / "worker" / "app" / "domain.py").read_text()
        self.assertNotIn("aio_pika", source)
        self.assertNotIn("sqlalchemy", source)


if __name__ == "__main__":
    unittest.main()
