"""Доменные события области «Прокат самокатов».

Каждое событие — неизменяемый dataclass (frozen=True), фиксирующий факт,
значимый для предметной области. События называются глаголом в прошедшем
времени и накапливаются в журнале агрегата Trip.
"""

from dataclasses import dataclass, field
from datetime import datetime

from .value_objects import AccountId, Money, TripId


class DomainEvent:
    """Базовый класс доменного события: фиксирует факт, значимый для области."""

    occurred_at: datetime


@dataclass(frozen=True)
class TripReserved(DomainEvent):
    trip_id: TripId
    occurred_at: datetime


@dataclass(frozen=True)
class PaymentAuthorized(DomainEvent):
    trip_id: TripId
    occurred_at: datetime
    hold_amount: Money = field(default_factory=Money.zero)


@dataclass(frozen=True)
class BookingExpired(DomainEvent):
    trip_id: TripId
    occurred_at: datetime


@dataclass(frozen=True)
class AuthorizationDeclined(DomainEvent):
    trip_id: TripId
    occurred_at: datetime
    reason: str = ""


@dataclass(frozen=True)
class TripCancelled(DomainEvent):
    trip_id: TripId
    occurred_at: datetime
    reason: str = ""


@dataclass(frozen=True)
class ScooterUnlockConfirmed(DomainEvent):
    trip_id: TripId
    occurred_at: datetime


@dataclass(frozen=True)
class TariffStarted(DomainEvent):
    trip_id: TripId
    occurred_at: datetime


@dataclass(frozen=True)
class TripStarted(DomainEvent):
    trip_id: TripId
    occurred_at: datetime


@dataclass(frozen=True)
class TripPaused(DomainEvent):
    trip_id: TripId
    occurred_at: datetime


@dataclass(frozen=True)
class TripResumed(DomainEvent):
    trip_id: TripId
    occurred_at: datetime


@dataclass(frozen=True)
class TripProlonged(DomainEvent):
    """Уведомление оператора: поездка длится более 12 часов (непрерывающий таймер)."""

    trip_id: TripId
    occurred_at: datetime


@dataclass(frozen=True)
class TripFinishRequested(DomainEvent):
    trip_id: TripId
    occurred_at: datetime
    cost: Money = field(default_factory=Money.zero)


@dataclass(frozen=True)
class PaymentCaptured(DomainEvent):
    trip_id: TripId
    occurred_at: datetime
    amount: Money = field(default_factory=Money.zero)


@dataclass(frozen=True)
class AccountBlocked(DomainEvent):
    """Списание не прошло: у клиента образовалась задолженность, аккаунт заблокирован."""

    trip_id: TripId
    account_id: AccountId
    occurred_at: datetime
    debt: Money = field(default_factory=Money.zero)
