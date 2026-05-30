from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from core.models import MarketSnapshot


class ChartsWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        self.price_plot = pg.PlotWidget(title="标的分时 / K线近似")
        self.aux_plot = pg.PlotWidget(title="四个月份期货基差变化（相对开盘）")
        self.gamma_drift_plot = pg.PlotWidget(title="Gamma Barycenter / Pin Strike Drift")
        self.iv_trend_plot = pg.PlotWidget(title="ATM Nearby 3-Strike Mean IV")
        layout.addWidget(self.price_plot, 0, 0)
        layout.addWidget(self.aux_plot, 0, 1)
        layout.addWidget(self.gamma_drift_plot, 1, 0)
        layout.addWidget(self.iv_trend_plot, 1, 1)

        self._metric_history: dict[str, deque[dict[str, float | str | pd.Timestamp]]] = {}
        self._basis_history: dict[str, deque[dict[str, float | str | pd.Timestamp]]] = {}

    def update_snapshot(self, snapshot: MarketSnapshot) -> None:
        self._remember_metrics(snapshot)
        self._remember_basis_curve(snapshot)
        self._update_price(snapshot)
        self._update_aux(snapshot)
        self._update_gamma_drift(snapshot)
        self._update_iv_trend(snapshot)

    def _history_key(self, snapshot: MarketSnapshot) -> str:
        return f"{snapshot.quote.code}|{snapshot.expiry_date or 'NA'}"

    def _remember_metrics(self, snapshot: MarketSnapshot) -> None:
        key = self._history_key(snapshot)
        history = self._metric_history.get(key)
        if history is None:
            history = deque(maxlen=240)
            self._metric_history[key] = history
        history.append(
            {
                "time": pd.Timestamp(snapshot.generated_at),
                "spot": snapshot.quote.last,
                "gamma_barycenter": snapshot.metrics.gamma_barycenter if snapshot.metrics.gamma_barycenter is not None else np.nan,
                "pin_strike": snapshot.metrics.pin_strike if snapshot.metrics.pin_strike is not None else np.nan,
                "gamma_confidence_score": snapshot.metrics.gamma_confidence_score,
                "gamma_confidence_label": snapshot.metrics.gamma_confidence_label,
                "atm_call_iv_mean_3": snapshot.metrics.atm_call_iv_mean_3 if snapshot.metrics.atm_call_iv_mean_3 is not None else np.nan,
                "atm_put_iv_mean_3": snapshot.metrics.atm_put_iv_mean_3 if snapshot.metrics.atm_put_iv_mean_3 is not None else np.nan,
            }
        )

    def _remember_basis_curve(self, snapshot: MarketSnapshot) -> None:
        if snapshot.basis_curve.empty:
            return
        key = snapshot.quote.code
        history = self._basis_history.get(key)
        if history is None:
            history = deque(maxlen=240)
            self._basis_history[key] = history
        row: dict[str, float | str | pd.Timestamp] = {"time": pd.Timestamp(snapshot.generated_at)}
        for _, item in snapshot.basis_curve.iterrows():
            bucket = str(item.get("bucket", "")).strip()
            if not bucket:
                continue
            try:
                row[f"basis_{bucket}"] = float(item.get("basis_change"))
            except (TypeError, ValueError):
                row[f"basis_{bucket}"] = np.nan
        history.append(row)

    def _update_price(self, snapshot: MarketSnapshot) -> None:
        self.price_plot.clear()
        self._set_plot_background(self.price_plot, snapshot)
        intraday = snapshot.intraday
        if not intraday.empty:
            closes = pd.to_numeric(intraday["close"], errors="coerce")
            valid = closes.notna()
            if valid.sum() > 0:
                x = np.arange(valid.sum())
                self.price_plot.plot(x, closes[valid].to_numpy(), pen=pg.mkPen("#2c7fb8", width=2))

        bary_pen = pg.mkPen("#d6a63a" if snapshot.metrics.gamma_confidence_label != "LOW" else "#777777", width=1.5, style=Qt.PenStyle.DashLine)
        pin_pen = pg.mkPen("#6cbf5f" if snapshot.metrics.gamma_confidence_label != "LOW" else "#666666", width=1.5, style=Qt.PenStyle.DashLine)
        pain_pen = pg.mkPen("#d95f02", width=1.5, style=Qt.PenStyle.DotLine)

        if snapshot.metrics.gamma_barycenter is not None:
            self.price_plot.addItem(pg.InfiniteLine(pos=float(snapshot.metrics.gamma_barycenter), angle=0, pen=bary_pen))
        if snapshot.metrics.pin_strike is not None:
            self.price_plot.addItem(pg.InfiniteLine(pos=float(snapshot.metrics.pin_strike), angle=0, pen=pin_pen))
        if snapshot.metrics.max_pain is not None:
            self.price_plot.addItem(pg.InfiniteLine(pos=float(snapshot.metrics.max_pain), angle=0, pen=pain_pen))
        self.price_plot.setTitle("标的分时 / K线近似  黄=Gamma Barycenter 绿=Pin Strike 橙=Max Pain")

    def _update_aux(self, snapshot: MarketSnapshot) -> None:
        self.aux_plot.clear()
        self._set_plot_background(self.aux_plot, snapshot)
        if not snapshot.basis_curve.empty:
            self._update_basis_curve_history(snapshot)
            return
        self._update_oi_distribution(snapshot)

    def _update_basis_curve_history(self, snapshot: MarketSnapshot) -> None:
        history = pd.DataFrame(list(self._basis_history.get(snapshot.quote.code, [])))
        if history.empty:
            return
        line_specs = [
            ("basis_Current", "#4ea1ff", "Current"),
            ("basis_Next", "#f59e0b", "Next"),
            ("basis_Quarter", "#10b981", "Quarter"),
            ("basis_Next Quarter", "#ef4444", "Next Quarter"),
        ]
        for column, color, _label in line_specs:
            if column not in history.columns:
                continue
            values = pd.to_numeric(history[column], errors="coerce")
            valid = values.notna()
            if valid.sum() == 0:
                continue
            x = np.arange(valid.sum())
            self.aux_plot.plot(x, values[valid].to_numpy(), pen=pg.mkPen(color, width=2))
        self.aux_plot.addItem(pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen("#888888", width=1)))
        self.aux_plot.setTitle("四个月份期货基差变化（日内，相对开盘）")

    def _update_oi_distribution(self, snapshot: MarketSnapshot) -> None:
        option_chain = snapshot.option_chain
        if option_chain.empty:
            return
        grouped = option_chain.groupby(["strike", "option_type"])["open_interest"].sum().unstack(fill_value=0)
        if grouped.empty:
            return
        strikes = grouped.index.to_numpy(dtype=float)
        width = max((strikes[1] - strikes[0]) * 0.35, 1.0) if len(strikes) > 1 else 1.0
        put_values = grouped.get("PUT", pd.Series(0, index=grouped.index)).to_numpy(dtype=float)
        call_values = grouped.get("CALL", pd.Series(0, index=grouped.index)).to_numpy(dtype=float)
        self.aux_plot.addItem(pg.BarGraphItem(x=strikes - width * 0.25, height=call_values, width=width * 0.45, brush="#1b9e77"))
        self.aux_plot.addItem(pg.BarGraphItem(x=strikes + width * 0.25, height=put_values, width=width * 0.45, brush="#d95f02"))
        overlays = [
            (snapshot.metrics.atm_strike, "#a7a7a7"),
            (snapshot.metrics.gamma_barycenter, "#d6a63a" if snapshot.metrics.gamma_confidence_label != "LOW" else "#777777"),
            (snapshot.metrics.pin_strike, "#6cbf5f" if snapshot.metrics.gamma_confidence_label != "LOW" else "#666666"),
            (snapshot.metrics.max_pain, "#d95f02"),
        ]
        for level, color in overlays:
            if level is not None:
                self.aux_plot.addItem(pg.InfiniteLine(pos=float(level), angle=90, pen=pg.mkPen(color, width=1)))
        self.aux_plot.setTitle("OI 分布")

    def _update_gamma_drift(self, snapshot: MarketSnapshot) -> None:
        self.gamma_drift_plot.clear()
        self._set_plot_background(self.gamma_drift_plot, snapshot)
        history = pd.DataFrame(list(self._metric_history.get(self._history_key(snapshot), [])))
        if history.empty:
            return
        history["gamma_barycenter"] = pd.to_numeric(history["gamma_barycenter"], errors="coerce")
        history["pin_strike"] = pd.to_numeric(history["pin_strike"], errors="coerce")
        history["spot"] = pd.to_numeric(history["spot"], errors="coerce")
        history["gamma_confidence_score"] = pd.to_numeric(history["gamma_confidence_score"], errors="coerce")
        history["barycenter_drift"] = history["gamma_barycenter"] - history["spot"]
        history["pin_drift"] = history["pin_strike"] - history["spot"]

        valid_high = history["gamma_confidence_score"].fillna(0) >= 0.40
        valid_low = history["gamma_confidence_score"].fillna(0) < 0.40
        if valid_high.any():
            high = history.loc[valid_high]
            self._plot_valid_series(self.gamma_drift_plot, high["barycenter_drift"], pg.mkPen("#d6a63a", width=2))
            self._plot_valid_series(self.gamma_drift_plot, high["pin_drift"], pg.mkPen("#6cbf5f", width=2))
        if valid_low.any():
            low = history.loc[valid_low]
            self._plot_valid_markers(self.gamma_drift_plot, low["barycenter_drift"], "o", 5, "#777777")
            self._plot_valid_markers(self.gamma_drift_plot, low["pin_drift"], "t", 6, "#666666")
        self.gamma_drift_plot.addItem(pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen("#888888", width=1)))

    def _update_iv_trend(self, snapshot: MarketSnapshot) -> None:
        self.iv_trend_plot.clear()
        self._set_plot_background(self.iv_trend_plot, snapshot)
        history = pd.DataFrame(list(self._metric_history.get(self._history_key(snapshot), [])))
        if history.empty:
            return
        call_iv_mean = pd.to_numeric(history["atm_call_iv_mean_3"], errors="coerce")
        put_iv_mean = pd.to_numeric(history["atm_put_iv_mean_3"], errors="coerce")
        if call_iv_mean.notna().sum() > 0:
            valid = call_iv_mean.notna()
            x = np.arange(valid.sum())
            self.iv_trend_plot.plot(x, (call_iv_mean[valid] * 100).to_numpy(), pen=pg.mkPen("#1b9e77", width=2))
        if put_iv_mean.notna().sum() > 0:
            valid = put_iv_mean.notna()
            x = np.arange(valid.sum())
            self.iv_trend_plot.plot(x, (put_iv_mean[valid] * 100).to_numpy(), pen=pg.mkPen("#d95f02", width=2))

    def _set_plot_background(self, plot: pg.PlotWidget, snapshot: MarketSnapshot) -> None:
        brush = "#000000"
        if snapshot.signal.label.value == "WATCH_BREAK":
            brush = "#241a06"
        elif snapshot.signal.label.value == "BREAK_UP":
            brush = "#152412"
        elif snapshot.signal.label.value == "BREAK_DOWN":
            brush = "#2a1212"
        plot.setBackground(brush)

    @staticmethod
    def _plot_valid_series(plot: pg.PlotWidget, series: pd.Series, pen) -> None:
        numeric = pd.to_numeric(series, errors="coerce")
        valid = numeric.notna()
        if valid.sum() == 0:
            return
        x = np.arange(valid.sum())
        plot.plot(x, numeric[valid].to_numpy(), pen=pen)

    @staticmethod
    def _plot_valid_markers(plot: pg.PlotWidget, series: pd.Series, symbol: str, size: int, brush: str) -> None:
        numeric = pd.to_numeric(series, errors="coerce")
        valid = numeric.notna()
        if valid.sum() == 0:
            return
        x = np.arange(valid.sum())
        plot.plot(
            x,
            numeric[valid].to_numpy(),
            pen=None,
            symbol=symbol,
            symbolSize=size,
            symbolBrush=brush,
        )
