"""Приём операций касс.

api проверяет только формат и публикует сообщение. Баллы считает worker.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import aio_pika
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from .broker import iso, make_session_factory, open_broker, publish_operation
from .config import load_settings
from .models import Account, Operation
from .schemas import OperationIn


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    engine, session_factory = make_session_factory(settings.database_url)
    connection, channel, exchange = await open_broker(settings.rabbitmq_url)
    app.state.engine = engine
    app.state.sessions = session_factory
    app.state.amqp = connection
    app.state.channel = channel
    app.state.exchange = exchange
    yield
    await connection.close()
    await engine.dispose()


app = FastAPI(title="Бонусная программа", lifespan=lifespan)


def money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return "{0:.2f}".format(value)


def operation_view(row: Operation, with_check: bool) -> dict:
    body = {
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
    if with_check:
        body["check_number"] = row.check_number
        body["amount"] = money(row.amount)
        body["occurred_at"] = iso(row.occurred_at)
    return body


@app.post("/operations", status_code=202)
async def post_operation(operation: OperationIn):
    payload = operation.model_dump(mode="json", exclude_none=True)
    if operation.amount is not None:
        payload["amount"] = format(operation.amount, "f")
    try:
        await publish_operation(app.state.exchange, payload)
    except Exception:
        return JSONResponse(
            {"detail": "брокер не подтвердил публикацию"},
            status_code=503,
        )
    return {"operation_id": operation.operation_id}


@app.get("/operations/{operation_id}")
async def get_operation(operation_id: str):
    async with app.state.sessions() as session:
        row = await session.get(Operation, operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="операция ещё не обработана")
    return operation_view(row, with_check=False)


@app.get("/accounts/{customer_id}")
async def get_account(customer_id: str):
    async with app.state.sessions() as session:
        account = await session.get(Account, customer_id)
        if account is None:
            raise HTTPException(status_code=404, detail="счёт не найден")
        rows = (
            await session.scalars(
                select(Operation)
                .where(Operation.customer_id == customer_id)
                .order_by(Operation.processed_at.desc())
                .limit(20)
            )
        ).all()
    return {
        "customer_id": account.customer_id,
        "balance": account.balance,
        "operations": [operation_view(row, with_check=True) for row in rows],
    }


@app.get("/health")
async def health():
    try:
        async with app.state.sessions() as session:
            await session.execute(text("SELECT 1"))
        await app.state.channel.declare_exchange(
            "loyalty", aio_pika.ExchangeType.DIRECT, durable=True
        )
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}
