"""Общие проверки параметров и настройка журналирования."""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Tuple

from fastapi import HTTPException

IATA = re.compile(r"^[A-Z]{3}$")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def parse_search(origin: str, destination: str,
                 search_date: str) -> Tuple[str, str, date]:
    origin = (origin or "").strip().upper()
    destination = (destination or "").strip().upper()
    if not IATA.match(origin):
        raise HTTPException(
            status_code=422,
            detail="Код аэропорта вылета должен состоять из трёх латинских букв",
        )
    if not IATA.match(destination):
        raise HTTPException(
            status_code=422,
            detail="Код аэропорта прилёта должен состоять из трёх латинских букв",
        )
    if origin == destination:
        raise HTTPException(
            status_code=422,
            detail="Аэропорты вылета и прилёта должны различаться",
        )
    try:
        day = date.fromisoformat(search_date)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Дата должна быть в формате YYYY-MM-DD",
        )
    return origin, destination, day


def health(service: str) -> dict:
    return {"status": "ok", "service": service}
