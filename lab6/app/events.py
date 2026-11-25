import asyncio
from collections import defaultdict
from dataclasses import dataclass

@dataclass(frozen=True)
class OrderStatusChanged:
    order_id: int
    status: str

_subscribers = defaultdict(set)
status_lock = asyncio.Lock()

def subscribe(order_id):
    queue = asyncio.Queue()
    _subscribers[order_id].add(queue)
    return queue

def unsubscribe(order_id, queue):
    _subscribers[order_id].discard(queue)
    if not _subscribers[order_id]:
        _subscribers.pop(order_id, None)

def publish(event):
    for queue in tuple(_subscribers.get(event.order_id, ())):
        queue.put_nowait(event)
