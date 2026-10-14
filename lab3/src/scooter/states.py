"""Состояния поездки.

У базового класса есть метод на каждое событие. По умолчанию метод
бросает TransitionNotAllowed. Конкретное состояние переопределяет только
свои переходы и возвращает следующее состояние.

Состояний девять, начальное — «Забронирована», конечных четыре.
Событие prolong (12 часов) оставляет то же состояние и только
пишет уведомление оператору.
"""

from datetime import datetime, timedelta
from typing import Dict, Type

from .exceptions import DomainInvariantViolation, TransitionNotAllowed

# Сроки процесса (регламент аренды самоката)
BOOKING_TIMEOUT = timedelta(minutes=10)   # бронь снимается автоматически
RIDE_PROLONG_AFTER = timedelta(hours=12)  # уведомление оператора


class TripState:
    """Базовое состояние: все события запрещены."""

    name = "Не определено"
    allowed = frozenset()

    def authorize(self, trip, now: datetime, approved: bool):
        raise TransitionNotAllowed(self.name, "authorize")

    def decline_authorization(self, trip, now: datetime, reason: str = ""):
        raise TransitionNotAllowed(self.name, "decline_authorization")

    def expire_booking(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "expire_booking")

    def cancel(self, trip, now: datetime, reason: str = ""):
        raise TransitionNotAllowed(self.name, "cancel")

    def unlock_confirmed(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "unlock_confirmed")

    def billing_started(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "billing_started")

    def start(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "start")

    def pause(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "pause")

    def resume(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "resume")

    def prolong(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "prolong")

    def request_finish(self, trip, now: datetime, parking_confirmed: bool):
        raise TransitionNotAllowed(self.name, "request_finish")

    def capture_succeeded(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "capture_succeeded")

    def capture_failed(self, trip, now: datetime):
        raise TransitionNotAllowed(self.name, "capture_failed")

    def __repr__(self) -> str:  # pragma: no cover - удобство отладки
        return "<{}>".format(type(self).__name__)


class Reserved(TripState):
    """Забронирована. Бронь снимается через 10 минут."""

    name = "Забронирована"
    allowed = frozenset({"authorize", "decline_authorization", "expire_booking", "cancel"})

    def authorize(self, trip, now, approved):
        # отказ в авторизации — нормальный бизнес-исход, срок брони не важен
        if not approved:
            return Declined()
        if now - trip.booking_started_at > BOOKING_TIMEOUT:
            raise DomainInvariantViolation(
                "Срок брони 10 минут истёк, авторизацию подтвердить нельзя")
        return Authorized()

    def decline_authorization(self, trip, now, reason=""):
        return Declined()

    def expire_booking(self, trip, now):
        if now - trip.booking_started_at < BOOKING_TIMEOUT:
            raise DomainInvariantViolation(
                "Бронь ещё действует: 10 минут не истекли")
        return Cancelled()

    def cancel(self, trip, now, reason=""):
        return Cancelled()


class Authorized(TripState):
    """Авторизация выполнена — ожидаем завершения обеих ветвей [T5 ‖ T6]."""

    name = "Авторизация выполнена"
    allowed = frozenset({"unlock_confirmed", "billing_started", "start"})

    def unlock_confirmed(self, trip, now):
        return Authorized()

    def billing_started(self, trip, now):
        return Authorized()

    def start(self, trip, now):
        if not trip.all_branches_completed():
            raise DomainInvariantViolation(
                "Поездка начинается только после разблокировки замка "
                "и запуска тарификации")
        return Active()


class Cancelled(TripState):
    """Отменена — конечное состояние (поездка не начата, списания нет)."""

    name = "Отменена"
    allowed = frozenset()


class Declined(TripState):
    """Отклонена — конечное состояние (авторизация платежа не пройдена)."""

    name = "Отклонена"
    allowed = frozenset()


class Active(TripState):
    """Активна — идёт поездка, тарификация по основной ставке."""

    name = "Активна"
    allowed = frozenset({"pause", "prolong", "request_finish"})

    def pause(self, trip, now):
        if trip.current_pause_open:
            raise DomainInvariantViolation("Поездка уже приостановлена")
        trip.open_pause(now)
        return Paused()

    def prolong(self, trip, now):
        # таймер непрерывающий: состояние не меняется, поездка продолжается
        if now - trip.started_at < RIDE_PROLONG_AFTER:
            raise DomainInvariantViolation("Поездка длится меньше 12 часов")
        return Active()

    def request_finish(self, trip, now, parking_confirmed):
        _require_parking(parking_confirmed)
        _require_pause_closed(trip)
        trip.set_cost(trip.calculate_cost(now))
        return AwaitingCapture()


class Paused(TripState):
    """Приостановлена — пауза тарифицируется по сниженной ставке."""

    name = "Приостановлена"
    allowed = frozenset({"resume", "prolong"})

    def resume(self, trip, now):
        trip.close_pause(now)
        return Active()

    def prolong(self, trip, now):
        # таймер непрерывающий: состояние не меняется, поездка продолжается
        if now - trip.started_at < RIDE_PROLONG_AFTER:
            raise DomainInvariantViolation("Поездка длится меньше 12 часов")
        return Paused()


class AwaitingCapture(TripState):
    """Ожидает списания — стоимость рассчитана, ждём результат списания."""

    name = "Ожидает списания"
    allowed = frozenset({"capture_succeeded", "capture_failed"})

    def capture_succeeded(self, trip, now):
        return CompletedPaid()

    def capture_failed(self, trip, now):
        return CompletedWithDebt()


class CompletedPaid(TripState):
    """Завершена с оплатой — конечное состояние успешного исхода."""

    name = "Завершена с оплатой"
    allowed = frozenset()


class CompletedWithDebt(TripState):
    """Завершена с задолженностью — конечное состояние, аккаунт заблокирован."""

    name = "Завершена с задолженностью"
    allowed = frozenset()


def _require_parking(parking_confirmed: bool) -> None:
    """Завершить поездку можно только в разрешённой зоне."""
    if not parking_confirmed:
        raise DomainInvariantViolation(
            "Завершить поездку можно только в разрешённой зоне парковки")


def _require_pause_closed(trip) -> None:
    if trip.current_pause_open:
        raise DomainInvariantViolation(
            "Перед завершением поездки нужно продолжить тарификацию (снять паузу)")


# Реестр состояний: имя класса -> сам класс. Нужен для восстановления агрегата
# из сохранённого состояния (TripFactory.restore).
STATES: Dict[str, Type[TripState]] = {
    cls.__name__: cls
    for cls in (
        Reserved, Authorized, Cancelled, Declined, Active, Paused,
        AwaitingCapture, CompletedPaid, CompletedWithDebt,
    )
}

FINAL_STATES = (Cancelled, Declined, CompletedPaid, CompletedWithDebt)


def initial_state() -> TripState:
    return Reserved()
