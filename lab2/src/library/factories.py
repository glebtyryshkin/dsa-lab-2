from uuid import UUID

from .aggregates import Book, Loan
from .value_objects import ISBN, BookId, Fine, LoanId, LoanPeriod, ReaderId


class BookFactory:
    @staticmethod
    def restore(state: dict) -> Book:
        # конструктор Book заново проверяет инварианты
        return Book(
            BookId(UUID(state["book_id"])),
            ISBN(state["isbn"]),
            state["title"],
            state["total_copies"],
            state["available_copies"],
        )


class LoanFactory:
    @staticmethod
    def restore(state: dict) -> Loan:
        # конструктор Loan заново проверяет правила 3 и 4
        fine = Fine(state["fine"]) if state["fine"] is not None else None
        return Loan(
            LoanId(UUID(state["loan_id"])),
            BookId(UUID(state["book_id"])),
            ReaderId(state["reader_id"]),
            LoanPeriod(state["issue_date"], state["due_date"]),
            state["return_date"],
            fine,
        )
