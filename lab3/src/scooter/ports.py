"""Порты (Protocol) для внешних участников процесса.

Внешние участники подключаются к прикладному слою через порт — описание нужных
операций. Доменный и прикладной слои зависят только от этих протоколов, конкретные
реализации лежат в пакете web и в заглушках stubs.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .value_objects import BatteryLevel, Money


class Clock(Protocol):
    """Часы приложения: текущее время получают только через этот порт."""

    def now(self) -> datetime: ...


@dataclass(frozen=True)
class AuthorizationResult:
    """Ответ банка на авторизацию 300 рублей."""

    approved: bool
    hold: Money
    reason: str = ""


@dataclass(frozen=True)
class CaptureResult:
    """Ответ банка на списание стоимости."""

    succeeded: bool
    amount: Money
    reason: str = ""


@dataclass(frozen=True)
class LockResult:
    """Ответ замка: открылся или нет."""

    unlocked: bool
    reason: str = ""


@dataclass(frozen=True)
class BatteryReading:
    """Телеметрия заряда самоката на момент завершения поездки."""

    level: BatteryLevel
    measured_at: datetime


@dataclass(frozen=True)
class ParkingStatus:
    """Самокат в разрешённой зоне или нет."""

    in_allowed_zone: bool
    zone: str = ""


class PaymentGateway(Protocol):
    """Порт внешнего платёжного сервиса (дорожка «Платёжный сервис»)."""

    def authorize(self, user_id: str, amount: Money) -> AuthorizationResult: ...
    def capture(self, user_id: str, amount: Money) -> CaptureResult: ...


class LockGateway(Protocol):
    """Порт замка и телеметрии самоката (дорожка «Замок и телеметрия»)."""

    def unlock(self, scooter_id: str) -> LockResult: ...
    def lock(self, scooter_id: str) -> LockResult: ...
    def read_battery(self, scooter_id: str) -> BatteryReading: ...


class ParkingZoneChecker(Protocol):
    """Порт проверки разрешённой зоны парковки."""

    def check(self, scooter_id: str, latitude: float, longitude: float) -> ParkingStatus: ...
