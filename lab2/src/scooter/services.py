from datetime import datetime

from .aggregates import Trip
from .repositories import ScooterRepository, TripRepository
from .value_objects import BatteryLevel, Money, ScooterId, Tariff, TripId, UserId


class StartTripService:
    """Доменный сервис: начало поездки меняет Scooter и создаёт Trip"""

    def __init__(self, scooters: ScooterRepository, trips: TripRepository):
        self._scooters = scooters
        self._trips = trips

    def start(self, scooter_id: ScooterId, user_id: UserId, tariff: Tariff,
              started_at: datetime) -> Trip:
        scooter = self._scooters.get(scooter_id)
        scooter.rent_out()  # правила 1, 2
        trip = Trip(TripId.new(), scooter_id, user_id, tariff, started_at)
        self._scooters.save(scooter)
        self._trips.save(trip)
        return trip


class FinishTripService:
    """Доменный сервис: завершение поездки меняет Trip и Scooter"""

    def __init__(self, scooters: ScooterRepository, trips: TripRepository):
        self._scooters = scooters
        self._trips = trips

    def finish(self, trip_id: TripId, finished_at: datetime,
               battery_after: BatteryLevel) -> Money:
        trip = self._trips.get(trip_id)
        scooter = self._scooters.get(trip.scooter_id)
        scooter.check_can_return()  # правило 6, до изменений
        cost = trip.finish(finished_at)  # правила 3, 4, 5
        scooter.return_from_trip(battery_after)
        self._trips.save(trip)
        self._scooters.save(scooter)
        return cost
