"""Синхронная версия сервиса поиска (задача 3.3).

Обработчики объявлены через def и выполняются FastAPI в пуле потоков.
Внутри — requests и синхронный SQLAlchemy Core.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from .api_common import configure_logging, health, parse_search
from .config import Settings
from .db import make_sync_engine
from .service import SearchUnavailable, SyncSearchService
from .suppliers import SyncSupplierClient

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    engine = make_sync_engine(settings)
    client = SyncSupplierClient(
        settings.supplier_urls,
        timeout=settings.supplier_timeout,
        retry_delay=settings.retry_delay,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.client = client
    app.state.service = SyncSearchService(
        engine, client, settings.route_limit, service_name="search-sync"
    )
    yield
    client.close()
    engine.dispose()


app = FastAPI(
    title="Агрегатор авиаперелётов — синхронная версия",
    lifespan=lifespan,
)


@app.get("/health")
def health_route():
    return health("search-sync")


@app.get("/search")
def search_route(
    origin: str,
    destination: str,
    search_date: str = Query(..., alias="date"),
):
    origin, destination, day = parse_search(origin, destination, search_date)
    try:
        return app.state.service.search(origin, destination, day)
    except SearchUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
