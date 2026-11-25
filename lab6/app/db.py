import os
from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

engine = create_async_engine(os.getenv('DATABASE_URL', 'postgresql+asyncpg://food:food@postgres:5432/food'), echo=True)
sessions = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class Restaurant(Base):
    __tablename__ = 'restaurants'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300))

class Dish(Base):
    __tablename__ = 'dishes'
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey('restaurants.id'))
    name: Mapped[str] = mapped_column(String(200))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

class Order(Base):
    __tablename__ = 'orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey('restaurants.id'))
    customer_name: Mapped[str] = mapped_column(String(200))
    delivery_address: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class OrderItem(Base):
    __tablename__ = 'order_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'))
    dish_id: Mapped[int] = mapped_column(ForeignKey('dishes.id'))
    quantity: Mapped[int]
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

async def one(model, ident):
    async with sessions() as s:
        return await s.get(model, ident)

async def get_restaurant(id): return await one(Restaurant, id)
async def get_dish(id): return await one(Dish, id)
async def get_order(id): return await one(Order, id)

async def rows(stmt):
    async with sessions() as s:
        return list((await s.scalars(stmt)).all())

async def list_restaurants(): return await rows(select(Restaurant).order_by(Restaurant.id))
async def list_dishes(restaurant_id): return await rows(select(Dish).where(Dish.restaurant_id == restaurant_id).order_by(Dish.id))
async def get_items(order_id): return await rows(select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id))
async def list_orders(status=None): return await rows(select(Order).where(Order.status == status) .order_by(Order.created_at.desc(), Order.id.desc()) if status else select(Order).order_by(Order.created_at.desc(), Order.id.desc()))

async def get_restaurants(ids):
    return {x.id: x for x in await rows(select(Restaurant).where(Restaurant.id.in_(ids)))}
async def get_dishes(ids):
    return {x.id: x for x in await rows(select(Dish).where(Dish.id.in_(ids)))}
async def get_items_by_orders(ids):
    result = {i: [] for i in ids}
    for item in await rows(select(OrderItem).where(OrderItem.order_id.in_(ids)).order_by(OrderItem.id)):
        result[item.order_id].append(item)
    return result
