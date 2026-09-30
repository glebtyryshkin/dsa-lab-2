import math
from datetime import datetime
from enum import Enum

from .exceptions import DomainInvariantViolation
from .value_objects import BatteryLevel, Money, ScooterId, Tariff, TripId, UserId

# минимальный заряд для выдачи, % (в задании не указан, взят свой)
MIN_BATTERY = 20


class ScooterStatus(Enum):
    FREE = "свободен"
    RENTED = "арендован"
    MAINTENANCE = "на обслуживании"


class Scooter:  # корень агрегата «самокат»
    def __init__(self, scooter_id: ScooterId, battery: BatteryLevel,
                 status: ScooterStatus = ScooterStatus.FREE):
        self._id = scooter_id
        self._battery = battery
        self._status = status

    @property
    def id(self) -> ScooterId:
        return self._id

    @property
    def battery(self) -> BatteryLevel:
        return self._battery

    @property
    def status(self) -> ScooterStatus:
        return self._status

    def rent_out(self) -> None:
        # правило 2
        if self._status is ScooterStatus.RENTED:
            raise DomainInvariantViolation("Самокат уже арендован")
        if self._status is ScooterStatus.MAINTENANCE:
            raise DomainInvariantViolation("Самокат на обслуживании")
        # правило 1
        if self._battery.percent < MIN_BATTERY:
            raise DomainInvariantViolation("Заряд ниже минимального порога")
        self._status = ScooterStatus.RENTED

    def check_can_return(self) -> None:
        # правило 6
        if self._status is not ScooterStatus.RENTED:
            raise DomainInvariantViolation("Самокат не арендован")

    def return_from_trip(self, battery: BatteryLevel) -> None:
        self.check_can_return()
        self._battery = battery
        self._status = ScooterStatus.FREE


class Trip:  # корень агрегата «поездка»
    def __init__(self, trip_id: TripId, scooter_id: ScooterId, user_id: UserId,
                 tariff: Tariff, started_at: datetime,
                 finished_at: datetime | None = None, cost: Money | None = None):
        if finished_at is None and cost is not None:
            raise DomainInvariantViolation("У незавершённой поездки нет стоимости")
        if finished_at is not None:
            # правило 3
            if finished_at < started_at:
                raise DomainInvariantViolation("Поездка завершена раньше, чем начата")
            # правило 4
            if cost != Trip._calc_cost(tariff, started_at, finished_at):
                raise DomainInvariantViolation("Стоимость не рассчитана по тарифу")
        self._id = trip_id
        self._scooter_id = scooter_id  # ссылка на самокат по ID
        self._user_id = user_id
        self._tariff = tariff
        self._started_at = started_at
        self._finished_at = finished_at
        self._cost = cost

    @property
    def id(self) -> TripId:
        return self._id

    @property
    def scooter_id(self) -> ScooterId:
        return self._scooter_id

    @property
    def cost(self) -> Money | None:
        return self._cost

    @property
    def is_finished(self) -> bool:
        return self._finished_at is not None

    @staticmethod
    def _calc_cost(tariff: Tariff, started_at: datetime,
                   finished_at: datetime) -> Money:
        # неполная минута считается как полная
        minutes = math.ceil((finished_at - started_at).total_seconds() / 60)
        return tariff.cost(minutes)

    def finish(self, finished_at: datetime) -> Money:
        # правило 5
        if self.is_finished:
            raise DomainInvariantViolation("Поездка уже завершена")
        # правило 3
        if finished_at < self._started_at:
            raise DomainInvariantViolation("Поездка завершена раньше, чем начата")
        # правило 4
        self._cost = Trip._calc_cost(self._tariff, self._started_at, finished_at)
        self._finished_at = finished_at
        return self._cost

    def to_state(self) -> dict:
        return {
            "trip_id": str(self._id.value),
            "scooter_id": str(self._scooter_id.value),
            "user_id": self._user_id.value,
            "price_per_minute": self._tariff.price_per_minute,
            "min_cost": self._tariff.min_cost,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "cost": self._cost.amount if self._cost else None,
        }
