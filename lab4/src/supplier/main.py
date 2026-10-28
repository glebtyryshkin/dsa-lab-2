"""Заглушка поставщика авиарейсов (задача 3.2).

Параметры задаются переменными окружения:
    SUPPLIER_ID, MEAN_DELAY_MS, DELAY_SPREAD, ERROR_RATE, SLOW_RATE,
    SLOW_DELAY_S, FLIGHTS_PER_DAY, SUPPLIER_SEED.

Задержка выполняется через await asyncio.sleep, поэтому одновременные
запросы не выстраиваются в очередь внутри имитатора.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date

from fastapi import FastAPI, HTTPException, Query, Response

from flightsearch.config import supplier_settings

from .data import response_body

settings = supplier_settings()
logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("supplier-{}".format(settings["supplier_id"]))

app = FastAPI(title="Поставщик {}".format(settings["supplier_id"]))


@app.get("/health")
async def health():
    return {"status": "ok", "supplier": settings["supplier_id"]}


@app.get("/flights")
async def flights(day: str = Query(..., alias="date")):
    try:
        search_date = date.fromisoformat(day)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Дата должна быть в формате YYYY-MM-DD",
        )

    started = time.perf_counter()
    # Случайные задержки и ошибки не связаны с генератором данных:
    # повторный запрос для той же даты может завершиться успешно.
    delay = (
        settings["mean_delay_ms"] / 1000.0
        * random.uniform(1 - settings["delay_spread"],
                         1 + settings["delay_spread"])
    )
    if random.random() < settings["slow_rate"]:
        delay = settings["slow_delay_s"]
    await asyncio.sleep(max(0.0, delay))

    if random.random() < settings["error_rate"]:
        raise HTTPException(status_code=500, detail="Имитация ошибки поставщика")

    payload = response_body(
        settings["supplier_id"],
        settings["seed"],
        search_date.isoformat(),
        settings["flights_per_day"],
    )
    log.debug(
        "flights %s: %d байт, %.0f мс",
        search_date, len(payload), (time.perf_counter() - started) * 1000,
    )
    # Готовый JSON отдаётся как bytes: FastAPI не сериализует 500 рейсов
    # заново на каждом запросе и не становится узким местом замера.
    return Response(content=payload, media_type="application/json")
