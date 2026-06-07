from __future__ import annotations

from datetime import date

import pandas as pd

from data.wind_client import WindClient, WindClientConfig


def _client(expiry_rule: str = "unknown") -> WindClient:
    return WindClient(
        WindClientConfig(
            product_id="AU",
            product={
                "quote_code": "AU00.SHF",
                "underlying_name": "沪金期货主连",
                "option_chain_enabled": True,
                "option_product_code": "AU",
                "option_code_style": "compact",
                "option_exchange_suffix": ".SHF",
                "option_expiry_rule": expiry_rule,
                "option_strike_step": 10,
                "option_strike_count": 12,
                "multiplier": 1000,
            },
            expiry_month_offset=0,
            retry_count=0,
            retry_delay_seconds=0.0,
            field_map={"realtime_fields": {}, "option_chain_fields": {}},
            use_mock_on_failure=True,
        )
    )


def test_parse_compact_option_code() -> None:
    client = _client()
    code = "AU2604C680.SHF"
    assert client._parse_option_type(code) == "CALL"
    assert client._parse_option_strike(code) == 680.0
    assert client._parse_option_expiry(code) is None


def test_parse_compact_option_code_with_month_start_expiry() -> None:
    client = _client(expiry_rule="month_start")
    assert client._parse_option_expiry("SC2605P500.INE") == "2026-05-01"


def test_stale_option_month_detection() -> None:
    client = _client()
    assert client._is_stale_option_month_code("2603", date(2026, 6, 7))
    assert not client._is_stale_option_month_code("2606", date(2026, 6, 7))
    assert not client._is_stale_option_month_code("2607", date(2026, 6, 7))


def test_option_quote_block_splits_and_skips_bad_code(monkeypatch) -> None:
    client = _client()
    codes = ["GOOD1.CFE", "BAD.CFE", "GOOD2.CFE"]

    def fake_call_with_retry(method_name: str, code_arg: str, fields_arg: str, **kwargs):
        assert method_name == "wsq"
        queried_codes = code_arg.split(",")
        if "BAD.CFE" in queried_codes:
            raise RuntimeError("Wind error code: -40522017")
        return (0, pd.DataFrame({"RT_LAST": [1.0] * len(queried_codes)}, index=queried_codes))

    monkeypatch.setattr(client, "_call_with_retry", fake_call_with_retry)

    result = client._fetch_option_quote_block(codes, ["rt_last"])

    assert result.index.tolist() == ["GOOD1.CFE", "GOOD2.CFE"]
