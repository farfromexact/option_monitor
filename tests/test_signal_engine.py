from __future__ import annotations

from datetime import datetime

import pandas as pd

from core.models import DerivedMetrics, SignalLabel, UnderlyingQuote
from core.signal_engine import SignalEngine


def test_signal_engine_pin_case() -> None:
    settings = {
        "signal": {
            "pin_proximity_ratio": 0.003,
            "break_proximity_ratio": 0.008,
            "concentration_threshold": 0.33,
            "watch_proximity_ratio": 0.005,
            "trend_return_threshold": 0.004,
            "volume_spike_ratio": 1.3,
            "near_expiry_days": 5,
        }
    }
    engine = SignalEngine(settings)
    option_chain = pd.DataFrame(
        [
            {"strike": 2.45, "open_interest": 9000},
            {"strike": 2.4, "open_interest": 1000},
            {"strike": 2.5, "open_interest": 1200},
        ]
    )
    intraday = pd.DataFrame({"close": [2.45, 2.451, 2.449, 2.45, 2.451, 2.45], "volume": [10, 11, 10, 12, 10, 11]})
    quote = UnderlyingQuote("510050.SH", "上证50ETF", 2.45, 2.44, 0.01, 0.4, 100000, timestamp=datetime.now())
    metrics = DerivedMetrics(
        atm_strike=2.45,
        atm_iv=0.18,
        max_pain=2.45,
        call_oi_center=2.45,
        put_oi_center=2.45,
        total_oi_center=2.45,
        gamma_barycenter=2.45,
        pin_strike=2.45,
        gamma_confidence_score=0.82,
        gamma_confidence_label="HIGH",
        gamma_structure_label="concentrated",
        gamma_top3_ratio=0.9,
        gamma_peak_ratio=0.7,
        gamma_weighted_std=0.01,
        gamma_normalized_dispersion=0.05,
        gamma_peak_sharpness=2.0,
        call_wing_iv=None,
        put_wing_iv=None,
        skew=None,
    )
    signal = engine.evaluate(option_chain, quote, intraday, metrics, remaining_days=2)
    assert signal.label == SignalLabel.PIN
