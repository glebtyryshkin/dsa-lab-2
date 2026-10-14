"""Проверки сервиса аренды."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.process import RentalProcess
from src.scooter.account import InMemoryAccountRepository
from src.scooter.aggregates import ScooterStatus
from src.scooter.exceptions import (DomainInvariantViolation, EntityNotFound,
                                    GatewayUnresolved, TransitionNotAllowed)
from src.scooter.factories import TripFactory
from src.scooter.repositories import (InMemoryScooterRepository,
                                      InMemoryTripRepository)
from src.scooter.stubs import (FakeLockGateway, FakeParkingZoneChecker,
                               FakePaymentGateway, FixedClock)
from src.scooter.value_objects import (AccountId, BatteryLevel, Money, ScooterId,
                                       Tariff, UserId)

T0 = datetime(2026, 10, 1, 10, 0)
TARIFF = Tariff(Decimal("10"), Decimal("100"), Decimal("3"))
USER = UserId("user-1")


class Rig:
    """Стенд: часы, репозитории в памяти, заглушки внешних участников."""

    def __init__(self, battery: int = 80, battery_after: int = 60,
                 approve: bool = True, capture_ok: bool = True,
                 in_zone: bool = True):
        self.clock = FixedClock(T0)
        self.scooters = InMemoryScooterRepository()
        self.trips = InMemoryTripRepository()
        self.accounts = InMemoryAccountRepository()
        self.payments = FakePaymentGateway(approve_authorization=approve,
                                           capture_succeeds=capture_ok)
        self.locks = FakeLockGateway(battery_after=battery_after)
        self.zones = FakeParkingZoneChecker(in_allowed_zone=in_zone)
        self.process = RentalProcess(self.clock, self.scooters, self.trips,
                                     self.accounts, self.payments, self.locks,
                                     self.zones)
        self.scooter = self.process.register_scooter(ScooterId.new(),
                                                     BatteryLevel(battery))

    def trip(self, user: UserId = USER):
        return self.process.create_rental(self.scooter.id, user, TARIFF)

    def started_trip(self, user: UserId = USER):
        return self.process.start_rental(self.trip(user).id)


# --- путь P1: основной путь ---

def test_path_p1_main():
    rig = Rig()
    trip = rig.started_trip()
    assert trip.status == "Активна"
    assert rig.scooter.status is ScooterStatus.RENTED
    rig.clock.advance(timedelta(minutes=25))
    trip = rig.process.finish_rental(trip.id, 55.0, 82.0, True, "photo-1")
    assert trip.status == "Завершена с оплатой"
    assert trip.cost == Money(Decimal("250"))  # 25 мин * 10 руб.
    assert rig.scooter.status is ScooterStatus.FREE
    assert rig.scooter.battery == BatteryLevel(60)
    assert [type(e).__name__ for e in trip.events] == [
        "TripReserved", "PaymentAuthorized", "TariffStarted",
        "ScooterUnlockConfirmed", "TripStarted", "TripFinishRequested",
        "PaymentCaptured"]


def test_parallel_branches_sequence():
    """Тариф и замок делаются по очереди, старт — после обоих."""
    rig = Rig()
    trip = rig.trip()
    rig.process.start_rental(trip.id)
    assert rig.locks.unlock_calls == [str(rig.scooter.id.value)]
    assert trip.unlock_confirmed and trip.billing_started
    assert trip.status == "Активна"
    assert trip.started_at == T0


# --- путь P2: задолженность ---

def test_path_p2_debt_blocks_rental():
    rig = Rig()
    account = rig.accounts.get(AccountId.of(USER))
    account.charge_failed(Money(Decimal("50")))
    rig.accounts.save(account)
    trip = rig.process.start_rental(rig.trip().id)
    assert trip.status == "Отменена"
    assert rig.scooter.status is ScooterStatus.FREE
    assert rig.process.customer_notices[-1]["outcome"] == "есть долг"
    assert rig.payments.authorize_calls == []  # авторизация не запрашивалась


# --- путь P3: истечение брони (таймер 10 минут) ---

def test_path_p3_booking_expired_on_boundary():
    rig = Rig()
    trip = rig.trip()
    rig.clock.advance(timedelta(minutes=9, seconds=59))
    assert rig.process.on_booking_expired(trip.id, rig.clock.now()) is False
    assert trip.status == "Забронирована"
    rig.clock.advance(timedelta(seconds=1))  # ровно 10 минут
    assert rig.process.on_booking_expired(trip.id, rig.clock.now()) is True
    assert trip.status == "Отменена"
    assert rig.scooter.status is ScooterStatus.FREE


def test_path_p3_via_tick():
    rig = Rig()
    trip = rig.trip()
    rig.clock.advance(timedelta(minutes=10))
    processed = rig.process.on_tick()
    assert processed == [{"trip_id": str(trip.id.value),
                          "event": "BookingExpired", "outcome": "бронь снята"}]
    assert trip.status == "Отменена"


# --- путь P4: авторизация отклонена ---

def test_path_p4_authorization_declined():
    rig = Rig(approve=False)
    trip = rig.process.start_rental(rig.trip().id)
    assert trip.status == "Отклонена"
    assert rig.scooter.status is ScooterStatus.FREE
    assert [type(e).__name__ for e in trip.events] == [
        "TripReserved", "AuthorizationDeclined"]
    with pytest.raises(TransitionNotAllowed):
        trip.execute("start", T0)


# --- путь P5: цикл «вне зоны парковки» ---

def test_path_p5_parking_cycle():
    rig = Rig(in_zone=False)
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=5))
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip.status == "Активна"      # завершить нельзя, поездка продолжается
    assert trip.cost is None
    rig.zones.in_allowed_zone = True
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip.status == "Завершена с оплатой"
    assert trip.cost == Money(Decimal("100"))  # 5 минут: меньше минимальной стоимости


# --- путь P6: пауза и продолжение ---

def test_path_p6_pause_and_resume_cost():
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=10))
    rig.process.pause_ride(trip.id)
    assert trip.status == "Приостановлена"
    rig.clock.advance(timedelta(minutes=20))
    rig.process.resume_ride(trip.id)
    assert trip.status == "Активна"
    rig.clock.advance(timedelta(minutes=5))
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    # 15 минут движения по 10 руб. + 20 минут паузы по 3 руб.
    assert trip.cost == Money(Decimal("210"))
    assert [type(e).__name__ for e in trip.events] == [
        "TripReserved", "PaymentAuthorized", "TariffStarted",
        "ScooterUnlockConfirmed", "TripStarted", "TripPaused", "TripResumed",
        "TripFinishRequested", "PaymentCaptured"]


def test_pause_twice_is_rejected():
    """Повторная пауза — недопустимое событие (409), а не нарушение инварианта."""
    rig = Rig()
    trip = rig.started_trip()
    rig.process.pause_ride(trip.id)
    with pytest.raises(TransitionNotAllowed, match="pause"):
        rig.process.pause_ride(trip.id)
    assert trip.status == "Приостановлена"


def test_finish_while_paused_is_rejected():
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=5))
    rig.process.pause_ride(trip.id)
    with pytest.raises(TransitionNotAllowed, match="request_finish"):
        rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip.status == "Приостановлена"


# --- пути P7/P8: непрерывающий таймер 12 часов ---

def test_path_p7_twelve_hours_notifies_operator():
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(hours=11, minutes=59, seconds=59))
    assert rig.process.on_trip_prolonged(trip.id, rig.clock.now()) is False
    assert trip.status == "Активна"
    rig.clock.advance(timedelta(seconds=1))  # ровно 12 часов
    assert rig.process.on_trip_prolonged(trip.id, rig.clock.now()) is True
    # таймер непрерывающий: состояние не меняется, поездка продолжается
    assert trip.status == "Активна"
    assert [type(e).__name__ for e in trip.events][-1] == "TripProlonged"
    assert len(rig.process.operator_notices) == 1
    # поездка продолжается: процесс не завершён
    assert "request_finish" in trip.allowed_events
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip.status == "Завершена с оплатой"


def test_path_p8_twelve_hours_then_failed_capture():
    rig = Rig(capture_ok=False)
    trip = rig.started_trip()
    rig.clock.advance(timedelta(hours=12))
    assert rig.process.on_trip_prolonged(trip.id, rig.clock.now()) is True
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip.status == "Завершена с задолженностью"
    account = rig.accounts.get(AccountId.of(USER))
    assert account.blocked is True
    assert account.debt == trip.cost


# --- неудачное списание ---

def test_capture_failure_blocks_account():
    rig = Rig(capture_ok=False)
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=7))
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip.status == "Завершена с задолженностью"
    account = rig.accounts.get(AccountId.of(USER))
    assert account.debt == Money(Decimal("100"))
    assert rig.process.customer_notices[-1]["outcome"] == "долг"
    # поездка закончилась физически: самокат возвращён в фонд и заблокирован,
    # а непогашенным остаётся долг клиента
    assert rig.scooter.status is ScooterStatus.FREE
    assert rig.locks.lock_calls == [str(rig.scooter.id.value)]


def test_blocked_account_cannot_start_new_ride():
    rig = Rig(capture_ok=False)
    rig.accounts.save(rig.accounts.get(AccountId.of(USER)))
    account = rig.process.get_account(AccountId.of(USER))
    account.charge_failed(Money(Decimal("100")))
    rig.accounts.save(account)
    trip = rig.process.start_rental(rig.trip().id)
    assert trip.status == "Отменена"


# --- инварианты ЛР2 в новом жизненном цикле ---

def test_low_battery_scooter_not_rented():
    rig = Rig(battery=19)
    with pytest.raises(DomainInvariantViolation, match="Заряд ниже"):
        rig.trip()
    assert rig.scooter.status is ScooterStatus.FREE


def test_battery_20_is_enough():
    rig = Rig(battery=20)
    trip = rig.trip()
    assert trip.status == "Забронирована"


def test_maintenance_scooter_not_rented():
    rig = Rig()
    from src.scooter.aggregates import Scooter
    broken = Scooter(ScooterId.new(), BatteryLevel(90), ScooterStatus.MAINTENANCE)
    rig.scooters.save(broken)
    with pytest.raises(DomainInvariantViolation, match="на обслуживании"):
        rig.process.create_rental(broken.id, USER, TARIFF)


def test_trip_not_found():
    rig = Rig()
    from src.scooter.value_objects import TripId
    with pytest.raises(EntityNotFound, match="Поездка не найдена"):
        rig.process.get_trip(TripId.new())


def test_finish_before_start_is_rejected():
    """Правило 3 ЛР2: поездка не может быть завершена раньше, чем начата."""
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=5))
    # стоимость считается от времени начала: раньше начала — нарушение правила 3
    with pytest.raises(DomainInvariantViolation, match="раньше, чем начата"):
        trip.calculate_cost(trip.started_at - timedelta(minutes=1))


def test_photo_is_required():
    rig = Rig()
    trip = rig.started_trip()
    with pytest.raises(DomainInvariantViolation, match="фотография"):
        rig.process.finish_rental(trip.id, 1.0, 1.0, True, "   ")


# --- шлюзы и GatewayUnresolved ---

def test_gateway_unresolved_when_predicates_incomplete(monkeypatch):
    """Вне зоны и без ответа зоны завершение невозможно."""
    import src.process as proc_module
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=3))
    # данные о зоне не получены: оба предиката шлюза вернули False
    monkeypatch.setattr(proc_module, "is_parking_allowed", lambda status: False)
    monkeypatch.setattr(proc_module, "is_parking_denied", lambda status: False)
    with pytest.raises(GatewayUnresolved, match="разрешённой зоне"):
        rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip.status == "Активна"


def test_gateway_unresolved_on_debt_check(monkeypatch):
    """Долг нельзя ни подтвердить, ни опровергнуть."""
    import src.process as proc_module
    rig = Rig()
    trip = rig.trip()
    monkeypatch.setattr(proc_module, "has_debt", lambda account: False)
    monkeypatch.setattr(proc_module, "no_debt", lambda account: False)
    with pytest.raises(GatewayUnresolved, match="Задолженность отсутствует?"):
        rig.process.ensure_no_debt(trip.id)
    assert trip.status == "Забронирована"


def test_gateway_unresolved_on_capture(monkeypatch):
    """Списание нельзя признать ни успешным, ни неуспешным."""
    import src.process as proc_module
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=3))
    monkeypatch.setattr(proc_module, "is_capture_succeeded", lambda result: False)
    monkeypatch.setattr(proc_module, "is_capture_failed", lambda result: False)
    with pytest.raises(GatewayUnresolved, match="Списание прошло?"):
        rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")


# --- восстановление агрегата ---

def test_restore_finished_trip():
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=25))
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    restored = TripFactory.restore(trip.to_state())
    assert restored.status == "Завершена с оплатой"
    assert restored.cost == Money(Decimal("250"))
    assert restored.id == trip.id
    assert len(restored.events) == len(trip.events)


def test_restore_active_trip():
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=4))
    rig.process.pause_ride(trip.id)
    restored = TripFactory.restore(trip.to_state())
    assert restored.status == "Приостановлена"
    assert restored.is_paused is True


def test_restore_with_wrong_cost_rejected():
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=25))
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    state = trip.to_state()
    state["cost"] = Decimal("7")
    with pytest.raises(DomainInvariantViolation, match="не рассчитана по тарифу"):
        TripFactory.restore(state)


# --- доменные события ---

def test_domain_events_are_immutable():
    rig = Rig()
    trip = rig.started_trip()
    event = trip.events[0]
    with pytest.raises(Exception):
        event.occurred_at = T0  # frozen dataclass
    assert type(event).__name__ == "TripReserved"


def test_tick_does_not_break_finished_trips():
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=5))
    rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    rig.clock.advance(timedelta(hours=13))
    assert rig.process.on_tick() == []
    assert trip.status == "Завершена с оплатой"


# --- чем закончилась поездка ---

def test_trip_outcome():
    from src.process import trip_outcome

    rig = Rig()
    trip = rig.started_trip()
    assert trip_outcome(trip) is None          # поездка ещё идёт
    rig.clock.advance(timedelta(minutes=5))
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip_outcome(trip) == "оплачено"

    rig = Rig(approve=False)
    trip = rig.process.start_rental(rig.trip().id)
    assert trip_outcome(trip) == "отказ банка"

    rig = Rig()
    account = rig.accounts.get(AccountId.of(USER))
    account.charge_failed(Money(Decimal("50")))
    rig.accounts.save(account)
    trip = rig.process.start_rental(rig.trip().id)
    assert trip_outcome(trip) == "есть долг"

    rig = Rig()
    trip = rig.trip()
    rig.clock.advance(timedelta(minutes=10))
    rig.process.on_tick()
    assert trip_outcome(trip) == "бронь снята"

    rig = Rig(capture_ok=False)
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=5))
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip_outcome(trip) == "долг"


def test_state_predicates_are_computable():
    """Свойства-предикаты агрегата вычисляются (не только хранимые признаки)."""
    rig = Rig(capture_ok=False)
    trip = rig.started_trip()
    assert trip.all_branches_completed() is True
    assert trip.is_paused is False
    assert trip.awaiting_payment is False
    assert trip.debt_formed is False
    assert trip.prolonged is False
    rig.clock.advance(timedelta(minutes=5))
    trip = rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    assert trip.debt_formed is True
    assert trip.is_finished is True

    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(hours=12))
    rig.process.on_trip_prolonged(trip.id, rig.clock.now())
    assert trip.prolonged is True
    assert trip.status == "Активна"


def test_start_after_finish_is_rejected():
    """Повторный старт завершённой поездки — недопустимое событие (409)."""
    rig = Rig()
    trip = rig.started_trip()
    rig.clock.advance(timedelta(minutes=5))
    rig.process.finish_rental(trip.id, 1.0, 1.0, True, "photo")
    with pytest.raises(TransitionNotAllowed, match="start"):
        rig.process.start_rental(trip.id)
