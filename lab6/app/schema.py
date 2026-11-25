from contextlib import aclosing
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, AsyncGenerator
import strawberry
import os
from strawberry.extensions import SchemaExtension
from graphql import GraphQLError
from strawberry.dataloader import DataLoader
from strawberry.types import Info
from . import db, events, services

@strawberry.enum
class OrderStatus(Enum):
    CREATED = 'CREATED'
    ACCEPTED = 'ACCEPTED'
    READY = 'READY'
    DELIVERED = 'DELIVERED'
    CANCELLED = 'CANCELLED'

def ident(value):
    try: return int(value)
    except (ValueError, TypeError): raise GraphQLError('Некорректный идентификатор', extensions={'code': 'VALIDATION_ERROR'})

async def safe(awaitable):
    try:
        return await awaitable
    except GraphQLError:
        raise
    except Exception as e:
        raise GraphQLError('Internal error', extensions={'code': 'INTERNAL_ERROR'}) from e

@strawberry.type
class Restaurant:
    id: strawberry.ID
    name: str
    address: str
    @strawberry.field
    async def dishes(self) -> list['Dish']:
        return [Dish.from_model(x) for x in await safe(db.list_dishes(int(self.id)))]
    @classmethod
    def from_model(cls, x): return cls(id=str(x.id), name=x.name, address=x.address)

@strawberry.type
class Dish:
    id: strawberry.ID
    name: str
    price: Decimal
    restaurant_id: strawberry.Private[int]
    @strawberry.field
    async def restaurant(self, info: Info) -> Restaurant:
        return Restaurant.from_model(await safe(db.get_restaurant(self.restaurant_id) if info.context['naive'] else info.context['restaurants'].load(self.restaurant_id)))
    @classmethod
    def from_model(cls, x): return cls(id=str(x.id), name=x.name, price=x.price, restaurant_id=x.restaurant_id)

@strawberry.type
class OrderItem:
    quantity: int
    price: Decimal
    dish_id: strawberry.Private[int]
    @strawberry.field
    def amount(self) -> Decimal: return self.price * self.quantity
    @strawberry.field
    async def dish(self, info: Info) -> Dish: return Dish.from_model(await safe(db.get_dish(self.dish_id) if info.context['naive'] else info.context['dishes'].load(self.dish_id)))
    @classmethod
    def from_model(cls, x): return cls(quantity=x.quantity, price=x.price, dish_id=x.dish_id)

@strawberry.type
class Order:
    id: strawberry.ID
    status: OrderStatus
    total: Decimal
    created_at: datetime
    customer_name: str
    delivery_address: str
    cancel_reason: Optional[str]
    restaurant_id: strawberry.Private[int]
    @strawberry.field
    async def restaurant(self, info: Info) -> Restaurant: return Restaurant.from_model(await safe(db.get_restaurant(self.restaurant_id) if info.context['naive'] else info.context['restaurants'].load(self.restaurant_id)))
    @strawberry.field
    async def items(self, info: Info) -> list[OrderItem]: return [OrderItem.from_model(x) for x in await safe(db.get_items(int(self.id)) if info.context['naive'] else info.context['items'].load(int(self.id)))]
    @classmethod
    def from_model(cls, x, status=None): return cls(id=str(x.id), status=OrderStatus(status or x.status), total=x.total, created_at=x.created_at, customer_name=x.customer_name, delivery_address=x.delivery_address, cancel_reason=x.cancel_reason, restaurant_id=x.restaurant_id)

@strawberry.input
class CreateOrderItemInput:
    dish_id: strawberry.ID
    quantity: int

@strawberry.input
class CreateOrderInput:
    restaurant_id: strawberry.ID
    customer_name: str
    delivery_address: str
    items: list[CreateOrderItemInput]

