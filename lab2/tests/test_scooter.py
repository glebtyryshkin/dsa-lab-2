from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.scooter.aggregates import Scooter, ScooterStatus, Trip
from src.scooter.exceptions import DomainInvariantViolation, InvalidValueObject
from src.scooter.factories import TripFactory
from src.scooter.repositories import InMemoryScooterRepository, InMemoryTripRepository
from src.scooter.services import FinishTripService, StartTripService
from src.scooter.value_objects import (BatteryLevel, Money, ScooterId, Tariff, TripId,
                                       UserId)

TARIFF = Tariff(Decimal("10"), Decimal("100"))
START = datetime(2026, 9, 28, 10, 0)


def make_trip():
    return Trip(TripId.new(), ScooterId.new(), UserId("user-1"), TARIFF, START)


# --- инварианты агрегатов ---

def test_low_battery():
    # правило 1: заряд ниже 20 %
    scooter = Scooter(ScooterId.new(), BatteryLevel(19))
    with pytest.raises(DomainInvariantViolation, match="Заряд ниже"):
        scooter.rent_out()


def test_battery_20_is_enough():
    # правило 1: ровно 20 % - можно выдать
    scooter = Scooter(ScooterId.new(), BatteryLevel(20))
    scooter.rent_out()
    assert scooter.status is ScooterStatus.RENTED


def test_rented_or_maintenance():
    # правило 2
    rented = Scooter(ScooterId.new(), BatteryLevel(80), ScooterStatus.RENTED)
    repair = Scooter(ScooterId.new(), BatteryLevel(80), ScooterStatus.MAINTENANCE)
    with pytest.raises(DomainInvariantViolation, match="уже арендован"):
        rented.rent_out()
    with pytest.raises(DomainInvariantViolation, match="на обслуживании"):
        repair.rent_out()


def test_finish_before_start():
    # правило 3
    trip = make_trip()
    with pytest.raises(DomainInvariantViolation, match="раньше, чем начата"):
        trip.finish(START - timedelta(minutes=5))


def test_cost_by_tariff():
    # правило 4: 25 мин по 10 руб., короткая поездка - по минимуму
    long_trip = make_trip()
    short_trip = make_trip()
    assert long_trip.finish(START + timedelta(minutes=25)) == Money(Decimal("250"))
    assert short_trip.finish(START + timedelta(minutes=3)) == Money(Decimal("100"))


def test_minute_rounded_up():
    # правило 4: 25 мин 1 с считается как 26 мин
    trip = make_trip()
    assert trip.finish(START + timedelta(minutes=25, seconds=1)) == Money(Decimal("260"))


def test_finish_twice():
    # правило 5
    trip = make_trip()
    trip.finish(START + timedelta(minutes=15))
    with pytest.raises(DomainInvariantViolation, match="уже завершена"):
        trip.finish(START + timedelta(minutes=20))


def test_return_not_rented():
    # правило 6
    scooter = Scooter(ScooterId.new(), BatteryLevel(80))
    with pytest.raises(DomainInvariantViolation, match="не арендован"):
        scooter.return_from_trip(BatteryLevel(70))


# --- объекты-значения ---

def test_battery_range():
    # правило 7
    with pytest.raises(InvalidValueObject, match="от 0 до 100"):
        BatteryLevel(101)


def test_tariff_invalid():
    # правило 8
    with pytest.raises(InvalidValueObject, match="больше нуля"):
        Tariff(Decimal("0"), Decimal("100"))


# --- доменные сервисы ---

def setup_services(scooter):
    scooters = InMemoryScooterRepository()
    trips = InMemoryTripRepository()
    scooters.save(scooter)
    return StartTripService(scooters, trips), FinishTripService(scooters, trips), trips


def test_trip_full():
    scooter = Scooter(ScooterId.new(), BatteryLevel(80))
    start, finish, _ = setup_services(scooter)
    trip = start.start(scooter.id, UserId("user-1"), TARIFF, START)
    assert scooter.status is ScooterStatus.RENTED
    cost = finish.finish(trip.id, START + timedelta(minutes=25), BatteryLevel(60))
    assert cost == Money(Decimal("250"))
    assert scooter.status is ScooterStatus.FREE
    assert scooter.battery == BatteryLevel(60)


def test_start_low_battery():
    scooter = Scooter(ScooterId.new(), BatteryLevel(10))
    start, _, _ = setup_services(scooter)
    with pytest.raises(DomainInvariantViolation, match="Заряд ниже"):
        start.start(scooter.id, UserId("user-1"), TARIFF, START)
    assert scooter.status is ScooterStatus.FREE


def test_finish_when_scooter_not_rented():
    scooter = Scooter(ScooterId.new(), BatteryLevel(80))
    _, finish, trips = setup_services(scooter)
    trip = Trip(TripId.new(), scooter.id, UserId("user-1"), TARIFF, START)
    trips.save(trip)
    with pytest.raises(DomainInvariantViolation, match="не арендован"):
        finish.finish(trip.id, START + timedelta(minutes=10), BatteryLevel(70))
    assert not trip.is_finished


# --- восстановление ---

def test_restore_trip():
    trip = make_trip()
    trip.finish(START + timedelta(minutes=25))
    restored = TripFactory.restore(trip.to_state())
    assert restored.id == trip.id
    assert restored.scooter_id == trip.scooter_id
    assert restored.is_finished
    assert restored.cost == Money(Decimal("250"))


def test_restore_trip_wrong_cost():
    trip = make_trip()
    trip.finish(START + timedelta(minutes=25))
    state = trip.to_state()
    state["cost"] = Decimal("50")
    with pytest.raises(DomainInvariantViolation, match="не рассчитана по тарифу"):
        TripFactory.restore(state)
