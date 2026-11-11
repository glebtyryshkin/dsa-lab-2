"""Накопление статистики магазина по событию."""

from __future__ import annotations


def empty_store(store_id: str) -> dict:
    return {
        "store_id": store_id,
        "applied_purchase": 0,
        "applied_return": 0,
        "applied_redeem": 0,
        "rejected": 0,
        "points_accrued": 0,
        "points_returned": 0,
        "points_spent": 0,
    }


def apply_event(stores: dict, event: dict) -> None:
    store = stores.get(event["store_id"])
    if store is None:
        store = empty_store(event["store_id"])
        stores[event["store_id"]] = store
    if event["status"] != "applied":
        store["rejected"] += 1
        return
    delta = abs(int(event["points_delta"]))
    if event["type"] == "purchase":
        store["applied_purchase"] += 1
        store["points_accrued"] += int(event["points_delta"])
    elif event["type"] == "return":
        store["applied_return"] += 1
        store["points_returned"] += delta
    elif event["type"] == "redeem":
        store["applied_redeem"] += 1
        store["points_spent"] += delta
