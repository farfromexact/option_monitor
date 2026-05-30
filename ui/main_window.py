from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.models import MarketSnapshot
from core.signal_engine import SignalEngine
from core.expiry_rules import RuleEngine
from data.option_loader import OptionDataLoader
from data.trading_calendar import TradingCalendar
from data.quote_updater import QuoteUpdater
from data.wind_client import WindClient, WindClientConfig
from ui.charts_widget import ChartsWidget
from ui.option_chain_widget import OptionChainWidget
from ui.summary_panel import SummaryPanel
from utils.helpers import ensure_directory, load_yaml

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.settings = load_yaml(config_path)
        self.products: dict[str, dict[str, Any]] = self.settings["products"]
        self.current_product_id = self.settings["market"]["default_product_id"]
        self.setWindowTitle(self.settings["app"]["title"])
        self.resize(1600, 960)

        wind_cfg = self.settings["wind"]
        self.client = WindClient(
            WindClientConfig(
                product_id=self.current_product_id,
                product=self.products[self.current_product_id],
                expiry_month_offset=self.settings["market"]["expiry_month_offset"],
                retry_count=wind_cfg["retry_count"],
                retry_delay_seconds=wind_cfg["retry_delay_seconds"],
                field_map={
                    "realtime_fields": wind_cfg["realtime_fields"],
                    "option_chain_fields": wind_cfg["option_chain_fields"],
                    "option_chain_date_format": wind_cfg["option_chain_date_format"],
                },
                use_mock_on_failure=self.settings["app"]["use_mock_on_wind_failure"],
            )
        )
        self.client.connect()
        calendar = TradingCalendar(fetcher=self.client.get_trading_days)
        rule_engine = RuleEngine(calendar, config_path.parent / "product_rules.yaml")
        self.loader = OptionDataLoader(self.client, rule_engine)
        self.signal_engine = SignalEngine(self.settings)
        self.updater = QuoteUpdater(self.client, self.loader, self.signal_engine, self.settings)
        self.updater.snapshot_ready.connect(self.on_snapshot)
        self.updater.error_occurred.connect(self.on_error)

        self.summary_panel = SummaryPanel()
        self.option_chain = OptionChainWidget()
        self.charts = ChartsWidget()
        self.status_label = QLabel("Ready")
        self.latest_snapshot: MarketSnapshot | None = None

        self._build_ui()
        self.summary_panel.set_wind_status(self.client.status_text())
        self.updater.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.updater.stop()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        container = QWidget()
        root = QVBoxLayout(container)
        root.addLayout(self._build_toolbar())
        root.addWidget(self.summary_panel)

        splitter = QSplitter(Qt.Orientation.Vertical)
        upper = QSplitter(Qt.Orientation.Horizontal)
        upper.addWidget(self.option_chain)
        upper.addWidget(self.charts)
        upper.setSizes([820, 780])
        splitter.addWidget(upper)
        root.addWidget(splitter)
        self.setCentralWidget(container)

        status = QStatusBar()
        status.addWidget(self.status_label)
        self.setStatusBar(status)

        export_action = QAction("Export Chain CSV", self)
        export_action.triggered.connect(self.export_chain_csv)
        self.menuBar().addAction(export_action)

    def _build_toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        self.product_combo = QComboBox()
        for product_id, product in self.products.items():
            self.product_combo.addItem(f"{product_id} | {product['display_name']}", product_id)
        self.product_combo.setCurrentText(f"{self.current_product_id} | {self.products[self.current_product_id]['display_name']}")
        self.product_combo.currentIndexChanged.connect(self._on_product_changed)

        self.expiry_combo = QComboBox()
        self.expiry_combo.addItems(["Front Month", "Next Month", "Third Month"])
        self.expiry_combo.setCurrentIndex(self.settings["market"]["expiry_month_offset"])
        self.expiry_combo.currentIndexChanged.connect(self._on_expiry_changed)

        self.granularity_combo = QComboBox()
        self.granularity_combo.addItems(["1m", "5m"])
        self.granularity_combo.setCurrentText(self.settings["charts"]["time_granularity"])
        self.granularity_combo.currentTextChanged.connect(self._on_granularity_changed)

        self.auto_refresh = QCheckBox("Auto Refresh")
        self.auto_refresh.setChecked(True)
        self.auto_refresh.toggled.connect(self._toggle_auto_refresh)

        self.strike_window = QSpinBox()
        self.strike_window.setRange(3, 20)
        self.strike_window.setValue(self.settings["market"]["display_strike_window"])
        self.strike_window.valueChanged.connect(self._apply_snapshot)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.updater.trigger_refresh)

        export_button = QPushButton("Export CSV")
        export_button.clicked.connect(self.export_chain_csv)

        layout.addWidget(QLabel("Product"))
        layout.addWidget(self.product_combo)
        layout.addWidget(QLabel("Expiry"))
        layout.addWidget(self.expiry_combo)
        layout.addWidget(QLabel("Granularity"))
        layout.addWidget(self.granularity_combo)
        layout.addWidget(QLabel("ATM Window"))
        layout.addWidget(self.strike_window)
        layout.addWidget(self.auto_refresh)
        layout.addWidget(refresh_button)
        layout.addWidget(export_button)
        layout.addStretch(1)
        return layout

    def on_snapshot(self, snapshot: MarketSnapshot) -> None:
        self.latest_snapshot = snapshot
        self.summary_panel.set_wind_status(self.client.status_text())
        self.status_label.setText(snapshot.signal.reason)
        self._apply_snapshot()

    def _apply_snapshot(self) -> None:
        if self.latest_snapshot is None:
            return
        snapshot = self.latest_snapshot
        self.summary_panel.update_snapshot(snapshot)
        filtered_chain = self._filter_chain(snapshot)
        shadow_snapshot = MarketSnapshot(
            quote=snapshot.quote,
            option_chain=filtered_chain,
            intraday=snapshot.intraday,
            basis_curve=snapshot.basis_curve.copy(),
            metrics=snapshot.metrics,
            signal=snapshot.signal,
            option_last_trading_day=snapshot.option_last_trading_day,
            option_expiry_date=snapshot.option_expiry_date,
            futures_last_trading_day=snapshot.futures_last_trading_day,
            remaining_trading_days=snapshot.remaining_trading_days,
            remaining_calendar_days=snapshot.remaining_calendar_days,
            contract_date_source=dict(snapshot.contract_date_source),
            front_month_validity=snapshot.front_month_validity,
            generated_at=snapshot.generated_at,
        )
        self.option_chain.update_chain(filtered_chain, snapshot.metrics, snapshot.quote.last, snapshot.signal)
        self.charts.update_snapshot(shadow_snapshot)

    def _filter_chain(self, snapshot: MarketSnapshot):
        chain = snapshot.option_chain
        if chain.empty:
            return chain
        if not self.settings["market"]["display_near_atm_only"] or snapshot.metrics.atm_strike is None:
            return chain
        strikes = sorted(chain["strike"].dropna().unique())
        if snapshot.metrics.atm_strike not in strikes:
            return chain
        idx = strikes.index(snapshot.metrics.atm_strike)
        window = self.strike_window.value()
        selected = strikes[max(0, idx - window): idx + window + 1]
        return chain[chain["strike"].isin(selected)].copy()

    def _on_product_changed(self, index: int) -> None:
        product_id = self.product_combo.itemData(index)
        if not product_id or product_id == self.current_product_id:
            return
        self.current_product_id = str(product_id)
        self.settings["market"]["default_product_id"] = self.current_product_id
        self.client.update_market(
            product_id=self.current_product_id,
            product=self.products[self.current_product_id],
            expiry_month_offset=self.settings["market"]["expiry_month_offset"],
        )
        self.updater.trigger_refresh()

    def _on_expiry_changed(self, index: int) -> None:
        self.settings["market"]["expiry_month_offset"] = index
        self.client.update_market(
            product_id=self.current_product_id,
            product=self.products[self.current_product_id],
            expiry_month_offset=index,
        )
        self.updater.trigger_refresh()

    def _on_granularity_changed(self, text: str) -> None:
        self.settings["charts"]["time_granularity"] = text
        self.updater.trigger_refresh()

    def _toggle_auto_refresh(self, checked: bool) -> None:
        if checked:
            self.updater.start()
        else:
            self.updater.timer.stop()

    def export_chain_csv(self) -> None:
        if self.latest_snapshot is None or self.latest_snapshot.option_chain.empty:
            QMessageBox.warning(self, "No Data", "Current product has no option chain data to export.")
            return
        export_dir = ensure_directory(self.settings["app"]["export_dir"])
        default_name = f"{self.current_product_id.lower()}_option_chain.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(export_dir / default_name), "CSV Files (*.csv)")
        if not path:
            return
        self.latest_snapshot.option_chain.to_csv(path, index=False, encoding="utf-8-sig")
        self.status_label.setText(f"Exported to {path}")

    def on_error(self, message: str) -> None:
        logger.error("UI received error: %s", message)
        self.summary_panel.set_wind_status(self.client.status_text())
        self.status_label.setText(message)
