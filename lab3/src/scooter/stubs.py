"""Заглушки внешних участников, хранящие данные в памяти (задачи 3.2 и 4.2).

Используются тестами и приложением: внешний участник (платёжный сервис, замок
самоката, зона парковки) реализован как порт и заглушка в памяти.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from .exceptions import ClockMovedBack
from .ports import (AuthorizationResult, BatteryReading, CaptureResult, LockResult,
                    ParkingStatus)
from .value_objects import BatteryLevel, Money


class FixedClock:
    """Часы с фиксированным временем — для тестов доменного и прикладного слоя."""

    def __init__(self, moment: datetime):
        self._now = moment

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta

    def set(self, moment: datetime) -> None:
        if moment < self._now:
            raise ClockMovedBack("Перевод часов назад запрещён")
        self._now = moment


class FakePaymentGateway:
    """Заглушка платёжного сервиса: сценарии ответа задаются снаружи."""

    def __init__(self, approve_authorization: bool = True,
                 capture_succeeds: bool = True,
                 hold: Optional[Money] = None):
        self.approve_authorization = approve_authorization
        self.capture_succeeds = capture_succeeds
        self.hold = hold if hold is not None else Money(Decimal("300"))
        self.authorize_calls: List[Tuple[str, Money]] = []
        self.capture_calls: List[Tuple[str, Money]] = []

    def authorize(self, user_id: str, amount: Money) -> AuthorizationResult:
        self.authorize_calls.append((user_id, amount))
        if not self.approve_authorization:
            return AuthorizationResult(False, Money.zero(), "Недостаточно средств")
        return AuthorizationResult(True, self.hold)

    def capture(self, user_id: str, amount: Money) -> CaptureResult:
        self.capture_calls.append((user_id, amount))
        if not self.capture_succeeds:
            return CaptureResult(False, amount, "Списание отклонено банком")
        return CaptureResult(True, amount)


class FakeLockGateway:
    """Заглушка замка и телеметрии самоката."""

    def __init__(self, battery_after: int = 70, unlock_succeeds: bool = True):
        self.battery_after = battery_after
        self.unlock_succeeds = unlock_succeeds
        self.unlock_calls: List[str] = []
        self.lock_calls: List[str] = []

    def unlock(self, scooter_id: str) -> LockResult:
        self.unlock_calls.append(scooter_id)
        if not self.unlock_succeeds:
            return LockResult(False, "Замок не отвечает")
        return LockResult(True)

    def lock(self, scooter_id: str) -> LockResult:
        self.lock_calls.append(scooter_id)
        return LockResult(True)

    def read_battery(self, scooter_id: str) -> BatteryReading:
        return BatteryReading(BatteryLevel(self.battery_after), datetime(2026, 10, 1, 0, 0))


class FakeParkingZoneChecker:
    """Заглушка проверки зоны парковки: зона задаётся снаружи."""

    def __init__(self, in_allowed_zone: bool = True, zone: str = "Зона А"):
        self.in_allowed_zone = in_allowed_zone
        self.zone = zone
        self.calls: List[Tuple[str, float, float]] = []

    def check(self, scooter_id: str, latitude: float, longitude: float) -> ParkingStatus:
        self.calls.append((scooter_id, latitude, longitude))
        return ParkingStatus(self.in_allowed_zone, self.zone)
