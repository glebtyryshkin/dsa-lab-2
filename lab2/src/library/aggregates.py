from datetime import date
from decimal import Decimal

from .exceptions import DomainInvariantViolation
from .value_objects import ISBN, BookId, Fine, LoanId, LoanPeriod, ReaderId

# штраф за день просрочки, руб. (в задании не указан, взят свой)
FINE_PER_DAY = Decimal("50")


class Book:  # корень агрегата «книга»
    def __init__(self, book_id: BookId, isbn: ISBN, title: str,
                 total_copies: int, available_copies: int):
        if total_copies <= 0:
            raise DomainInvariantViolation("Число экземпляров должно быть больше нуля")
        if not 0 <= available_copies <= total_copies:
            raise DomainInvariantViolation("Неверное число свободных экземпляров")
        self._id = book_id
        self._isbn = isbn
        self._title = title
        self._total_copies = total_copies
        self._available_copies = available_copies

    @property
    def id(self) -> BookId:
        return self._id

    @property
    def available_copies(self) -> int:
        return self._available_copies

    def checkout(self) -> None:
        # правило 1
        if self._available_copies == 0:
            raise DomainInvariantViolation("Нет свободных экземпляров книги")
        self._available_copies -= 1

    def check_can_return(self) -> None:
        if self._available_copies == self._total_copies:
            raise DomainInvariantViolation("Все экземпляры книги уже в фонде")

    def return_copy(self) -> None:
        self.check_can_return()
        self._available_copies += 1

    def to_state(self) -> dict:
        return {
            "book_id": str(self._id.value),
            "isbn": self._isbn.value,
            "title": self._title,
            "total_copies": self._total_copies,
            "available_copies": self._available_copies,
        }


class Loan:  # корень агрегата «выдача»
    def __init__(self, loan_id: LoanId, book_id: BookId, reader_id: ReaderId,
                 period: LoanPeriod, return_date: date | None = None,
                 fine: Fine | None = None):
        if return_date is None and fine is not None:
            raise DomainInvariantViolation("У незакрытой выдачи не может быть штрафа")
        if return_date is not None:
            # правило 3
            if return_date < period.issue_date:
                raise DomainInvariantViolation("Дата возврата раньше даты выдачи")
            # правило 4
            if fine != Loan._calc_fine(period, return_date):
                raise DomainInvariantViolation("Штраф рассчитан неверно")
        self._id = loan_id
        self._book_id = book_id  # ссылка на книгу по ID
        self._reader_id = reader_id
        self._period = period
        self._return_date = return_date
        self._fine = fine

    @property
    def id(self) -> LoanId:
        return self._id

    @property
    def book_id(self) -> BookId:
        return self._book_id

    @property
    def reader_id(self) -> ReaderId:
        return self._reader_id

    @property
    def fine(self) -> Fine | None:
        return self._fine

    @property
    def is_closed(self) -> bool:
        return self._return_date is not None

    @staticmethod
    def _calc_fine(period: LoanPeriod, return_date: date) -> Fine:
        return Fine(FINE_PER_DAY * period.days_overdue(return_date))

    def return_book(self, return_date: date) -> Fine:
        # правило 7
        if self.is_closed:
            raise DomainInvariantViolation("Выдача уже закрыта возвратом")
        # правило 3
        if return_date < self._period.issue_date:
            raise DomainInvariantViolation("Дата возврата раньше даты выдачи")
        # правило 4
        self._fine = Loan._calc_fine(self._period, return_date)
        self._return_date = return_date
        return self._fine

    def to_state(self) -> dict:
        return {
            "loan_id": str(self._id.value),
            "book_id": str(self._book_id.value),
            "reader_id": self._reader_id.value,
            "issue_date": self._period.issue_date,
            "due_date": self._period.due_date,
            "return_date": self._return_date,
            "fine": self._fine.amount if self._fine else None,
        }
