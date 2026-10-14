"""Агрегаты области «Прокат самокатов».

Агрегат Scooter перенесён из лабораторной работы №2 без изменения правил
(нельзя выдать самокат с зарядом ниже порога, нельзя выдать арендованный или
находящийся на обслуживании). Агрегат Trip дополнен жизненным циклом из ЛР3:
текущее состояние хранится объектом паттерна «Состояние» в закрытом атрибуте,
публичного сеттера у него нет, журнал доменных событий доступен только для чтения.
"""

import math
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple

from . import events as ev
from .exceptions import DomainInvariantViolation, TransitionNotAllowed
from .events import DomainEvent
from .states import (AwaitingCapture, CompletedWithDebt, FINAL_STATES, Reserved,
                     TripState)
from .value_objects import (AccountId, BatteryLevel, Money, ScooterId, Tariff,
                            TripId, UserId)

# минимальный заряд для выдачи, % (в задании не указан, взят свой)
MIN_BATTERY = 20

# сумма предварительной авторизации платежа, руб. (из регламента)
AUTH_HOLD = Money(Decimal("300"))


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
        # правило 2 ЛР2
        if self._status is ScooterStatus.RENTED:
            raise DomainInvariantViolation("Самокат уже арендован")
        if self._status is ScooterStatus.MAINTENANCE:
            raise DomainInvariantViolation("Самокат на обслуживании")
        # правило 1 ЛР2
        if self._battery.percent < MIN_BATTERY:
            raise DomainInvariantViolation("Заряд ниже минимального порога")
        self._status = ScooterStatus.RENTED

    def check_can_return(self) -> None:
        # правило 6 ЛР2
        if self._status is not ScooterStatus.RENTED:
            raise DomainInvariantViolation("Самокат не арендован")
        self._status = ScooterStatus.FREE

    def apply_telemetry(self, battery: BatteryLevel) -> None:
        """Обновить заряд по телеметрии (конец поездки)."""
        self._battery = battery

    def to_state(self) -> Dict[str, object]:
        return {
            "scooter_id": str(self._id.value),
            "battery": self._battery.percent,
            "status": self._status.name,
        }


