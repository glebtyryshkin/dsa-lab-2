from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import aio_pika
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

NSK = timezone(timedelta(hours=7))

DIRECT_EXCHANGE = "loyalty"
OPERATIONS_QUEUE = "operations"
ROUTING_KEY = "operation"
QUORUM = {"x-queue-type": "quorum"}


def make_session_factory(database_url: str):
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


def iso(value: datetime) -> str:
    return value.astimezone(NSK).isoformat(timespec="seconds")


async def open_broker(url: str):
    connection = await aio_pika.connect_robust(url)
    channel = await connection.channel(
        publisher_confirms=True,
        on_return_raises=True,
    )
    exchange = await channel.declare_exchange(
        DIRECT_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True
    )
    queue = await channel.declare_queue(
        OPERATIONS_QUEUE, durable=True, arguments=QUORUM
    )
    await queue.bind(exchange, routing_key=ROUTING_KEY)
    return connection, channel, exchange


async def publish_operation(exchange, payload: dict) -> None:
    message = aio_pika.Message(
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        message_id=payload["operation_id"],
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await exchange.publish(message, routing_key=ROUTING_KEY, mandatory=True)
