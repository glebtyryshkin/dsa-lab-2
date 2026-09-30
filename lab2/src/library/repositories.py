from typing import Protocol

from .aggregates import Book, Loan
from .exceptions import DomainError
from .value_objects import BookId, LoanId, ReaderId


class BookRepository(Protocol):
    def get(self, book_id: BookId) -> Book: ...
    def save(self, book: Book) -> None: ...


class LoanRepository(Protocol):
    def get(self, loan_id: LoanId) -> Loan: ...
    def find_open(self, book_id: BookId, reader_id: ReaderId) -> Loan | None: ...
    def save(self, loan: Loan) -> None: ...


class InMemoryBookRepository:
    def __init__(self) -> None:
        self._items: dict[BookId, Book] = {}

    def get(self, book_id: BookId) -> Book:
        if book_id not in self._items:
            raise DomainError("Книга не найдена")
        return self._items[book_id]

    def save(self, book: Book) -> None:
        self._items[book.id] = book


class InMemoryLoanRepository:
    def __init__(self) -> None:
        self._items: dict[LoanId, Loan] = {}

    def get(self, loan_id: LoanId) -> Loan:
        if loan_id not in self._items:
            raise DomainError("Выдача не найдена")
        return self._items[loan_id]

    def find_open(self, book_id: BookId, reader_id: ReaderId) -> Loan | None:
        for loan in self._items.values():
            if (loan.book_id == book_id and loan.reader_id == reader_id
                    and not loan.is_closed):
                return loan
        return None

    def save(self, loan: Loan) -> None:
        self._items[loan.id] = loan
