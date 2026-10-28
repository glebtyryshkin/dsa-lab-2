import json
from datetime import date

from loadtest import build_queries, latency_stats, percentile
from supplier.data import generate_flights, response_body


def test_percentile_nearest_rank():
    values = list(range(1, 21))
    assert percentile(values, 50) == 10
    assert percentile(values, 95) == 19
    assert percentile(values, 99) == 20


def test_latency_stats_empty():
    assert latency_stats([])["p95"] is None


def test_queries_are_reproducible():
    first = build_queries(42, count=100)
    second = build_queries(42, count=100)
    assert first == second
    assert all(item["origin"] != item["destination"] for item in first)


def test_supplier_flights_are_deterministic():
    first = generate_flights("S1", 0, "2026-11-05", 500)
    second = generate_flights("S1", 0, "2026-11-05", 500)
    assert first == second
    assert len(first) == 500
    assert len({item["uid"] for item in first}) == 500


def test_supplier_response_is_valid_json():
    payload = json.loads(response_body("S2", 0, "2026-11-05", 500))
    assert payload["supplier"] == "S2"
    assert payload["date"] == "2026-11-05"
    assert len(payload["flights"]) == 500
