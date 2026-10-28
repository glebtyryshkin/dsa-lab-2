"""Настройки сервиса из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import List, Tuple

DEFAULT_START = date(2026, 11, 1)


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value in (None, "") else int(value)


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value in (None, "") else float(value)


def _date(name: str, default: date) -> date:
    value = os.getenv(name)
    return default if value in (None, "") else date.fromisoformat(value)


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_url_sync: str
    database_url_async: str
    supplier_urls: Tuple[str, ...]
    supplier_timeout: float
    retry_delay: float
    seed_start_date: date
    seed_days: int
    own_flights_per_day: int
    route_limit: int
    db_pool_size: int
    db_max_overflow: int
    search_concurrency: int
    process_pool_workers: int

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = _str(
            "DATABASE_URL",
            "postgresql://postgres:postgres@db:5432/flights",
        )
        sync_default = database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        async_default = database_url.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
        urls = tuple(
            item.strip()
            for item in _str(
                "SUPPLIER_URLS",
                "http://supplier1:8000,http://supplier2:8000,"
                "http://supplier3:8000",
            ).split(",")
            if item.strip()
        )
        return cls(
            database_url=database_url,
            database_url_sync=_str("DATABASE_URL_SYNC", sync_default),
            database_url_async=_str("DATABASE_URL_ASYNC", async_default),
            supplier_urls=urls,
            supplier_timeout=_float("SUPPLIER_TIMEOUT", 2.0),
            retry_delay=_float("SUPPLIER_RETRY_DELAY", 0.2),
            seed_start_date=_date("SEED_START_DATE", DEFAULT_START),
            seed_days=_int("SEED_DAYS", 120),
            own_flights_per_day=_int("OWN_FLIGHTS_PER_DAY", 1500),
            route_limit=_int("ROUTE_LIMIT", 20),
            db_pool_size=_int("DB_POOL_SIZE", 20),
            db_max_overflow=_int("DB_MAX_OVERFLOW", 40),
            search_concurrency=_int("SEARCH_CONCURRENCY", 35),
            process_pool_workers=_int("PROCESS_POOL_WORKERS", 2),
        )


def supplier_settings() -> dict:
    """Параметры заглушки поставщика."""
    return {
        "supplier_id": _str("SUPPLIER_ID", "S1"),
        "mean_delay_ms": _int("MEAN_DELAY_MS", 300),
        "delay_spread": _float("DELAY_SPREAD", 0.30),
        "error_rate": _float("ERROR_RATE", 0.05),
        "slow_rate": _float("SLOW_RATE", 0.0),
        "slow_delay_s": _float("SLOW_DELAY_S", 3.0),
        "flights_per_day": _int("FLIGHTS_PER_DAY", 500),
        "seed": _int("SUPPLIER_SEED", 0),
    }
