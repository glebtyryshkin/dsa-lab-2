"""Статистика по магазинам.

Очередь своя, копия события приходит через обменник fanout.
Данные живут в памяти процесса.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import aio_pika
from fastapi import FastAPI, HTTPException

from .config import load_settings
from .stats import apply_event

log = logging.getLogger("loyalty.analytics")

EVENTS_EXCHANGE = "loyalty.events"
QUEUE_NAME = "analytics.events"
QUORUM = {"x-queue-type": "quorum"}


class Board:
    def __init__(self):
        self.stores: dict = {}
        self.received = 0
        self.seen: set[str] = set()

    def handle(self, event: dict) -> None:
        operation_id = event["operation_id"]
        if operation_id in self.seen:
            return
        self.seen.add(operation_id)
        self.received += 1
        apply_event(self.stores, event)
        log.info(
            "событие %s магазин %s %s",
            operation_id,
            event["store_id"],
            event["status"],
        )


async def declare(channel):
    exchange = await channel.declare_exchange(
        EVENTS_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
    )
    queue = await channel.declare_queue(QUEUE_NAME, durable=True, arguments=QUORUM)
    await queue.bind(exchange)
    return queue


async def consume(url: str, board: Board) -> None:
    connection = await aio_pika.connect_robust(url)
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await declare(channel)
        log.info("слушаю очередь %s", QUEUE_NAME)
        async with queue.iterator() as messages:
            async for message in messages:
                async with message.process():
                    event = json.loads(message.body.decode("utf-8"))
                    board.handle(event)
    finally:
        await connection.close()


async def consume_forever(url: str, board: Board) -> None:
    while True:
        try:
            await consume(url, board)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("получение событий прервано, повтор через 2 с")
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = load_settings()
    board = Board()
    app.state.board = board
    task = asyncio.create_task(consume_forever(settings.rabbitmq_url, board))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Статистика бонусной программы", lifespan=lifespan)


@app.get("/stores")
async def stores():
    board: Board = app.state.board
    return [board.stores[key] for key in sorted(board.stores)]


@app.get("/stores/{store_id}")
async def store(store_id: str):
    board: Board = app.state.board
    row = board.stores.get(store_id)
    if row is None:
        raise HTTPException(status_code=404, detail="магазин не найден")
    return row


@app.get("/stats")
async def stats():
    return {"events_received": app.state.board.received}
