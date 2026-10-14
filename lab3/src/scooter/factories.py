"""Фабрики восстановления агрегатов из сохранённого состояния.

Восстановление идёт через конструктор агрегата, поэтому инварианты проверяются
повторно: например, поездка со стоимостью, не соответствующей тарифу, не
восстановится (правило 4 ЛР2 сохраняется и в ЛР3).
"""

from decimal import Decimal
from uuid import UUID

from .account import Account
from .aggregates import Scooter, ScooterStatus, Trip
from .states import TripState
from .value_objects import AccountId, BatteryLevel, Money, ScooterId, Tariff, TripId, UserId


def _state_by_name(name: str) -> TripState:
    """Найти объект состояния по названию (причастие) или по имени класса."""
    from .states import STATES
    for cls in STATES.values():
        if cls.name == name or cls.__name__ == name:
            return cls()
    raise ValueError("Неизвестное состояние поездки: {}".format(name))


class ScooterFactory:
    @staticmethod
    def restore(state: dict) -> Scooter:
        return Scooter(
            ScooterId(UUID(state["scooter_id"])),
            BatteryLevel(state["battery"]),
            ScooterStatus[state["status"]],
        )


class TripFactory:
    @staticmethod
    def restore(state: dict) -> Trip:
        cost = Money(Decimal(str(state["cost"]))) if state["cost"] is not None else None
        tariff = Tariff(
            Decimal(str(state["price_per_minute"])),
            Decimal(str(state["min_cost"])),
            Decimal(str(state.get("pause_price_per_minute", "0"))),
        )
        pauses = [(start, end) for start, end in state.get("pauses", [])]
        return Trip(
            TripId(UUID(state["trip_id"])),
            ScooterId(UUID(state["scooter_id"])),
            UserId(state["user_id"]),
            tariff,
            state["created_at"],
            state=_state_by_name(state["status"]),
            parking_confirmed=state.get("parking_confirmed", False),
            captured=state.get("captured", False),
            unlocked=state.get("unlocked", False),
            billing_started=state.get("billing_started", False),
            started_at=state.get("started_at"),
            finished_at=state.get("finished_at"),
            booking_started_at=state.get("booking_started_at"),
            pauses=pauses,
            cost=cost,
            events=state.get("events", []),
        )


class AccountFactory:
    @staticmethod
    def restore(state: dict) -> Account:
        return Account(
            AccountId(state["account_id"]),
            Money(Decimal(str(state["debt"]))),
            state["blocked"],
        )
