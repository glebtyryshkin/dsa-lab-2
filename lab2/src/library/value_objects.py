from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from .exceptions import InvalidValueObject


@dataclass(frozen=True)
class BookId:
    value: UUID

    @staticmethod
    def new() -> "BookId":
        return BookId(uuid4())


@dataclass(frozen=True)
class LoanId:
    value: UUID

    @staticmethod
    def new() -> "LoanId":
        return LoanId(uuid4())


@dataclass(frozen=True)
class ReaderId:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("Пустой идентификатор читателя")


@dataclass(frozen=True)
class ISBN:
    value: str

    def __post_init__(self):
        if len(self.value) != 13 or not self.value.isdigit():
            raise InvalidValueObject("ISBN должен состоять из 13 цифр")


@dataclass(frozen=True)
class LoanPeriod:
    issue_date: date
    due_date: date

    def __post_init__(self):
        # правило 6
        if self.due_date < self.issue_date:
            raise InvalidValueObject("Срок возврата не может быть раньше даты выдачи")

    def days_overdue(self, return_date: date) -> int:
        return max(0, (return_date - self.due_date).days)


@dataclass(frozen=True)
class Fine:
    amount: Decimal

    def __post_init__(self):
        # правило 5
        if self.amount < 0:
            raise InvalidValueObject("Сумма штрафа не может быть отрицательной")
