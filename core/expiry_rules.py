from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from data.trading_calendar import TradingCalendar
from utils.helpers import load_yaml

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProductRule:
    product_code: str
    exchange: str
    option_rule_type: str
    futures_rule_type: str
    description: str
    supports_manual_override: bool
    option_rule_params: dict[str, Any] = field(default_factory=dict)
    futures_rule_params: dict[str, Any] = field(default_factory=dict)
    date_source_priority: dict[str, list[str]] = field(default_factory=dict)
    manual_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContractMonth:
    year: int
    month: int

    @property
    def code(self) -> str:
        return f"{self.year % 100:02d}{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(slots=True)
class ContractDateInfo:
    product_code: str
    exchange: str
    contract_month: str
    option_last_trading_day: date | None
    option_last_trading_day_source: str
    option_expiry_date: date | None
    option_expiry_date_source: str
    futures_last_trading_day: date | None
    futures_last_trading_day_source: str
    remaining_trading_days: int | None
    remaining_calendar_days: int | None
    is_expired: bool
    is_front_valid: bool
    selected_rule_type: str
    source_priority_result: dict[str, str]
    raw_vendor_fields: dict[str, Any]


class RuleEngine:
    def __init__(self, trade_calendar: TradingCalendar, rules_path: str | Path) -> None:
        self.trade_calendar = trade_calendar
        self.rules = self._load_rules(rules_path)

    def _load_rules(self, rules_path: str | Path) -> dict[str, ProductRule]:
        payload = load_yaml(rules_path)
        products = payload.get("products", {})
        return {
            code.upper(): ProductRule(
                product_code=code.upper(),
                exchange=str(config["exchange"]).upper(),
                option_rule_type=str(config["option_rule_type"]),
                futures_rule_type=str(config["futures_rule_type"]),
                description=str(config.get("description", "")),
                supports_manual_override=bool(config.get("supports_manual_override", False)),
                option_rule_params=dict(config.get("option_rule_params", {})),
                futures_rule_params=dict(config.get("futures_rule_params", {})),
                date_source_priority={key: list(value) for key, value in dict(config.get("date_source_priority", {})).items()},
                manual_overrides=dict(config.get("manual_overrides", {})),
            )
            for code, config in products.items()
        }

    def get_rule(self, product_code: str) -> ProductRule:
        code = product_code.upper()
        if code not in self.rules:
            raise KeyError(f"No product rule configured for {code}")
        return self.rules[code]

    def build_contract_date_info(
        self,
        product_code: str,
        contract_month: ContractMonth,
        asof_date: date,
        metadata: dict[str, Any] | None = None,
    ) -> ContractDateInfo:
        metadata = metadata or {}
        rule = self.get_rule(product_code)
        option_last_trading_day, option_last_source = self._resolve_date_field(
            field_name="option_last_trading_day",
            product_rule=rule,
            contract_month=contract_month,
            metadata=metadata,
        )
        option_expiry_date, option_expiry_source = self._resolve_date_field(
            field_name="option_expiry_date",
            product_rule=rule,
            contract_month=contract_month,
            metadata=metadata,
        )
        futures_last_trading_day, futures_last_source = self._resolve_date_field(
            field_name="futures_last_trading_day",
            product_rule=rule,
            contract_month=contract_month,
            metadata=metadata,
        )
        remaining_trading_days = self.get_remaining_trading_days(option_last_trading_day, asof_date, rule.exchange)
        remaining_calendar_days = self.get_remaining_calendar_days(option_last_trading_day, asof_date)
        is_expired = bool(option_last_trading_day is not None and remaining_trading_days is not None and remaining_trading_days < 0)

        info = ContractDateInfo(
            product_code=rule.product_code,
            exchange=rule.exchange,
            contract_month=contract_month.label,
            option_last_trading_day=option_last_trading_day,
            option_last_trading_day_source=option_last_source,
            option_expiry_date=option_expiry_date,
            option_expiry_date_source=option_expiry_source,
            futures_last_trading_day=futures_last_trading_day,
            futures_last_trading_day_source=futures_last_source,
            remaining_trading_days=remaining_trading_days,
            remaining_calendar_days=remaining_calendar_days,
            is_expired=is_expired,
            is_front_valid=not is_expired,
            selected_rule_type=rule.option_rule_type,
            source_priority_result={
                "option_last_trading_day": option_last_source,
                "option_expiry_date": option_expiry_source,
                "futures_last_trading_day": futures_last_source,
            },
            raw_vendor_fields=metadata,
        )
        vendor_summary = {
            "option_expiry_date": metadata.get("option_expiry_date"),
            "option_last_trading_day": metadata.get("option_last_trading_day"),
            "futures_last_trading_day": metadata.get("futures_last_trading_day"),
            "contract_code_count": metadata.get("contract_code_count"),
            "contract_code_sample": metadata.get("contract_code_sample"),
        }
        logger.info(
            "Contract date info product=%s contract_month=%s exchange=%s vendor=%s option_last=%s option_expiry=%s futures_last=%s trading_dte=%s calendar_dte=%s rule=%s source=%s",
            info.product_code,
            info.contract_month,
            info.exchange,
            vendor_summary,
            _fmt_date(info.option_last_trading_day),
            _fmt_date(info.option_expiry_date),
            _fmt_date(info.futures_last_trading_day),
            info.remaining_trading_days,
            info.remaining_calendar_days,
            info.selected_rule_type,
            info.source_priority_result,
        )
        return info

    def get_option_last_trading_day(
        self,
        product_code: str,
        contract_month: ContractMonth,
        metadata: dict[str, Any] | None = None,
    ) -> date | None:
        return self.build_contract_date_info(product_code, contract_month, date.today(), metadata).option_last_trading_day

    def get_option_expiry_date(
        self,
        product_code: str,
        contract_month: ContractMonth,
        metadata: dict[str, Any] | None = None,
    ) -> date | None:
        return self.build_contract_date_info(product_code, contract_month, date.today(), metadata).option_expiry_date

    def get_futures_last_trading_day(
        self,
        product_code: str,
        contract_month: ContractMonth,
        metadata: dict[str, Any] | None = None,
    ) -> date | None:
        return self.build_contract_date_info(product_code, contract_month, date.today(), metadata).futures_last_trading_day

    def get_remaining_trading_days(self, target_date: date | None, asof_date: date, exchange: str) -> int | None:
        if target_date is None:
            return None
        if target_date < asof_date:
            return -1
        trading_days = self.trade_calendar.trading_days_between(asof_date, target_date, exchange, inclusive_end=True)
        if not trading_days:
            return -1 if target_date < asof_date else 0
        return max(len(trading_days) - 1, 0)

    @staticmethod
    def get_remaining_calendar_days(target_date: date | None, asof_date: date) -> int | None:
        if target_date is None:
            return None
        return (target_date - asof_date).days

    def get_active_contract_months(
        self,
        product_code: str,
        contract_months: list[ContractMonth],
        asof_date: date,
        metadata_by_month: dict[str, dict[str, Any]] | None = None,
    ) -> list[ContractDateInfo]:
        metadata_by_month = metadata_by_month or {}
        infos = [
            self.build_contract_date_info(product_code, month, asof_date, metadata_by_month.get(month.code, {}))
            for month in contract_months
        ]
        return [info for info in infos if info.is_front_valid]

    def get_front_valid_month(
        self,
        product_code: str,
        contract_months: list[ContractMonth],
        asof_date: date,
        metadata_by_month: dict[str, dict[str, Any]] | None = None,
    ) -> ContractDateInfo | None:
        active = self.get_active_contract_months(product_code, contract_months, asof_date, metadata_by_month)
        return active[0] if active else None

    def get_next_valid_month(
        self,
        product_code: str,
        contract_months: list[ContractMonth],
        asof_date: date,
        offset: int = 1,
        metadata_by_month: dict[str, dict[str, Any]] | None = None,
    ) -> ContractDateInfo | None:
        active = self.get_active_contract_months(product_code, contract_months, asof_date, metadata_by_month)
        if len(active) <= offset:
            return None
        return active[offset]

    def _resolve_date_field(
        self,
        *,
        field_name: str,
        product_rule: ProductRule,
        contract_month: ContractMonth,
        metadata: dict[str, Any],
    ) -> tuple[date | None, str]:
        priority = product_rule.date_source_priority.get(field_name, ["derived", "vendor"])
        for source in priority:
            if source == "manual_override":
                value = self._manual_override_date(product_rule, field_name, contract_month)
            elif source == "vendor":
                value = self._vendor_date(metadata, field_name)
            elif source == "derived":
                value = self._derived_date(product_rule, field_name, contract_month, metadata)
            else:
                value = None
            if value is not None:
                return value, source
        return None, "unavailable"

    def _manual_override_date(self, product_rule: ProductRule, field_name: str, contract_month: ContractMonth) -> date | None:
        overrides = product_rule.manual_overrides.get(contract_month.code, {})
        value = overrides.get(field_name)
        return _coerce_date(value)

    def _vendor_date(self, metadata: dict[str, Any], field_name: str) -> date | None:
        field_map = {
            "option_last_trading_day": ["option_last_trading_day", "vendor_option_last_trading_day", "last_trading_day"],
            "option_expiry_date": ["option_expiry_date", "vendor_option_expiry_date", "expiry_date"],
            "futures_last_trading_day": ["futures_last_trading_day", "vendor_futures_last_trading_day", "underlying_futures_last_trading_day"],
        }
        for candidate in field_map.get(field_name, []):
            if candidate in metadata:
                coerced = _coerce_date(metadata[candidate])
                if coerced is not None:
                    return coerced
        return None

    def _derived_date(
        self,
        product_rule: ProductRule,
        field_name: str,
        contract_month: ContractMonth,
        metadata: dict[str, Any],
    ) -> date | None:
        if product_rule.option_rule_type == "PREV_MONTH_NTH_LAST_TRADING_DAY" and field_name in {"option_last_trading_day", "option_expiry_date"}:
            n = int(product_rule.option_rule_params.get("n", 5))
            prev_year, prev_month = _previous_month(contract_month.year, contract_month.month)
            return self.trade_calendar.nth_last_trading_day_of_month(prev_year, prev_month, product_rule.exchange, n)
        if product_rule.option_rule_type == "THIRD_FRIDAY_OF_CONTRACT_MONTH" and field_name in {"option_last_trading_day", "option_expiry_date"}:
            return _third_friday(contract_month.year, contract_month.month)
        if product_rule.option_rule_type == "SC_INE_SPECIAL" and field_name in {"option_last_trading_day", "option_expiry_date"}:
            # Separate branch for SC. Prefer vendor/manual; if unavailable, do not silently fall back to SHFE metal logic.
            if field_name == "option_expiry_date":
                return self._vendor_date(metadata, field_name) or self._vendor_date(metadata, "option_last_trading_day")
            return self._vendor_date(metadata, field_name)
        if product_rule.futures_rule_type == "SAME_AS_OPTION_LAST_TRADING_DAY" and field_name == "futures_last_trading_day":
            return self._derived_date(product_rule, "option_last_trading_day", contract_month, metadata)
        return None


def contract_month_from_code(code: str | None) -> ContractMonth | None:
    if not code:
        return None
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) < 4:
        return None
    year = 2000 + int(digits[:2])
    month = int(digits[2:4])
    if month < 1 or month > 12:
        return None
    return ContractMonth(year=year, month=month)


def _coerce_date(value: Any) -> date | None:
    if value in (None, "", "None", "nan"):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m", "%Y/%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _fmt_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _third_friday(year: int, month: int) -> date:
    current = date(year, month, 1)
    fridays: list[date] = []
    while current.month == month:
        if current.weekday() == 4:
            fridays.append(current)
        current = current + timedelta(days=1)
    if len(fridays) < 3:
        raise ValueError(f"Unable to determine third Friday for {year:04d}-{month:02d}")
    return fridays[2]
