"""Детерминированные рейсы заглушки поставщика."""

from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Tuple

from flightsearch.domain import AIRPORTS


@lru_cache(maxsize=512)
def generate_flights(supplier_id: str, seed: int, day_iso: str,
                     count: int) -> Tuple[dict, ...]:
    """Для одной даты поставщик всегда возвращает одни и те же рейсы."""
    day = date.fromisoformat(day_iso)
    rng = random.Random("{}:{}:{}".format(supplier_id, seed, day_iso))
    flights = []
    for index in range(count):
        origin = rng.choice(AIRPORTS)
        destination = rng.choice([item for item in AIRPORTS if item != origin])
        minutes = rng.randrange(5 * 60, 23 * 60)
        departure = datetime.combine(day, time.min) + timedelta(minutes=minutes)
        duration = rng.randrange(60, 361)
        arrival = departure + timedelta(minutes=duration)
        price = round(rng.uniform(3000, 25000), 2)
        flight_number = "{}{:04d}".format(supplier_id, index + 1)
        flights.append({
            "uid": "{}:{}:{}".format(supplier_id, flight_number, day_iso),
            "carrier": supplier_id,
            "flight_number": flight_number,
            "origin": origin,
            "destination": destination,
            "departure": departure.isoformat(timespec="minutes"),
            "arrival": arrival.isoformat(timespec="minutes"),
            "price": price,
            "supplier": supplier_id,
        })
    return tuple(flights)


@lru_cache(maxsize=512)
def response_body(supplier_id: str, seed: int, day_iso: str,
                  count: int) -> bytes:
    """Готовый JSON: сериализация не повторяется на каждом запросе."""
    payload = {
        "supplier": supplier_id,
        "date": day_iso,
        "flights": generate_flights(supplier_id, seed, day_iso, count),
    }
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
