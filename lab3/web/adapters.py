"""Часы приложения и заглушки банка, замка и зоны парковки.

datetime.now() вызывается только здесь: часы показывают системное время
со сдвигом, назад их перевести нельзя.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List

from src.scooter.exceptions import ClockMovedBack
from src.scooter.ports import (AuthorizationResult, BatteryReading, CaptureResult,
                               LockResult, ParkingStatus)
from src.scooter.value_objects import BatteryLevel, Money


class ShiftedClock:
    """Часы приложения: системное время со сдвигом, только вперёд.

    Допустимость перевода проверяется по максимальному установленному моменту
    (high-water mark): часы приложения никогда не могут показывать время раньше
    уже пройденного. Сравнение с «текущим» системным временем для этого не
    годится — между двумя вызовами now() проходит несколько микросекунд.
    """

    def __init__(self, offset: timedelta = timedelta(0)):
        self._offset = offset
        self._high_water = self.now()

    def now(self) -> datetime:
        return datetime.now() + self._offset

    @property
    def offset(self) -> timedelta:
        return self._offset

    def move_to(self, moment: datetime) -> None:
        """Установить часы на указанный момент. Перевод назад запрещён."""
        if moment < self._high_water:
            raise ClockMovedBack(
                "Перевод часов приложения назад запрещён: {} < {}".format(
                    moment.isoformat(), self._high_water.isoformat()))
        self._offset = moment - datetime.now()
        self._high_water = moment


class LockedClock:
    """Часы для тестов: время задаётся вручную, начало отсчёта — при создании."""

    def __init__(self, start: datetime):
        self._start = start
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return self._start + self._offset

    def advance(self, delta: timedelta) -> None:
        self._offset += delta

    def move_to(self, moment: datetime) -> None:
        if moment < self.now():
            raise ClockMovedBack("Перевод часов приложения назад запрещён")
        self._offset = moment - self._start


class InMemoryParkingZoneChecker:
    """Заглушка проверки зоны парковки, данные хранятся в памяти."""

    def __init__(self, in_allowed_zone: bool = True, zone: str = "Зона А"):
        self.in_allowed_zone = in_allowed_zone
        self.zone = zone
        self.calls: List = []

    def check(self, scooter_id: str, latitude: float, longitude: float) -> ParkingStatus:
        self.calls.append((scooter_id, latitude, longitude))
        return ParkingStatus(self.in_allowed_zone, self.zone)


class InMemoryLockGateway:
    """Заглушка замка и телеметрии: состояние замков хранится в памяти."""

    def __init__(self, battery_after: int = 70):
        self.battery_after = battery_after
        self.unlocked: Dict[str, bool] = {}

    def unlock(self, scooter_id: str) -> LockResult:
        self.unlocked[scooter_id] = True
        return LockResult(True)

    def lock(self, scooter_id: str) -> LockResult:
        self.unlocked[scooter_id] = False
        return LockResult(True)

    def read_battery(self, scooter_id: str) -> BatteryReading:
        return BatteryReading(BatteryLevel(self.battery_after), datetime.now())


class InMemoryPaymentGateway:
    """Заглушка платёжного сервиса, операции хранятся в памяти.

    Поведение задаётся PAYMENT_APPROVE_AUTH и PAYMENT_CAPTURE_OK,
    чтобы проверить отказ банка и неуспешное списание без правки кода.
    """

    def __init__(self, approve_authorization: bool = True, capture_ok: bool = True,
                 hold: Money = None):
        self.approve_authorization = approve_authorization
        self.capture_ok = capture_ok
        self.hold = hold if hold is not None else Money(Decimal("300"))
        self.operations: List[Dict[str, object]] = []

    def authorize(self, user_id: str, amount: Money) -> AuthorizationResult:
        self.operations.append({"kind": "authorize", "user_id": user_id,
                                "amount": str(amount.amount),
                                "approved": self.approve_authorization})
        if not self.approve_authorization:
            return AuthorizationResult(False, Money.zero(), "Недостаточно средств")
        return AuthorizationResult(True, self.hold)

    def capture(self, user_id: str, amount: Money) -> CaptureResult:
        self.operations.append({"kind": "capture", "user_id": user_id,
                                "amount": str(amount.amount),
                                "succeeded": self.capture_ok})
        if not self.capture_ok:
            return CaptureResult(False, amount, "Списание отклонено банком")
        return CaptureResult(True, amount)
