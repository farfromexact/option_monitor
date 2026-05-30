from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from core.calculations import prepare_option_chain
from core.expiry_rules import ContractDateInfo, RuleEngine, contract_month_from_code
from data.wind_client import WindClient

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OptionChainLoadResult:
    chain: pd.DataFrame
    option_last_trading_day: str | None
    option_expiry_date: str | None
    futures_last_trading_day: str | None
    remaining_trading_days: int | None
    remaining_calendar_days: int | None
    selected_contract_info: ContractDateInfo | None = None
    candidate_contracts: list[ContractDateInfo] = field(default_factory=list)
    active_contracts: list[ContractDateInfo] = field(default_factory=list)

    @property
    def expiry_date(self) -> str | None:
        # Deprecated compatibility alias for the old single-field UI.
        return self.option_expiry_date

    @property
    def remaining_days(self) -> int | None:
        # Deprecated compatibility alias for the old single-field UI.
        return self.remaining_trading_days


class OptionDataLoader:
    def __init__(self, client: WindClient, rule_engine: RuleEngine) -> None:
        self.client = client
        self.rule_engine = rule_engine

    def load_option_chain(self, expiry_month_offset: int, asof_date: date | None = None) -> OptionChainLoadResult:
        asof_date = asof_date or date.today()
        chain = self.client.get_option_chain()
        prepared = prepare_option_chain(chain)
        if prepared.empty:
            return self.load_empty_option_chain()

        candidate_infos = self._build_candidate_infos(prepared, asof_date)
        active_infos = [info for info in candidate_infos if info.is_front_valid]
        logger.info(
            "Front month evaluation product=%s candidates=%s active=%s",
            self.client.config.product_id,
            [
                {
                    "month": info.contract_month,
                    "option_last_trading_day": info.option_last_trading_day.isoformat() if info.option_last_trading_day else None,
                    "is_expired": info.is_expired,
                    "source": info.option_last_trading_day_source,
                }
                for info in candidate_infos
            ],
            [info.contract_month for info in active_infos],
        )

        selected_info = self._select_contract_info(candidate_infos, active_infos, expiry_month_offset)
        filtered = prepared
        if selected_info is not None:
            filtered = prepared[prepared["expiry_month"] == selected_info.contract_month].copy()

        return OptionChainLoadResult(
            chain=filtered.reset_index(drop=True),
            option_last_trading_day=_fmt_date(selected_info.option_last_trading_day if selected_info else None),
            option_expiry_date=_fmt_date(selected_info.option_expiry_date if selected_info else None),
            futures_last_trading_day=_fmt_date(selected_info.futures_last_trading_day if selected_info else None),
            remaining_trading_days=selected_info.remaining_trading_days if selected_info else None,
            remaining_calendar_days=selected_info.remaining_calendar_days if selected_info else None,
            selected_contract_info=selected_info,
            candidate_contracts=candidate_infos,
            active_contracts=active_infos,
        )

    def load_empty_option_chain(self) -> OptionChainLoadResult:
        return OptionChainLoadResult(
            chain=prepare_option_chain(pd.DataFrame()),
            option_last_trading_day=None,
            option_expiry_date=None,
            futures_last_trading_day=None,
            remaining_trading_days=None,
            remaining_calendar_days=None,
        )

    def load_intraday(self, granularity: str, lookback_points: int) -> pd.DataFrame:
        return self.client.get_intraday_bars(granularity, lookback_points)

    def load_empty_intraday(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])

    def _build_candidate_infos(self, prepared: pd.DataFrame, asof_date: date) -> list[ContractDateInfo]:
        month_labels = sorted(label for label in prepared.get("expiry_month", pd.Series(dtype=str)).dropna().unique().tolist() if label)
        candidate_infos: list[ContractDateInfo] = []
        for month_label in month_labels:
            month_rows = prepared[prepared["expiry_month"] == month_label].copy()
            if month_rows.empty:
                continue
            contract_month = contract_month_from_code(str(month_rows["contract_code"].iloc[0]))
            if contract_month is None:
                logger.warning("Skip contract month parsing failure product=%s month_label=%s", self.client.config.product_id, month_label)
                continue
            metadata = self._build_month_metadata(month_rows)
            try:
                info = self.rule_engine.build_contract_date_info(
                    product_code=self.client.config.product_id,
                    contract_month=contract_month,
                    asof_date=asof_date,
                    metadata=metadata,
                )
            except KeyError:
                logger.warning(
                    "No product rule configured for product=%s; fallback to vendor/parsed month selection only",
                    self.client.config.product_id,
                )
                info = ContractDateInfo(
                    product_code=self.client.config.product_id,
                    exchange=str(self.client.product.get("exchange", "")),
                    contract_month=month_label,
                    option_last_trading_day=None,
                    option_last_trading_day_source="unconfigured",
                    option_expiry_date=None,
                    option_expiry_date_source="unconfigured",
                    futures_last_trading_day=None,
                    futures_last_trading_day_source="unconfigured",
                    remaining_trading_days=None,
                    remaining_calendar_days=None,
                    is_expired=False,
                    is_front_valid=True,
                    selected_rule_type="UNCONFIGURED",
                    source_priority_result={},
                    raw_vendor_fields=metadata,
                )
            candidate_infos.append(info)
        candidate_infos.sort(key=lambda item: item.contract_month)
        return candidate_infos

    def _select_contract_info(
        self,
        candidate_infos: list[ContractDateInfo],
        active_infos: list[ContractDateInfo],
        expiry_month_offset: int,
    ) -> ContractDateInfo | None:
        if active_infos:
            selected_index = min(max(expiry_month_offset, 0), len(active_infos) - 1)
            selected = active_infos[selected_index]
            logger.info(
                "Selected front-valid month product=%s offset=%s month=%s active=%s",
                self.client.config.product_id,
                expiry_month_offset,
                selected.contract_month,
                [info.contract_month for info in active_infos],
            )
            return selected
        if candidate_infos:
            selected_index = min(max(expiry_month_offset, 0), len(candidate_infos) - 1)
            selected = candidate_infos[selected_index]
            logger.warning(
                "No front-valid month found product=%s; fallback to candidate month=%s candidates=%s",
                self.client.config.product_id,
                selected.contract_month,
                [info.contract_month for info in candidate_infos],
            )
            return selected
        return None

    @staticmethod
    def _build_month_metadata(month_rows: pd.DataFrame) -> dict[str, object]:
        metadata: dict[str, object] = {}
        expiry_values = month_rows.get("expiry_date")
        if expiry_values is not None:
            non_null = expiry_values.dropna()
            if not non_null.empty:
                metadata["option_expiry_date"] = str(non_null.iloc[0])
        option_last_values = month_rows.get("option_last_trading_day")
        if option_last_values is not None:
            non_null = option_last_values.dropna()
            if not non_null.empty:
                metadata["option_last_trading_day"] = str(non_null.iloc[0])
        futures_last_values = month_rows.get("underlying_futures_last_trading_day")
        if futures_last_values is not None:
            non_null = futures_last_values.dropna()
            if not non_null.empty:
                metadata["futures_last_trading_day"] = str(non_null.iloc[0])
        contract_codes = sorted(month_rows["contract_code"].dropna().astype(str).unique().tolist())
        metadata["contract_code_count"] = len(contract_codes)
        metadata["contract_code_sample"] = contract_codes[:6]
        return metadata


def _fmt_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None
