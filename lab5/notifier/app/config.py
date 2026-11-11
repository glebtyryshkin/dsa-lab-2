from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    rabbitmq_url: str
    min_delay_ms: int
    max_delay_ms: int
    unsubscribed: frozenset


def load_settings() -> Settings:
    raw = os.getenv("UNSUBSCRIBED", "")
    unsubscribed = frozenset(part.strip() for part in raw.split(",") if part.strip())
    return Settings(
        rabbitmq_url=os.environ["RABBITMQ_URL"],
        min_delay_ms=int(os.getenv("MIN_DELAY_MS", "200")),
        max_delay_ms=int(os.getenv("MAX_DELAY_MS", "1500")),
        unsubscribed=unsubscribed,
    )
