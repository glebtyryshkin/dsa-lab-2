"""Агрегат «Аккаунт клиента» и его репозиторий.

Аккаунт добавлен в ЛР3: регламент требует, чтобы при неудачном списании у
клиента образовалась задолженность и аккаунт был заблокирован до её погашения,
а перед началом поездки система проверяла отсутствие задолженности.
"""

from decimal import Decimal
from typing import Dict, List, Protocol

from .exceptions import DomainInvariantViolation
from .value_objects import AccountId, Money


class Account:  # корень агрегата «аккаунт клиента»
    def __init__(self, account_id: AccountId, debt: Money = None,
                 blocked: bool = False):
        self._id = account_id
        self._debt = debt if debt is not None else Money(Decimal("0"))
        self._blocked = blocked
        if self._debt.amount > 0 and not self._blocked:
            raise DomainInvariantViolation(
                "Аккаунт с задолженностью должен быть заблокирован")

    @property
    def id(self) -> AccountId:
        return self._id

    @property
    def debt(self) -> Money:
        return self._debt

    @property
    def blocked(self) -> bool:
        return self._blocked

    def has_debt(self) -> bool:
        """Есть ли у клиента долг."""
        return self._debt.amount > Decimal("0")

    def charge_failed(self, amount: Money) -> None:
        """Списание не прошло: задолженность и блокировка аккаунта."""
        self._debt = self._debt + amount
        self._blocked = True

    def pay_debt(self, amount: Money) -> None:
        if amount.amount <= 0:
            raise DomainInvariantViolation("Сумма погашения должна быть больше нуля")
        if amount.amount > self._debt.amount:
            raise DomainInvariantViolation("Погашение больше суммы задолженности")
        self._debt = Money(self._debt.amount - amount.amount)
        if self._debt.amount == 0:
            self._blocked = False

    def to_state(self) -> Dict[str, object]:
        return {
            "account_id": self._id.value,
            "debt": str(self._debt.amount),
            "blocked": self._blocked,
        }


class AccountRepository(Protocol):
    def get(self, account_id: AccountId) -> Account: ...
    def save(self, account: Account) -> None: ...
    def all(self) -> List[Account]: ...


class InMemoryAccountRepository:
    def __init__(self) -> None:
        self._items: Dict[AccountId, Account] = {}

    def get(self, account_id: AccountId) -> Account:
        if account_id not in self._items:
            # новый клиент: аккаунт без задолженности создаётся при первом обращении
            return Account(account_id)
        return self._items[account_id]

    def save(self, account: Account) -> None:
        self._items[account.id] = account

    def all(self) -> List[Account]:
        return list(self._items.values())
