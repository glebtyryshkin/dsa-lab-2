from decimal import Decimal
from sqlalchemy import select
from . import db, events

class NotFound(Exception): pass
class TransitionNotAllowed(Exception): pass
class OrderValidationError(Exception): pass

async def create_order(restaurant_id, customer_name, delivery_address, items):
    if not customer_name.strip() or not delivery_address.strip():
        raise OrderValidationError('Укажите имя и адрес')
    if not 1 <= len(items) <= 10 or len({i[0] for i in items}) != len(items):
        raise OrderValidationError('Заказ должен содержать от 1 до 10 разных блюд')
    if any(not 1 <= quantity <= 20 for _, quantity in items):
        raise OrderValidationError('Количество должно быть от 1 до 20')
    async with db.sessions.begin() as s:
        if not await s.get(db.Restaurant, restaurant_id): raise NotFound('Ресторан не найден')
        dishes = {d.id: d for d in (await s.scalars(select(db.Dish).where(db.Dish.id.in_([i[0] for i in items])))).all()}
        if any(i not in dishes for i, _ in items):
            raise NotFound('Блюдо не найдено')
        if any(dishes[i].restaurant_id != restaurant_id for i, _ in items):
            raise OrderValidationError('Все блюда должны принадлежать выбранному ресторану')
        order = db.Order(restaurant_id=restaurant_id, customer_name=customer_name, delivery_address=delivery_address, status='CREATED', total=sum((dishes[i].price * q for i, q in items), Decimal(0)))
        s.add(order)
        await s.flush()
        for i, q in items: s.add(db.OrderItem(order_id=order.id, dish_id=i, quantity=q, price=dishes[i].price))
        await s.refresh(order)
    events.publish(events.OrderStatusChanged(order.id, order.status))
    return order

async def transition(id, event, allowed, target, reason=None):
    async with events.status_lock:
        return await _transition(id, event, allowed, target, reason)

async def _transition(id, event, allowed, target, reason=None):
    if event == 'cancel' and not 3 <= len(reason.strip()) <= 200:
        raise OrderValidationError('Причина отмены должна содержать от 3 до 200 символов')
    async with db.sessions.begin() as s:
        order = await s.scalar(select(db.Order).where(db.Order.id == id).with_for_update())
        if order is None: raise NotFound('Заказ не найден')
        if order.status not in allowed: raise TransitionNotAllowed(f'Событие {event} недопустимо в состоянии {order.status}')
        order.status = target
        if event == 'cancel': order.cancel_reason = reason.strip()
        await s.flush()
    events.publish(events.OrderStatusChanged(order.id, target))
    return order

async def accept(id): return await transition(id, 'accept', {'CREATED'}, 'ACCEPTED')
async def mark_ready(id): return await transition(id, 'mark_ready', {'ACCEPTED'}, 'READY')
async def mark_delivered(id): return await transition(id, 'mark_delivered', {'READY'}, 'DELIVERED')
async def cancel(id, reason): return await transition(id, 'cancel', {'CREATED', 'ACCEPTED'}, 'CANCELLED', reason)
