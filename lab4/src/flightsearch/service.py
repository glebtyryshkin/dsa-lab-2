"""Прикладной сервис поиска авиаперелётов."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from typing import Dict, List, Sequence

from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import load_own_flights, load_own_flights_async
from .domain import Flight, build_routes
from .suppliers import AsyncSupplierClient, SupplierResult, SyncSupplierClient

log = logging.getLogger("search")


class SearchUnavailable(Exception):
    """Не ответил ни один поставщик и в своей базе нет рейсов."""


def _response(service: str, origin: str, destination: str, day: date,
              own: Sequence[Flight], supplier_results: Sequence[SupplierResult],
              routes, timings: Dict[str, float]) -> dict:
    unavailable = [item.name for item in supplier_results if not item.ok]
    answered = sum(1 for item in supplier_results if item.ok)
    all_flights = list(own)
    for item in supplier_results:
        all_flights.extend(item.flights)
    return {
        "service": service,
        "origin": origin,
        "destination": destination,
        "date": day.isoformat(),
        "routes": [route.to_dict() for route in routes],
        "unavailable_suppliers": unavailable,
        "suppliers_answered": answered,
        "own_flights": len(own),
        "processing_time_ms": round(timings["total_ms"], 1),
        "timings_ms": {key: round(value, 1) for key, value in timings.items()},
        "counts": {
            "flights": len(all_flights),
            "routes": len(routes),
        },
    }


class SyncSearchService:
    """Синхронная версия: последовательные запросы и синхронная БД."""

    def __init__(self, engine: Engine, suppliers: SyncSupplierClient,
                 route_limit: int = 20, service_name: str = "search-sync"):
        self._engine = engine
        self._suppliers = suppliers
        self._route_limit = route_limit
        self._service_name = service_name

    def search(self, origin: str, destination: str, day: date) -> dict:
        started = time.perf_counter()
        own = load_own_flights(self._engine, day)
        after_db = time.perf_counter()
        supplier_results = self._suppliers.fetch_all(day)
        after_suppliers = time.perf_counter()
        flights = list(own)
        for item in supplier_results:
            flights.extend(item.flights)
        routes = build_routes(flights, origin, destination, day,
                              self._route_limit)
        finished = time.perf_counter()
        timings = {
            "db_ms": (after_db - started) * 1000,
            "suppliers_ms": (after_suppliers - after_db) * 1000,
            "route_ms": (finished - after_suppliers) * 1000,
            "total_ms": (finished - started) * 1000,
        }
        if not own and all(not item.ok for item in supplier_results):
            raise SearchUnavailable(
                "Поиск временно недоступен: поставщики не ответили, "
                "в собственной базе нет рейсов на эту дату"
            )
        log.info(
            "search %s-%s %s: %d своих, %d маршрутов, %.0f мс, нет ответа: %s",
            origin, destination, day, len(own), len(routes),
            timings["total_ms"],
            ",".join(item.name for item in supplier_results if not item.ok)
            or "-",
        )
        return _response(self._service_name, origin, destination, day, own,
                         supplier_results, routes, timings)


class AsyncSearchService:
    """Полностью асинхронная версия: БД и поставщики одновременно."""

    def __init__(self, engine: AsyncEngine, suppliers: AsyncSupplierClient,
                 route_limit: int = 20, service_name: str = "search-async"):
        self._engine = engine
        self._suppliers = suppliers
        self._route_limit = route_limit
        self._service_name = service_name

    async def _compute_routes(self, flights, origin: str, destination: str,
                              day: date):
        return build_routes(flights, origin, destination, day,
                            self._route_limit)

    async def search(self, origin: str, destination: str, day: date) -> dict:
        started = time.perf_counter()
        endpoints = self._suppliers.endpoints
        tasks = [self._suppliers.fetch_one(name, url, day)
                 for name, url in endpoints]
        results = await asyncio.gather(
            load_own_flights_async(self._engine, day),
            *tasks,
            return_exceptions=True,
        )
        after_io = time.perf_counter()

        own_result = results[0]
        if isinstance(own_result, Exception):
            raise own_result
        own = list(own_result)
        supplier_results: List[SupplierResult] = []
        for index, item in enumerate(results[1:]):
            if isinstance(item, Exception):
                supplier_results.append(SupplierResult(
                    name=endpoints[index][0],
                    error=type(item).__name__,
                ))
            else:
                supplier_results.append(item)

        flights = list(own)
        for item in supplier_results:
            flights.extend(item.flights)
        # Составление маршрутов занимает единицы миллисекунд и не ходит
        # в сеть или базу. Выполняем его в цикле событий: вынос в пул
        # потоков на высокой конкурентности только добавляет борьбу за GIL.
        routes = await self._compute_routes(
            flights, origin, destination, day
        )
        finished = time.perf_counter()
        timings = {
            "io_wall_ms": (after_io - started) * 1000,
            "suppliers_max_ms": max(
                (item.elapsed_ms for item in supplier_results), default=0.0
            ),
            "suppliers_sum_ms": sum(
                item.elapsed_ms for item in supplier_results
            ),
            "route_ms": (finished - after_io) * 1000,
            "total_ms": (finished - started) * 1000,
        }
        if not own and all(not item.ok for item in supplier_results):
            raise SearchUnavailable(
                "Поиск временно недоступен: поставщики не ответили, "
                "в собственной базе нет рейсов на эту дату"
            )
        log.info(
            "search-async %s-%s %s: %d своих, %d маршрутов, %.0f мс, "
            "нет ответа: %s",
            origin, destination, day, len(own), len(routes),
            timings["total_ms"],
            ",".join(item.name for item in supplier_results if not item.ok)
            or "-",
        )
        return _response(self._service_name, origin, destination, day, own,
                         supplier_results, routes, timings)


class ProcessSearchService(AsyncSearchService):
    """Версия 4: вычисления маршрутов выполняются в пуле процессов."""

    def __init__(self, engine: AsyncEngine, suppliers: AsyncSupplierClient,
                 executor, route_limit: int = 20,
                 service_name: str = "search-process"):
        super().__init__(engine, suppliers, route_limit, service_name)
        self._executor = executor

    async def _compute_routes(self, flights, origin: str, destination: str,
                              day: date):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            build_routes,
            flights,
            origin,
            destination,
            day,
            self._route_limit,
        )
