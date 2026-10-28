"""Полностью асинхронная версия сервиса (задача 4.2).

httpx.AsyncClient и асинхронный движок SQLAlchemy создаются один раз в
lifespan. База данных и три поставщика опрашиваются одновременно через
asyncio.gather, на каждого поставщика отводится asyncio.timeout(2).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncio

import httpx
from fastapi import FastAPI, HTTPException, Query

from .api_common import configure_logging, health, parse_search
from .config import Settings
from .db import make_async_engine
from .service import AsyncSearchService, SearchUnavailable
from .suppliers import AsyncSupplierClient

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    engine = make_async_engine(settings)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        # Пул соединений ограничен: при высокой конкурентности httpx
        # тратит заметную часть процессора на перебор соединений.
        limits=httpx.Limits(max_connections=30,
                            max_keepalive_connections=30),
    )
    suppliers = AsyncSupplierClient(
        client,
        settings.supplier_urls,
        timeout=settings.supplier_timeout,
        retry_delay=settings.retry_delay,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.client = client
    # Ограничение одновременных поисков: при 80 клиентах один процесс
    # иначе тратит процессор на перебор соединений httpx, а не на работу.
    app.state.search_slots = asyncio.Semaphore(settings.search_concurrency)
    app.state.service = AsyncSearchService(
        engine, suppliers, settings.route_limit, service_name="search-async"
    )
    yield
    await client.aclose()
    await engine.dispose()


app = FastAPI(
    title="Агрегатор авиаперелётов — асинхронная версия",
    lifespan=lifespan,
)


@app.get("/health")
async def health_route():
    return health("search-async")


@app.get("/search")
async def search_route(
    origin: str,
    destination: str,
    search_date: str = Query(..., alias="date"),
):
    origin, destination, day = parse_search(origin, destination, search_date)
    try:
        async with app.state.search_slots:
            return await app.state.service.search(origin, destination, day)
    except SearchUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
