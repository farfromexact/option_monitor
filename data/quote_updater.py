from __future__ import annotations

import logging
from collections import deque

import pandas as pd

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from core.calculations import build_metrics
from core.models import DerivedMetrics, MarketSnapshot, SignalSnapshot, UnderlyingQuote
from core.signal_engine import SignalEngine
from data.option_loader import OptionDataLoader
from data.wind_client import WindClient

logger = logging.getLogger(__name__)


class QuoteWorker(QObject):
    snapshot_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(
        self,
        client: WindClient,
        loader: OptionDataLoader,
        signal_engine: SignalEngine,
        settings: dict,
    ) -> None:
        super().__init__()
        self.client = client
        self.loader = loader
        self.signal_engine = signal_engine
        self.settings = settings
        self._busy = False
        self._quote_history: dict[str, deque[dict[str, float | str]]] = {}

    def refresh(self) -> None:
        if self._busy:
            logger.info("Skip refresh because previous cycle is still running")
            return
        self._busy = True
        try:
            if not self.client.is_mock_mode and self.client.status_text() != "CONNECTED":
                self.client.connect()
            quote = UnderlyingQuote(**self.client.get_underlying_quote())
            market_cfg = self.settings["market"]
            chart_cfg = self.settings["charts"]
            try:
                load_result = self.loader.load_option_chain(market_cfg["expiry_month_offset"])
                chain = load_result.chain
            except Exception as exc:
                logger.warning("Option chain refresh failed, continue with quote-only snapshot: %s", exc)
                load_result = self.loader.load_empty_option_chain()
                chain = load_result.chain
            try:
                intraday = self.loader.load_intraday(chart_cfg["time_granularity"], chart_cfg["lookback_points"])
            except Exception as exc:
                logger.warning("Intraday refresh failed, continue with empty intraday frame: %s", exc)
                intraday = self._build_intraday_fallback(quote, chart_cfg["lookback_points"])
            else:
                self._remember_quote(quote)
                intraday = self._merge_intraday_with_quote_history(intraday, quote, chart_cfg["lookback_points"])
            try:
                basis_curve = self.client.get_index_future_basis_curve()
            except Exception as exc:
                logger.warning("Basis curve refresh failed, continue with empty basis frame: %s", exc)
                basis_curve = pd.DataFrame()
            metrics: DerivedMetrics = build_metrics(chain, quote.last)
            signal: SignalSnapshot = self.signal_engine.evaluate(
                option_chain=chain,
                quote=quote,
                intraday=intraday,
                metrics=metrics,
                remaining_days=load_result.remaining_trading_days,
            )
            self.snapshot_ready.emit(
                MarketSnapshot(
                    quote=quote,
                    option_chain=chain,
                    intraday=intraday,
                    basis_curve=basis_curve,
                    metrics=metrics,
                    signal=signal,
                    option_last_trading_day=load_result.option_last_trading_day,
                    option_expiry_date=load_result.option_expiry_date,
                    futures_last_trading_day=load_result.futures_last_trading_day,
                    remaining_trading_days=load_result.remaining_trading_days,
                    remaining_calendar_days=load_result.remaining_calendar_days,
                    contract_date_source=(load_result.selected_contract_info.source_priority_result if load_result.selected_contract_info else {}),
                    front_month_validity=(
                        "VALID"
                        if load_result.selected_contract_info is not None and load_result.selected_contract_info.is_front_valid
                        else "EXPIRED CONTRACT SELECTED"
                        if load_result.selected_contract_info is not None and load_result.selected_contract_info.is_expired
                        else None
                    ),
                )
            )
        except Exception as exc:
            logger.exception("Refresh failed")
            self.error_occurred.emit(str(exc))
        finally:
            self._busy = False

    def _history_for_code(self, code: str, maxlen: int) -> deque[dict[str, float | str]]:
        history = self._quote_history.get(code)
        if history is None or history.maxlen != maxlen:
            history = deque(maxlen=maxlen)
            self._quote_history[code] = history
        return history

    def _remember_quote(self, quote: UnderlyingQuote, maxlen: int = 240) -> None:
        history = self._history_for_code(quote.code, maxlen)
        history.append(
            {
                "datetime": quote.timestamp.isoformat(sep=" ", timespec="seconds"),
                "open": quote.last,
                "high": quote.last,
                "low": quote.last,
                "close": quote.last,
                "volume": quote.volume,
            }
        )

    def _build_intraday_fallback(self, quote: UnderlyingQuote, lookback_points: int) -> pd.DataFrame:
        self._remember_quote(quote, max(lookback_points, 60))
        history = self._history_for_code(quote.code, max(lookback_points, 60))
        if not history:
            return self.loader.load_empty_intraday()
        df = pd.DataFrame(list(history))
        return df.tail(lookback_points).reset_index(drop=True)

    def _merge_intraday_with_quote_history(self, intraday: pd.DataFrame, quote: UnderlyingQuote, lookback_points: int) -> pd.DataFrame:
        self._remember_quote(quote, max(lookback_points, 240))
        history = pd.DataFrame(list(self._history_for_code(quote.code, max(lookback_points, 240))))
        if intraday.empty:
            return history.tail(lookback_points).reset_index(drop=True)
        combined = pd.concat([intraday, history], ignore_index=True)
        combined["datetime"] = pd.to_datetime(combined["datetime"], errors="coerce")
        combined = combined.dropna(subset=["datetime"]).sort_values("datetime")
        combined = combined.drop_duplicates(subset=["datetime"], keep="last")
        return combined.tail(lookback_points).reset_index(drop=True)


class QuoteUpdater(QObject):
    snapshot_ready = Signal(object)
    error_occurred = Signal(str)
    refresh_requested = Signal()

    def __init__(
        self,
        client: WindClient,
        loader: OptionDataLoader,
        signal_engine: SignalEngine,
        settings: dict,
    ) -> None:
        super().__init__()
        self.thread = QThread()
        self.worker = QuoteWorker(client, loader, signal_engine, settings)
        self.worker.moveToThread(self.thread)
        self.worker.snapshot_ready.connect(self.snapshot_ready)
        self.worker.error_occurred.connect(self.error_occurred)
        self.refresh_requested.connect(self.worker.refresh)
        self.thread.start()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.worker.refresh)
        self.timer.setInterval(int(settings["refresh"]["quote_seconds"] * 1000))

    def start(self) -> None:
        self.timer.start()
        self.refresh_requested.emit()

    def stop(self) -> None:
        self.timer.stop()
        self.thread.quit()
        self.thread.wait(3000)

    def trigger_refresh(self) -> None:
        self.refresh_requested.emit()
