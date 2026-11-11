"""Уведомления покупателям.

События читаются в фоне, чтобы HTTP-маршруты не ждали очередь.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aio_pika
from fastapi import FastAPI

from .config import load_settings
from .text import notification_text

log = logging.getLogger("loyalty.notifier")

NSK = timezone(timedelta(hours=7))
EVENTS_EXCHANGE = "loyalty.events"
QUEUE_NAME = "notifier.events"
QUORUM = {"x-queue-type": "quorum"}


class Inbox:
    def __init__(self, unsubscribed: frozenset, min_delay_ms: int, max_delay_ms: int):
        self.unsubscribed = unsubscribed
        self.min_delay_ms = min_delay_ms
        self.max_delay_ms = max_delay_ms
        self.notifications: list[dict] = []
        self.received = 0
        self.sent = 0
        self.seen: set[str] = set()

    async def handle(self, event: dict) -> None:
        operation_id = event["operation_id"]
        if operation_id in self.seen:
            return
        self.seen.add(operation_id)
        self.received += 1
        customer_id = event["customer_id"]
        if customer_id in self.unsubscribed:
            log.info("покупатель %s отписан, уведомление не отправлено", customer_id)
            return
        text = notification_text(event)
        delay_ms = random.randint(self.min_delay_ms, self.max_delay_ms)
        await asyncio.sleep(delay_ms / 1000)
        self.notifications.append(
            {
                "operation_id": operation_id,
                "customer_id": customer_id,
                "text": text,
                "sent_at": datetime.now(NSK).replace(microsecond=0).isoformat(timespec="seconds"),
            }
        )
        self.sent += 1
        log.info("отправлено за %s мс, %s: %s", delay_ms, customer_id, text)


async def declare(channel):
    exchange = await channel.declare_exchange(
        EVENTS_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
    )
    queue = await channel.declare_queue(QUEUE_NAME, durable=True, arguments=QUORUM)
    await queue.bind(exchange)
    return queue


async def consume(url: str, inbox: Inbox) -> None:
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
                    await inbox.handle(event)
    finally:
        await connection.close()


async def consume_forever(url: str, inbox: Inbox) -> None:
    while True:
        try:
            await consume(url, inbox)
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
    inbox = Inbox(settings.unsubscribed, settings.min_delay_ms, settings.max_delay_ms)
    app.state.inbox = inbox
    task = asyncio.create_task(consume_forever(settings.rabbitmq_url, inbox))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Уведомления бонусной программы", lifespan=lifespan)


@app.get("/notifications")
async def notifications(customer_id: str):
    rows = [
        item for item in app.state.inbox.notifications if item["customer_id"] == customer_id
    ]
    return rows


@app.get("/stats")
async def stats():
    inbox: Inbox = app.state.inbox
    return {
        "events_received": inbox.received,
        "notifications_sent": inbox.sent,
    }
