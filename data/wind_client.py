from __future__ import annotations

import logging
import random
import re
import time
from calendar import monthcalendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.helpers import safe_float

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WindClientConfig:
    product_id: str
    product: dict[str, Any]
    expiry_month_offset: int
    retry_count: int
    retry_delay_seconds: float
    field_map: dict[str, Any]
    use_mock_on_failure: bool = True


class WindClient:
    def __init__(self, config: WindClientConfig) -> None:
        self.config = config
        self._w = None
        self._connected = False
        self._mock_mode = False
        self._option_code_cache: dict[tuple[str, str], list[str]] = {}
        self._option_chain_cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._intraday_failed_codes: set[str] = set()
        self._calendar_cache: dict[tuple[str, str, str], list[date]] = {}

    @property
    def is_mock_mode(self) -> bool:
        return self._mock_mode

    @property
    def product(self) -> dict[str, Any]:
        return self.config.product

    @property
    def quote_code(self) -> str:
        return str(self.product["quote_code"])

    @property
    def quote_name(self) -> str:
        return str(self.product["underlying_name"])

    def update_market(self, product_id: str, product: dict[str, Any], expiry_month_offset: int) -> None:
        product_changed = self.config.product_id != product_id
        self.config.product_id = product_id
        self.config.product = product
        self.config.expiry_month_offset = expiry_month_offset
        if product_changed:
            self._intraday_failed_codes.discard(self.quote_code)
        logger.info("Switched market to product=%s quote=%s", product_id, self.quote_code)

    def connect(self) -> bool:
        try:
            w = self._import_windpy()
            self._w = w
            self._w.start()
            self._connected = bool(getattr(self._w, "isconnected", lambda: True)())
            logger.info("Wind connected=%s", self._connected)
        except Exception as exc:
            logger.warning("Wind unavailable, reason=%s", exc)
            self._connected = False
            self._mock_mode = self.config.use_mock_on_failure
        return self._connected or self._mock_mode

    def _import_windpy(self):
        try:
            from WindPy import w  # type: ignore

            return w
        except Exception as first_exc:
            logger.warning("Direct WindPy import failed, trying local compatibility shim: %s", first_exc)

        import sys

        candidate_root = Path.home() / "Wind" / "Wind.NET.Client" / "WindNET" / "x64"
        shim_site_packages = Path.cwd() / ".windpy_shim" / "site-packages"
        shim_site_packages.mkdir(parents=True, exist_ok=True)
        pth_file = shim_site_packages / "WindPy.pth"
        pth_file.write_text(str(candidate_root), encoding="utf-8")
        sys.path.insert(0, str(shim_site_packages))
        sys.path.insert(0, str(candidate_root))

        from WindPy import w  # type: ignore

        return w

    def status_text(self) -> str:
        if self._connected:
            return "CONNECTED"
        if self._mock_mode:
            return "MOCK"
        return "DISCONNECTED"

    def get_underlying_quote(self) -> dict[str, Any]:
        if self._mock_mode:
            return self._mock_quote()
        if not self._connected:
            raise RuntimeError("Wind is not connected. Please start and log in to Wind Terminal.")

        fields = list(self.config.field_map["realtime_fields"].values())
        raw = self._call_with_retry("wsq", self.quote_code, ",".join(fields))
        return self._normalize_quote(raw)

    def get_option_chain(self, expiry_month_offset: int | None = None) -> pd.DataFrame:
        if not self.product.get("option_chain_enabled", False):
            return pd.DataFrame()
        if self._mock_mode:
            return self._mock_option_chain(expiry_month_offset)
        if not self._connected:
            raise RuntimeError("Wind is not connected. Please start and log in to Wind Terminal.")

        style = str(self.product.get("option_code_style", "cfe_hyphen"))
        # Keep the generated option universe date-scoped so stale Wind logs do
        # not keep yesterday's/month-old expired contracts alive in a long run.
        cache_key = (self.config.product_id, date.today().isoformat())
        option_codes = self._option_code_cache.get(cache_key, [])
        if not option_codes:
            discovered_codes = self._discover_option_codes()
            discovered_months = self._expand_option_month_codes(self._extract_option_month_codes(discovered_codes))
            if discovered_months:
                logger.info("Expanded option months product=%s base=%s expanded=%s", self.config.product_id, discovered_months[0], discovered_months)
            should_guess = bool(discovered_months) or bool(self.product.get("option_allow_blind_guess", True))
            generated_codes = (
                self._build_option_candidate_codes(discovered_months if discovered_months else None)
                if self.product.get("option_guess_codes", True) and should_guess
                else []
            )
            option_codes = sorted(set(discovered_codes + generated_codes))
            self._option_code_cache[cache_key] = option_codes
        if not option_codes:
            return pd.DataFrame()
        chain_cache = self._option_chain_cache.get(self.config.product_id)
        if chain_cache is not None and (time.time() - chain_cache[0]) <= 2.5:
            cached_df = chain_cache[1].copy()
            return cached_df.reset_index(drop=True)
        market_fields = [
            "rt_last",
            "rt_pre_close",
            "rt_opt_vol",
            "rt_vol",
            "rt_bid1",
            "rt_ask1",
            "rt_opt_oi",
            "rt_oi",
            "rt_pre_oi",
            "rt_imp_volatility",
            "rt_delta",
            "rt_gamma",
            "rt_vega",
            "rt_theta",
        ]
        market_df = self._fetch_option_quote_block(option_codes, market_fields)
        if market_df.empty:
            return pd.DataFrame()
        market_df = market_df.rename(columns={str(col).upper(): str(col).upper() for col in market_df.columns})
        market_df["contract_code"] = market_df.index.astype(str)
        market_df = market_df.rename(
            columns={
                "RT_LAST": "last",
                "RT_PRE_CLOSE": "pre_close",
                "RT_OPT_VOL": "volume",
                "RT_VOL": "volume_fallback",
                "RT_BID1": "bid1",
                "RT_ASK1": "ask1",
                "RT_OPT_OI": "open_interest",
                "RT_OI": "open_interest_fallback",
                "RT_PRE_OI": "open_interest_prior",
                "RT_IMP_VOLATILITY": "iv",
                "RT_DELTA": "delta",
                "RT_GAMMA": "gamma",
                "RT_VEGA": "vega",
                "RT_THETA": "theta",
            }
        )
        market_df["contract_name"] = market_df["contract_code"]
        market_df["option_type"] = market_df["contract_code"].map(self._parse_option_type)
        market_df["strike"] = market_df["contract_code"].map(self._parse_option_strike)
        market_df["expiry_month"] = market_df["contract_code"].map(self._parse_option_expiry_month)
        market_df["expiry_date"] = market_df["contract_code"].map(self._parse_option_expiry)
        market_df["multiplier"] = int(self.product.get("multiplier", 1))
        market_df["last"] = pd.to_numeric(market_df.get("last"), errors="coerce")
        market_df["pre_close"] = pd.to_numeric(market_df.get("pre_close"), errors="coerce")
        market_df["volume"] = pd.to_numeric(market_df.get("volume"), errors="coerce")
        market_df["open_interest"] = pd.to_numeric(market_df.get("open_interest"), errors="coerce")
        market_df["iv"] = pd.to_numeric(market_df.get("iv"), errors="coerce")
        market_df["delta"] = pd.to_numeric(market_df.get("delta"), errors="coerce")
        market_df["gamma"] = pd.to_numeric(market_df.get("gamma"), errors="coerce")
        market_df["vega"] = pd.to_numeric(market_df.get("vega"), errors="coerce")
        market_df["theta"] = pd.to_numeric(market_df.get("theta"), errors="coerce")
        if "volume_fallback" in market_df.columns:
            fallback_volume = pd.to_numeric(market_df["volume_fallback"], errors="coerce")
            missing_or_zero = market_df["volume"].isna() | (market_df["volume"] <= 0)
            market_df.loc[missing_or_zero, "volume"] = fallback_volume.loc[missing_or_zero]
        if "open_interest_fallback" in market_df.columns:
            market_df["open_interest"] = market_df["open_interest"].combine_first(pd.to_numeric(market_df["open_interest_fallback"], errors="coerce"))
        if "open_interest_prior" in market_df.columns:
            prior_oi = pd.to_numeric(market_df["open_interest_prior"], errors="coerce")
            missing_or_zero = market_df["open_interest"].isna() | (market_df["open_interest"] <= 0)
            market_df.loc[missing_or_zero, "open_interest"] = prior_oi.loc[missing_or_zero]
        if "last" not in market_df.columns:
            market_df["last"] = np.nan
        if "pre_close" not in market_df.columns:
            market_df["pre_close"] = np.nan
        if "volume" not in market_df.columns:
            market_df["volume"] = np.nan
        if "open_interest" not in market_df.columns:
            market_df["open_interest"] = np.nan
        for greek_field in ["iv", "delta", "gamma", "vega", "theta"]:
            if greek_field not in market_df.columns:
                market_df[greek_field] = np.nan
        market_df["change"] = market_df["last"] - market_df["pre_close"]
        market_df["pct_change"] = np.where(
            pd.to_numeric(market_df["pre_close"], errors="coerce").fillna(0) != 0,
            market_df["change"] / market_df["pre_close"] * 100,
            np.nan,
        )
        activity_columns = ["last", "pre_close", "bid1", "ask1", "volume", "open_interest", "iv", "delta", "gamma", "vega", "theta"]
        for column in activity_columns:
            if column not in market_df.columns:
                market_df[column] = np.nan
        activity_frame = market_df[activity_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        market_df = market_df.loc[(activity_frame.abs().sum(axis=1) > 0)].copy()
        market_df = market_df.dropna(subset=["strike"])
        market_df = market_df[market_df["option_type"].isin(["CALL", "PUT"])]
        self._option_chain_cache[self.config.product_id] = (time.time(), market_df.copy())
        if market_df.empty:
            logger.warning("No live option rows detected for product=%s; candidate code pattern may not match Wind symbols", self.config.product_id)
        return market_df.reset_index(drop=True)

    def get_intraday_bars(self, interval: str, lookback_points: int) -> pd.DataFrame:
        if self._mock_mode:
            return self._mock_intraday(interval, lookback_points)
        if not self._connected:
            raise RuntimeError("Wind is not connected. Please start and log in to Wind Terminal.")
        if self.quote_code in self._intraday_failed_codes:
            raise RuntimeError(f"Intraday temporarily disabled for {self.quote_code}")

        end_time = datetime.now()
        minutes = {
            "1m": max(lookback_points, 30),
            "5m": max(lookback_points * 5, 60),
            "tick": max(lookback_points, 30),
        }.get(interval, max(lookback_points, 30))
        begin_time = end_time - timedelta(minutes=minutes)
        bar_size = {"1m": "BarSize=1", "5m": "BarSize=5", "tick": "BarSize=1"}.get(interval, "BarSize=1")
        try:
            raw = self._call_with_retry(
                "wsi",
                self.quote_code,
                "open,high,low,close,volume",
                begin_time,
                end_time,
                bar_size,
                usedf=True,
                retry_override=0,
            )
        except Exception:
            self._intraday_failed_codes.add(self.quote_code)
            raise
        df = self._to_dataframe(raw, expect_time_index=True)
        if df.empty:
            raise RuntimeError(f"Wind intraday bars returned empty data for {self.quote_code}")
        df = df.rename(columns={c: str(c).lower() for c in df.columns})
        index_name = df.index.name or "index"
        df = df.reset_index().rename(columns={index_name: "datetime"})
        return df.tail(lookback_points).reset_index(drop=True)

    def get_trading_days(self, exchange: str, start_day: date, end_day: date) -> list[date]:
        cache_key = (exchange.upper(), start_day.isoformat(), end_day.isoformat())
        if cache_key in self._calendar_cache:
            return list(self._calendar_cache[cache_key])
        if self._mock_mode:
            raise RuntimeError("Trading calendar is unavailable in mock mode without static cache.")
        if not self._connected:
            raise RuntimeError("Wind is not connected. Please start and log in to Wind Terminal.")
        raw = self._call_with_retry(
            "tdays",
            start_day.isoformat(),
            end_day.isoformat(),
            f"TradingCalendar={exchange.upper()}",
            usedf=True,
        )
        df = self._to_dataframe(raw, expect_time_index=True)
        if df.empty:
            self._calendar_cache[cache_key] = []
            return []
        trading_days = [ts.date() for ts in pd.to_datetime(df.index, errors="coerce") if not pd.isna(ts)]
        self._calendar_cache[cache_key] = trading_days
        return list(trading_days)

    def get_index_future_basis_curve(self) -> pd.DataFrame:
        if str(self.product.get("asset_class")) != "index_future":
            return pd.DataFrame()
        underlying_code = str(self.product.get("option_underlying_code", "")).strip()
        if not underlying_code:
            return pd.DataFrame()
        buckets_and_codes = self._index_future_contract_codes()
        if not buckets_and_codes:
            return pd.DataFrame()

        if self._mock_mode:
            return self._mock_index_future_basis_curve(buckets_and_codes)
        if not self._connected:
            raise RuntimeError("Wind is not connected. Please start and log in to Wind Terminal.")

        future_codes = [code for _, code in buckets_and_codes]
        future_df = self._fetch_quote_frame(future_codes, ["rt_last", "rt_open", "rt_pre_close"])
        spot_df = self._fetch_quote_frame([underlying_code], ["rt_last", "rt_open", "rt_pre_close"])
        if future_df.empty or spot_df.empty:
            return pd.DataFrame()

        spot_last = _prefer_live_price(safe_float(spot_df.iloc[0].get("RT_LAST")), safe_float(spot_df.iloc[0].get("RT_PRE_CLOSE")))
        spot_open = _prefer_open_price(safe_float(spot_df.iloc[0].get("RT_OPEN")), safe_float(spot_df.iloc[0].get("RT_PRE_CLOSE")))
        if spot_last is None or spot_open is None:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for bucket, code in buckets_and_codes:
            if code not in future_df.index:
                continue
            future_last = _prefer_live_price(safe_float(future_df.loc[code].get("RT_LAST")), safe_float(future_df.loc[code].get("RT_PRE_CLOSE")))
            future_open = _prefer_open_price(safe_float(future_df.loc[code].get("RT_OPEN")), safe_float(future_df.loc[code].get("RT_PRE_CLOSE")))
            if future_last is None or future_open is None:
                continue
            basis_open = future_open - spot_open
            basis_now = future_last - spot_last
            rows.append(
                {
                    "bucket": bucket,
                    "code": code,
                    "future_last": future_last,
                    "future_open": future_open,
                    "spot_last": spot_last,
                    "spot_open": spot_open,
                    "basis_open": basis_open,
                    "basis_now": basis_now,
                    "basis_change": basis_now - basis_open,
                }
            )
        return pd.DataFrame(rows)

    def _call_with_retry(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        if self._w is None:
            raise RuntimeError("Wind client is not initialized")
        retry_count = int(kwargs.pop("retry_override", self.config.retry_count))
        log_failures = bool(kwargs.pop("log_failures", True))

        for attempt in range(1, retry_count + 2):
            try:
                method = getattr(self._w, method_name)
                result = method(*args, **kwargs)
                error_code = result[0] if isinstance(result, tuple) and len(result) == 2 else getattr(result, "ErrorCode", 0)
                if error_code not in (0, None):
                    raise RuntimeError(f"Wind error code: {error_code}")
                return result
            except Exception as exc:
                if log_failures:
                    logger.warning("Wind call failed attempt=%s method=%s reason=%s", attempt, method_name, exc)
                if attempt > retry_count:
                    raise
                time.sleep(self.config.retry_delay_seconds)
        raise RuntimeError("Wind retry unexpectedly exhausted")

    def _build_option_candidate_codes(self, month_codes: list[str] | None = None) -> list[str]:
        product_code = str(self.product["option_product_code"])
        step = int(self.product.get("option_strike_step", 50))
        count = int(self.product.get("option_strike_count", 12))
        quote = self.get_underlying_quote()
        spot = quote["last"]
        if spot <= 0:
            pre_close = quote["pre_close"]
            spot = pre_close if pre_close > 0 else step * 10
        center = int(round(spot / step) * step)
        code_style = str(self.product.get("option_code_style", "cfe_hyphen"))
        exchange_suffix = str(self.product.get("option_exchange_suffix", ".CFE"))
        strikes = range(center - count * step, center + (count + 1) * step, step)
        codes: list[str] = []
        if month_codes is None:
            month_scan_count = max(int(self.product.get("option_month_scan_count", 1)), 3)
            month_codes = []
            for month_index in range(0, month_scan_count):
                month_date = date.today().replace(day=1) + timedelta(days=31 * month_index)
                month_codes.append(month_date.strftime("%y%m"))
        for month_code in month_codes:
            for strike in strikes:
                if strike <= 0:
                    continue
                if code_style == "compact":
                    codes.append(f"{product_code}{month_code}C{strike}{exchange_suffix}")
                    codes.append(f"{product_code}{month_code}P{strike}{exchange_suffix}")
                else:
                    codes.append(f"{product_code}{month_code}-C-{strike}{exchange_suffix}")
                    codes.append(f"{product_code}{month_code}-P-{strike}{exchange_suffix}")
        return codes

    def _discover_option_codes(self) -> list[str]:
        log_path = self._latest_wmain_log()
        if log_path is None:
            return []
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        product_code = str(self.product.get("option_product_code", "")).upper()
        if not product_code:
            return []
        code_style = str(self.product.get("option_code_style", "cfe_hyphen"))
        exchange_suffix = re.escape(str(self.product.get("option_exchange_suffix", ".CFE")).upper())
        if code_style == "compact":
            pattern = rf"\b{product_code}(\d{{4}})[CP]\d+{exchange_suffix}\b"
        else:
            pattern = rf"\b{product_code}(\d{{4}})-[CP]-\d+{exchange_suffix}\b"
        matches = re.finditer(pattern, text)
        discovered: dict[str, str] = {}
        for match in matches:
            full_code = match.group(0)
            month_code = match.group(1)
            discovered[full_code] = month_code
        if not discovered:
            return []

        unique_months = sorted(set(discovered.values()))
        active_months = [month for month in unique_months if not self._is_stale_option_month_code(month)]
        stale_months = [month for month in unique_months if month not in active_months]
        if stale_months:
            logger.info(
                "Ignoring stale discovered option months product=%s stale=%s current_month=%s",
                self.config.product_id,
                stale_months,
                date.today().strftime("%y%m"),
            )
        if not active_months:
            return []
        month_scan_count = max(int(self.product.get("option_month_scan_count", 1)), 3)
        selected_months = active_months[:month_scan_count]
        return sorted(code for code, month_code in discovered.items() if month_code in selected_months)

    def _extract_option_month_codes(self, codes: list[str]) -> list[str]:
        months = {month for month in (self._parse_option_month_code(code) for code in codes) if month}
        return sorted(months)

    def _expand_option_month_codes(self, discovered_months: list[str]) -> list[str]:
        if not discovered_months:
            return []
        month_scan_count = int(self.product.get("option_month_scan_count", 1))
        base_month = discovered_months[0]
        year = 2000 + int(base_month[:2])
        month = int(base_month[2:])
        expanded: list[str] = []
        for month_offset in range(month_scan_count):
            total_month = month + month_offset
            expanded_year = year + (total_month - 1) // 12
            expanded_month = ((total_month - 1) % 12) + 1
            expanded.append(f"{expanded_year % 100:02d}{expanded_month:02d}")
        return expanded

    def _fetch_option_quote_block(self, codes: list[str], fields: list[str]) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame()
        frames: list[pd.DataFrame] = []
        chunk_size = 40
        for start in range(0, len(codes), chunk_size):
            chunk = codes[start:start + chunk_size]
            frames.extend(self._fetch_option_quote_chunk(chunk, fields, start))
        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, axis=0)
        combined = combined[~combined.index.duplicated(keep="first")]
        return combined

    def _fetch_option_quote_chunk(self, codes: list[str], fields: list[str], offset: int) -> list[pd.DataFrame]:
        if not codes:
            return []
        try:
            raw = self._call_with_retry(
                "wsq",
                ",".join(codes),
                ",".join(fields),
                usedf=True,
                retry_override=0,
                log_failures=False,
            )
            df = self._to_dataframe(raw)
            return [df] if not df.empty else []
        except Exception as exc:
            error_code = _extract_wind_error_code(exc)
            if len(codes) == 1:
                logger.warning("Skip option code=%s because quote query failed: %s", codes[0], exc)
                return []
            if error_code not in (-40522017, -40522007, None):
                logger.warning(
                    "Option quote query failed for chunk starting=%s size=%s code=%s, skip chunk: %s",
                    offset,
                    len(codes),
                    error_code,
                    exc,
                )
                return []
            midpoint = len(codes) // 2
            logger.warning(
                "Option quote query failed for chunk starting=%s size=%s code=%s, splitting: %s",
                offset,
                len(codes),
                error_code,
                exc,
            )
            return self._fetch_option_quote_chunk(codes[:midpoint], fields, offset) + self._fetch_option_quote_chunk(
                codes[midpoint:],
                fields,
                offset + midpoint,
            )

    def _fetch_quote_frame(self, codes: list[str], fields: list[str]) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame()
        raw = self._call_with_retry("wsq", ",".join(codes), ",".join(fields), usedf=True)
        df = self._to_dataframe(raw)
        if df.empty:
            return pd.DataFrame()
        df.columns = [str(col).upper() for col in df.columns]
        df.index = df.index.astype(str)
        return df

    def _index_future_contract_codes(self, asof_date: date | None = None) -> list[tuple[str, str]]:
        asof_date = asof_date or date.today()
        product_prefix = self.config.product_id.upper()
        exchange_suffix = ".CFE"
        current_month = asof_date.month
        current_year = asof_date.year
        next_month_date = (asof_date.replace(day=1) + timedelta(days=32)).replace(day=1)
        quarter_month = _next_quarter_month(current_month + 1)
        quarter_year = current_year + (1 if quarter_month < current_month else 0)
        next_quarter_month = _next_quarter_month(quarter_month + 3)
        next_quarter_year = quarter_year + (1 if next_quarter_month <= quarter_month else 0)
        contract_months = [
            ("Current", current_year, current_month),
            ("Next", next_month_date.year, next_month_date.month),
            ("Quarter", quarter_year, quarter_month),
            ("Next Quarter", next_quarter_year, next_quarter_month),
        ]
        return [(bucket, f"{product_prefix}{year % 100:02d}{month:02d}{exchange_suffix}") for bucket, year, month in contract_months]

    @staticmethod
    def _parse_option_type(contract_code: str) -> str:
        code = str(contract_code).upper()
        if "-C-" in code:
            return "CALL"
        if "-P-" in code:
            return "PUT"
        compact_match = re.search(r"\d{4}([CP])\d+\.(?:SHF|INE|DCE|CZC|GFEX)$", code)
        if compact_match:
            return "CALL" if compact_match.group(1) == "C" else "PUT"
        return ""

    @staticmethod
    def _parse_option_strike(contract_code: str) -> float | None:
        code = str(contract_code).upper()
        match = re.search(r"-(?:C|P)-(\d+)\.(?:CFE|SHF|INE|DCE|CZC|GFEX)$", code)
        if match:
            return float(match.group(1))
        compact_match = re.search(r"\d{4}[CP](\d+)\.(?:SHF|INE|DCE|CZC|GFEX)$", code)
        return float(compact_match.group(1)) if compact_match else None

    def _parse_option_expiry(self, contract_code: str) -> str | None:
        code = str(contract_code).upper()
        match = re.match(r"^[A-Z]+(\d{2})(\d{2})-[CP]-\d+\.(?:CFE|SHF|INE|DCE|CZC|GFEX)$", code)
        if not match:
            match = re.match(r"^[A-Z]+(\d{2})(\d{2})[CP]\d+\.(?:SHF|INE|DCE|CZC|GFEX)$", code)
        if not match:
            return None
        year = 2000 + int(match.group(1))
        month = int(match.group(2))
        expiry_rule = str(self.product.get("option_expiry_rule", "third_friday"))
        if expiry_rule == "third_friday":
            return WindClient._third_friday(year, month).isoformat()
        if expiry_rule == "month_start":
            return date(year, month, 1).isoformat()
        return None

    @staticmethod
    def _parse_option_expiry_month(contract_code: str) -> str | None:
        code = str(contract_code).upper()
        match = re.match(r"^[A-Z]+(\d{2})(\d{2})(?:-[CP]-|[CP])\d+\.(?:CFE|SHF|INE|DCE|CZC|GFEX)$", code)
        if not match:
            return None
        year = 2000 + int(match.group(1))
        month = int(match.group(2))
        return f"{year:04d}-{month:02d}"

    @staticmethod
    def _parse_option_month_code(contract_code: str) -> str | None:
        code = str(contract_code).upper()
        match = re.match(r"^[A-Z]+(\d{4})(?:-[CP]-|[CP])\d+\.(?:CFE|SHF|INE|DCE|CZC|GFEX)$", code)
        return match.group(1) if match else None

    @staticmethod
    def _is_stale_option_month_code(month_code: str, asof_date: date | None = None) -> bool:
        asof_date = asof_date or date.today()
        parsed = _parse_month_code(month_code)
        if parsed is None:
            return False
        year, month = parsed
        return (year, month) < (asof_date.year, asof_date.month)

    @staticmethod
    def _third_friday(year: int, month: int) -> date:
        weeks = monthcalendar(year, month)
        fridays = [week[4] for week in weeks if week[4] != 0]
        if len(fridays) < 3:
            raise ValueError(f"Unable to determine third Friday for {year:04d}-{month:02d}")
        return date(year, month, fridays[2])

    @staticmethod
    def _latest_wmain_log() -> Path | None:
        root = Path.home() / "Wind" / "Wind.NET.Client" / "WindNET" / "log" / "wbox"
        candidates = sorted(root.glob("WMain_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def _normalize_quote(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, tuple) and len(raw) == 2:
            err, df = raw
            if err not in (0, None):
                raise RuntimeError(f"Wind returned error code {err}")
            series = df.iloc[0] if not df.empty else pd.Series(dtype=float)
            values = {field: safe_float(series.get(wind_field)) or 0.0 for field, wind_field in self.config.field_map["realtime_fields"].items()}
        else:
            fields = list(self.config.field_map["realtime_fields"].keys())
            values = {}
            for idx, field in enumerate(fields):
                value = None
                try:
                    value = raw.Data[idx][0]
                except Exception:
                    pass
                values[field] = safe_float(value) or 0.0

        return {
            "code": self.quote_code,
            "name": self.quote_name,
            "last": _prefer_live_price(values["last"], values["pre_close"]) or 0.0,
            "pre_close": values["pre_close"],
            "change": values["change"] if values["last"] not in (None, 0.0) else 0.0,
            "pct_change": values["pct_change"] if values["last"] not in (None, 0.0) else 0.0,
            "volume": values["volume"],
            "bid1": values["bid1"],
            "ask1": values["ask1"],
            "timestamp": datetime.now(),
        }

    def _to_dataframe(self, raw: Any, expect_time_index: bool = False) -> pd.DataFrame:
        if isinstance(raw, tuple) and len(raw) == 2:
            err, df = raw
            if err not in (0, None):
                raise RuntimeError(f"Wind returned error code {err}")
            out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
        elif isinstance(raw, pd.DataFrame):
            out = raw.copy()
        else:
            raise RuntimeError(f"Unsupported Wind result type: {type(raw)}")
        if expect_time_index:
            out.index = pd.to_datetime(out.index, errors="coerce")
        return out

    def _mock_quote(self) -> dict[str, Any]:
        base = 3500 + 30 * np.sin(time.time() / 60) if self.quote_code.endswith(".CFE") else 2.45 + 0.03 * np.sin(time.time() / 60)
        last = round(base + random.uniform(-5 if base > 100 else -0.01, 5 if base > 100 else 0.01), 4)
        pre_close = round(last * 0.997, 4)
        change = round(last - pre_close, 4)
        pct_change = round(change / pre_close * 100, 3) if pre_close else 0.0
        tick = 0.2 if base > 100 else 0.001
        return {
            "code": self.quote_code,
            "name": self.quote_name,
            "last": last,
            "pre_close": pre_close,
            "change": change,
            "pct_change": pct_change,
            "volume": 1_000_000 + random.randint(0, 300_000),
            "bid1": round(last - tick, 4),
            "ask1": round(last + tick, 4),
            "timestamp": datetime.now(),
        }

    def _mock_option_chain(self, expiry_month_offset: int | None) -> pd.DataFrame:
        if not self.product.get("option_chain_enabled", False):
            return pd.DataFrame()

        offset = self.config.expiry_month_offset if expiry_month_offset is None else expiry_month_offset
        month_anchor = date.today().replace(day=1) + timedelta(days=31 * offset)
        expiry_date = self._third_friday(month_anchor.year, month_anchor.month)
        spot = self._mock_quote()["last"]
        step = 50.0 if spot > 1000 else 0.05
        center = round(spot / step) * step
        strikes = np.round(np.arange(center - 8 * step, center + 9 * step, step), 2)
        rows: list[dict[str, Any]] = []
        for strike in strikes:
            moneyness = (spot - strike) / max(abs(spot), 1)
            base_oi = max(1500, int(8000 * np.exp(-abs(moneyness) * 8)))
            gamma = max(0.01, 0.15 * np.exp(-abs(moneyness) * 6))
            for option_type in ("PUT", "CALL"):
                directional = 1 if option_type == "CALL" else -1
                intrinsic = max(0.0, directional * (spot - strike))
                time_value = max(step * 0.2, abs(spot) * 0.008 * np.exp(-abs(moneyness) * 5))
                last = round(intrinsic + time_value + random.uniform(-step * 0.05, step * 0.05), 4)
                iv = round(0.16 + abs(moneyness) * 0.18 + random.uniform(-0.01, 0.01), 4)
                rows.append(
                    {
                        "contract_code": f"MOCK.{self.config.product_id}.{expiry_date:%y%m}.{strike:.2f}.{option_type[0]}",
                        "contract_name": f"{self.product.get('option_product_code', 'OPT')}{expiry_date:%m}-{strike:.2f}-{option_type}",
                        "option_type": option_type,
                        "strike": float(strike),
                        "expiry_date": expiry_date.isoformat(),
                        "last": last,
                        "change": round(random.uniform(-step * 0.1, step * 0.1), 4),
                        "pct_change": round(random.uniform(-5, 5), 3),
                        "volume": random.randint(30, 5000),
                        "open_interest": int(base_oi * random.uniform(0.8, 1.2)),
                        "iv": iv,
                        "delta": round((0.5 + moneyness * 2.2) * directional, 4),
                        "gamma": round(gamma, 5),
                        "vega": round(0.02 + gamma * 0.3, 5),
                        "theta": round(-0.03 - abs(moneyness) * 0.02, 5),
                        "multiplier": int(self.product.get("multiplier", 1)),
                    }
                )
        return pd.DataFrame(rows)

    def _mock_intraday(self, interval: str, lookback_points: int) -> pd.DataFrame:
        freq = {"1m": "1min", "5m": "5min", "tick": "1min"}.get(interval, "1min")
        now = pd.Timestamp.now().floor("min")
        index = pd.date_range(end=now, periods=lookback_points, freq=freq)
        base = 3500.0 if self.quote_code.endswith(".CFE") else 2.45
        amplitude = 25.0 if base > 100 else 0.02
        baseline = base + amplitude * np.sin(np.linspace(0, 4 * np.pi, lookback_points))
        noise = np.random.normal(0, amplitude * 0.15, lookback_points)
        close = baseline + noise
        volume = np.random.randint(10_000, 90_000, lookback_points)
        return pd.DataFrame(
            {
                "datetime": index,
                "open": close - np.random.normal(0, amplitude * 0.1, lookback_points),
                "high": close + np.abs(np.random.normal(0, amplitude * 0.12, lookback_points)),
                "low": close - np.abs(np.random.normal(0, amplitude * 0.12, lookback_points)),
                "close": close,
                "volume": volume,
            }
        )

    def _mock_index_future_basis_curve(self, buckets_and_codes: list[tuple[str, str]]) -> pd.DataFrame:
        spot_open = self._mock_quote()["pre_close"]
        spot_last = self._mock_quote()["last"]
        rows: list[dict[str, Any]] = []
        for idx, (bucket, code) in enumerate(buckets_and_codes):
            carry = (idx - 1) * 6.0
            future_open = spot_open + carry + idx * 2
            future_last = spot_last + carry + idx * 2 + random.uniform(-3, 3)
            basis_open = future_open - spot_open
            basis_now = future_last - spot_last
            rows.append(
                {
                    "bucket": bucket,
                    "code": code,
                    "future_last": future_last,
                    "future_open": future_open,
                    "spot_last": spot_last,
                    "spot_open": spot_open,
                    "basis_open": basis_open,
                    "basis_now": basis_now,
                    "basis_change": basis_now - basis_open,
                }
            )
        return pd.DataFrame(rows)


def _next_quarter_month(start_month: int) -> int:
    for month in (3, 6, 9, 12):
        if start_month <= month:
            return month
    return 3


def _extract_wind_error_code(exc: Exception) -> int | None:
    match = re.search(r"Wind error code:\s*(-?\d+)", str(exc))
    return int(match.group(1)) if match else None


def _parse_month_code(month_code: str) -> tuple[int, int] | None:
    text = str(month_code).strip()
    if not re.fullmatch(r"\d{4}", text):
        return None
    year = 2000 + int(text[:2])
    month = int(text[2:])
    if month < 1 or month > 12:
        return None
    return year, month


def _prefer_live_price(last_value: float | None, fallback_value: float | None) -> float | None:
    if last_value is not None and last_value > 0:
        return last_value
    if fallback_value is not None and fallback_value > 0:
        return fallback_value
    return None


def _prefer_open_price(open_value: float | None, fallback_value: float | None) -> float | None:
    if open_value is not None and open_value > 0:
        return open_value
    if fallback_value is not None and fallback_value > 0:
        return fallback_value
    return None
