from __future__ import annotations

import re
from pathlib import Path


def latest_wmain_log() -> Path | None:
    root = Path.home() / "Wind" / "Wind.NET.Client" / "WindNET" / "log" / "wbox"
    candidates = sorted(root.glob("WMain_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def extract_option_codes(log_path: Path) -> list[str]:
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    codes = set(re.findall(r"\b(?:HO|IO|MO)\d{4}-[CP]-\d+\.CFE\b", text))
    return sorted(codes)


def main() -> int:
    from WindPy import w

    result = w.start(waitTime=60)
    print("start_err", getattr(result, "ErrorCode", None))
    print("connected", w.isconnected())

    log_path = latest_wmain_log()
    print("log_path", log_path)
    if not log_path:
        return 0

    codes = extract_option_codes(log_path)
    print("discovered_codes", len(codes))
    for code in codes[:20]:
        print(code)

    sample_codes = codes[:6]
    if not sample_codes:
        return 0

    wsq_fields = ["rt_last", "rt_pre_close", "rt_vol", "rt_open_interest"]
    wss_fields = [
        "sec_name",
        "close",
        "pre_close",
        "w_info_strikeprice",
        "w_info_exercisingend",
        "s_info_tunit",
    ]

    for code in sample_codes:
        print(f"\n[CODE] {code}")
        for field in wsq_fields:
            try:
                res = w.wsq(code, field, usedf=True)
                print("WSQ", field, res[0])
                if res[0] == 0:
                    print(res[1])
            except Exception as exc:
                print("WSQ", field, "EXC", repr(exc))

        for field in wss_fields:
            try:
                res = w.wss(code, field, "tradeDate=s_trade_date(windcode,now(),0)", usedf=True)
                print("WSS", field, res[0])
                if res[0] == 0:
                    print(res[1])
            except Exception as exc:
                print("WSS", field, "EXC", repr(exc))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
