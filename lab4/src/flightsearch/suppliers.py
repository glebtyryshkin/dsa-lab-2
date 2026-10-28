"""Клиенты трёх поставщиков: синхронный и асинхронный.

На каждого поставщика отводится 2 секунды, включая повторный запрос.
Повтор выполняется один раз через 200 мс, если первый ответ завершился
ошибкой. Тайм-аут повторять бессмысленно: общий срок уже истёк.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import httpx
import requests

from .domain import Flight


@dataclass
class SupplierResult:
    name: str
    flights: List[Flight] = field(default_factory=list)
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


def _name_from_url(url: str) -> str:
    host = urlparse(url).hostname or url
    if host.startswith("supplier"):
        return "S" + host[len("supplier"):]
    return host


def _parse_payload(payload: dict, name: str, day: date) -> List[Flight]:
    items = payload.get("flights")
    if not isinstance(items, list):
        raise ValueError("В ответе поставщика нет списка flights")
    flights: List[Flight] = []
    for item in items:
        departure = datetime.fromisoformat(str(item["departure"]))
        arrival = datetime.fromisoformat(str(item["arrival"]))
        price = Decimal(str(item["price"]))
        flights.append(
            Flight(
                uid=str(item.get("uid") or "{}:{}:{}".format(
                    name, item["flight_number"], day.isoformat())),
                carrier=str(item["carrier"]),
                flight_number=str(item["flight_number"]),
                origin=str(item["origin"]),
                destination=str(item["destination"]),
                departure=departure,
                arrival=arrival,
                price=price,
                supplier=str(item.get("supplier", name)),
            )
        )
    return flights


class SyncSupplierClient:
    """Последовательный опрос поставщиков библиотекой requests."""

    def __init__(self, urls: Sequence[str], timeout: float = 2.0,
                 retry_delay: float = 0.2):
        self._endpoints: List[Tuple[str, str]] = [
            (_name_from_url(url), url.rstrip("/")) for url in urls
        ]
        self._timeout = timeout
        self._retry_delay = retry_delay
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=16, pool_maxsize=16, max_retries=0
        )
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def close(self) -> None:
        self._session.close()

    def fetch_all(self, day: date) -> List[SupplierResult]:
        return [self.fetch_one(name, url, day) for name, url in self._endpoints]

    def fetch_one(self, name: str, url: str, day: date) -> SupplierResult:
        started = time.monotonic()
        deadline = started + self._timeout
        last_error = "timeout"
        attempts = 0
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = "timeout"
                break
            attempts = attempt
            try:
                response = self._session.get(
                    "{}/flights".format(url),
                    params={"date": day.isoformat()},
                    timeout=remaining,
                )
                if response.status_code == 200:
                    flights = _parse_payload(response.json(), name, day)
                    return SupplierResult(
                        name=name,
                        flights=flights,
                        elapsed_ms=(time.monotonic() - started) * 1000,
                        attempts=attempts,
                    )
                last_error = "HTTP {}".format(response.status_code)
            except (requests.RequestException, ValueError, KeyError,
                    InvalidOperation, TypeError) as exc:
                last_error = type(exc).__name__
            if attempt == 1:
                remaining = deadline - time.monotonic()
                if remaining <= self._retry_delay:
                    break
                time.sleep(self._retry_delay)
        return SupplierResult(
            name=name,
            error=last_error,
            elapsed_ms=(time.monotonic() - started) * 1000,
            attempts=attempts,
        )


class AsyncSupplierClient:
    """Одновременный опрос поставщиков через httpx.AsyncClient."""

    def __init__(self, client: httpx.AsyncClient, urls: Sequence[str],
                 timeout: float = 2.0, retry_delay: float = 0.2):
        self._client = client
        self._endpoints: List[Tuple[str, str]] = [
            (_name_from_url(url), url.rstrip("/")) for url in urls
        ]
        self._timeout = timeout
        self._retry_delay = retry_delay

    @property
    def endpoints(self) -> List[Tuple[str, str]]:
        return list(self._endpoints)

    async def fetch_one(self, name: str, url: str, day: date) -> SupplierResult:
        started = asyncio.get_running_loop().time()
        deadline = started + self._timeout
        last_error = "timeout"
        attempts = 0
        try:
            async with asyncio.timeout(self._timeout):
                for attempt in (1, 2):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        last_error = "timeout"
                        break
                    attempts = attempt
                    try:
                        response = await self._client.get(
                            "{}/flights".format(url),
                            params={"date": day.isoformat()},
                            timeout=remaining,
                        )
                        if response.status_code == 200:
                            flights = _parse_payload(response.json(), name, day)
                            return SupplierResult(
                                name=name,
                                flights=flights,
                                elapsed_ms=(
                                    asyncio.get_running_loop().time() - started
                                ) * 1000,
                                attempts=attempts,
                            )
                        last_error = "HTTP {}".format(response.status_code)
                    except (httpx.HTTPError, ValueError, KeyError,
                            InvalidOperation, TypeError) as exc:
                        last_error = type(exc).__name__
                    if attempt == 1:
                        remaining = (
                            deadline - asyncio.get_running_loop().time()
                        )
                        if remaining <= self._retry_delay:
                            break
                        await asyncio.sleep(self._retry_delay)
        except TimeoutError:
            last_error = "timeout"
        return SupplierResult(
            name=name,
            error=last_error,
            elapsed_ms=(asyncio.get_running_loop().time() - started) * 1000,
            attempts=attempts,
        )

    async def fetch_all(self, day: date) -> List[SupplierResult]:
        tasks = [
            self.fetch_one(name, url, day) for name, url in self._endpoints
        ]
        return list(await asyncio.gather(*tasks))
