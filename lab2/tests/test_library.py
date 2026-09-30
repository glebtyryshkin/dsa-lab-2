from datetime import date
from decimal import Decimal

import pytest

from src.library.aggregates import Book, Loan
from src.library.exceptions import (DomainError, DomainInvariantViolation,
                                    InvalidValueObject)
from src.library.factories import BookFactory, LoanFactory
from src.library.repositories import InMemoryBookRepository, InMemoryLoanRepository
from src.library.services import CheckoutService, ReturnBookService
from src.library.value_objects import ISBN, BookId, Fine, LoanId, LoanPeriod, ReaderId

PERIOD = LoanPeriod(date(2026, 9, 1), date(2026, 9, 15))


def make_book(total=1, available=1):
    return Book(BookId.new(), ISBN("9785389074468"), "1984", total, available)


def make_loan():
    return Loan(LoanId.new(), BookId.new(), ReaderId("reader-1"), PERIOD)


# --- инварианты агрегатов ---

def test_no_free_copies():
    # правило 1
    book = make_book(total=1, available=0)
    with pytest.raises(DomainInvariantViolation, match="Нет свободных"):
        book.checkout()


def test_return_before_issue():
    # правило 3
    loan = make_loan()
    with pytest.raises(DomainInvariantViolation, match="раньше даты выдачи"):
        loan.return_book(date(2026, 8, 31))


def test_late_return_fine():
    # правило 4: 3 дня просрочки по 50 руб.
    loan = make_loan()
    assert loan.return_book(date(2026, 9, 18)) == Fine(Decimal("150"))


def test_return_on_time():
    # правило 4: возврат в срок - штраф 0
    loan = make_loan()
    assert loan.return_book(date(2026, 9, 15)) == Fine(Decimal("0"))


def test_loan_closed_twice():
    # правило 7
    loan = make_loan()
    loan.return_book(date(2026, 9, 10))
    with pytest.raises(DomainInvariantViolation, match="уже закрыта"):
        loan.return_book(date(2026, 9, 11))


# --- объекты-значения ---

def test_negative_fine():
    # правило 5
    with pytest.raises(InvalidValueObject, match="не может быть отрицательной"):
        Fine(Decimal("-1"))


def test_due_before_issue():
    # правило 6
    with pytest.raises(InvalidValueObject, match="Срок возврата"):
        LoanPeriod(date(2026, 9, 10), date(2026, 9, 9))


# --- доменные сервисы ---

def setup_services(book):
    books = InMemoryBookRepository()
    loans = InMemoryLoanRepository()
    books.save(book)
    return CheckoutService(books, loans), ReturnBookService(books, loans), loans


def test_checkout_unknown_book():
    checkout, _, _ = setup_services(make_book())
    with pytest.raises(DomainError, match="Книга не найдена"):
        checkout.checkout(BookId.new(), ReaderId("reader-1"), PERIOD)


def test_return_by_other_reader():
    # правило 2: выдачи этой книги этому читателю нет
    book = make_book()
    checkout, returns, _ = setup_services(book)
    loan = checkout.checkout(book.id, ReaderId("reader-1"), PERIOD)
    with pytest.raises(DomainInvariantViolation, match="Нет незакрытой выдачи"):
        returns.return_book(book.id, ReaderId("reader-2"), date(2026, 9, 10))
    assert not loan.is_closed


def test_return_when_all_copies_in_fund():
    book = make_book(total=1, available=1)
    _, returns, loans = setup_services(book)
    loan = Loan(LoanId.new(), book.id, ReaderId("reader-1"), PERIOD)
    loans.save(loan)
    with pytest.raises(DomainInvariantViolation, match="уже в фонде"):
        returns.return_book(book.id, ReaderId("reader-1"), date(2026, 9, 10))
    assert not loan.is_closed


def test_return_book():
    book = make_book()
    checkout, returns, loans = setup_services(book)
    checkout.checkout(book.id, ReaderId("reader-1"), PERIOD)
    fine = returns.return_book(book.id, ReaderId("reader-1"), date(2026, 9, 20))
    assert fine == Fine(Decimal("250"))
    assert book.available_copies == 1
    assert loans.find_open(book.id, ReaderId("reader-1")) is None


# --- восстановление ---

def test_restore_loan():
    loan = make_loan()
    loan.return_book(date(2026, 9, 18))
    restored = LoanFactory.restore(loan.to_state())
    assert restored.id == loan.id
    assert restored.book_id == loan.book_id
    assert restored.reader_id == loan.reader_id
    assert restored.is_closed
    assert restored.fine == Fine(Decimal("150"))


def test_restore_loan_wrong_fine():
    loan = make_loan()
    loan.return_book(date(2026, 9, 18))
    state = loan.to_state()
    state["fine"] = Decimal("10")
    with pytest.raises(DomainInvariantViolation, match="Штраф рассчитан неверно"):
        LoanFactory.restore(state)


def test_restore_book_wrong_copies():
    state = make_book(total=2, available=2).to_state()
    state["available_copies"] = 5
    with pytest.raises(DomainInvariantViolation, match="свободных экземпляров"):
        BookFactory.restore(state)
