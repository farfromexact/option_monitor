from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd


class SignalLabel(str, Enum):
    PIN = "PIN"
    NEUTRAL = "NEUTRAL"
    BREAK_UP = "BREAK_UP"
    BREAK_DOWN = "BREAK_DOWN"
    WATCH_BREAK = "WATCH_BREAK"


@dataclass(slots=True)
class UnderlyingQuote:
    code: str
    name: str
    last: float
    pre_close: float
    change: float
    pct_change: float
    volume: float
    bid1: float | None = None
    ask1: float | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(slots=True)
class DerivedMetrics:
    atm_strike: float | None
    atm_iv: float | None
    max_pain: float | None
    call_oi_center: float | None
    put_oi_center: float | None
    total_oi_center: float | None
    gamma_barycenter: float | None
    pin_strike: float | None
    gamma_confidence_score: float
    gamma_confidence_label: str
    gamma_structure_label: str
    gamma_top3_ratio: float | None
    gamma_peak_ratio: float | None
    gamma_weighted_std: float | None
    gamma_normalized_dispersion: float | None
    gamma_peak_sharpness: float | None
    call_wing_iv: float | None
    put_wing_iv: float | None
    skew: float | None
    atm_call_iv_mean_3: float | None = None
    atm_put_iv_mean_3: float | None = None

    @property
    def gamma_center(self) -> float | None:
        # Deprecated compatibility alias. New logic should use gamma_barycenter.
        return self.gamma_barycenter


@dataclass(slots=True)
class SignalSnapshot:
    label: SignalLabel
    reason: str
    diagnostics: dict[str, Any]


@dataclass(slots=True)
class MarketSnapshot:
    quote: UnderlyingQuote
    option_chain: pd.DataFrame
    intraday: pd.DataFrame
    metrics: DerivedMetrics
    signal: SignalSnapshot
    option_last_trading_day: str | None
    option_expiry_date: str | None
    futures_last_trading_day: str | None
    remaining_trading_days: int | None
    remaining_calendar_days: int | None
    basis_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    contract_date_source: dict[str, str] = field(default_factory=dict)
    front_month_validity: str | None = None
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def expiry_date(self) -> str | None:
        return self.option_expiry_date

    @property
    def remaining_days(self) -> int | None:
        return self.remaining_trading_days
