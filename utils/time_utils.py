from __future__ import annotations

from datetime import date, datetime


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def days_between(target: date | None) -> int | None:
    if target is None:
        return None
    return (target - date.today()).days
