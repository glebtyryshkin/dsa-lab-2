"""Предметная область: рейсы и построение маршрутов.

Модуль не обращается ни к сети, ни к базе данных: его можно проверять
отдельно и вызывать из синхронной и асинхронной версий сервиса.
"""

from __future__ import annotations

import heapq
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Sequence, Tuple

# 30 аэропортов собственной базы расписания.
AIRPORTS: Tuple[str, ...] = (
    "LED", "SVO", "DME", "VKO", "AER", "KZN", "OVB", "SVX", "UFA", "ROV",
    "KRR", "KJA", "CEK", "TJM", "SGC", "PEE", "KUF", "VOG", "MRV", "IJK",
    "IKT", "UUD", "BQS", "PKC", "YKS", "GDX", "MMK", "ASF", "ARH", "RTW",
)

AIRPORT_NAMES: Dict[str, str] = {
    "LED": "Санкт-Петербург (Пулково)",
    "SVO": "Москва (Шереметьево)",
    "DME": "Москва (Домодедово)",
    "VKO": "Москва (Внуково)",
    "AER": "Сочи",
    "KZN": "Казань",
    "OVB": "Новосибирск (Толмачёво)",
    "SVX": "Екатеринбург (Кольцово)",
    "UFA": "Уфа",
    "ROV": "Ростов-на-Дону (Платов)",
    "KRR": "Краснодар",
    "KJA": "Красноярск (Емельяново)",
    "CEK": "Челябинск",
    "TJM": "Тюмень",
    "SGC": "Сургут",
    "PEE": "Пермь",
    "KUF": "Самара",
    "VOG": "Волгоград",
    "MRV": "Минеральные Воды",
    "IJK": "Ижевск",
    "IKT": "Иркутск",
    "UUD": "Улан-Удэ",
    "BQS": "Благовещенск",
    "PKC": "Петропавловск-Камчатский",
    "YKS": "Якутск",
    "GDX": "Магадан",
    "MMK": "Мурманск",
    "ASF": "Астрахань",
    "ARH": "Архангельск",
    "RTW": "Саратов (Гагарин)",
}

MIN_CONNECTION = timedelta(hours=1)
MAX_CONNECTION = timedelta(hours=6)


@dataclass(frozen=True)
class Flight:
    """Рейс поставщика или собственной базы."""

    uid: str
    carrier: str
    flight_number: str
    origin: str
    destination: str
    departure: datetime
    arrival: datetime
    price: Decimal
    supplier: str

    def __post_init__(self) -> None:
        if len(self.origin) != 3 or len(self.destination) != 3:
            raise ValueError("Код аэропорта должен состоять из трёх букв")
        if self.origin == self.destination:
            raise ValueError("Аэропорт вылета и прилёта совпадают")
        if self.arrival <= self.departure:
            raise ValueError("Время прилёта должно быть позже времени вылета")
        if self.price < 0:
            raise ValueError("Цена рейса не может быть отрицательной")

    def to_dict(self) -> dict:
        return {
            "carrier": self.carrier,
            "flight_number": self.flight_number,
            "origin": self.origin,
            "destination": self.destination,
            "departure": self.departure.isoformat(timespec="minutes"),
            "arrival": self.arrival.isoformat(timespec="minutes"),
            "price": float(self.price),
            "supplier": self.supplier,
        }


@dataclass(frozen=True)
class Route:
    """Маршрут из одного, двух или трёх рейсов."""

    flights: Tuple[Flight, ...]

    @property
    def price(self) -> Decimal:
        return sum((flight.price for flight in self.flights), Decimal("0"))

    @property
    def transfers(self) -> int:
        return len(self.flights) - 1

    @property
    def signature(self) -> Tuple[str, ...]:
        return tuple(flight.uid for flight in self.flights)

    def to_dict(self) -> dict:
        return {
            "price": float(self.price),
            "transfers": self.transfers,
            "flights": [flight.to_dict() for flight in self.flights],
        }


def _connections(
    flights_by_origin: Dict[str, Sequence[Flight]],
    departure_times: Dict[str, Sequence[datetime]],
    airport: str,
    earliest: datetime,
    latest: datetime,
) -> Sequence[Flight]:
    """Рейсы из аэропорта, вылетающие в заданном окне пересадки."""
    flights = flights_by_origin.get(airport)
    if not flights:
        return ()
    times = departure_times[airport]
    left = bisect_left(times, earliest)
    right = bisect_right(times, latest)
    return flights[left:right]


def build_routes(
    flights: Iterable[Flight],
    origin: str,
    destination: str,
    search_date: date,
    limit: int = 20,
) -> List[Route]:
    """Построить до `limit` самых дешёвых маршрутов.

    Правила методического руководства:
    * все рейсы вылетают в дату поиска;
    * пересадка выполняется в аэропорту прилёта предыдущего рейса;
    * время между рейсами от 1 до 6 часов включительно;
    * аэропорты в маршруте не повторяются;
    * маршрут содержит не более двух пересадок.
    """
    if origin == destination:
        return []

    flights_by_origin: Dict[str, List[Flight]] = {}
    seen_uids = set()
    for flight in flights:
        if flight.departure.date() != search_date:
            continue
        if flight.uid in seen_uids:
            continue
        seen_uids.add(flight.uid)
        flights_by_origin.setdefault(flight.origin, []).append(flight)

    departure_times: Dict[str, List[datetime]] = {}
    for airport, items in flights_by_origin.items():
        items.sort(key=lambda item: (item.departure, item.arrival, item.uid))
        departure_times[airport] = [item.departure for item in items]

    # В куче хранятся самые дорогие из уже выбранных маршрутов: так можно
    # отбрасывать всё, что дороже текущего худшего из двадцати.
    best: List[Tuple[Decimal, int, Route]] = []
    counter = 0

    def offer(route: Route) -> None:
        nonlocal counter
        price = route.price

        if len(best) < limit:
            heapq.heappush(best, (-price, counter, route))
            counter += 1
        elif price < -best[0][0]:
            heapq.heapreplace(best, (-price, counter, route))
            counter += 1

    for first in flights_by_origin.get(origin, ()):
        if first.destination == destination:
            offer(Route((first,)))
            continue
        # Промежуточный аэропорт не должен совпадать с началом и концом.
        if first.destination == origin:
            continue

        second_earliest = first.arrival + MIN_CONNECTION
        second_latest = first.arrival + MAX_CONNECTION
        for second in _connections(
            flights_by_origin, departure_times, first.destination,
            second_earliest, second_latest,
        ):
            if second.destination == destination:
                offer(Route((first, second)))
                continue
            if second.destination in (origin, first.destination):
                continue

            third_earliest = second.arrival + MIN_CONNECTION
            third_latest = second.arrival + MAX_CONNECTION
            for third in _connections(
                flights_by_origin, departure_times, second.destination,
                third_earliest, third_latest,
            ):
                if third.destination != destination:
                    continue
                # Все четыре аэропорта маршрута должны быть разными.
                if len({origin, first.destination, second.destination,
                        destination}) != 4:
                    continue
                offer(Route((first, second, third)))

    routes = [route for _, _, route in best]
    routes.sort(key=lambda route: (route.price, route.signature))
    return routes


def parse_date(value: str) -> date:
    """Разобрать дату в формате ISO 8601 (YYYY-MM-DD)."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Дата должна быть в формате YYYY-MM-DD") from exc
