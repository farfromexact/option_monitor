from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core.models import DerivedMetrics, SignalLabel, SignalSnapshot, UnderlyingQuote


class SignalEngine:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self._history: dict[str, dict[str, float]] = {}

    def evaluate(
        self,
        option_chain: pd.DataFrame,
        quote: UnderlyingQuote,
        intraday: pd.DataFrame,
        metrics: DerivedMetrics,
        remaining_days: int | None,
    ) -> SignalSnapshot:
        signal_cfg = self.settings["signal"]
        proximity_base = max(quote.last, 1e-6)

        pin_strike = metrics.pin_strike
        gamma_barycenter = metrics.gamma_barycenter
        gamma_meaningful = metrics.gamma_confidence_label == "HIGH"
        max_pain = metrics.max_pain or metrics.total_oi_center or metrics.atm_strike

        distance_to_pin = abs(quote.last - pin_strike) / proximity_base if pin_strike is not None else math.inf
        distance_to_barycenter = abs(quote.last - gamma_barycenter) / proximity_base if gamma_barycenter is not None else math.inf
        distance_to_pain = abs(quote.last - max_pain) / proximity_base if max_pain is not None else math.inf

        total_oi = float(option_chain["open_interest"].sum()) if not option_chain.empty else 0.0
        top_strike_oi = float(option_chain.groupby("strike")["open_interest"].sum().max()) if total_oi > 0 else 0.0
        oi_concentration = float(top_strike_oi / total_oi) if total_oi > 0 else 0.0
        gamma_concentration = float(metrics.gamma_peak_ratio or 0.0)

        trend_return = 0.0
        realized_vol = 0.0
        volume_ratio = 1.0
        if len(intraday) >= 6:
            closes = intraday["close"].astype(float)
            sixth = float(closes.iloc[-6])
            last = float(closes.iloc[-1])
            trend_return = float((last / sixth) - 1) if sixth else 0.0
            realized_vol = float(closes.pct_change().tail(15).std(ddof=0) or 0.0)
            recent_volume = float(intraday["volume"].tail(5).mean())
            baseline_volume = float(intraday["volume"].head(max(5, len(intraday) // 2)).mean())
            volume_ratio = recent_volume / baseline_volume if baseline_volume else 1.0

        near_expiry = remaining_days is not None and remaining_days <= signal_cfg["near_expiry_days"]
        confidence_component = float(metrics.gamma_confidence_score)
        pin_score = self._clamp01(
            (1 - min(distance_to_pin, 1.0) / max(signal_cfg["pin_proximity_ratio"], 1e-6)) * 0.40
            + confidence_component * 0.35
            + oi_concentration * 0.15
            + (1.0 if near_expiry else 0.0) * 0.10
        ) if pin_strike is not None else 0.0
        break_score = self._clamp01(
            (min(distance_to_pin, 1.0) / max(signal_cfg["break_proximity_ratio"], 1e-6)) * 0.25
            + (abs(trend_return) / max(signal_cfg["trend_return_threshold"], 1e-6)) * 0.30
            + (volume_ratio / max(signal_cfg["volume_spike_ratio"], 1e-6)) * 0.25
            + confidence_component * 0.20
        ) if pin_strike is not None else 0.0

        pin_case = (
            gamma_meaningful
            and pin_strike is not None
            and distance_to_pin <= signal_cfg["pin_proximity_ratio"]
            and near_expiry
        )
        break_case = (
            gamma_meaningful
            and pin_strike is not None
            and distance_to_pin >= signal_cfg["break_proximity_ratio"]
            and abs(trend_return) >= signal_cfg["trend_return_threshold"]
            and volume_ratio >= signal_cfg["volume_spike_ratio"]
        )
        watch_case = (
            metrics.gamma_confidence_label in {"HIGH", "MEDIUM"}
            and pin_strike is not None
            and min(distance_to_pin, distance_to_pain) <= signal_cfg["watch_proximity_ratio"]
        )

        history = self._history.get(quote.code, {})
        oi_change = self._delta(total_oi, history.get("total_oi"))
        gamma_barycenter_drift = self._delta(metrics.gamma_barycenter, history.get("gamma_barycenter"))
        pin_strike_drift = self._delta(metrics.pin_strike, history.get("pin_strike"))
        atm_iv_change = self._delta(metrics.atm_iv, history.get("atm_iv"))
        skew_change = self._delta(metrics.skew, history.get("skew"))
        self._history[quote.code] = {
            "total_oi": total_oi,
            "gamma_barycenter": float(metrics.gamma_barycenter) if metrics.gamma_barycenter is not None else math.nan,
            "pin_strike": float(metrics.pin_strike) if metrics.pin_strike is not None else math.nan,
            "atm_iv": float(metrics.atm_iv) if metrics.atm_iv is not None else math.nan,
            "skew": float(metrics.skew) if metrics.skew is not None else math.nan,
        }

        if metrics.gamma_confidence_label == "LOW":
            label = SignalLabel.NEUTRAL
            reason = f"No dominant gamma center: {metrics.gamma_structure_label} gamma structure."
        elif pin_case:
            label = SignalLabel.PIN
            reason = "Gamma center meaningful; spot is close to Pin Strike and pin structure may dominate intraday."
        elif break_case and trend_return > 0:
            label = SignalLabel.BREAK_UP
            reason = "High-confidence gamma structure is weakening and price is breaking away upward from Pin Strike."
        elif break_case and trend_return < 0:
            label = SignalLabel.BREAK_DOWN
            reason = "High-confidence gamma structure is weakening and price is breaking away downward from Pin Strike."
        elif watch_case:
            label = SignalLabel.WATCH_BREAK
            reason = "Spot is near Pin Strike / Max Pain; watch for local pin versus break resolution."
        else:
            label = SignalLabel.NEUTRAL
            reason = f"{metrics.gamma_structure_label.title()} gamma structure; no strong pin or break edge."

        signal_strength_score = self._resolve_signal_strength(
            label=label,
            pin_score=pin_score,
            break_score=break_score,
            confidence_score=confidence_component,
        )
        signal_strength_label = self._strength_label(signal_strength_score)
        signal_logic = self._build_signal_logic(
            label=label,
            gamma_confidence_label=metrics.gamma_confidence_label,
            gamma_structure_label=metrics.gamma_structure_label,
            near_expiry=near_expiry,
            pin_case=pin_case,
            break_case=break_case,
            watch_case=watch_case,
        )

        return SignalSnapshot(
            label=label,
            reason=reason,
            diagnostics={
                "pin_score": round(pin_score, 4),
                "break_score": round(break_score, 4),
                "distance_to_pin": round(distance_to_pin, 6),
                "distance_to_barycenter": round(distance_to_barycenter, 6),
                "distance_to_pain": round(distance_to_pain, 6),
                "oi_concentration": round(oi_concentration, 4),
                "gamma_concentration": round(gamma_concentration, 4),
                "gamma_confidence_score": round(metrics.gamma_confidence_score, 4),
                "gamma_confidence_label": metrics.gamma_confidence_label,
                "gamma_structure_label": metrics.gamma_structure_label,
                "trend_return": round(trend_return, 6),
                "realized_vol": round(realized_vol, 6),
                "volume_ratio": round(volume_ratio, 4),
                "oi_change": oi_change,
                "gamma_barycenter_drift": gamma_barycenter_drift,
                "pin_strike_drift": pin_strike_drift,
                "atm_iv_change": atm_iv_change,
                "skew_intraday_change": skew_change,
                "near_pin_alert": gamma_meaningful and distance_to_pin <= signal_cfg["pin_proximity_ratio"],
                "near_pain_alert": distance_to_pain <= signal_cfg["watch_proximity_ratio"],
                "break_alert": break_case,
                "gamma_barycenter_meaningful": gamma_meaningful,
                "near_expiry": near_expiry,
                "signal_strength_score": round(signal_strength_score, 4),
                "signal_strength_label": signal_strength_label,
                "signal_logic": signal_logic,
            },
        )

    @staticmethod
    def _clamp01(value: float) -> float:
        if math.isnan(value):
            return 0.0
        return max(0.0, min(1.0, value))

    @staticmethod
    def _delta(current: float | None, previous: float | None) -> float | None:
        if current is None or previous is None:
            return None
        if isinstance(previous, float) and math.isnan(previous):
            return None
        return float(current - previous)

    @staticmethod
    def _resolve_signal_strength(
        *,
        label: SignalLabel,
        pin_score: float,
        break_score: float,
        confidence_score: float,
    ) -> float:
        if label == SignalLabel.PIN:
            return max(pin_score, confidence_score)
        if label in {SignalLabel.BREAK_UP, SignalLabel.BREAK_DOWN}:
            return max(break_score, confidence_score)
        if label == SignalLabel.WATCH_BREAK:
            return max(min(max(pin_score, break_score), 0.75), confidence_score * 0.8)
        return min(max(pin_score, break_score, confidence_score * 0.6), 0.55)

    @staticmethod
    def _strength_label(score: float) -> str:
        if score >= 0.75:
            return "STRONG"
        if score >= 0.45:
            return "MEDIUM"
        return "WEAK"

    @staticmethod
    def _build_signal_logic(
        *,
        label: SignalLabel,
        gamma_confidence_label: str,
        gamma_structure_label: str,
        near_expiry: bool,
        pin_case: bool,
        break_case: bool,
        watch_case: bool,
    ) -> str:
        clauses = [
            f"gamma_conf={gamma_confidence_label}",
            f"struct={gamma_structure_label}",
            f"near_expiry={'Y' if near_expiry else 'N'}",
        ]
        if pin_case:
            clauses.append("trigger=pin_case")
        elif break_case:
            clauses.append("trigger=break_case")
        elif watch_case:
            clauses.append("trigger=watch_case")
        else:
            clauses.append("trigger=none")
        clauses.append(f"label={label.value}")
        return " | ".join(clauses)
