"""Веб на Flask: маршруты аренды и часы приложения."""

import logging
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Flask, jsonify, request

from src.process import RentalProcess
from src.scooter.account import InMemoryAccountRepository
from src.scooter.exceptions import (ClockMovedBack, DomainInvariantViolation,
                                    EntityNotFound, GatewayUnresolved,
                                    InvalidValueObject, TransitionNotAllowed)
from src.scooter.repositories import InMemoryScooterRepository, InMemoryTripRepository
from src.scooter.value_objects import (BatteryLevel, Money, ScooterId, Tariff, TripId,
                                       UserId)

from .adapters import (InMemoryLockGateway, InMemoryPaymentGateway,
                       InMemoryParkingZoneChecker, ShiftedClock)

log = logging.getLogger("web")

# коды ответов из таблицы 7 методического руководства
STATUS_BY_EXCEPTION = (
    (TransitionNotAllowed, 409),
    (InvalidValueObject, 422),
    (DomainInvariantViolation, 422),
    (ClockMovedBack, 422),
    (EntityNotFound, 404),
    (GatewayUnresolved, 500),
)

# коды «error» из тела ответа
CODE_BY_EXCEPTION = (
    (TransitionNotAllowed, "transition_not_allowed"),
    (InvalidValueObject, "invalid_value"),
    (DomainInvariantViolation, "invariant_violation"),
    (ClockMovedBack, "clock_moved_back"),
    (EntityNotFound, "not_found"),
    (GatewayUnresolved, "gateway_unresolved"),
)


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def create_app(config: dict = None) -> Flask:
    """Собрать приложение: часы, репозитории и прикладной сервис — один раз."""
    app = Flask(__name__)
    app.config.update(config or {})

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    clock = ShiftedClock()
    scooters = InMemoryScooterRepository()
    trips = InMemoryTripRepository()
    accounts = InMemoryAccountRepository()
    payments = InMemoryPaymentGateway(
        approve_authorization=_flag("PAYMENT_APPROVE_AUTH", True),
        capture_ok=_flag("PAYMENT_CAPTURE_OK", True),
    )
    locks = InMemoryLockGateway()
    zones = InMemoryParkingZoneChecker(
        in_allowed_zone=_flag("PARKING_ALLOWED", True),
        zone=os.getenv("PARKING_ZONE", "Зона А"),
    )
    process = RentalProcess(clock, scooters, trips, accounts, payments, locks, zones)

    # объекты доступны маршрутам как атрибуты приложения (создаются один раз)
    app.extensions["clock"] = clock
    app.extensions["process"] = process
    app.extensions["payments"] = payments
    app.extensions["locks"] = locks
    app.extensions["zones"] = zones

    # --- обработчики ошибок: тело ответа {"error": "<код>", "message": "<описание>"} ---

    def _error_response(exc: Exception, status: int, code: str):
        log.warning("Ошибка %s: %s", code, exc)
        return jsonify({"error": code, "message": str(exc)}), status

    for exc_type, status in STATUS_BY_EXCEPTION:
        def _make_handler(exc_type=exc_type, status=status):
            def handler(exc):
                for cls, code in CODE_BY_EXCEPTION:
                    if isinstance(exc, cls):
                        return _error_response(exc, status, code)
                return _error_response(exc, status, "error")
            return handler
        app.register_error_handler(exc_type, _make_handler())

    @app.errorhandler(Exception)
    def unhandled(exc):  # pragma: no cover - защита от неожиданных ошибок
        log.exception("Необработанная ошибка")
        return jsonify({"error": "internal_error", "message": str(exc)}), 500

    # --- вспомогательные функции ---

    def payload() -> dict:
        if not request.is_json:
            return {}
        data = request.get_json(silent=True)
        if data is None:
            raise InvalidValueObject("Тело запроса не является корректным JSON")
        if not isinstance(data, dict):
            raise InvalidValueObject("Тело запроса должно быть объектом JSON")
        return data

    def required(data: dict, field: str):
        if field not in data or data[field] in (None, ""):
            raise InvalidValueObject("Не передано обязательное поле «{}»".format(field))
        return data[field]

    def parse_uuid(value: str, what: str):
        from uuid import UUID
        try:
            return UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            raise InvalidValueObject("Некорректный идентификатор {}: {}".format(what, value))

    def parse_trip_id(value: str) -> TripId:
        return TripId(parse_uuid(value, "поездки"))

    def parse_scooter_id(value: str) -> ScooterId:
        return ScooterId(parse_uuid(value, "самоката"))

    def parse_tariff(data: dict) -> Tariff:
        try:
            price = Decimal(str(data.get("price_per_minute", "10")))
            min_cost = Decimal(str(data.get("min_cost", "100")))
            pause = Decimal(str(data.get("pause_price_per_minute", "0")))
        except (InvalidOperation, TypeError, ValueError):
            raise InvalidValueObject("Некорректные значения тарифа")
        return Tariff(price, min_cost, pause)

    def parse_float(value, field: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise InvalidValueObject("Некорректное значение поля «{}»".format(field))

    # --- маршруты бизнес-операций ---

    @app.get("/health")
    def health():
        """Проверка, что сервер отвечает."""
        return jsonify({"status": "ok", "service": "lab3-rental",
                        "time": clock.now().isoformat()}), 200

    @app.post("/scooters")
    def register_scooter():
        """Служебный маршрут: поставить самокат на учёт."""
        data = payload()
        scooter_id = parse_scooter_id(required(data, "scooter_id"))
        battery = data.get("battery", 80)
        try:
            battery_value = BatteryLevel(int(battery))
        except (TypeError, ValueError):
            raise InvalidValueObject("Некорректный уровень заряда")
        scooter = process.register_scooter(scooter_id, battery_value)
        return jsonify(scooter.to_state()), 201

    @app.get("/scooters/<scooter_id>")
    def get_scooter(scooter_id: str):
        scooter = process.get_scooter(parse_scooter_id(scooter_id))
        return jsonify(scooter.to_state()), 200

    @app.post("/rentals")
    def create_rental():
        """Забронировать самокат."""
        data = payload()
        scooter_id = parse_scooter_id(required(data, "scooter_id"))
        user_id = UserId(str(required(data, "user_id")))
        tariff = parse_tariff(data)
        trip = process.create_rental(scooter_id, user_id, tariff)
        return jsonify(process.trip_state_view(trip.id)), 201

    @app.get("/rentals/<trip_id>")
    def get_rental(trip_id: str):
        """Текущее состояние сущности, допустимые события и журнал доменных событий."""
        return jsonify(process.trip_state_view(parse_trip_id(trip_id))), 200

    @app.post("/rentals/<trip_id>/start")
    def start_rental(trip_id: str):
        """Проверить долг, авторизовать оплату и начать поездку."""
        trip = process.start_rental(parse_trip_id(trip_id))
        return jsonify(process.trip_state_view(trip.id)), 200

    @app.post("/rentals/<trip_id>/debt-check")
    def debt_check(trip_id: str):
        """Проверить, нет ли у клиента долга."""
        trip = process.ensure_no_debt(parse_trip_id(trip_id))
        return jsonify(process.trip_state_view(trip.id)), 200

    @app.post("/rentals/<trip_id>/authorize")
    def authorize(trip_id: str):
        """Запросить авторизацию и принять ответ банка.

        Если в теле передан approved, берётся он. Иначе решает заглушка
        (переменная PAYMENT_APPROVE_AUTH).
        """
        internal = parse_trip_id(trip_id)
        data = payload()
        process.request_payment_authorization(internal)
        trip = process.get_trip(internal)
        if "approved" in data:
            approved = bool(data["approved"])
            reason = str(data.get("reason", "Указано в запросе"))
        else:
            result = app.extensions["payments"].authorize(trip.user_id.value,
                                                          Money(Decimal("300")))
            approved, reason = result.approved, result.reason
        trip = process.receive_authorization_result(internal, approved, reason)
        return jsonify(process.trip_state_view(trip.id)), 200

    @app.post("/rentals/<trip_id>/unlock")
    def unlock(trip_id: str):
        """Открыть замок."""
        internal = parse_trip_id(trip_id)
        process.unlock_scooter(internal)
        return jsonify(process.trip_state_view(internal)), 200

    @app.post("/rentals/<trip_id>/billing")
    def start_billing(trip_id: str):
        """Запустить тариф."""
        internal = parse_trip_id(trip_id)
        process.start_tariff(internal)
        return jsonify(process.trip_state_view(internal)), 200

    @app.post("/rentals/<trip_id>/pause")
    def pause(trip_id: str):
        """Поставить поездку на паузу."""
        trip = process.pause_ride(parse_trip_id(trip_id))
        return jsonify(process.trip_state_view(trip.id)), 200

    @app.post("/rentals/<trip_id>/resume")
    def resume(trip_id: str):
        """Снять паузу."""
        trip = process.resume_ride(parse_trip_id(trip_id))
        return jsonify(process.trip_state_view(trip.id)), 200

    @app.post("/rentals/<trip_id>/parking-check")
    def parking_check(trip_id: str):
        """Проверить зону парковки."""
        internal = parse_trip_id(trip_id)
        data = payload()
        zone = process.check_parking_zone(
            internal,
            parse_float(data.get("latitude", 0), "latitude"),
            parse_float(data.get("longitude", 0), "longitude"),
        )
        return jsonify({"in_allowed_zone": zone.in_allowed_zone, "zone": zone.zone}), 200

    @app.post("/rentals/<trip_id>/finish")
    def finish(trip_id: str):
        """Завершить поездку и списать стоимость.

        Вне разрешённой зоны поездка не заканчивается: outcome пустой,
        статус остаётся прежним.
        """
        internal = parse_trip_id(trip_id)
        data = payload()
        trip = process.finish_rental(
            internal,
            parse_float(data.get("latitude", 0), "latitude"),
            parse_float(data.get("longitude", 0), "longitude"),
            bool(data.get("parking_confirmed", True)),
            str(required(data, "photo_ref")),
        )
        body = process.trip_state_view(trip.id)
        if body["outcome"] is None:
            body["message"] = ("Самокат не в разрешённой зоне парковки: "
                               "поездка продолжается")
        return jsonify(body), 200

    @app.post("/rentals/<trip_id>/cost")
    def ride_cost(trip_id: str):
        """Посчитать стоимость."""
        internal = parse_trip_id(trip_id)
        return jsonify({"cost": str(process.calculate_ride_cost(internal))}), 200

    @app.get("/accounts/<account_id>")
    def get_account(account_id: str):
        from src.scooter.value_objects import AccountId
        account = process.get_account(AccountId(account_id))
        return jsonify(account.to_state()), 200

    @app.get("/operator/notices")
    def operator_notices():
        """Сообщения оператору."""
        notices = [
            {"trip_id": str(n["trip_id"].value), "occurred_at": n["occurred_at"].isoformat(),
             "message": n["message"]}
            for n in process.operator_notices
        ]
        return jsonify({"count": len(notices), "notices": notices}), 200

    @app.get("/customer/notices")
    def customer_notices():
        """Сообщения клиенту."""
        notices = [
            {"trip_id": str(n["trip_id"].value), "outcome": n["outcome"],
             "message": n["message"]}
            for n in process.customer_notices
        ]
        return jsonify({"count": len(notices), "notices": notices}), 200

    @app.post("/internal/tick")
    def internal_tick():
        """Сдвинуть часы вперёд и обработать таймеры."""
        data = payload()
        raw = data.get("now")
        current = clock.now()
        if raw is None:
            moment = current
        else:
            try:
                moment = datetime.fromisoformat(str(raw))
            except ValueError:
                raise InvalidValueObject(
                    "Время должно быть в формате ISO 8601, например 2026-10-01T10:00:00")
        if moment < current:
            raise ClockMovedBack("Перевод часов назад запрещён")
        clock.move_to(moment)
        processed = process.on_tick(moment)
        return jsonify({"now": moment.isoformat(),
                        "offset_seconds": int(clock.offset.total_seconds()),
                        "processed": processed}), 200

    @app.get("/internal/clock")
    def internal_clock():
        """Текущее время часов приложения и сдвиг."""
        return jsonify({"now": clock.now().isoformat(),
                        "offset_seconds": int(clock.offset.total_seconds())}), 200

    log.info("Приложение lab3-rental собрано: маршрутов %d", len(list(app.url_map.iter_rules())))
    return app
