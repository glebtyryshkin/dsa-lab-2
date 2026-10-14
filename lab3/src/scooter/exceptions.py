"""Доменные и прикладные исключения области «Прокат самокатов» (ЛР3).

Иерархия разделена на два уровня: доменный (DomainError) и прикладной
(ApplicationError). Адаптер Flask сопоставляет их с кодами ответа.
"""


class DomainError(Exception):
    """Базовая доменная ошибка."""


class DomainInvariantViolation(DomainError):
    """Нарушено бизнес-правило (инвариант агрегата)."""


class InvalidValueObject(DomainError):
    """Неверное значение объекта-значения."""


class TransitionNotAllowed(DomainError):
    """Событие недопустимо в текущем состоянии сущности.

    Соответствует коду ответа 409 Conflict.
    """

    def __init__(self, state: str, event: str):
        self.state = state
        self.event = event
        super().__init__(f"Событие «{event}» недопустимо в состоянии «{state}»")


class EntityNotFound(DomainError):
    """Сущность не найдена в репозитории (код ответа 404)."""


class ApplicationError(Exception):
    """Базовая ошибка прикладного слоя."""


class GatewayUnresolved(ApplicationError):
    """Ни одно условие исключающего шлюза не выполнено, потока по умолчанию нет.

    Соответствует коду ответа 500: означает ошибку в схеме или в реализации.
    """


class ClockMovedBack(ApplicationError):
    """Попытка перевести часы приложения назад (код ответа 422)."""
