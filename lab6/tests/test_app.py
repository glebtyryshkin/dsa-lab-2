import os
import tempfile
from pathlib import Path

_test_dir = tempfile.TemporaryDirectory(prefix='lab6-tests-')
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///' + str(Path(_test_dir.name) / 'test.sqlite3')
import pytest
import pytest_asyncio
from sqlalchemy import event
from app import db, events, services
from app.schema import schema, context_getter

@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    async with db.engine.begin() as c:
        await c.run_sync(db.Base.metadata.drop_all)
        await c.run_sync(db.Base.metadata.create_all)
    async with db.sessions.begin() as s:
        s.add_all([db.Restaurant(id=1, name='Один', address='Адрес'), db.Restaurant(id=2, name='Два', address='Адрес')])
        await s.flush()
        s.add_all([db.Dish(id=1, restaurant_id=1, name='Суп', price='100.00'), db.Dish(id=2, restaurant_id=2, name='Чай', price='50.00')])
    yield
    events._subscribers.clear()

async def create():
    return await services.create_order(1, 'Имя', 'Адрес', [(1, 2)])

@pytest.mark.asyncio
async def test_validation_and_transitions():
    for items in ([], [(1, 1)] * 2, [(1, 0)], [(1, 21)], [(2, 1)]):
        with pytest.raises(services.OrderValidationError): await services.create_order(1, 'Имя', 'Адрес', items)
    with pytest.raises(services.NotFound): await services.create_order(1, 'Имя', 'Адрес', [(999, 1)])
    o = await create()
    assert str(o.total) == '200.00'
    with pytest.raises(services.TransitionNotAllowed): await services.mark_ready(o.id)
    with pytest.raises(services.OrderValidationError): await services.cancel(o.id, 'x')
    assert (await db.get_order(o.id)).status == 'CREATED'
    await services.accept(o.id)
    await services.mark_ready(o.id)
    await services.mark_delivered(o.id)
    with pytest.raises(services.TransitionNotAllowed): await services.cancel(o.id, 'причина')
    with pytest.raises(services.NotFound): await services.accept(999)

@pytest.mark.asyncio
async def test_graphql_and_batching():
    for _ in range(4): await create()
    count = 0
    def before(*args):
        nonlocal count
        count += 1
    event.listen(db.engine.sync_engine, 'before_cursor_execute', before)
    try:
        result = await schema.execute('{ orders { id restaurant { name } items { quantity price amount dish { name } } } }', context_value=context_getter())
    finally: event.remove(db.engine.sync_engine, 'before_cursor_execute', before)
    assert not result.errors, result.errors
    assert len(result.data['orders']) == 4
    assert count == 4
    result = await schema.execute('mutation { acceptOrder(id:"999") { id } }', context_value=context_getter())
    assert result.data == {'acceptOrder': None}
    assert result.errors[0].extensions['code'] == 'NOT_FOUND'

@pytest.mark.asyncio
async def test_subscription_and_cleanup():
    o = await create()
    stream = await schema.subscribe('subscription($id: ID!) { orderStatusChanged(orderId: $id) { status } }', variable_values={'id': str(o.id)}, context_value=context_getter())
    assert (await anext(stream)).data['orderStatusChanged']['status'] == 'CREATED'
    await services.accept(o.id)
    await services.mark_ready(o.id)
    await services.mark_delivered(o.id)
    for expected in ['ACCEPTED', 'READY', 'DELIVERED']:
        assert (await anext(stream)).data['orderStatusChanged']['status'] == expected
    with pytest.raises(StopAsyncIteration): await anext(stream)
    assert not events._subscribers

@pytest.mark.asyncio
async def test_internal_error_is_masked(monkeypatch):
    async def fail(): raise RuntimeError('секретная внутренняя ошибка')
    monkeypatch.setattr(db, 'list_restaurants', fail)
    result = await schema.execute('{ restaurants { name } }', context_value=context_getter())
    assert result.errors[0].message == 'Internal error'
    assert result.errors[0].extensions == {'code': 'INTERNAL_ERROR'}

@pytest.mark.asyncio
async def test_terminal_and_missing_subscription():
    o = await create()
    await services.cancel(o.id, 'Передумал')
    query = 'subscription($id: ID!) { orderStatusChanged(orderId: $id) { status } }'
    stream = await schema.subscribe(query, variable_values={'id': str(o.id)}, context_value=context_getter())
    assert (await anext(stream)).data['orderStatusChanged']['status'] == 'CANCELLED'
    with pytest.raises(StopAsyncIteration): await anext(stream)
    stream = await schema.subscribe(query, variable_values={'id': '999'}, context_value=context_getter())
    assert (await anext(stream)).errors[0].extensions['code'] == 'NOT_FOUND'
    await stream.aclose()
    assert not events._subscribers

@pytest.mark.asyncio
async def test_subscription_masks_internal_error(monkeypatch):
    async def fail(_): raise RuntimeError('secret_db_password')
    monkeypatch.setattr(db, 'get_order', fail)
    stream = await schema.subscribe('subscription { orderStatusChanged(orderId:"1") { id } }', context_value=context_getter())
    result = await anext(stream)
    assert result.errors[0].message == 'Internal error'
    assert result.errors[0].extensions['code'] == 'INTERNAL_ERROR'
    await stream.aclose()

@pytest.mark.asyncio
async def test_subscription_nested_error_is_masked(monkeypatch):
    o = await create()
    async def fail(_): raise RuntimeError('secret nested failure')
    monkeypatch.setattr(db, 'get_restaurants', fail)
    stream = await schema.subscribe('subscription($id:ID!) { orderStatusChanged(orderId:$id) { restaurant { name } } }', variable_values={'id': str(o.id)}, context_value=context_getter())
    result = await anext(stream)
    assert result.errors[0].message == 'Internal error'
    assert result.errors[0].extensions['code'] == 'INTERNAL_ERROR'
    await stream.aclose()

@pytest.mark.asyncio
async def test_subscription_disconnect_removes_queue():
    o = await create()
    stream = await schema.subscribe('subscription($id:ID!) { orderStatusChanged(orderId:$id) { status } }', variable_values={'id':str(o.id)}, context_value=context_getter())
    assert (await anext(stream)).data['orderStatusChanged']['status'] == 'CREATED'
    assert events._subscribers[o.id]
    await stream.aclose()
    assert o.id not in events._subscribers
