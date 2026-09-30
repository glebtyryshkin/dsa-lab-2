from datetime import date

from .aggregates import Loan
from .exceptions import DomainInvariantViolation
from .repositories import BookRepository, LoanRepository
from .value_objects import BookId, Fine, LoanId, LoanPeriod, ReaderId


class CheckoutService:
    """Доменный сервис: выдача меняет Book и создаёт Loan"""

    def __init__(self, books: BookRepository, loans: LoanRepository):
        self._books = books
        self._loans = loans

    def checkout(self, book_id: BookId, reader_id: ReaderId,
                 period: LoanPeriod) -> Loan:
        book = self._books.get(book_id)
        book.checkout()  # правило 1
        loan = Loan(LoanId.new(), book_id, reader_id, period)
        self._books.save(book)
        self._loans.save(loan)
        return loan


class ReturnBookService:
    """Доменный сервис: возврат меняет Loan и Book"""

    def __init__(self, books: BookRepository, loans: LoanRepository):
        self._books = books
        self._loans = loans

    def return_book(self, book_id: BookId, reader_id: ReaderId,
                    return_date: date) -> Fine:
        # правило 2: ищем незакрытую выдачу этой книги этому читателю
        loan = self._loans.find_open(book_id, reader_id)
        if loan is None:
            raise DomainInvariantViolation(
                "Нет незакрытой выдачи этой книги этому читателю")
        book = self._books.get(book_id)
        book.check_can_return()  # до изменений
        fine = loan.return_book(return_date)  # правила 3, 4, 7
        book.return_copy()
        self._loans.save(loan)
        self._books.save(book)
        return fine
