"""Объекты-значения области «Прокат самокатов».

Модуль развивает объекты-значения лабораторной работы №2: добавлены AccountId
(идентификатор аккаунта клиента) и DurationWindow (интервал приостановки
тарификации). Правила 7 и 8 из ЛР2 (заряд 0–100 %, цена за минуту больше нуля)
сохранены без изменений.
"""

from dataclasses import dataclass
from datetime import datetime
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
class AccountId:
    """Идентификатор аккаунта клиента (новый объект-значение ЛР3)."""

    value: str

    @staticmethod
    def of(user_id: UserId) -> "AccountId":
        return AccountId(user_id.value)

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("Пустой идентификатор аккаунта")


@dataclass(frozen=True)
class BatteryLevel:
    percent: int

    def __post_init__(self):
        # правило 7 ЛР2
        if not 0 <= self.percent <= 100:
            raise InvalidValueObject("Заряд должен быть от 0 до 100 %")


@dataclass(frozen=True)
class Money:
    amount: Decimal

    def __post_init__(self):
        if self.amount < 0:
            raise InvalidValueObject("Сумма не может быть отрицательной")

    @staticmethod
    def zero() -> "Money":
        return Money(Decimal("0"))

    def __add__(self, other: "Money") -> "Money":
        return Money(self.amount + other.amount)

    def __str__(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True)
class Tariff:
    """Тариф поездки: цена за минуту движения и цена за минуту паузы.

    Поле pause_price_per_minute добавлено в ЛР3: регламент требует, чтобы пауза
    тарифицировалась по сниженной ставке. Правило 8 ЛР2 сохранено.
    """

    price_per_minute: Decimal
    min_cost: Decimal
    pause_price_per_minute: Decimal = Decimal("0")

    def __post_init__(self):
        # правило 8 ЛР2
        if self.price_per_minute <= 0:
            raise InvalidValueObject("Цена за минуту должна быть больше нуля")
        if self.min_cost < 0:
            raise InvalidValueObject("Минимальная стоимость меньше нуля")
        if self.pause_price_per_minute < 0:
            raise InvalidValueObject("Цена минуты паузы не может быть отрицательной")
        if self.pause_price_per_minute > self.price_per_minute:
            raise InvalidValueObject("Цена минуты паузы не может быть выше цены движения")

    def cost(self, active_minutes: int, pause_minutes: int = 0) -> Money:
        """Стоимость поездки: движение + пауза, но не меньше минимальной."""
        ride = self.price_per_minute * active_minutes
        pause = self.pause_price_per_minute * pause_minutes
        return Money(max(self.min_cost, ride + pause))

    def to_state(self) -> dict:
        return {
            "price_per_minute": str(self.price_per_minute),
            "min_cost": str(self.min_cost),
            "pause_price_per_minute": str(self.pause_price_per_minute),
        }


@dataclass(frozen=True)
class DurationWindow:
    """Интервал приостановки тарификации [start, end) (новый объект-значение ЛР3)."""

    start: datetime
    end: datetime

    def __post_init__(self):
        if self.end <= self.start:
            raise InvalidValueObject("Конец паузы должен быть позже начала")

    def seconds_within(self, start: datetime, end: datetime) -> int:
        """Сколько секунд интервала попадает в отрезок [start, end]."""
        left = max(self.start, start)
        right = min(self.end, end)
        if right <= left:
            return 0
        return int((right - left).total_seconds())
