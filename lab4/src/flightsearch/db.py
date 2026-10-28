"""Чтение собственного расписания из PostgreSQL."""

from __future__ import annotations

from datetime import date
from typing import List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.engine import Engine

from .domain import Flight
from .config import Settings

SQL = text(
    "SELECT id, carrier, flight_number, origin, destination, departure, "
    "arrival, price FROM flights WHERE flight_date = :day"
)


def make_sync_engine(settings: Settings) -> Engine:
    from sqlalchemy import create_engine

    return create_engine(
        settings.database_url_sync,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


def make_async_engine(settings: Settings) -> AsyncEngine:
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(
        settings.database_url_async,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


def _row_to_flight(row) -> Flight:
    return Flight(
        uid="own:{}".format(row.id),
        carrier=row.carrier,
        flight_number=row.flight_number,
        origin=row.origin.strip(),
        destination=row.destination.strip(),
        departure=row.departure,
        arrival=row.arrival,
        price=row.price,
        supplier="own",
    )


def load_own_flights(engine: Engine, day: date) -> List[Flight]:
    with engine.connect() as connection:
        rows = connection.execute(SQL, {"day": day}).mappings().all()
    return [_row_to_flight(row) for row in rows]


async def load_own_flights_async(engine: AsyncEngine, day: date) -> List[Flight]:
    async with engine.connect() as connection:
        result = await connection.execute(SQL, {"day": day})
        rows = result.mappings().all()
    return [_row_to_flight(row) for row in rows]
