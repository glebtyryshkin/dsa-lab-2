"""Проверки маршрутов Flask."""

import pathlib

import pytest

from web.wsgi import create_app

SCOOTER_ID = "11111111-1111-1111-1111-111111111111"
UNKNOWN_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        test_client.post("/scooters", json={"scooter_id": SCOOTER_ID, "battery": 80})
        yield test_client


def create_rental(client, user="user-1"):
    response = client.post("/rentals", json={
        "scooter_id": SCOOTER_ID,
        "user_id": user,
        "price_per_minute": "10",
        "min_cost": "100",
        "pause_price_per_minute": "3",
    })
    assert response.status_code == 201
    return response.get_json()["trip_id"]


def start(client, trip_id):
    response = client.post("/rentals/{}/start".format(trip_id))
    assert response.status_code == 200
    return response.get_json()


# --- маршруты ---

def test_all_business_routes_exist():
    app = create_app()
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    for expected in ("/health", "/scooters", "/scooters/<scooter_id>", "/rentals",
                     "/rentals/<trip_id>", "/rentals/<trip_id>/start",
                     "/rentals/<trip_id>/debt-check", "/rentals/<trip_id>/authorize",
                     "/rentals/<trip_id>/unlock", "/rentals/<trip_id>/billing",
                     "/rentals/<trip_id>/pause", "/rentals/<trip_id>/resume",
                     "/rentals/<trip_id>/parking-check", "/rentals/<trip_id>/finish",
                     "/rentals/<trip_id>/cost", "/accounts/<account_id>",
                     "/operator/notices", "/internal/tick", "/internal/clock"):
        assert expected in rules, expected


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_get_rental_returns_state_events_and_journal(client):
    trip_id = create_rental(client)
    body = client.get("/rentals/{}".format(trip_id)).get_json()
    assert body["status"] == "Забронирована"
    assert body["allowed_events"] == ["authorize", "cancel", "decline_authorization",
                                      "expire_booking"]
    assert body["events"][0]["type"] == "TripReserved"


def test_main_path_over_http(client):
    trip_id = create_rental(client)
    assert start(client, trip_id)["status"] == "Активна"
    assert client.post("/rentals/{}/pause".format(trip_id)).get_json()["status"] == "Приостановлена"
    assert client.post("/rentals/{}/resume".format(trip_id)).get_json()["status"] == "Активна"
    response = client.post("/rentals/{}/finish".format(trip_id),
                           json={"latitude": 55.0, "longitude": 82.0,
                                 "photo_ref": "photo-1"})
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "Завершена с оплатой"
    assert body["cost"] == "100.00"
    assert body["events"][-1]["type"] == "PaymentCaptured"


def test_separate_parallel_routes(client):
    trip_id = create_rental(client)
    client.post("/rentals/{}/debt-check".format(trip_id))
    client.post("/rentals/{}/authorize".format(trip_id), json={"approved": True})
    assert client.post("/rentals/{}/billing".format(trip_id)).get_json()["status"] == "Авторизация выполнена"
    assert client.post("/rentals/{}/unlock".format(trip_id)).get_json()["status"] == "Авторизация выполнена"
    # старт после открытия замка и запуска тарифа
    assert start(client, trip_id)["status"] == "Активна"
    # повторный старт в состоянии «Активна» недопустим
    assert client.post("/rentals/{}/start".format(trip_id)).status_code == 409


