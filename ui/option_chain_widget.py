from __future__ import annotations

import math

import pandas as pd
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from core.models import DerivedMetrics, SignalSnapshot


class OptionChainWidget(QTableWidget):
    HEADERS = [
        "Call Theta",
        "Call Delta",
        "Call Gamma",
        "Call Vega",
        "Call IV",
        "Call OI",
        "Call Vol",
        "Call Last",
        "Strike",
        "Put Last",
        "Put Vol",
        "Put OI",
        "Put IV",
        "Put Vega",
        "Put Gamma",
        "Put Delta",
        "Put Theta",
    ]

    def __init__(self) -> None:
        super().__init__(0, len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(False)
        self.setStyleSheet(
            "QTableWidget { background: #232323; color: #f3f3f3; gridline-color: #3a3a3a; }"
            "QHeaderView::section { background: #2f2f2f; color: #f3f3f3; padding: 4px; border: 0px; }"
        )

    def update_chain(self, option_chain: pd.DataFrame, metrics: DerivedMetrics, spot: float, signal: SignalSnapshot | None = None) -> None:
        rows = _build_display_rows(option_chain)
        self.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setForeground(QColor("#f3f3f3"))
                self.setItem(row_idx, col_idx, item)

            strike = float(rows[row_idx][8]) if rows[row_idx][8] else math.nan
            if metrics.atm_strike is not None and math.isclose(strike, metrics.atm_strike):
                for col in range(self.columnCount()):
                    self.item(row_idx, col).setBackground(QColor("#5c4b1f"))
            elif abs(strike - spot) / max(spot, 1e-6) <= 0.003:
                for col in range(self.columnCount()):
                    self.item(row_idx, col).setBackground(QColor("#16384c"))

            if metrics.total_oi_center is not None and math.isclose(strike, metrics.total_oi_center):
                self.item(row_idx, 8).setBackground(QColor("#1f4d2f"))
                self.item(row_idx, 8).setForeground(QColor("#f3f3f3"))
            if metrics.pin_strike is not None and metrics.gamma_confidence_label != "LOW" and math.isclose(strike, metrics.pin_strike, rel_tol=0.0, abs_tol=max(0.5, strike * 0.003)):
                self.item(row_idx, 8).setBackground(QColor("#2f6b3a"))
                self.item(row_idx, 8).setForeground(QColor("#f3f3f3"))
            if metrics.max_pain is not None and math.isclose(strike, metrics.max_pain, rel_tol=0.0, abs_tol=max(0.5, strike * 0.003)):
                self.item(row_idx, 8).setBackground(QColor("#7a3216"))
                self.item(row_idx, 8).setForeground(QColor("#f3f3f3"))
            if signal is not None and signal.label.value == "WATCH_BREAK":
                diagnostics = signal.diagnostics
                if (
                    (metrics.pin_strike is not None and math.isclose(strike, metrics.pin_strike, rel_tol=0.0, abs_tol=max(1.0, strike * 0.005)))
                    or (metrics.max_pain is not None and math.isclose(strike, metrics.max_pain, rel_tol=0.0, abs_tol=max(1.0, strike * 0.005)))
                ):
                    for col in range(self.columnCount()):
                        self.item(row_idx, col).setBackground(QColor("#7a5b12"))

        self._highlight_column_extremes(option_chain)

        self.resizeColumnsToContents()

    def _highlight_column_extremes(self, option_chain: pd.DataFrame) -> None:
        if option_chain.empty or self.rowCount() == 0:
            return

        highlight_specs = [
            ("CALL", "volume", 6, "max", False),
            ("CALL", "open_interest", 5, "max", False),
            ("CALL", "iv", 4, "min", False),
            ("CALL", "vega", 3, "max", False),
            ("CALL", "gamma", 2, "max", False),
            ("CALL", "theta", 0, "max", True),
            ("PUT", "volume", 10, "max", False),
            ("PUT", "open_interest", 11, "max", False),
            ("PUT", "iv", 12, "min", False),
            ("PUT", "vega", 13, "max", False),
            ("PUT", "gamma", 14, "max", False),
            ("PUT", "theta", 16, "max", True),
        ]

        strike_to_row = {}
        for row_idx in range(self.rowCount()):
            item = self.item(row_idx, 8)
            if item is None:
                continue
            try:
                strike_to_row[float(item.text())] = row_idx
            except ValueError:
                continue

        for option_type, field, col_idx, mode, use_abs in highlight_specs:
            subset = option_chain[option_chain["option_type"] == option_type].copy()
            if subset.empty or field not in subset.columns:
                continue
            values = pd.to_numeric(subset[field], errors="coerce")
            subset = subset.assign(_metric_value=values.abs() if use_abs else values)
            subset = subset.dropna(subset=["strike", "_metric_value"])
            if subset.empty:
                continue
            target_value = float(subset["_metric_value"].min()) if mode == "min" else float(subset["_metric_value"].max())
            if mode == "max" and target_value <= 0:
                continue
            target_rows = subset[subset["_metric_value"] == target_value]
            for _, max_row in target_rows.iterrows():
                row_idx = strike_to_row.get(float(max_row["strike"]))
                if row_idx is None:
                    continue
                cell = self.item(row_idx, col_idx)
                if cell is None:
                    continue
                if field == "iv":
                    cell.setBackground(QColor("#a85d12"))
                    cell.setForeground(QColor("#fff0d6"))
                else:
                    cell.setBackground(QColor("#8c6d1f"))
                    cell.setForeground(QColor("#fff4c2"))


def _build_display_rows(option_chain: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    if option_chain.empty:
        return rows
    strikes = sorted(option_chain["strike"].dropna().unique())
    for strike in strikes:
        put_row = option_chain[(option_chain["strike"] == strike) & (option_chain["option_type"] == "PUT")]
        call_row = option_chain[(option_chain["strike"] == strike) & (option_chain["option_type"] == "CALL")]
        put = put_row.iloc[0] if not put_row.empty else None
        call = call_row.iloc[0] if not call_row.empty else None
        rows.append(
            [
                _f(call, "theta"),
                _f(call, "delta"),
                _f(call, "gamma"),
                _f(call, "vega"),
                _f(call, "iv", pct=True),
                _f(call, "open_interest", integer=True),
                _f(call, "volume", integer=True),
                _f(call, "last"),
                f"{strike:.2f}",
                _f(put, "last"),
                _f(put, "volume", integer=True),
                _f(put, "open_interest", integer=True),
                _f(put, "iv", pct=True),
                _f(put, "vega"),
                _f(put, "gamma"),
                _f(put, "delta"),
                _f(put, "theta"),
            ]
        )
    return rows


def _f(row: pd.Series | None, field: str, pct: bool = False, integer: bool = False) -> str:
    if row is None:
        return "-"
    value = row.get(field)
    if pd.isna(value):
        return "-"
    if integer:
        return f"{int(value):,}"
    if pct:
        return f"{float(value):.2%}"
    return f"{float(value):.4f}"
