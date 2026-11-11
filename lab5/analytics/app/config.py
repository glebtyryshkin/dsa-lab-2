from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    rabbitmq_url: str


def load_settings() -> Settings:
    return Settings(rabbitmq_url=os.environ["RABBITMQ_URL"])
