"""Сервис аренды электросамоката.

Шаги процесса: бронь, проверка долга, авторизация, замок и тариф, пауза,
завершение и списание. Если условие нельзя выбрать, выбрасывается
GatewayUnresolved. Состояние поездки меняют методы агрегата.
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from .scooter.account import Account, AccountRepository
from .scooter.aggregates import Scooter, ScooterStatus, Trip
from .scooter.exceptions import (DomainInvariantViolation, EntityNotFound,
                                 GatewayUnresolved)
from .scooter.ports import (Clock, LockGateway, ParkingStatus, ParkingZoneChecker,
                            PaymentGateway)
from .scooter.repositories import ScooterRepository, TripRepository
from .scooter.states import BOOKING_TIMEOUT, RIDE_PROLONG_AFTER
from .scooter.value_objects import (AccountId, BatteryLevel, Money, ScooterId, Tariff,
                                    TripId, UserId)

# сумма предварительной авторизации платежа, руб. (регламент: 300 рублей)
AUTH_AMOUNT = Money(Decimal("300"))


def has_debt(account: Account) -> bool:
    """У клиента есть задолженность."""
    return account.has_debt()


def no_debt(account: Account) -> bool:
    """Задолженности нет."""
    return not account.has_debt()


def is_authorization_approved(approved: bool) -> bool:
    """Банк одобрил авторизацию."""
    return bool(approved)


def is_ride_paused(trip: Trip) -> bool:
    """Поездка на паузе."""
    return trip.is_paused


def is_parking_allowed(status: ParkingStatus) -> bool:
    """Самокат в разрешённой зоне."""
    return bool(status.in_allowed_zone)


def is_parking_denied(status: ParkingStatus) -> bool:
    """Самокат вне разрешённой зоны, поездку пока не завершить."""
    return not status.in_allowed_zone


def is_capture_succeeded(result) -> bool:
    """Списание прошло."""
    return bool(result.succeeded)


def is_capture_failed(result) -> bool:
    """Списание не прошло."""
    return not bool(result.succeeded)


class RentalProcess:
    """Прикладной сервис процесса аренды электросамоката."""

    def __init__(self,
                 clock: Clock,
                 scooters: ScooterRepository,
                 trips: TripRepository,
                 accounts: AccountRepository,
                 payments: PaymentGateway,
                 locks: LockGateway,
                 zones: ParkingZoneChecker):
        self._clock = clock
        self._scooters = scooters
        self._trips = trips
        self._accounts = accounts
        self._payments = payments
        self._locks = locks
        self._zones = zones
        self._operator_notices: List[Dict[str, object]] = []
        self._customer_notices: List[Dict[str, object]] = []

    # --- служебные ---

    def _save(self, trip: Trip) -> None:
        self._trips.save(trip)

    def _load(self, trip_id: TripId) -> Trip:
        return self._trips.get(trip_id)

    def _account_of(self, trip: Trip) -> Account:
        return self._accounts.get(AccountId.of(trip.user_id))

    @property
    def operator_notices(self) -> tuple:
        """Уведомления оператору."""
        return tuple(self._operator_notices)

    @property
    def customer_notices(self) -> tuple:
        """Сообщения клиенту, например о блокировке."""
        return tuple(self._customer_notices)

    def register_scooter(self, scooter_id: ScooterId, battery: BatteryLevel,
                         status: ScooterStatus = ScooterStatus.FREE) -> Scooter:
        """Служебная операция: поставить самокат на учёт."""
        scooter = Scooter(scooter_id, battery, status)
        self._scooters.save(scooter)
        return scooter

    def create_rental(self, scooter_id: ScooterId, user_id: UserId,
                      tariff: Tariff) -> Trip:
        """Забронировать самокат. Поездка сразу в состоянии «Забронирована»."""
        now = self._clock.now()
        scooter = self._scooters.get(scooter_id)
        scooter.rent_out()  # правила 1 и 2 ЛР2: заряд и состояние самоката
        self._scooters.save(scooter)
        trip = Trip(TripId.new(), scooter_id, user_id, tariff, now)
        self._save(trip)
        return trip

    def ensure_no_debt(self, trip_id: TripId) -> Trip:
        """Если есть долг, бронь снимается и поездка не начинается."""
        trip = self._load(trip_id)
        account = self._account_of(trip)
        if has_debt(account):
            trip.execute("cancel", self._clock.now(), reason="Задолженность клиента")
            self._save(trip)
            self._release_scooter(trip)
            self._customer_notices.append(
                {"trip_id": trip.id, "outcome": "есть долг",
                 "message": "Поездка не начата: задолженность"})
        elif not no_debt(account):
            raise GatewayUnresolved("Задолженность отсутствует?")
        return trip

    def request_payment_authorization(self, trip_id: TripId) -> None:
        """Запросить у банка авторизацию 300 рублей."""
        self._load(trip_id)

    def receive_authorization_result(self, trip_id: TripId, approved: bool,
                                     reason: str = "") -> Trip:
        """Разобрать ответ банка. Если отказал, поездка отклоняется."""
        trip = self._load(trip_id)
        now = self._clock.now()
        if is_authorization_approved(approved):
            trip.execute("authorize", now, approved=True)
            self._save(trip)
            return trip
        if not is_authorization_approved(approved):
            # поток по умолчанию: любое неодобренное значение
            trip.execute("decline_authorization", now, reason=reason)
            self._save(trip)
            self._release_scooter(trip)
            return trip
        raise GatewayUnresolved("Авторизация одобрена?")  # pragma: no cover

    def start_parallel_branches(self, trip_id: TripId) -> None:
        """Открыть замок и запустить тариф."""
        self.start_tariff(trip_id)
        self.unlock_scooter(trip_id)

    def start_tariff(self, trip_id: TripId) -> None:
        """Запустить тариф."""
        trip = self._load(trip_id)
        trip.execute("billing_started", self._clock.now())
        self._save(trip)

    def unlock_scooter(self, trip_id: TripId) -> None:
        """Открыть замок самоката."""
        trip = self._load(trip_id)
        result = self._locks.unlock(str(trip.scooter_id.value))
        if not result.unlocked:
            raise DomainInvariantViolation(
                "Замок не разблокирован: {}".format(result.reason or "нет ответа"))
        trip.execute("unlock_confirmed", self._clock.now())
        self._save(trip)

    def start_ride(self, trip_id: TripId) -> Trip:
        """Начать поездку, когда замок открыт и тариф запущен."""
        trip = self._load(trip_id)
        trip.execute("start", self._clock.now())
        self._save(trip)
        return trip

    def start_rental(self, trip_id: TripId) -> Trip:
        """Проверить долг, авторизовать оплату, открыть замок и начать поездку.

        Если авторизация уже пройдена отдельно, остаются замок, тариф и старт.
        """
        trip = self._load(trip_id)
        if trip.status == "Забронирована":
            self.ensure_no_debt(trip_id)
            trip = self._load(trip_id)
            if trip.status != "Забронирована":
                return trip  # есть долг, бронь снята
            self.request_payment_authorization(trip_id)
            result = self._payments.authorize(trip.user_id.value, AUTH_AMOUNT)
            self.receive_authorization_result(trip_id, result.approved, result.reason)
            trip = self._load(trip_id)
            if trip.status != "Авторизация выполнена":
                return trip  # банк отказал
        if trip.status == "Авторизация выполнена":
            self.start_parallel_branches(trip_id)
            return self.start_ride(trip_id)
        # Любое другое состояние (в том числе конечное) — событие start недопустимо:
        # решение принимает объект состояния, приложение отвечает кодом 409
        trip.execute("start", self._clock.now())
        return trip

    def pause_ride(self, trip_id: TripId) -> Trip:
        """Поставить поездку на паузу. Повторная пауза недопустима."""
        trip = self._load(trip_id)
        if is_ride_paused(trip):
            trip.execute("pause", self._clock.now())
        trip.execute("pause", self._clock.now())
        self._save(trip)
        return trip

    def resume_ride(self, trip_id: TripId) -> Trip:
        """Снять паузу."""
        trip = self._load(trip_id)
        trip.execute("resume", self._clock.now())
        self._save(trip)
        return trip

    def check_parking_zone(self, trip_id: TripId, latitude: float,
                           longitude: float) -> ParkingStatus:
        """Проверить, стоит ли самокат в разрешённой зоне."""
        trip = self._load(trip_id)
        return self._zones.check(str(trip.scooter_id.value), latitude, longitude)

    def receive_scooter_photo(self, trip_id: TripId, photo_ref: str) -> None:
        """Принять фотографию самоката."""
        if not (photo_ref or "").strip():
            raise DomainInvariantViolation("Не получена фотография самоката")
        self._load(trip_id)

    def calculate_ride_cost(self, trip_id: TripId) -> Money:
        """Посчитать стоимость поездки."""
        trip = self._load(trip_id)
        return trip.calculate_cost(self._clock.now())

    def finish_rental(self, trip_id: TripId, latitude: float, longitude: float,
                      parking_confirmed: bool, photo_ref: str) -> Trip:
        """Завершить поездку и списать стоимость, если зона парковки разрешена."""
        trip = self._load(trip_id)
        status = self.check_parking_zone(trip_id, latitude, longitude)
        if is_parking_denied(status):
            # вне зоны поездка продолжается
            return trip
        if is_parking_allowed(status):
            self.receive_scooter_photo(trip_id, photo_ref)
            trip.execute("request_finish", self._clock.now(),
                         parking_confirmed=parking_confirmed)
            self._save(trip)
            return self.capture_ride_cost(trip_id)
        # ни одно условие шлюза не выполнено, потока по умолчанию нет
        raise GatewayUnresolved("Самокат в разрешённой зоне парковки?")  # pragma: no cover

    def request_capture(self, trip_id: TripId) -> None:
        """Запросить списание стоимости."""
        self._load(trip_id)

    def capture_ride_cost(self, trip_id: TripId) -> Trip:
        """Списать стоимость. Если банк отказал, поездка закрывается с долгом."""
        trip = self._load(trip_id)
        account = self._account_of(trip)
        if account.blocked:
            # аккаунт заблокирован задолженностью — новое списание невозможно
            self._finish_without_payment(trip_id, "Аккаунт заблокирован задолженностью")
            return trip
        self.request_capture(trip_id)
        result = self._payments.capture(trip.user_id.value, trip.cost)
        if is_capture_succeeded(result):
            trip.execute("capture_succeeded", self._clock.now())
            self._save(trip)
            self.release_scooter_after_ride(trip_id)
        elif is_capture_failed(result):
            self._finish_without_payment(trip_id, getattr(result, "reason", ""))
        else:
            # ни одно условие шлюза не выполнено, потока по умолчанию нет
            raise GatewayUnresolved("Списание прошло?")
        return trip

    def _finish_without_payment(self, trip_id: TripId, reason: str) -> None:
        """Поездка уже закончилась, но деньги не списались: фиксируем долг."""
        trip = self._load(trip_id)
        trip.execute("capture_failed", self._clock.now())
        self._save(trip)
        self.register_debt_and_block_account(trip_id, reason)
        self.release_scooter_after_ride(trip_id)

    def register_debt_and_block_account(self, trip_id: TripId,
                                        reason: str = "") -> Account:
        """Записать долг и заблокировать аккаунт."""
        trip = self._load(trip_id)
        account = self._account_of(trip)
        if account.blocked and account.debt.amount > 0:
            account.charge_failed(Money.zero())
        else:
            account.charge_failed(trip.cost)
        self._accounts.save(account)
        self._save(trip)
        self._customer_notices.append(
            {"trip_id": trip.id, "outcome": "долг", "reason": reason,
             "message": "Поездка завершена с задолженностью, аккаунт заблокирован",
             "debt": str(account.debt)})
        return account

    def notify_operator(self, trip_id: TripId, message: str) -> None:
        """Сообщить оператору."""
        trip = self._load(trip_id)
        self._operator_notices.append(
            {"trip_id": trip.id, "occurred_at": self._clock.now(), "message": message})

    def release_scooter_after_ride(self, trip_id: TripId) -> None:
        """Возврат самоката в фонд после успешной оплаты (правило 6 ЛР2)."""
        trip = self._load(trip_id)
        scooter = self._scooters.get(trip.scooter_id)
        scooter.check_can_return()
        reading = self._locks.read_battery(str(trip.scooter_id.value))
        scooter.apply_telemetry(reading.level)
        self._locks.lock(str(trip.scooter_id.value))
        self._scooters.save(scooter)

    def _release_scooter(self, trip: Trip) -> None:
        """Освободить самокат, если поездка не началась."""
        try:
            scooter = self._scooters.get(trip.scooter_id)
        except EntityNotFound:
            return
        if scooter.status is ScooterStatus.RENTED:
            scooter.check_can_return()
            self._scooters.save(scooter)

    def on_booking_expired(self, trip_id: TripId, now: datetime) -> bool:
        """Снять бронь, если прошло 10 минут. True, если бронь снята."""
        trip = self._load(trip_id)
        if "expire_booking" not in trip.allowed_events:
            return False
        if now - trip.booking_started_at < BOOKING_TIMEOUT:
            return False
        trip.execute("expire_booking", now)
        self._save(trip)
        self._release_scooter(trip)
        return True

    def on_trip_prolonged(self, trip_id: TripId, now: datetime) -> bool:
        """Если поездка длится больше 12 часов, уведомить оператора."""
        trip = self._load(trip_id)
        if "prolong" not in trip.allowed_events:
            return False
        if now - trip.started_at < RIDE_PROLONG_AFTER:
            return False
        trip.execute("prolong", now)
        self._save(trip)
        self.notify_operator(
            trip_id, "Поездка длится более 12 часов, требуется связаться с клиентом")
        return True

    def on_tick(self, now: Optional[datetime] = None) -> List[Dict[str, object]]:
        """Проверить таймеры по всем поездкам. Вызывается из POST /internal/tick."""
        moment = now if now is not None else self._clock.now()
        processed: List[Dict[str, object]] = []
        for trip in list(self._trips.all()):
            if self.on_booking_expired(trip.id, moment):
                processed.append({"trip_id": str(trip.id.value),
                                  "event": "BookingExpired", "outcome": "бронь снята"})
            if self.on_trip_prolonged(trip.id, moment):
                processed.append({"trip_id": str(trip.id.value),
                                  "event": "TripProlonged", "outcome": "уведомление"})
        return processed

    # --- чтение ---

    def get_trip(self, trip_id: TripId) -> Trip:
        return self._load(trip_id)

    def get_account(self, account_id: AccountId) -> Account:
        return self._accounts.get(account_id)

    def get_scooter(self, scooter_id: ScooterId) -> Scooter:
        return self._scooters.get(scooter_id)

    def trip_state_view(self, trip_id: TripId) -> Dict[str, object]:
        """Представление сущности для GET /rentals/<id>: состояние, события, журнал."""
        trip = self._load(trip_id)
        return {
            "trip_id": str(trip.id.value),
            "scooter_id": str(trip.scooter_id.value),
            "user_id": trip.user_id.value,
            "status": trip.status,
            "outcome": trip_outcome(trip),
            "allowed_events": sorted(trip.allowed_events),
            "cost": str(trip.cost) if trip.cost is not None else None,
            "started_at": trip.started_at.isoformat() if trip.started_at else None,
            "finished_at": trip.finished_at.isoformat() if trip.finished_at else None,
            "parking_confirmed": trip.parking_confirmed,
            "events": [
                {"type": type(event).__name__, "occurred_at": event.occurred_at.isoformat()}
                for event in trip.events
            ],
        }


def trip_outcome(trip: Trip) -> Optional[str]:
    """Чем закончилась поездка, или None, если она ещё идёт."""
    from .scooter import events as ev
    from .scooter.states import Cancelled, CompletedPaid, CompletedWithDebt, Declined
    state = trip.state
    if isinstance(state, CompletedPaid):
        return "оплачено"
    if isinstance(state, CompletedWithDebt):
        return "долг"
    if isinstance(state, Declined):
        return "отказ банка"
    if isinstance(state, Cancelled):
        expired = any(isinstance(event, ev.BookingExpired) for event in trip.events)
        return "бронь снята" if expired else "есть долг"
    return None
