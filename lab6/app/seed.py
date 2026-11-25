import asyncio
from decimal import Decimal
from sqlalchemy import select
from . import db

async def main():
    async with db.engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    async with db.sessions.begin() as s:
        if await s.scalar(select(db.Restaurant.id).limit(1)) is not None:
            raise RuntimeError('База уже содержит рестораны; заполнение отменено')
        restaurants = [db.Restaurant(name=f'Ресторан {i}', address=f'Улица {i}') for i in range(1, 6)]
        s.add_all(restaurants)
        await s.flush()
        menus = []
        for restaurant in restaurants:
            menu = [db.Dish(restaurant_id=restaurant.id, name=f'Блюдо {restaurant.id}-{i}', price=Decimal(100 + i * 25)) for i in range(1, 11)]
            s.add_all(menu)
            menus.append(menu)
        await s.flush()
        statuses = ['CREATED', 'ACCEPTED', 'READY', 'DELIVERED', 'CANCELLED']
        for i in range(100):
            restaurant = restaurants[i % 5]
            dish = menus[i % 5][(i // 5) % 10]
            status = statuses[(i // 5) % 5]
            quantity = i % 3 + 1
            order = db.Order(restaurant_id=restaurant.id, customer_name=f'Клиент {i+1}', delivery_address=f'Адрес {i+1}', status=status, cancel_reason='Передумал' if status == 'CANCELLED' else None, total=dish.price * quantity)
            s.add(order)
            await s.flush()
            s.add(db.OrderItem(order_id=order.id, dish_id=dish.id, quantity=quantity, price=dish.price))
    await db.engine.dispose()

if __name__ == '__main__': asyncio.run(main())
