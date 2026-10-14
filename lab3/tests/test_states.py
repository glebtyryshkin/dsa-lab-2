"""Проверки состояний поездки."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.scooter.aggregates import Trip
from src.scooter.exceptions import DomainInvariantViolation, TransitionNotAllowed
from src.scooter.states import STATES, TripState
from src.scooter.value_objects import ScooterId, Tariff, TripId, UserId

T0 = datetime(2026, 10, 1, 10, 0)
TARIFF = Tariff(Decimal("10"), Decimal("100"), Decimal("3"))

# Таблица переходов. Ключ — состояние, значение —
# отображение «событие -> целевое состояние»; недопустимые переходы не указаны.
TRANSITION_TABLE = {
    "Забронирована": {
        "authorize": "Авторизация выполнена",
        "decline_authorization": "Отклонена",
        "expire_booking": "Отменена",
        "cancel": "Отменена",
    },
    "Авторизация выполнена": {
        "unlock_confirmed": "Авторизация выполнена",
        "billing_started": "Авторизация выполнена",
        "start": "Активна",
    },
    "Отменена": {},
    "Отклонена": {},
    "Активна": {
        "pause": "Приостановлена",
        "prolong": "Активна",
        "request_finish": "Ожидает списания",
    },
    "Приостановлена": {
        "resume": "Активна",
        "prolong": "Приостановлена",
    },
    "Ожидает списания": {
        "capture_succeeded": "Завершена с оплатой",
        "capture_failed": "Завершена с задолженностью",
    },
    "Завершена с оплатой": {},
    "Завершена с задолженностью": {},
}

ALL_EVENTS = (
    "authorize", "decline_authorization", "expire_booking", "cancel",
    "unlock_confirmed", "billing_started", "start", "pause", "resume", "prolong",
    "request_finish", "capture_succeeded", "capture_failed",
)


def state_by_name(name: str) -> TripState:
    for cls in STATES.values():
        if cls.name == name:
            return cls()
    raise AssertionError("нет состояния " + name)


def make_trip(state: TripState) -> Trip:
    return Trip(TripId.new(), ScooterId.new(), UserId("user-1"), TARIFF, T0, state=state)


def make_reserved_trip() -> Trip:
    """Свежая поездка: конструктор ставит начальное состояние «Забронирована»."""
    return Trip(TripId.new(), ScooterId.new(), UserId("user-1"), TARIFF, T0)


# --- 1. состав состояний и таблица переходов ---

def test_all_states_present():
    names = sorted(cls.name for cls in STATES.values())
    assert names == sorted(TRANSITION_TABLE.keys())
    assert len(STATES) == 9  # каждому состоянию таблицы — отдельный класс


def test_each_state_is_separate_class():
    classes = [cls for cls in STATES.values()]
    assert len(set(classes)) == len(classes)
    for cls in classes:
        assert issubclass(cls, TripState)
        assert isinstance(cls.allowed, frozenset)


@pytest.mark.parametrize("state_name", sorted(TRANSITION_TABLE))
def test_allowed_events_match_table(state_name):
    expected = frozenset(TRANSITION_TABLE[state_name])
    assert state_by_name(state_name).allowed == expected


@pytest.mark.parametrize("state_name,event", [
    (state_name, event)
    for state_name in sorted(TRANSITION_TABLE)
    for event in ALL_EVENTS
])
def test_transition_availability(state_name, event):
    """Событие либо переопределено в классе состояния, либо запрещено по умолчанию."""
    state = state_by_name(state_name)
    allowed = event in TRANSITION_TABLE[state_name]
    overridden = getattr(type(state), event) is not getattr(TripState, event)
    assert overridden is allowed, "{} / {}".format(state_name, event)


@pytest.mark.parametrize("state_name,event", [
    (state_name, event)
    for state_name in sorted(TRANSITION_TABLE)
    for event in ALL_EVENTS
    if event not in TRANSITION_TABLE[state_name]
])
def test_forbidden_event_raises(state_name, event):
    """Недопустимое событие: TransitionNotAllowed с указанием состояния и события."""
    state = state_by_name(state_name)
    trip = make_trip(state)
    kwargs = {}
    if event == "authorize":
        kwargs["approved"] = True
    if event == "request_finish":
        kwargs["parking_confirmed"] = True
    with pytest.raises(TransitionNotAllowed) as exc:
        getattr(state, event)(trip, T0, **kwargs)
    assert state_name in str(exc.value)
    assert event in str(exc.value)


@pytest.mark.parametrize("event", ALL_EVENTS)
def test_forbidden_event_does_not_change_state(event):
    """Отклонённое событие не меняет состояние агрегата и журнал."""
    trip = make_trip(state_by_name("Отменена"))
    before = (trip.status, trip.events)
    kwargs = {"approved": True} if event == "authorize" else {}
    if event == "request_finish":
        kwargs["parking_confirmed"] = True
    with pytest.raises(TransitionNotAllowed):
        trip.execute(event, T0, **kwargs)
    assert (trip.status, trip.events) == before


# --- 2. состояние хранится в закрытом атрибуте ---

def test_state_has_no_public_setter():
    trip = make_reserved_trip()
    with pytest.raises(AttributeError):
        trip.status = "Завершена с оплатой"
    with pytest.raises(AttributeError):
        trip.state = state_by_name("Отменена")


def test_trip_created_reserved():
    """Создание поездки = бронирование: начальное состояние «Забронирована»."""
    trip = make_reserved_trip()
    assert trip.status == "Забронирована"
    assert [type(e).__name__ for e in trip.events] == ["TripReserved"]
    assert trip.booking_started_at == T0


def test_state_changes_only_through_events():
    trip = make_reserved_trip()
    trip.execute("authorize", T0, approved=True)
    assert trip.status == "Авторизация выполнена"
    assert [type(e).__name__ for e in trip.events] == ["TripReserved", "PaymentAuthorized"]


# --- 3. инварианты, зависящие от состояния ---

def test_authorize_after_booking_timeout():
    trip = make_reserved_trip()
    with pytest.raises(DomainInvariantViolation, match="Срок брони"):
        trip.execute("authorize", T0 + timedelta(minutes=10, seconds=1), approved=True)
    assert trip.status == "Забронирована"


def test_declined_authorization_is_not_blocked_by_timeout():
    """Отказ в авторизации — нормальный бизнес-исход, срок брони не важен."""
    trip = make_reserved_trip()
    trip.execute("authorize", T0 + timedelta(hours=1), approved=False)
    assert trip.status == "Отклонена"


def _make_active_trip():
    trip = make_reserved_trip()
    trip.execute("authorize", T0, approved=True)
    trip.execute("billing_started", T0)
    trip.execute("unlock_confirmed", T0)
    trip.execute("start", T0)
    return trip


def test_start_requires_both_parallel_branches():
    trip = make_reserved_trip()
    trip.execute("authorize", T0, approved=True)
    trip.execute("billing_started", T0)
    with pytest.raises(DomainInvariantViolation, match="после разблокировки замка"):
        trip.execute("start", T0)
    trip.execute("unlock_confirmed", T0)
    trip.execute("start", T0)
    assert trip.status == "Активна"
    assert sorted(trip.allowed_events) == ["pause", "prolong", "request_finish"]


def test_finish_requires_allowed_parking_zone():
    trip = _make_active_trip()
    with pytest.raises(DomainInvariantViolation, match="разрешённой зоне парковки"):
        trip.execute("request_finish", T0 + timedelta(minutes=5), parking_confirmed=False)
    assert trip.status == "Активна"
    assert trip.cost is None


def test_prolong_requires_twelve_hours():
    """Таймер непрерывающий: состояние не меняется, поездка продолжается."""
    trip = _make_active_trip()
    with pytest.raises(DomainInvariantViolation, match="меньше 12 часов"):
        trip.execute("prolong", T0 + timedelta(hours=11, minutes=59))
    trip.execute("prolong", T0 + timedelta(hours=12))
    assert trip.status == "Активна"
    assert [type(e).__name__ for e in trip.events][-1] == "TripProlonged"
