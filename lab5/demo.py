"""Прогон операций касс против поднятого стенда.

Запуск из каталога lab5, когда контейнеры уже отвечают:
    python3 demo.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000"
NOTIFIER = "http://127.0.0.1:8001"
ANALYTICS = "http://127.0.0.1:8002"
WHEN = "2026-11-10T14:32:05+07:00"


def request(method, url, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else None
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed


def operation(operation_id, op_type, customer_id, store_id, check_number, amount=None, points=None):
    body = {
        "operation_id": operation_id,
        "type": op_type,
        "customer_id": customer_id,
        "store_id": store_id,
        "check_number": check_number,
        "occurred_at": WHEN,
    }
    if amount is not None:
        body["amount"] = amount
    if points is not None:
        body["points"] = points
    return body


def post_and_wait(body):
    status, posted = request("POST", API + "/operations", body)
    if status != 202:
        raise SystemExit("ожидался 202, получен {0}: {1}".format(status, posted))
    operation_id = body["operation_id"]
    deadline = time.time() + 15
    last = None
    while time.time() < deadline:
        code, last = request("GET", API + "/operations/" + operation_id)
        if code == 200:
            return last
        time.sleep(0.2)
    raise SystemExit("операция {0} не обработана: {1}".format(operation_id, last))


def show(result):
    delta = "{:+d}".format(result["points_delta"])
    reason = result["reject_reason"] or "-"
    print(
        "{0:<16} {1:<9} {2:<9} {3:>5}  balance {4:<5} {5}".format(
            result["operation_id"],
            result["type"],
            result["status"],
            delta,
            result["balance_after"],
            reason,
        )
    )


def expect(result, status, delta, balance, reason):
    actual = (
        result["status"],
        result["points_delta"],
        result["balance_after"],
        result["reject_reason"],
    )
    wanted = (status, delta, balance, reason)
    if actual != wanted:
        raise SystemExit("не сошлось {0}: {1}, ожидали {2}".format(result["operation_id"], actual, wanted))
    show(result)


def wait_events(expected):
    deadline = time.time() + 45
    notifier = analytics = None
    while time.time() < deadline:
        _, notifier = request("GET", NOTIFIER + "/stats")
        _, analytics = request("GET", ANALYTICS + "/stats")
        if (
            notifier
            and analytics
            and notifier.get("events_received") == expected
            and analytics.get("events_received") == expected
            and notifier.get("notifications_sent") == expected - 1
        ):
            return notifier, analytics
        time.sleep(0.5)
    raise SystemExit("события не дошли: notifier={0} analytics={1}".format(notifier, analytics))


def main():
    code, health = request("GET", API + "/health")
    print("health {0} {1}".format(code, health))
    if code != 200:
        raise SystemExit("api не готов")

    print("{0:<16} {1:<9} {2:<9} {3:>5}  {4:<12} {5}".format(
        "operation", "type", "status", "delta", "balance", "reason"
    ))
    expect(post_and_wait(operation(
        "S12-K03-000154", "purchase", "C1042", "S12", "S12-000154", "2490.00"
    )), "applied", 124, 124, None)
    expect(post_and_wait(operation(
        "S12-K03-000200", "redeem", "C1042", "S12", "S12-000200", "2490.00", 30
    )), "applied", -30, 94, None)
    expect(post_and_wait(operation(
        "S12-K03-000201", "redeem", "C1042", "S12", "S12-000201", "2490.00", 800
    )), "rejected", 0, 94, "limit_exceeded")
    expect(post_and_wait(operation(
        "S12-K03-000202", "redeem", "C1042", "S12", "S12-000202", "1000.00", 200
    )), "rejected", 0, 94, "insufficient_balance")
    expect(post_and_wait(operation(
        "S12-K03-000155", "purchase", "C1042", "S12", "S12-000154", "100.00"
    )), "rejected", 0, 94, "duplicate_purchase")
    expect(post_and_wait(operation(
        "S12-K03-000156", "return", "C7777", "S12", "S12-000154"
    )), "rejected", 0, 0, "customer_mismatch")
    expect(post_and_wait(operation(
        "S12-K03-000157", "return", "C1042", "S12", "S12-999999"
    )), "rejected", 0, 94, "purchase_not_found")
    expect(post_and_wait(operation(
        "S12-K03-000158", "return", "C1042", "S12", "S12-000154"
    )), "applied", -124, -30, None)
    expect(post_and_wait(operation(
        "S12-K03-000159", "return", "C1042", "S12", "S12-000154"
    )), "rejected", 0, -30, "already_returned")
    expect(post_and_wait(operation(
        "S12-K03-000300", "purchase", "C1042", "S12", "S12-000300", "1000.00"
    )), "applied", 50, 20, None)
    expect(post_and_wait(operation(
        "S12-K03-000400", "purchase", "C9001", "S12", "S12-000400", "2000.00"
    )), "applied", 100, 100, None)
    expect(post_and_wait(operation(
        "S07-K01-000001", "purchase", "C1042", "S07", "S07-000001", "500.00"
    )), "applied", 25, 45, None)

    code, bad = request("POST", API + "/operations", {
        "operation_id": "bad",
        "type": "purchase",
        "customer_id": "C1042",
        "store_id": "S12",
        "check_number": "S12-bad",
        "occurred_at": WHEN,
    })
    print("покупка без amount: HTTP {0}".format(code))
    if code != 422:
        raise SystemExit("ожидался 422, получен {0}: {1}".format(code, bad))

    code, account = request("GET", API + "/accounts/C1042")
    print("счёт C1042: balance {0}, операций {1}".format(account["balance"], len(account["operations"])))
    if account["balance"] != 45 or len(account["operations"]) != 10:
        raise SystemExit("неверный счёт C1042: {0}".format(account))

    notifier, analytics = wait_events(12)
    print("notifier  {0}".format(notifier))
    print("analytics {0}".format(analytics))

    code, notes = request("GET", NOTIFIER + "/notifications?customer_id=C1042")
    print("уведомления C1042: {0}".format(len(notes)))
    print("  первое: {0}".format(notes[0]["text"]))
    print("  возврат: {0}".format(next(item["text"] for item in notes if "Списано 124" in item["text"])))
    code, silent = request("GET", NOTIFIER + "/notifications?customer_id=C9001")
    print("уведомления C9001 (отписан): {0}".format(silent))
    if len(notes) != 10 or silent != []:
        raise SystemExit("уведомления не сошлись")

    code, store = request("GET", ANALYTICS + "/stores/S12")
    print(
        "магазин S12: покупок {0}, возвратов {1}, оплат {2}, отклонено {3}".format(
            store["applied_purchase"], store["applied_return"],
            store["applied_redeem"], store["rejected"],
        )
    )
    print(
        "  баллы: начислено {0}, возвраты {1}, оплата {2}".format(
            store["points_accrued"], store["points_returned"], store["points_spent"],
        )
    )
    wanted = {
        "store_id": "S12",
        "applied_purchase": 3,
        "applied_return": 1,
        "applied_redeem": 1,
        "rejected": 6,
        "points_accrued": 274,
        "points_returned": 124,
        "points_spent": 30,
    }
    if store != wanted:
        raise SystemExit("статистика S12 не сошлась")
    code, missing = request("GET", ANALYTICS + "/stores/S99")
    print("магазин S99: HTTP {0}".format(code))
    if code != 404:
        raise SystemExit("для магазина без событий ожидался 404")
    print("проверка пройдена")


if __name__ == "__main__":
    main()
