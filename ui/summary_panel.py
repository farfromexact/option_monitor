from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel

from core.models import MarketSnapshot
from utils.time_utils import now_text


class SummaryPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 4px; }"
            "QLabel { color: #f3f3f3; }"
        )
        self.labels: dict[str, QLabel] = {}
        self.value_fields = {
            "product",
            "exchange",
            "as_of",
            "wind_status",
            "underlying",
            "last",
            "pct_change",
            "option_last_trading_day",
            "option_expiry_date",
            "futures_last_trading_day",
            "trading_dte",
            "calendar_dte",
            "front_month_validity",
            "date_source",
            "atm_strike",
            "atm_iv",
            "max_pain",
            "oi_center",
            "gamma_barycenter",
            "pin_strike",
            "gamma_confidence",
            "gamma_structure",
            "gamma_status",
            "signal",
            "signal_strength",
            "signal_logic",
            "signal_scores",
        }
        fields = [
            "product",
            "exchange",
            "as_of",
            "wind_status",
            "underlying",
            "last",
            "pct_change",
            "option_last_trading_day",
            "option_expiry_date",
            "futures_last_trading_day",
            "trading_dte",
            "calendar_dte",
            "front_month_validity",
            "date_source",
            "atm_strike",
            "atm_iv",
            "max_pain",
            "oi_center",
            "gamma_barycenter",
            "pin_strike",
            "gamma_confidence",
            "gamma_structure",
            "gamma_status",
            "signal",
            "signal_strength",
            "signal_logic",
            "signal_scores",
        ]
        layout = QGridLayout(self)
        for idx, field in enumerate(fields):
            title = QLabel(field.replace("_", " ").upper())
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setStyleSheet("padding: 2px 4px; border-radius: 3px;")
            layout.addWidget(title, idx // 4 * 2, idx % 4)
            layout.addWidget(value, idx // 4 * 2 + 1, idx % 4)
            self.labels[field] = value

    def set_wind_status(self, text: str) -> None:
        self.labels["wind_status"].setText(text)

    def update_snapshot(self, snapshot: MarketSnapshot) -> None:
        metrics = snapshot.metrics
        diagnostics = snapshot.signal.diagnostics
        self.labels["product"].setText(snapshot.quote.code.split(".")[0])
        self.labels["exchange"].setText(snapshot.quote.code.split(".")[-1] if "." in snapshot.quote.code else "-")
        self.labels["as_of"].setText(now_text())
        self.labels["underlying"].setText(f"{snapshot.quote.code} / {snapshot.quote.name}")
        self.labels["last"].setText(f"{snapshot.quote.last:.4f}")
        self.labels["pct_change"].setText(f"{snapshot.quote.pct_change:.2f}%")
        self.labels["option_last_trading_day"].setText(snapshot.option_last_trading_day or "-")
        self.labels["option_expiry_date"].setText(snapshot.option_expiry_date or "-")
        self.labels["futures_last_trading_day"].setText(snapshot.futures_last_trading_day or "-")
        self.labels["trading_dte"].setText(_fmt_int(snapshot.remaining_trading_days))
        self.labels["calendar_dte"].setText(_fmt_int(snapshot.remaining_calendar_days))
        self.labels["front_month_validity"].setText(snapshot.front_month_validity or "-")
        self.labels["date_source"].setText(_format_date_source(snapshot.contract_date_source))
        self.labels["atm_strike"].setText(_fmt(metrics.atm_strike))
        self.labels["atm_iv"].setText(_fmt(metrics.atm_iv, pct=True))
        self.labels["max_pain"].setText(_fmt(metrics.max_pain))
        self.labels["oi_center"].setText(_fmt(metrics.total_oi_center))
        self.labels["gamma_barycenter"].setText(_fmt(metrics.gamma_barycenter))
        self.labels["pin_strike"].setText(_fmt(metrics.pin_strike))
        self.labels["gamma_confidence"].setText(f"{metrics.gamma_confidence_label} ({metrics.gamma_confidence_score:.2f})")
        self.labels["gamma_structure"].setText(metrics.gamma_structure_label.upper())
        self.labels["gamma_status"].setText(_gamma_status_text(metrics))
        self.labels["signal"].setText(snapshot.signal.label.value)
        self.labels["signal_strength"].setText(_format_signal_strength(diagnostics))
        self.labels["signal_logic"].setText(_format_signal_logic(snapshot))
        self.labels["signal_scores"].setText(_format_signal_scores(diagnostics))

        self._reset_styles()
        self._apply_signal_styles(snapshot)

    def _reset_styles(self) -> None:
        for field in self.value_fields:
            self.labels[field].setStyleSheet("padding: 2px 4px; border-radius: 3px; color: #f3f3f3;")

    def _apply_signal_styles(self, snapshot: MarketSnapshot) -> None:
        metrics = snapshot.metrics
        diagnostics = snapshot.signal.diagnostics
        self._set_metric_style("signal", self._color_for_signal(snapshot.signal.label.value))
        self._set_metric_style("signal_strength", self._color_for_strength(str(diagnostics.get("signal_strength_label", "WEAK"))))
        self._set_metric_style("gamma_confidence", self._color_for_confidence(metrics.gamma_confidence_label))
        self._set_metric_style("gamma_structure", self._color_for_structure(metrics.gamma_structure_label))
        if snapshot.remaining_trading_days is not None and snapshot.remaining_trading_days < 0:
            for field in ("trading_dte", "front_month_validity", "option_last_trading_day"):
                self._set_metric_style(field, "#7b2d2d")
        if snapshot.front_month_validity == "EXPIRED CONTRACT SELECTED":
            self._set_metric_style("front_month_validity", "#7b2d2d")
        if metrics.gamma_confidence_label == "LOW":
            self._set_metric_style("gamma_status", "#555555")

    def _set_metric_style(self, field: str, background: str) -> None:
        self.labels[field].setStyleSheet(
            f"padding: 2px 4px; border-radius: 3px; background: {background}; color: #f3f3f3;"
        )

    @staticmethod
    def _color_for_signal(signal: str) -> str:
        return {
            "PIN": "#2f6b3a",
            "WATCH_BREAK": "#8a6a14",
            "BREAK_UP": "#9a4a12",
            "BREAK_DOWN": "#7b2d2d",
        }.get(signal, "#3a3a3a")

    @staticmethod
    def _color_for_confidence(label: str) -> str:
        return {"HIGH": "#2f6b3a", "MEDIUM": "#8a6a14", "LOW": "#555555"}.get(label, "#3a3a3a")

    @staticmethod
    def _color_for_structure(label: str) -> str:
        return {
            "concentrated": "#2f6b3a",
            "bimodal": "#7a4ea3",
            "diffuse": "#555555",
            "flat": "#555555",
        }.get(label, "#3a3a3a")

    @staticmethod
    def _color_for_strength(label: str) -> str:
        return {
            "STRONG": "#2f6b3a",
            "MEDIUM": "#8a6a14",
            "WEAK": "#555555",
        }.get(label, "#3a3a3a")


def _fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:.2%}" if pct else f"{value:.4f}"


def _fmt_int(value: int | None) -> str:
    return "-" if value is None else str(value)


def _format_date_source(source_map: dict[str, str]) -> str:
    if not source_map:
        return "-"
    compressed = [
        f"opt_last={source_map.get('option_last_trading_day', '-')}",
        f"opt_exp={source_map.get('option_expiry_date', '-')}",
        f"fut_last={source_map.get('futures_last_trading_day', '-')}",
    ]
    return " | ".join(compressed)


def _gamma_status_text(metrics) -> str:
    if metrics.gamma_confidence_label == "LOW":
        return "No dominant gamma center"
    if metrics.gamma_confidence_label == "HIGH":
        return "Gamma center meaningful"
    return "Weak / transitional gamma structure"


def _format_signal_strength(diagnostics: dict[str, object]) -> str:
    label = str(diagnostics.get("signal_strength_label", "WEAK"))
    score = diagnostics.get("signal_strength_score")
    if score is None:
        return label
    return f"{label} ({float(score):.2f})"


def _format_signal_logic(snapshot: MarketSnapshot) -> str:
    diagnostics = snapshot.signal.diagnostics
    return str(diagnostics.get("signal_logic", snapshot.signal.reason))


def _format_signal_scores(diagnostics: dict[str, object]) -> str:
    pin_score = diagnostics.get("pin_score")
    break_score = diagnostics.get("break_score")
    distance_to_pin = diagnostics.get("distance_to_pin")
    distance_to_pain = diagnostics.get("distance_to_pain")
    parts = []
    if pin_score is not None:
        parts.append(f"pin={float(pin_score):.2f}")
    if break_score is not None:
        parts.append(f"break={float(break_score):.2f}")
    if distance_to_pin is not None:
        parts.append(f"dist_pin={float(distance_to_pin):.2%}")
    if distance_to_pain is not None:
        parts.append(f"dist_pain={float(distance_to_pain):.2%}")
    return " | ".join(parts) if parts else "-"