class Trip:  # корень агрегата «поездка» (ключевая сущность ЛР3)
    def __init__(self, trip_id: TripId, scooter_id: ScooterId, user_id: UserId,
                 tariff: Tariff, created_at: datetime,
                 state: Optional[TripState] = None,
                 parking_confirmed: bool = False,
                 captured: bool = False,
                 unlocked: bool = False,
                 billing_started: bool = False,
                 started_at: Optional[datetime] = None,
                 finished_at: Optional[datetime] = None,
                 booking_started_at: Optional[datetime] = None,
                 pauses: Optional[List[Tuple[datetime, Optional[datetime]]]] = None,
                 cost: Optional[Money] = None,
                 events: Optional[List[DomainEvent]] = None):
        if started_at is not None and started_at < created_at:
            # правило 3 ЛР2
            raise DomainInvariantViolation("Поездка начата раньше, чем создана")
        self._id = trip_id
        self._scooter_id = scooter_id  # ссылка на самокат по ID
        self._user_id = user_id
        self._tariff = tariff
        self._created_at = created_at
        self._state: TripState = state if state is not None else Reserved()
        self._parking_confirmed = parking_confirmed
        self._captured = captured
        self._unlocked = unlocked
        self._billing_started = billing_started
        self._started_at = started_at
        self._finished_at = finished_at
        # поездка создаётся уже забронированной, срок брони идёт с этого момента
        self._booking_started_at = (booking_started_at if booking_started_at is not None
                                    else created_at)
        self._pauses: List[Tuple[datetime, Optional[datetime]]] = list(pauses) if pauses else []
        self._cost = cost
        self._events: List[DomainEvent] = list(events) if events else []
        if events is None:
            # свежая поездка: журнал начинается с события бронирования
            self._events.append(ev.TripReserved(trip_id, created_at))
        self._validate_consistency()

    # --- проверки согласованности (вызываются конструктором и фабрикой) ---

    def _validate_consistency(self) -> None:
        """Инварианты жизненного цикла при восстановлении агрегата."""
        if self._cost is not None:
            if not isinstance(self._state, FINAL_STATES + (AwaitingCapture,)):
                raise DomainInvariantViolation(
                    "Стоимость может быть рассчитана только после завершения поездки")
            if self._started_at is None or self._finished_at is None:
                raise DomainInvariantViolation(
                    "У поездки со стоимостью должны быть время начала и окончания")
            if self._cost != self.calculate_cost(self._finished_at):
                # правило 4 ЛР2
                raise DomainInvariantViolation("Стоимость не рассчитана по тарифу")
        elif isinstance(self._state, FINAL_STATES) and self._started_at is not None:
            raise DomainInvariantViolation("У завершённой поездки должна быть стоимость")
        if self._finished_at is not None and self._started_at is None:
            raise DomainInvariantViolation("У завершённой поездки нет времени начала")
        if self._events and self._events[0].occurred_at < self._created_at:
            raise DomainInvariantViolation("Событие журнала раньше создания поездки")
        for start, end in self._pauses:
            if end is not None and end <= start:
                raise DomainInvariantViolation("Пауза закончилась раньше, чем началась")

    # --- свойства (только чтение) ---

    @property
    def id(self) -> TripId:
        return self._id

    @property
    def scooter_id(self) -> ScooterId:
        return self._scooter_id

    @property
    def user_id(self) -> UserId:
        return self._user_id

    @property
    def tariff(self) -> Tariff:
        return self._tariff

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def booking_started_at(self) -> Optional[datetime]:
        return self._booking_started_at

    @property
    def started_at(self) -> Optional[datetime]:
        return self._started_at

    @property
    def finished_at(self) -> Optional[datetime]:
        return self._finished_at

    @property
    def status(self) -> str:
        """Название текущего состояния (причастие)."""
        return self._state.name

    @property
    def state(self) -> TripState:
        return self._state

    @property
    def allowed_events(self) -> frozenset:
        """Какие события сейчас допустимы."""
        return self._state.allowed

    @property
    def events(self) -> tuple:
        return tuple(self._events)

    @property
    def cost(self) -> Optional[Money]:
        return self._cost

    @property
    def pauses(self) -> tuple:
        return tuple(self._pauses)

    @property
    def parking_confirmed(self) -> bool:
        return self._parking_confirmed

    @property
    def is_paused(self) -> bool:
        """Поездка сейчас на паузе."""
        return self.current_pause_open

    @property
    def awaiting_payment(self) -> bool:
        """Стоимость посчитана, ждём ответ банка."""
        return isinstance(self._state, AwaitingCapture)

    @property
    def debt_formed(self) -> bool:
        """Списание не прошло, аккаунт заблокирован."""
        return isinstance(self._state, CompletedWithDebt)

    @property
    def prolonged(self) -> bool:
        """Сработал непрерывающий таймер 12 часов (событие prolong уже записано)."""
        return any(isinstance(event, ev.TripProlonged) for event in self._events)

    @property
    def captured(self) -> bool:
        return self._captured

    @property
    def unlock_confirmed(self) -> bool:
        return self._unlocked

    @property
    def billing_started(self) -> bool:
        return self._billing_started

    @property
    def is_finished(self) -> bool:
        return isinstance(self._state, FINAL_STATES)

    @property
    def current_pause_open(self) -> bool:
        """Открыта ли пауза (последняя пауза ещё не закрыта)."""
        return bool(self._pauses) and self._pauses[-1][1] is None

    # --- методы, используемые классами состояний ---

    def all_branches_completed(self) -> bool:
        """Замок открыт и тариф запущен."""
        return self._unlocked and self._billing_started

    def open_pause(self, now: datetime) -> None:
        if self.current_pause_open:
            raise DomainInvariantViolation("Поездка уже приостановлена")
        if self._started_at is not None and now < self._started_at:
            raise DomainInvariantViolation("Пауза не может начаться раньше начала поездки")
        self._pauses.append((now, None))

    def close_pause(self, now: datetime) -> None:
        if not self.current_pause_open:
            raise DomainInvariantViolation("Поездка не находится на паузе")
        opened = self._pauses[-1][0]
        if now <= opened:
            raise DomainInvariantViolation("Пауза не может закончиться раньше начала")
        self._pauses[-1] = (opened, now)

    def set_cost(self, cost: Money) -> None:
        self._cost = cost

    def calculate_cost(self, finished_at: Optional[datetime],
                       pauses: Optional[List[Tuple[datetime, Optional[datetime]]]] = None) -> Money:
        """Стоимость поездки: движение и пауза раздельно, но не меньше минимальной.

        Неполная минута считается как полная (правило 4 ЛР2).
        """
        if finished_at is None:
            raise DomainInvariantViolation("Неизвестно время окончания поездки")
        if self._started_at is None:
            raise DomainInvariantViolation("Неизвестно время начала поездки")
        if finished_at < self._started_at:
            # правило 3 ЛР2
            raise DomainInvariantViolation("Поездка завершена раньше, чем начата")
        total_seconds = int((finished_at - self._started_at).total_seconds())
        pause_seconds = 0
        for start, end in (pauses if pauses is not None else self._pauses):
            left = max(start, self._started_at)
            right = min(end if end is not None else finished_at, finished_at)
            if right > left:
                pause_seconds += int((right - left).total_seconds())
        active_seconds = max(0, total_seconds - pause_seconds)
        active_minutes = int(math.ceil(active_seconds / 60.0))
        pause_minutes = int(math.ceil(pause_seconds / 60.0))
        return self._tariff.cost(active_minutes, pause_minutes)

    # --- обработка событий: единственный способ изменить состояние ---

    def execute(self, event: str, now: datetime, **kwargs) -> None:
        """Выполнить событие жизненного цикла.

        Новое состояние присваивается только после того, как метод объекта
        состояния вернул результат: если метод выбросил исключение, состояние
        агрегата не меняется.
        """
        handler = getattr(self._state, event, None)
        if handler is None:
            raise TransitionNotAllowed(self._state.name, event)
        next_state = handler(self, now, **kwargs)
        self._state = next_state
        self._record(event, now, **kwargs)

    def _record(self, event: str, now: datetime, **kwargs) -> None:
        trip_id = self._id
        if event == "authorize":
            self._events.append(ev.PaymentAuthorized(trip_id, now, AUTH_HOLD))
        elif event == "decline_authorization":
            self._events.append(ev.AuthorizationDeclined(trip_id, now, kwargs.get("reason", "")))
        elif event == "expire_booking":
            self._events.append(ev.BookingExpired(trip_id, now))
        elif event == "cancel":
            self._events.append(ev.TripCancelled(trip_id, now, kwargs.get("reason", "")))
        elif event == "unlock_confirmed":
            self._unlocked = True
            self._events.append(ev.ScooterUnlockConfirmed(trip_id, now))
        elif event == "billing_started":
            self._billing_started = True
            self._events.append(ev.TariffStarted(trip_id, now))
        elif event == "start":
            self._started_at = now
            self._events.append(ev.TripStarted(trip_id, now))
        elif event == "pause":
            self._events.append(ev.TripPaused(trip_id, now))
        elif event == "resume":
            self._events.append(ev.TripResumed(trip_id, now))
        elif event == "prolong":
            self._events.append(ev.TripProlonged(trip_id, now))
        elif event == "request_finish":
            self._parking_confirmed = True
            self._finished_at = now
            self._events.append(ev.TripFinishRequested(trip_id, now, self._cost or Money.zero()))
        elif event == "capture_succeeded":
            self._captured = True
            self._finished_at = now
            self._events.append(ev.PaymentCaptured(trip_id, now, self._cost or Money.zero()))
        elif event == "capture_failed":
            self._finished_at = now
            self._events.append(ev.AccountBlocked(trip_id, AccountId.of(self._user_id), now,
                                                  self._cost or Money.zero()))
        else:  # pragma: no cover - защита от опечатки в имени события
            raise TransitionNotAllowed(self._state.name, event)

    # --- сериализация ---

    def to_state(self) -> Dict[str, object]:
        return {
            "trip_id": str(self._id.value),
            "scooter_id": str(self._scooter_id.value),
            "user_id": self._user_id.value,
            "price_per_minute": str(self._tariff.price_per_minute),
            "min_cost": str(self._tariff.min_cost),
            "pause_price_per_minute": str(self._tariff.pause_price_per_minute),
            "created_at": self._created_at,
            "booking_started_at": self._booking_started_at,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "pauses": [(start, end) for start, end in self._pauses],
            "parking_confirmed": self._parking_confirmed,
            "unlocked": self._unlocked,
            "billing_started": self._billing_started,
            "captured": self._captured,
            "cost": self._cost.amount if self._cost is not None else None,
            "status": self.status,
            "events": list(self._events),
        }
