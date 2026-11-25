import asyncio
import json
import tempfile
from pathlib import Path
import httpx
import websockets

URL = 'http://localhost:8000/graphql'
records = []

async def request(client, title, query, variables=None):
    response = await client.post(URL, json={'query': query, 'variables': variables})
    response.raise_for_status()
    result = response.json()
    records.append({'title': title, 'query': query, 'variables': variables, 'response': result})
    return result

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        result = await request(client, 'Заказ со связанными данными', 'query { order(id:"1") { id status total customerName deliveryAddress restaurant { id name address dishes { id name price } } items { quantity price amount dish { id name } } } }')
        assert result['data']['order']['items']
        result = await request(client, 'Заказы ACCEPTED', 'query { orders(status:ACCEPTED) { id status } }')
        assert len(result['data']['orders']) == 20 and all(x['status'] == 'ACCEPTED' for x in result['data']['orders'])
        result = await request(client, 'Создание заказа', 'mutation { createOrder(input:{restaurantId:"1",customerName:"Иван",deliveryAddress:"ул. Ленина, 1",items:[{dishId:"1",quantity:2}]}) { id status total } }')
        order_id = result['data']['createOrder']['id']
        assert result['data']['createOrder']['status'] == 'CREATED'
        subscription = f'subscription {{ orderStatusChanged(orderId:"{order_id}") {{ id status }} }}'
        async with websockets.connect('ws://localhost:8000/graphql', subprotocols=['graphql-transport-ws']) as ws:
            await ws.send(json.dumps({'type':'connection_init'}))
            assert json.loads(await ws.recv())['type'] == 'connection_ack'
            await ws.send(json.dumps({'id':'1','type':'subscribe','payload':{'query':subscription}}))
            initial = json.loads(await asyncio.wait_for(ws.recv(), 5))
            statuses = [initial['payload']['data']['orderStatusChanged']['status']]
            for operation, expected in [('acceptOrder','ACCEPTED'), ('markOrderReady','READY'), ('markOrderDelivered','DELIVERED')]:
                result = await request(client, operation, f'mutation {{ {operation}(id:"{order_id}") {{ id status }} }}')
                assert result['data'][operation]['status'] == expected
                message = json.loads(await asyncio.wait_for(ws.recv(), 5))
                statuses.append(message['payload']['data']['orderStatusChanged']['status'])
            completion = json.loads(await asyncio.wait_for(ws.recv(), 5))
            assert statuses == ['CREATED','ACCEPTED','READY','DELIVERED'] and completion['type'] == 'complete'
            records.append({'title':'Подписка WebSocket','query':subscription,'response':{'statuses':statuses,'lastMessage':completion}})
        result = await request(client, 'Блюдо другого ресторана', 'mutation { createOrder(input:{restaurantId:"1",customerName:"Иван",deliveryAddress:"Адрес",items:[{dishId:"11",quantity:1}]}) { id } }')
        assert result['errors'][0]['extensions']['code'] == 'VALIDATION_ERROR'
        result = await request(client, 'Недопустимый переход', f'mutation {{ acceptOrder(id:"{order_id}") {{ id status }} }}')
        assert result['errors'][0]['extensions']['code'] == 'TRANSITION_NOT_ALLOWED'
        result = await request(client, 'Состояние после ошибки', f'query {{ order(id:"{order_id}") {{ id status }} }}')
        assert result['data']['order']['status'] == 'DELIVERED'
        result = await request(client, 'Несуществующий заказ', 'mutation { acceptOrder(id:"999999") { id } }')
        assert result['errors'][0]['extensions']['code'] == 'NOT_FOUND'
    path = Path(tempfile.gettempdir()) / 'lab6-results.json'
    with path.open('w', encoding='utf-8') as f: json.dump(records,f,ensure_ascii=False,indent=2)
    print('Все сценарии прошли; order_id =', order_id, '; данные:', path)

if __name__ == '__main__': asyncio.run(main())
