class DomainError(Exception):
    """Базовая доменная ошибка."""


class DomainInvariantViolation(DomainError):
    """Нарушено бизнес-правило."""


class InvalidValueObject(DomainError):
    """Неверное значение объекта-значения."""
