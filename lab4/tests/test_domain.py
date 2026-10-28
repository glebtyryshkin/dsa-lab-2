from datetime import date, datetime, timedelta
from decimal import Decimal

from src.flightsearch.domain import Flight, build_routes

DAY = date(2026, 11, 1)


def flight(uid, origin, destination, start_hour, duration_hours, price,
           next_day=False):
    start = datetime.combine(DAY + (timedelta(days=1) if next_day else timedelta()),
                             datetime.min.time()) + timedelta(hours=start_hour)
    return Flight(
        uid=uid,
        carrier="T",
        flight_number=uid,
        origin=origin,
        destination=destination,
        departure=start,
        arrival=start + timedelta(hours=duration_hours),
        price=Decimal(str(price)),
        supplier="test",
    )


def test_direct_route():
    flights = [flight("direct", "OVB", "LED", 8, 3, 1000)]
    routes = build_routes(flights, "OVB", "LED", DAY)
    assert len(routes) == 1
    assert routes[0].transfers == 0
    assert routes[0].price == Decimal("1000")


def test_one_transfer_and_two_transfers():
    flights = [
        flight("a-b", "OVB", "SVO", 7, 2, 400),
        flight("b-d", "SVO", "LED", 11, 2, 400),   # пересадка 2 часа
        flight("b-c", "SVO", "KZN", 11, 2, 300),
        flight("c-d", "KZN", "LED", 15, 2, 300),   # пересадка 2 часа
        flight("direct", "OVB", "LED", 9, 4, 1500),
    ]
    routes = build_routes(flights, "OVB", "LED", DAY)
    assert [route.transfers for route in routes] == [1, 2, 0]
    assert [route.price for route in routes] == [
        Decimal("800"), Decimal("1000"), Decimal("1500")]


def test_connection_window_boundaries():
    flights = [
        flight("a-b", "OVB", "SVO", 7, 2, 400),
        flight("too-early", "SVO", "LED", 9, 2, 100),   # 0 часов
        flight("one-hour", "SVO", "LED", 10, 2, 400),    # ровно 1 час
        flight("six-hours", "SVO", "LED", 15, 2, 500),   # ровно 6 часов
        flight("too-late", "SVO", "LED", 16, 2, 100),     # 7 часов
    ]
    routes = build_routes(flights, "OVB", "LED", DAY)
    prices = {route.price for route in routes}
    assert prices == {Decimal("800"), Decimal("900")}
    assert Decimal("500") not in prices  # 100 + 400 и 100 + 500, а не 500


def test_airports_do_not_repeat():
    flights = [
        flight("a-b", "OVB", "SVO", 7, 2, 100),
        flight("b-a", "SVO", "OVB", 10, 2, 100),
        flight("a-d", "OVB", "LED", 13, 2, 100),
    ]
    routes = build_routes(flights, "OVB", "LED", DAY)
    # Остаётся только прямой рейс: маршрут OVB -> SVO -> OVB -> LED
    # повторяет аэропорт OVB и запрещён.
    assert [route.signature for route in routes] == [("a-d",)]


def test_flights_of_other_date_are_ignored():
    flights = [
        flight("direct", "OVB", "LED", 8, 3, 1000, next_day=True),
    ]
    assert build_routes(flights, "OVB", "LED", DAY) == []


def test_no_route():
    flights = [flight("a-b", "OVB", "SVO", 8, 2, 100)]
    assert build_routes(flights, "OVB", "LED", DAY) == []


def test_twenty_cheapest_routes():
    flights = []
    for index in range(30):
        flights.append(flight("direct-{}".format(index), "OVB", "LED",
                              6 + index % 10, 2, 1000 + index))
    routes = build_routes(flights, "OVB", "LED", DAY, limit=20)
    assert len(routes) == 20
    assert routes[0].price == Decimal("1000")
    assert routes[-1].price == Decimal("1019")


def test_transfer_uses_arrival_airport():
    flights = [
        flight("a-b", "OVB", "SVO", 7, 2, 100),
        flight("c-d", "KZN", "LED", 11, 2, 100),
    ]
    assert build_routes(flights, "OVB", "LED", DAY) == []
