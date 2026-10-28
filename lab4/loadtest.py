#!/usr/bin/env python3
"""Нагрузочный скрипт для сервиса поиска (задача 3.5).

Режимы:
  closed — C клиентов, каждый отправляет следующий запрос после ответа;
  open   — запросы с заданной интенсивностью, интервалы распределены
           по экспоненциальному закону.

Во время замера отдельная задача каждые 100 мс опрашивает GET /health.
Перцентили считаются методом ближайшего ранга.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import httpx

AIRPORTS = (
    "LED", "SVO", "DME", "VKO", "AER", "KZN", "OVB", "SVX", "UFA", "ROV",
    "KRR", "KJA", "CEK", "TJM", "SGC", "PEE", "KUF", "VOG", "MRV", "IJK",
    "IKT", "UUD", "BQS", "PKC", "YKS", "GDX", "MMK", "ASF", "ARH", "RTW",
)
SEED_START = date(2026, 11, 1)
SEED_DAYS = 120


def build_queries(seed: int, count: int = 5000) -> List[Dict[str, str]]:
    """Одинаковый набор запросов для всех версий сервиса."""
    rng = random.Random(seed)
    queries = []
    for _ in range(count):
        origin = rng.choice(AIRPORTS)
        destination = rng.choice([item for item in AIRPORTS if item != origin])
        day = SEED_START + timedelta(days=rng.randrange(SEED_DAYS))
        queries.append({
            "origin": origin,
            "destination": destination,
            "date": day.isoformat(),
        })
    return queries


def percentile(values: List[float], percent: float) -> Optional[float]:
    """Перцентиль методом ближайшего ранга."""
    if not values:
        return None
    ordered = sorted(values)
    index = math.ceil(percent / 100.0 * len(ordered)) - 1
    index = max(0, min(index, len(ordered) - 1))
    return ordered[index]


def latency_stats(values: List[float]) -> dict:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "p99": None,
                "max": None, "mean": None}
    return {
        "count": len(values),
        "p50": round(percentile(values, 50), 1),
        "p95": round(percentile(values, 95), 1),
        "p99": round(percentile(values, 99), 1),
        "max": round(max(values), 1),
        "mean": round(sum(values) / len(values), 1),
    }


class Metrics:
    def __init__(self) -> None:
        self.started = 0
        self.completed = 0
        self.errors = 0
        self.status_errors: Dict[str, int] = {}
        self.success: List[float] = []
        self.health_started = 0
        self.health_completed = 0
        self.health_errors = 0
        self.health_success: List[float] = []
        self.rows: List[dict] = []

    def add(self, kind: str, latency_ms: float, status: Optional[int],
            error: Optional[str], started_at: float) -> None:
        self.rows.append({
            "kind": kind,
            "started_at": round(started_at, 6),
            "latency_ms": round(latency_ms, 2),
            "status": status,
            "error": error,
        })
        if kind == "search":
            self.completed += 1
            if error is not None:
                self.errors += 1
            elif status is not None and status >= 400:
                self.errors += 1
                self.status_errors[str(status)] = (
                    self.status_errors.get(str(status), 0) + 1
                )
            else:
                self.success.append(latency_ms)
        else:
            self.health_completed += 1
            if error is not None or (status is not None and status >= 400):
                self.health_errors += 1
            else:
                self.health_success.append(latency_ms)


async def request_once(client: httpx.AsyncClient, query: dict,
                       timeout: float):
    started = time.perf_counter()
    try:
        response = await client.get(
            "/search", params=query, timeout=timeout
        )
        return (response.status_code, None,
                (time.perf_counter() - started) * 1000)
    except (httpx.TimeoutException, httpx.HTTPError, OSError) as exc:
        return (None, type(exc).__name__,
                (time.perf_counter() - started) * 1000)
    except asyncio.CancelledError:
        raise


async def health_once(client: httpx.AsyncClient, timeout: float):
    started = time.perf_counter()
    try:
        response = await client.get("/health", timeout=timeout)
        return (response.status_code, None,
                (time.perf_counter() - started) * 1000)
    except (httpx.TimeoutException, httpx.HTTPError, OSError) as exc:
        return (None, type(exc).__name__,
                (time.perf_counter() - started) * 1000)


async def run(args: argparse.Namespace) -> dict:
    queries = build_queries(args.seed)
    query_index = 0

    def next_query() -> dict:
        nonlocal query_index
        query = queries[query_index % len(queries)]
        query_index += 1
        return query

    metrics = Metrics()
    loop = asyncio.get_running_loop()
    measure_start = float("inf")
    measure_end = float("inf")

    limits = httpx.Limits(
        max_connections=max(100, args.clients * 3, args.max_outstanding),
        max_keepalive_connections=max(50, args.clients * 2),
    )
    client = httpx.AsyncClient(
        base_url=args.url.rstrip("/"),
        timeout=httpx.Timeout(args.timeout),
        limits=limits,
    )

    stop = asyncio.Event()
    workers: List[asyncio.Task] = []
    open_tasks: set = set()
    semaphore = asyncio.Semaphore(args.max_outstanding)

    async def closed_worker() -> None:
        while not stop.is_set():
            query = next_query()
            started_at = loop.time()
            measured = started_at >= measure_start
            if measured:
                metrics.started += 1
            try:
                status, error, latency = await request_once(
                    client, query, args.timeout
                )
            except asyncio.CancelledError:
                raise
            if measured:
                metrics.add("search", latency, status, error,
                            started_at - measure_start)

    async def open_sender(query: dict) -> None:
        started_at = loop.time()
        measured = started_at >= measure_start
        if measured:
            metrics.started += 1
        try:
            status, error, latency = await request_once(
                client, query, args.timeout
            )
            if measured:
                metrics.add("search", latency, status, error,
                            started_at - measure_start)
        finally:
            semaphore.release()

    async def open_scheduler() -> None:
        rng = random.Random(args.seed + 1)
        while True:
            interval = rng.expovariate(args.rate)
            await asyncio.sleep(interval)
            if loop.time() >= measure_end:
                break
            await semaphore.acquire()
            task = asyncio.create_task(open_sender(next_query()))
            open_tasks.add(task)
            task.add_done_callback(open_tasks.discard)

    async def health_poller() -> None:
        while True:
            now = loop.time()
            if now >= measure_end:
                break
            measured = now >= measure_start
            if measured:
                metrics.health_started += 1
            status, error, latency = await health_once(client, args.timeout)
            if measured:
                metrics.add("health", latency, status, error,
                            now - measure_start)
            elapsed = loop.time() - now
            await asyncio.sleep(max(0.0, 0.1 - elapsed))

    # Прогрев: соединения и кэши заполняются до начала учёта.
    if args.mode == "closed":
        workers = [asyncio.create_task(closed_worker())
                   for _ in range(args.clients)]
    else:
        workers = [asyncio.create_task(open_scheduler())]
    health_task = asyncio.create_task(health_poller())

    await asyncio.sleep(args.warmup)
    measure_start = loop.time()
    measure_end = measure_start + args.duration
    await asyncio.sleep(args.duration)

    stop.set()
    # Даём короткое время на завершение уже отправленных запросов.
    await asyncio.sleep(0.5)
    for task in workers + [health_task]:
        task.cancel()
    await asyncio.gather(*workers, health_task, return_exceptions=True)
    for task in list(open_tasks):
        task.cancel()
    if open_tasks:
        await asyncio.gather(*open_tasks, return_exceptions=True)
    await client.aclose()

    aborted = max(0, metrics.started - metrics.completed)
    health_aborted = max(0, metrics.health_started - metrics.health_completed)
    total_requests = metrics.started
    error_count = metrics.errors
    result = {
        "label": args.label,
        "url": args.url,
        "mode": args.mode,
        "clients": args.clients if args.mode == "closed" else None,
        "rate": args.rate if args.mode == "open" else None,
        "duration_s": args.duration,
        "warmup_s": args.warmup,
        "timeout_s": args.timeout,
        "started": metrics.started,
        "completed": metrics.completed,
        "aborted": aborted,
        "success": len(metrics.success),
        "errors": error_count,
        "error_rate": round(error_count / total_requests * 100, 2)
        if total_requests else 0.0,
        "rps": round(metrics.completed / args.duration, 2),
        "offered_rps": round(total_requests / args.duration, 2),
        "status_errors": metrics.status_errors,
        "latency_ms": latency_stats(metrics.success),
        "health": {
            "started": metrics.health_started,
            "completed": metrics.health_completed,
            "aborted": health_aborted,
            "errors": metrics.health_errors,
            "aborted": health_aborted,
            "error_rate": round(
                metrics.health_errors / metrics.health_started * 100, 2
            ) if metrics.health_started else 0.0,
            "latency_ms": latency_stats(metrics.health_success),
        },
        "raw": {
            "success_ms": [round(item, 2) for item in metrics.success],
            "health_ms": [round(item, 2) for item in metrics.health_success],
        },
    }
    raw_rows = metrics.rows
    return result, raw_rows


def print_summary(result: dict) -> None:
    latency = result["latency_ms"]
    health = result["health"]["latency_ms"]
    print(
        "{label}: {mode} {clients}{rate} | RPS={rps:.2f} "
        "({completed}/{started}) | p50={p50} p95={p95} p99={p99} "
        "max={max} мс | ошибки={err:.1f}% | /health p95={hp95} мс".format(
            label=result["label"],
            mode=result["mode"],
            clients=("C={}".format(result["clients"])
                     if result["clients"] else ""),
            rate=(" λ={}".format(result["rate"]) if result["rate"] else ""),
            rps=result["rps"],
            completed=result["completed"],
            started=result["started"],
            p50=latency["p50"],
            p95=latency["p95"],
            p99=latency["p99"],
            max=latency["max"],
            err=result["error_rate"],
            hp95=health["p95"],
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--mode", choices=("closed", "open"), required=True)
    parser.add_argument("--clients", type=int, default=1)
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--warmup", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-outstanding", type=int, default=2000)
    parser.add_argument("--label", default="run")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--csv-out", default=None)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration должен быть больше нуля")

    result, rows = asyncio.run(run(args))
    print_summary(result)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.csv_out:
        path = Path(args.csv_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("kind", "started_at", "latency_ms", "status",
                            "error"),
            )
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
