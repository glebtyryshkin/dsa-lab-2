from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class OperationIn(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    type: Literal["purchase", "return", "redeem"]
    customer_id: str = Field(min_length=1, max_length=128)
    store_id: str = Field(min_length=1, max_length=128)
    check_number: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    amount: Decimal | None = None
    points: int | None = None

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("у occurred_at должен быть часовой пояс")
        return value

    @field_validator("points")
    @classmethod
    def points_are_whole(cls, value: int | None) -> int | None:
        if isinstance(value, bool):
            raise ValueError("points должно быть целым числом")
        return value

    @model_validator(mode="after")
    def check_fields(self):
        if self.type in ("purchase", "redeem"):
            if self.amount is None or self.amount <= 0:
                raise ValueError("amount должно быть положительным")
        elif self.amount is not None:
            raise ValueError("в возврате поле amount не передаётся")

        if self.type == "redeem":
            if self.points is None or self.points <= 0:
                raise ValueError("points должно быть положительным целым")
        elif self.points is not None:
            raise ValueError("points передаётся только для оплаты баллами")
        return self
