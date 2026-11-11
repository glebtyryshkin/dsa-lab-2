"""Расчёт баллов.

Чистые функции: без базы и без брокера. Порядок причин отказа
совпадает с таблицей методички: берётся первая подходящая.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExistingPurchase:
    customer_id: str
    points: int


@dataclass(frozen=True)
class Decision:
    status: str
    points_delta: int
    balance_after: int
    reject_reason: str | None


def accrued_points(amount: Decimal) -> int:
    """5 % суммы чека, дробная часть отбрасывается."""
    return int((amount * Decimal(5)) // Decimal(100))


def exceeds_redeem_limit(points: int, amount: Decimal) -> bool:
    """Баллами можно оплатить не более 30 % суммы чека."""
    limit = amount * Decimal(30) / Decimal(100)
    return Decimal(points) > limit


def decide(
    op_type: str,
    customer_id: str,
    amount: Decimal | None,
    points: int | None,
    balance: int,
    existing_purchase: ExistingPurchase | None,
    already_returned: bool,
) -> Decision:
    if op_type == "purchase":
        if existing_purchase is not None:
            return _reject("duplicate_purchase", balance)
        if amount is None:
            raise ValueError("у покупки нет суммы")
        return _apply(accrued_points(amount), balance)

    if op_type == "return":
        if existing_purchase is None:
            return _reject("purchase_not_found", balance)
        if existing_purchase.customer_id != customer_id:
            return _reject("customer_mismatch", balance)
        if already_returned:
            return _reject("already_returned", balance)
        return _apply(-existing_purchase.points, balance)

    if op_type == "redeem":
        if amount is None or points is None:
            raise ValueError("у оплаты баллами нет суммы или числа баллов")
        if exceeds_redeem_limit(points, amount):
            return _reject("limit_exceeded", balance)
        if points > balance:
            return _reject("insufficient_balance", balance)
        return _apply(-points, balance)

    raise ValueError("неизвестный тип операции: {0}".format(op_type))


def _apply(delta: int, balance: int) -> Decision:
    return Decision("applied", delta, balance + delta, None)


def _reject(reason: str, balance: int) -> Decision:
    return Decision("rejected", 0, balance, reason)
