from uuid import UUID

from .aggregates import Trip
from .value_objects import Money, ScooterId, Tariff, TripId, UserId


class TripFactory:
    @staticmethod
    def restore(state: dict) -> Trip:
        # конструктор Trip заново проверяет правила 3 и 4
        cost = Money(state["cost"]) if state["cost"] is not None else None
        return Trip(
            TripId(UUID(state["trip_id"])),
            ScooterId(UUID(state["scooter_id"])),
            UserId(state["user_id"]),
            Tariff(state["price_per_minute"], state["min_cost"]),
            state["started_at"],
            state["finished_at"],
            cost,
        )
