from typing import Protocol

from .aggregates import Scooter, Trip
from .exceptions import DomainError
from .value_objects import ScooterId, TripId


class ScooterRepository(Protocol):
    def get(self, scooter_id: ScooterId) -> Scooter: ...
    def save(self, scooter: Scooter) -> None: ...


class TripRepository(Protocol):
    def get(self, trip_id: TripId) -> Trip: ...
    def save(self, trip: Trip) -> None: ...


class InMemoryScooterRepository:
    def __init__(self) -> None:
        self._items: dict[ScooterId, Scooter] = {}

    def get(self, scooter_id: ScooterId) -> Scooter:
        if scooter_id not in self._items:
            raise DomainError("Самокат не найден")
        return self._items[scooter_id]

    def save(self, scooter: Scooter) -> None:
        self._items[scooter.id] = scooter


class InMemoryTripRepository:
    def __init__(self) -> None:
        self._items: dict[TripId, Trip] = {}

    def get(self, trip_id: TripId) -> Trip:
        if trip_id not in self._items:
            raise DomainError("Поездка не найдена")
        return self._items[trip_id]

    def save(self, trip: Trip) -> None:
        self._items[trip.id] = trip
