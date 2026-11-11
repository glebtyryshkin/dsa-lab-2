"""Текст уведомления покупателю."""

from __future__ import annotations


def points_word(value: int) -> str:
    number = abs(value)
    if 11 <= number % 100 <= 14:
        return "баллов"
    last = number % 10
    if last == 1:
        return "балл"
    if 2 <= last <= 4:
        return "балла"
    return "баллов"


def grouped(value: int) -> str:
    sign = "-" if value < 0 else ""
    digits = "{0:,}".format(abs(value)).replace(",", " ")
    return sign + digits


def notification_text(event: dict) -> str:
    if event["status"] != "applied":
        return "Операция отклонена: {0}".format(event["reject_reason"])
    delta = int(event["points_delta"])
    amount = abs(delta)
    if event["type"] == "purchase" or delta > 0:
        verb = "Начислено"
    else:
        verb = "Списано"
    return "{0} {1} {2}, баланс {3}".format(
        verb, grouped(amount), points_word(amount), grouped(int(event["balance_after"]))
    )
