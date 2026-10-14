"""Репозитории агрегатов Scooter и Trip.

Описания репозиториев — Protocol (порт), реализации хранят агрегаты в словаре
в памяти, как и в лабораторной работе №2. Если запись не найдена, выбрасывается
EntityNotFound, который адаптер переводит в код ответа 404.
"""

from typing import Dict, List, Protocol

from .aggregates import Scooter, Trip
from .exceptions import EntityNotFound
from .value_objects import ScooterId, TripId


class ScooterRepository(Protocol):
    def get(self, scooter_id: ScooterId) -> Scooter: ...
    def save(self, scooter: Scooter) -> None: ...
    def all(self) -> List[Scooter]: ...


class TripRepository(Protocol):
    def get(self, trip_id: TripId) -> Trip: ...
    def save(self, trip: Trip) -> None: ...
    def all(self) -> List[Trip]: ...


class InMemoryScooterRepository:
    def __init__(self) -> None:
        self._items: Dict[ScooterId, Scooter] = {}

    def get(self, scooter_id: ScooterId) -> Scooter:
        if scooter_id not in self._items:
            raise EntityNotFound("Самокат не найден")
        return self._items[scooter_id]

    def save(self, scooter: Scooter) -> None:
        self._items[scooter.id] = scooter

    def all(self) -> List[Scooter]:
        return list(self._items.values())


class InMemoryTripRepository:
    def __init__(self) -> None:
        self._items: Dict[TripId, Trip] = {}

    def get(self, trip_id: TripId) -> Trip:
        if trip_id not in self._items:
            raise EntityNotFound("Поездка не найдена")
        return self._items[trip_id]

    def save(self, trip: Trip) -> None:
        self._items[trip.id] = trip

    def all(self) -> List[Trip]:
        return list(self._items.values())

    def find_active_by_scooter(self, scooter_id: ScooterId) -> Trip:
        for trip in self._items.values():
            if trip.scooter_id == scooter_id and not trip.is_finished:
                return trip
        raise EntityNotFound("Активная поездка этого самоката не найдена")