def test_authorize_body_controls_result(client):
    """T4: результат авторизации приходит в теле запроса (поток сообщений)."""
    trip_id = create_rental(client)
    response = client.post("/rentals/{}/authorize".format(trip_id),
                           json={"approved": False, "reason": "Недостаточно средств"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "Отклонена"
    assert body["outcome"] == "отказ банка"


def test_finish_outside_zone_keeps_ride_running(client):
    """Вне разрешённой зоны поездка продолжается."""
    from web.wsgi import create_app as build
    app = build()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        test_client.post("/scooters", json={"scooter_id": SCOOTER_ID, "battery": 80})
        trip_id = create_rental(test_client)
        start(test_client, trip_id)
        app.extensions["zones"].in_allowed_zone = False
        response = test_client.post("/rentals/{}/finish".format(trip_id),
                                    json={"photo_ref": "photo"})
        body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "Активна"
    assert body["outcome"] is None
    assert "продолжается" in body["message"]


def test_outcome_field_on_main_path(client):
    trip_id = create_rental(client)
    start(client, trip_id)
    body = client.post("/rentals/{}/finish".format(trip_id),
                       json={"photo_ref": "photo"}).get_json()
    assert body["outcome"] == "оплачено"


def test_409_on_repeated_pause(client):
    trip_id = create_rental(client)
    start(client, trip_id)
    assert client.post("/rentals/{}/pause".format(trip_id)).status_code == 200
    response = client.post("/rentals/{}/pause".format(trip_id))
    assert response.status_code == 409
    assert response.get_json()["error"] == "transition_not_allowed"
    assert client.get("/rentals/{}".format(trip_id)).get_json()["status"] == "Приостановлена"


def test_409_on_start_after_finish(client):
    trip_id = create_rental(client)
    start(client, trip_id)
    client.post("/rentals/{}/finish".format(trip_id), json={"photo_ref": "photo"})
    assert client.post("/rentals/{}/start".format(trip_id)).status_code == 409


def test_parking_check_route(client):
    trip_id = create_rental(client)
    start(client, trip_id)
    body = client.post("/rentals/{}/parking-check".format(trip_id),
                       json={"latitude": 55.0, "longitude": 82.0}).get_json()
    assert body == {"in_allowed_zone": True, "zone": "Зона А"}


def test_cost_route(client):
    trip_id = create_rental(client)
    start(client, trip_id)
    assert client.post("/rentals/{}/cost".format(trip_id)).get_json()["cost"] == "100.00"


# --- коды ответов из таблицы 7 ---

def test_409_transition_not_allowed(client):
    trip_id = create_rental(client)
    response = client.post("/rentals/{}/pause".format(trip_id))
    assert response.status_code == 409
    assert response.get_json()["error"] == "transition_not_allowed"
    # состояние не изменилось
    assert client.get("/rentals/{}".format(trip_id)).get_json()["status"] == "Забронирована"


def test_409_after_finish(client):
    trip_id = create_rental(client)
    start(client, trip_id)
    client.post("/rentals/{}/finish".format(trip_id),
                json={"photo_ref": "photo-1"})
    response = client.post("/rentals/{}/resume".format(trip_id))
    assert response.status_code == 409
    assert client.get("/rentals/{}".format(trip_id)).get_json()["status"] == "Завершена с оплатой"


def test_422_invalid_value(client):
    response = client.post("/rentals", json={"user_id": "user-1"})
    assert response.status_code == 422
    assert response.get_json()["error"] == "invalid_value"


def test_422_invalid_uuid(client):
    response = client.get("/rentals/not-a-uuid")
    assert response.status_code == 422


def test_422_invariant_violation(client):
    client.post("/scooters", json={"scooter_id": UNKNOWN_ID, "battery": 5})
    response = client.post("/rentals", json={"scooter_id": UNKNOWN_ID, "user_id": "u"})
    assert response.status_code == 422
    assert "Заряд ниже" in response.get_json()["message"]


def test_422_bad_json_body(client):
    response = client.post("/rentals", data="{не json}",
                           content_type="application/json")
    assert response.status_code == 422


def test_404_unknown_trip(client):
    response = client.get("/rentals/{}".format(UNKNOWN_ID))
    assert response.status_code == 404
    assert response.get_json()["error"] == "not_found"


def test_404_unknown_scooter(client):
    assert client.get("/scooters/{}".format(UNKNOWN_ID)).status_code == 404


# --- часы приложения и служебный маршрут ---

def test_tick_moves_clock_forward_and_expires_booking(client):
    trip_id = create_rental(client)
    clock = client.get("/internal/clock").get_json()
    offset = clock["offset_seconds"]
    target = _plus(offset, minutes=11)
    response = client.post("/internal/tick", json={"now": target})
    assert response.status_code == 200
    body = response.get_json()
    assert body["processed"][0]["event"] == "BookingExpired"
    assert body["processed"][0]["outcome"] == "бронь снята"
    assert client.get("/rentals/{}".format(trip_id)).get_json()["status"] == "Отменена"


def test_tick_rejects_clock_moved_back(client):
    response = client.post("/internal/tick", json={"now": "2000-01-01T00:00:00"})
    assert response.status_code == 422
    assert response.get_json()["error"] == "clock_moved_back"


def test_tick_rejects_bad_format(client):
    response = client.post("/internal/tick", json={"now": "01.10.2026 10:00"})
    assert response.status_code == 422
    assert response.get_json()["error"] == "invalid_value"


def test_tick_without_time_uses_app_clock(client):
    response = client.post("/internal/tick", json={})
    assert response.status_code == 200
    assert response.get_json()["processed"] == []


def test_tick_notifies_operator_after_twelve_hours(client):
    trip_id = create_rental(client)
    start(client, trip_id)
    offset = client.get("/internal/clock").get_json()["offset_seconds"]
    response = client.post("/internal/tick", json={"now": _plus(offset, hours=12, minutes=1)})
    assert response.status_code == 200
    events = [row["event"] for row in response.get_json()["processed"]]
    assert "TripProlonged" in events
    body = client.get("/rentals/{}".format(trip_id)).get_json()
    # таймер непрерывающий: состояние прежнее, в журнале — TripProlonged
    assert body["status"] == "Активна"
    assert body["events"][-1]["type"] == "TripProlonged"
    notices = client.get("/operator/notices").get_json()
    assert notices["count"] == 1
    assert "12 часов" in notices["notices"][0]["message"]


def test_500_gateway_unresolved(client, monkeypatch):
    """GatewayUnresolved: ни одно условие шлюза не выполнено (код 500)."""
    from web.wsgi import create_app as build
    import src.process as proc_module
    app = build()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        test_client.post("/scooters", json={"scooter_id": SCOOTER_ID, "battery": 80})
        trip_id = create_rental(test_client)
        start(test_client, trip_id)
        # оба ответа по списанию ложные
        monkeypatch.setattr(proc_module, "is_capture_succeeded", lambda result: False)
        monkeypatch.setattr(proc_module, "is_capture_failed", lambda result: False)
        response = test_client.post("/rentals/{}/finish".format(trip_id),
                                    json={"photo_ref": "photo"})
    assert response.status_code == 500
    assert response.get_json()["error"] == "gateway_unresolved"


def test_error_body_shape(client):
    body = client.get("/rentals/{}".format(UNKNOWN_ID)).get_json()
    assert set(body.keys()) == {"error", "message"}


# --- слои ---

def test_domain_and_application_do_not_import_flask():
    root = pathlib.Path(__file__).resolve().parents[1]
    checked = list((root / "src").rglob("*.py"))
    assert checked, "не найдено файлов прикладного и доменного слоёв"
    for path in checked:
        text = path.read_text(encoding="utf-8")
        assert "import flask" not in text and "from flask" not in text, path


def test_datetime_now_only_in_adapters():
    root = pathlib.Path(__file__).resolve().parents[1]
    for path in (root / "src").rglob("*.py"):
        assert "datetime.now()" not in path.read_text(encoding="utf-8"), path


def _plus(offset_seconds, minutes=0, hours=0):
    """Время приложения плюс сдвиг — в формате ISO 8601 без микросекунд."""
    from datetime import datetime, timedelta
    moment = datetime.now() + timedelta(seconds=offset_seconds,
                                         minutes=minutes, hours=hours)
    return moment.replace(microsecond=0).isoformat()
