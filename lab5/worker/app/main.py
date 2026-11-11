"""Обработчик операций.

Сообщение из очереди operations подтверждаем только после фиксации
транзакции и подтверждения публикации события брокером.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import aio_pika
from sqlalchemy import select

from .config import load_settings
from .db import create_tables, make_session_factory
from .domain import ExistingPurchase, decide
from .models import Account, Operation

log = logging.getLogger("loyalty.worker")

NSK = timezone(timedelta(hours=7))
READY = Path("/tmp/ready")

DIRECT_EXCHANGE = "loyalty"
EVENTS_EXCHANGE = "loyalty.events"
OPERATIONS_QUEUE = "operations"
NOTIFIER_QUEUE = "notifier.events"
ANALYTICS_QUEUE = "analytics.events"
ROUTING_KEY = "operation"
QUORUM = {"x-queue-type": "quorum"}


class BadMessage(Exception):
    pass


def iso(value: datetime) -> str:
    return value.astimezone(NSK).isoformat(timespec="seconds")


def event_from(row: Operation) -> dict:
    return {
        "operation_id": row.operation_id,
        "type": row.type,
        "customer_id": row.customer_id,
        "store_id": row.store_id,
        "status": row.status,
        "points_delta": row.points_delta,
        "balance_after": row.balance_after,
        "reject_reason": row.reject_reason,
        "processed_at": iso(row.processed_at),
    }


def parse_body(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BadMessage("не удалось разобрать JSON") from exc
    if not isinstance(data, dict) or "operation_id" not in data:
        raise BadMessage("в сообщении нет operation_id")
    return data


async def apply_operation(session, data: dict) -> dict:
    operation_id = str(data["operation_id"])
    existing = await session.get(Operation, operation_id)
    if existing is not None:
        return event_from(existing)

    try:
        occurred_at = datetime.fromisoformat(data["occurred_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BadMessage("некорректное occurred_at") from exc

    op_type = data.get("type")
    customer_id = data.get("customer_id")
    check_number = data.get("check_number")
    if not op_type or not customer_id or not check_number:
        raise BadMessage("не хватает полей операции")

    amount = None
    if data.get("amount") is not None:
        amount = Decimal(str(data["amount"]))
    points = data.get("points")
    if points is not None:
        points = int(points)

    account = await session.get(Account, customer_id)
    if account is None:
        account = Account(customer_id=customer_id, balance=0)
        session.add(account)
        await session.flush()

    purchase_row = await session.scalar(
        select(Operation).where(
            Operation.check_number == check_number,
            Operation.type == "purchase",
            Operation.status == "applied",
        )
    )
    returned_id = await session.scalar(
        select(Operation.operation_id).where(
            Operation.check_number == check_number,
            Operation.type == "return",
            Operation.status == "applied",
        )
    )
    existing_purchase = None
    if purchase_row is not None:
        existing_purchase = ExistingPurchase(
            customer_id=purchase_row.customer_id,
            points=purchase_row.points_delta,
        )

    try:
        decision = decide(
            op_type=op_type,
            customer_id=customer_id,
            amount=amount,
            points=points,
            balance=account.balance,
            existing_purchase=existing_purchase,
            already_returned=returned_id is not None,
        )
    except ValueError as exc:
        raise BadMessage(str(exc)) from exc

    processed_at = datetime.now(NSK).replace(microsecond=0)
    row = Operation(
        operation_id=operation_id,
        type=op_type,
        customer_id=customer_id,
        store_id=data.get("store_id") or "",
        check_number=check_number,
        amount=amount,
        points_delta=decision.points_delta,
        balance_after=decision.balance_after,
        status=decision.status,
        reject_reason=decision.reject_reason,
        occurred_at=occurred_at,
        processed_at=processed_at,
    )
    account.balance = decision.balance_after
    session.add(row)
    await session.flush()
    return event_from(row)


async def publish_event(exchange, event: dict) -> None:
    message = aio_pika.Message(
        body=json.dumps(event).encode("utf-8"),
        content_type="application/json",
        message_id=event["operation_id"],
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await exchange.publish(message, routing_key="", mandatory=True)


async def declare_topology(channel):
    direct = await channel.declare_exchange(
        DIRECT_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True
    )
    operations = await channel.declare_queue(
        OPERATIONS_QUEUE, durable=True, arguments=QUORUM
    )
    await operations.bind(direct, routing_key=ROUTING_KEY)

    events = await channel.declare_exchange(
        EVENTS_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
    )
    for name in (NOTIFIER_QUEUE, ANALYTICS_QUEUE):
        queue = await channel.declare_queue(name, durable=True, arguments=QUORUM)
        await queue.bind(events)
    return operations, events


async def handle_message(session_factory, events, message) -> None:
    data = parse_body(message.body)
    async with session_factory() as session:
        async with session.begin():
            event = await apply_operation(session, data)
    await publish_event(events, event)
    log.info(
        "%s %s %s delta=%s balance=%s reason=%s",
        event["operation_id"],
        event["type"],
        event["status"],
        event["points_delta"],
        event["balance_after"],
        event["reject_reason"],
    )


async def consume(session_factory, operations, events) -> None:
    async with operations.iterator() as messages:
        async for message in messages:
            try:
                async with message.process(requeue=True):
                    try:
                        await handle_message(session_factory, events, message)
                    except BadMessage:
                        log.exception("сообщение отброшено")
            except Exception:
                log.exception("обработка не удалась, сообщение возвращено в очередь")
                await asyncio.sleep(1)


async def wait_tables(engine) -> None:
    last_error = None
    for _ in range(30):
        try:
            await create_tables(engine)
            return
        except Exception as exc:
            last_error = exc
            log.warning("база ещё не готова: %s", exc)
            await asyncio.sleep(1)
    raise last_error


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = load_settings()
    engine, session_factory = make_session_factory(settings.database_url)
    await wait_tables(engine)

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel(
            publisher_confirms=True,
            on_return_raises=True,
        )
        await channel.set_qos(prefetch_count=1)
        operations, events = await declare_topology(channel)
        READY.write_text("ok", encoding="utf-8")
        log.info("таблицы созданы, обменники и очереди объявлены")
        await consume(session_factory, operations, events)


if __name__ == "__main__":
    asyncio.run(main())
