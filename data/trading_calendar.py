from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from utils.helpers import load_yaml

logger = logging.getLogger(__name__)

TradingDaysFetcher = Callable[[str, date, date], list[date]]


@dataclass(slots=True)
class CalendarWindow:
    exchange: str
    start: date
    end: date
    trading_days: list[date]


class TradingCalendar:
    def __init__(
        self,
        fetcher: TradingDaysFetcher | None = None,
        cache_path: str | Path | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, set[date]] = {}
        self._windows: dict[str, tuple[date, date]] = {}
        self._load_static_cache()

    def _load_static_cache(self) -> None:
        if self.cache_path is None or not self.cache_path.exists():
            return
        payload = load_yaml(self.cache_path)
        for exchange, values in payload.get("exchanges", {}).items():
            parsed = {date.fromisoformat(value) for value in values}
            self._cache[exchange.upper()] = parsed
            if parsed:
                self._windows[exchange.upper()] = (min(parsed), max(parsed))

    def is_trading_day(self, day: date, exchange: str) -> bool:
        self._ensure_window(exchange, day - timedelta(days=10), day + timedelta(days=10))
        return day in self._cache.get(exchange.upper(), set())

    def previous_trading_day(self, day: date, exchange: str, n: int = 1) -> date:
        if n < 1:
            raise ValueError("n must be >= 1")
        current = day
        remaining = n
        while remaining > 0:
            current -= timedelta(days=1)
            if self.is_trading_day(current, exchange):
                remaining -= 1
        return current

    def next_trading_day(self, day: date, exchange: str, n: int = 1) -> date:
        if n < 1:
            raise ValueError("n must be >= 1")
        current = day
        remaining = n
        while remaining > 0:
            current += timedelta(days=1)
            if self.is_trading_day(current, exchange):
                remaining -= 1
        return current

    def nth_last_trading_day_of_month(self, year: int, month: int, exchange: str, n: int) -> date:
        if n < 1:
            raise ValueError("n must be >= 1")
        month_start = date(year, month, 1)
        next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        self._ensure_window(exchange, month_start, next_month + timedelta(days=5))
        month_days = sorted(day for day in self._cache.get(exchange.upper(), set()) if month_start <= day < next_month)
        if len(month_days) < n:
            raise ValueError(f"Not enough trading days in {year:04d}-{month:02d} for exchange {exchange}")
        return month_days[-n]

    def last_trading_day_of_prev_month(self, contract_year: int, contract_month: int, exchange: str) -> date:
        prev_year, prev_month = _previous_month(contract_year, contract_month)
        return self.nth_last_trading_day_of_month(prev_year, prev_month, exchange, 1)

    def trading_days_between(self, start_day: date, end_day: date, exchange: str, inclusive_end: bool = True) -> list[date]:
        if end_day < start_day:
            return []
        self._ensure_window(exchange, start_day, end_day)
        days = sorted(day for day in self._cache.get(exchange.upper(), set()) if start_day <= day <= end_day)
        if not inclusive_end and end_day in days:
            days = [day for day in days if day != end_day]
        return days

    def _ensure_window(self, exchange: str, start_day: date, end_day: date) -> None:
        exchange_key = exchange.upper()
        current_window = self._windows.get(exchange_key)
        if current_window and start_day >= current_window[0] and end_day <= current_window[1]:
            return
        if self.fetcher is None:
            raise RuntimeError(f"Trading calendar for {exchange_key} is unavailable and no fetcher is configured")
        fetched_days = self.fetcher(exchange_key, start_day, end_day)
        parsed = self._cache.setdefault(exchange_key, set())
        parsed.update(fetched_days)
        if parsed:
            self._windows[exchange_key] = (min(parsed), max(parsed))
        logger.info(
            "Loaded trading calendar exchange=%s start=%s end=%s count=%s",
            exchange_key,
            start_day.isoformat(),
            end_day.isoformat(),
            len(fetched_days),
        )


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1
