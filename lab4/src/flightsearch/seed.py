"""Генератор и загрузчик собственной базы расписания.

Запуск: python -m flightsearch.seed
Генератор детерминирован: при повторном запуске получаются те же рейсы.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, time, timedelta
from typing import Iterator, Tuple

import psycopg

from .config import Settings
from .domain import AIRPORTS

log = logging.getLogger("seed")

CARRIERS = ("S7", "SU", "U6", "N4", "FV", "DP", "5N", "UT", "YC", "R3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS flights (
    id BIGSERIAL PRIMARY KEY,
    carrier TEXT NOT NULL,
    flight_number TEXT NOT NULL,
    origin CHAR(3) NOT NULL,
    destination CHAR(3) NOT NULL,
    departure TIMESTAMP NOT NULL,
    arrival TIMESTAMP NOT NULL,
    price NUMERIC(10, 2) NOT NULL,
    flight_date DATE NOT NULL
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_flights_date ON flights (flight_date)",
    "CREATE INDEX IF NOT EXISTS ix_flights_date_origin "
    "ON flights (flight_date, origin)",
)


def generate_rows(settings: Settings) -> Iterator[Tuple]:
    """Поток строк для COPY: 1500 рейсов в сутки в течение 120 дней."""
    rng = random.Random("own-{}".format(settings.seed_start_date.isoformat()))
    for day_index in range(settings.seed_days):
        day = settings.seed_start_date + timedelta(days=day_index)
        for index in range(settings.own_flights_per_day):
            origin = rng.choice(AIRPORTS)
            destination = rng.choice([item for item in AIRPORTS if item != origin])
            carrier = rng.choice(CARRIERS)
            flight_number = "{}{:04d}".format(carrier, index + 1)
            minutes = rng.randrange(5 * 60, 23 * 60)
            departure = datetime.combine(day, time.min) + timedelta(minutes=minutes)
            duration = rng.randrange(60, 361)
            arrival = departure + timedelta(minutes=duration)
            price = round(rng.uniform(3000, 25000), 2)
            yield (
                carrier, flight_number, origin, destination,
                departure, arrival, price, day,
            )


def main() -> None:
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    expected = settings.seed_days * settings.own_flights_per_day
    log.info("Подключение к базе %s", settings.database_url)
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            for statement in INDEXES:
                cur.execute(statement)
            cur.execute("SELECT count(*) FROM flights")
            count = cur.fetchone()[0]
            if count == expected:
                log.info("В таблице уже %d рейсов, загрузка не нужна", count)
                conn.commit()
                return
            if count:
                log.info("В таблице %d рейсов вместо %d: перезаполняем",
                         count, expected)
                cur.execute("TRUNCATE flights RESTART IDENTITY")
            log.info("Загрузка %d рейсов за %d дней", expected,
                     settings.seed_days)
            with cur.copy(
                "COPY flights (carrier, flight_number, origin, destination, "
                "departure, arrival, price, flight_date) FROM STDIN"
            ) as copy:
                for row in generate_rows(settings):
                    copy.write_row(row)
            cur.execute("ANALYZE flights")
        conn.commit()
    log.info("Готово: %d рейсов", expected)


if __name__ == "__main__":
    main()
