from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from .exceptions import InvalidValueObject


@dataclass(frozen=True)
class ScooterId:
    value: UUID

    @staticmethod
    def new() -> "ScooterId":
        return ScooterId(uuid4())


@dataclass(frozen=True)
class TripId:
    value: UUID

    @staticmethod
    def new() -> "TripId":
        return TripId(uuid4())


@dataclass(frozen=True)
class UserId:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("Пустой идентификатор пользователя")


@dataclass(frozen=True)
class BatteryLevel:
    percent: int

    def __post_init__(self):
        # правило 7
        if not 0 <= self.percent <= 100:
            raise InvalidValueObject("Заряд должен быть от 0 до 100 %")


@dataclass(frozen=True)
class Money:
    amount: Decimal

    def __post_init__(self):
        if self.amount < 0:
            raise InvalidValueObject("Сумма не может быть отрицательной")


@dataclass(frozen=True)
class Tariff:
    price_per_minute: Decimal
    min_cost: Decimal

    def __post_init__(self):
        # правило 8
        if self.price_per_minute <= 0:
            raise InvalidValueObject("Цена за минуту должна быть больше нуля")
        if self.min_cost < 0:
            raise InvalidValueObject("Минимальная стоимость меньше нуля")

    def cost(self, minutes: int) -> Money:
        return Money(max(self.min_cost, self.price_per_minute * minutes))