async def run(operation, *args):
    try: return Order.from_model(await operation(*args))
    except (services.NotFound, services.TransitionNotAllowed, services.OrderValidationError) as e:
        codes = {services.NotFound:'NOT_FOUND', services.TransitionNotAllowed:'TRANSITION_NOT_ALLOWED', services.OrderValidationError:'VALIDATION_ERROR'}
        raise GraphQLError(str(e), extensions={'code': codes[type(e)]}) from e
    except GraphQLError: raise
    except Exception as e: raise GraphQLError('Internal error', extensions={'code':'INTERNAL_ERROR'}) from e

@strawberry.type
class Query:
    @strawberry.field
    async def order(self, id: strawberry.ID) -> Optional[Order]:
        x = await safe(db.get_order(ident(id)))
        return Order.from_model(x) if x else None
    @strawberry.field
    async def orders(self, status: Optional[OrderStatus] = None) -> list[Order]: return [Order.from_model(x) for x in await safe(db.list_orders(status.value if status else None))]
    @strawberry.field
    async def restaurants(self) -> list[Restaurant]: return [Restaurant.from_model(x) for x in await safe(db.list_restaurants())]

@strawberry.type
class Mutation:
    @strawberry.mutation
    async def create_order(self, input: CreateOrderInput) -> Optional[Order]: return await run(services.create_order, ident(input.restaurant_id), input.customer_name, input.delivery_address, [(ident(x.dish_id), x.quantity) for x in input.items])
    @strawberry.mutation
    async def accept_order(self, id: strawberry.ID) -> Optional[Order]: return await run(services.accept, ident(id))
    @strawberry.mutation
    async def mark_order_ready(self, id: strawberry.ID) -> Optional[Order]: return await run(services.mark_ready, ident(id))
    @strawberry.mutation
    async def mark_order_delivered(self, id: strawberry.ID) -> Optional[Order]: return await run(services.mark_delivered, ident(id))
    @strawberry.mutation
    async def cancel_order(self, id: strawberry.ID, reason: str) -> Optional[Order]: return await run(services.cancel, ident(id), reason)

async def subscription_events(order_id):
    async with events.status_lock:
        order = await db.get_order(order_id)
        queue = events.subscribe(order_id) if order is not None else None
    try:
        if order is None:
            raise GraphQLError('Заказ не найден', extensions={'code': 'NOT_FOUND'})
        yield Order.from_model(order)
        if order.status in ('DELIVERED', 'CANCELLED'):
            return
        while True:
            event = await queue.get()
            order = await db.get_order(order_id)
            yield Order.from_model(order, event.status)
            if event.status in ('DELIVERED', 'CANCELLED'):
                return
    finally:
        if queue is not None:
            events.unsubscribe(order_id, queue)

@strawberry.type
class Subscription:
    @strawberry.subscription
    async def order_status_changed(self, order_id: strawberry.ID) -> AsyncGenerator[Optional[Order], None]:
        try:
            async with aclosing(subscription_events(ident(order_id))) as stream:
                async for order in stream:
                    yield order
        except GraphQLError:
            raise
        except Exception as e:
            raise GraphQLError('Internal error', extensions={'code': 'INTERNAL_ERROR'}) from e

class MaskUnexpectedErrors(SchemaExtension):
    def on_operation(self):
        yield
        result = self.execution_context.result
        if result and result.errors:
            for error in result.errors:
                if error.original_error is not None and not isinstance(error.original_error, GraphQLError):
                    error.message = 'Internal error'
                    error.extensions = {'code': 'INTERNAL_ERROR'}

schema = strawberry.Schema(query=Query, mutation=Mutation, subscription=Subscription, extensions=[MaskUnexpectedErrors])

def context_getter():
    async def load(fn, keys):
        result = await fn(keys)
        return [result.get(k) for k in keys]
    return {'naive': os.getenv('NAIVE_LOADING', '0') == '1', 'restaurants': DataLoader(load_fn=lambda keys: load(db.get_restaurants, keys)), 'dishes': DataLoader(load_fn=lambda keys: load(db.get_dishes, keys)), 'items': DataLoader(load_fn=lambda keys: load(db.get_items_by_orders, keys))}
