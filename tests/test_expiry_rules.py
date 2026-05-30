from __future__ import annotations

from datetime import date, timedelta

from core.expiry_rules import ContractMonth, RuleEngine
from data.trading_calendar import TradingCalendar


def _business_days(start_day: date, end_day: date, remove: set[date] | None = None) -> list[date]:
    remove = remove or set()
    days: list[date] = []
    current = start_day
    while current <= end_day:
        if current.weekday() < 5 and current not in remove:
            days.append(current)
        current += timedelta(days=1)
    return days


def _mock_calendar() -> TradingCalendar:
    shfe_days = _business_days(date(2026, 3, 1), date(2026, 5, 31), remove={date(2026, 3, 31)})
    ine_days = _business_days(date(2026, 4, 1), date(2026, 6, 30), remove={date(2026, 5, 1), date(2026, 5, 4)})
    cfe_days = _business_days(date(2026, 3, 1), date(2026, 4, 30))
    calendars = {"SHFE": shfe_days, "INE": ine_days, "CFE": cfe_days}

    def fetcher(exchange: str, start_day: date, end_day: date) -> list[date]:
        return [day for day in calendars[exchange] if start_day <= day <= end_day]

    return TradingCalendar(fetcher=fetcher)


def _engine() -> RuleEngine:
    return RuleEngine(_mock_calendar(), "config/product_rules.yaml")


def test_shfe_prev_month_5th_last_trading_day() -> None:
    engine = _engine()
    contract_month = ContractMonth(2026, 4)
    info = engine.build_contract_date_info("AU", contract_month, asof_date=date(2026, 3, 20))
    assert info.option_last_trading_day == date(2026, 3, 24)
    assert info.option_expiry_date == date(2026, 3, 24)
    assert info.selected_rule_type == "PREV_MONTH_NTH_LAST_TRADING_DAY"


def test_remaining_trading_days_not_negative_for_live_contract() -> None:
    engine = _engine()
    remaining = engine.get_remaining_trading_days(date(2026, 3, 24), date(2026, 3, 20), "SHFE")
    assert remaining is not None
    assert remaining >= 0
    assert remaining == 2


def test_front_month_filters_expired_contract() -> None:
    engine = _engine()
    contract_months = [ContractMonth(2026, 4), ContractMonth(2026, 5)]
    front = engine.get_front_valid_month("AU", contract_months, asof_date=date(2026, 3, 26))
    assert front is not None
    assert front.contract_month == "2026-05"
    active = engine.get_active_contract_months("AU", contract_months, asof_date=date(2026, 3, 26))
    assert [item.contract_month for item in active] == ["2026-05"]


def test_calendar_days_and_trading_days_are_different() -> None:
    engine = _engine()
    trading_days = engine.get_remaining_trading_days(date(2026, 3, 24), date(2026, 3, 20), "SHFE")
    calendar_days = engine.get_remaining_calendar_days(date(2026, 3, 24), date(2026, 3, 20))
    assert trading_days == 2
    assert calendar_days == 4
    assert trading_days != calendar_days


def test_sc_uses_separate_rule_branch() -> None:
    engine = _engine()
    info = engine.build_contract_date_info("SC", ContractMonth(2026, 5), asof_date=date(2026, 4, 20), metadata={})
    assert info.selected_rule_type == "SC_INE_SPECIAL"
    assert info.option_last_trading_day is None
    assert info.option_expiry_date is None


def test_cfe_index_option_uses_third_friday_rule() -> None:
    engine = _engine()
    info = engine.build_contract_date_info("IH", ContractMonth(2026, 3), asof_date=date(2026, 3, 10))
    assert info.selected_rule_type == "THIRD_FRIDAY_OF_CONTRACT_MONTH"
    assert info.option_last_trading_day == date(2026, 3, 20)
    assert info.option_expiry_date == date(2026, 3, 20)
