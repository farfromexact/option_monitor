from __future__ import annotations

from typing import Iterable


def build_candidates(product_code: str, month_code: str, strikes: Iterable[int], suffix: str) -> list[str]:
    codes: list[str] = []
    for strike in strikes:
        codes.append(f"{product_code}{month_code}C{strike}{suffix}")
        codes.append(f"{product_code}{month_code}P{strike}{suffix}")
        codes.append(f"{product_code}{month_code}-C-{strike}{suffix}")
        codes.append(f"{product_code}{month_code}-P-{strike}{suffix}")
    return codes


def main() -> int:
    from WindPy import w

    result = w.start(waitTime=60)
    print("start_err", getattr(result, "ErrorCode", None))
    print("connected", w.isconnected())

    products = [
        ("AU", "2604", ".SHF", [640, 660, 680, 700, 720]),
        ("CU", "2604", ".SHF", [72000, 74000, 76000, 78000]),
        ("AG", "2604", ".SHF", [7600, 7800, 8000, 8200]),
        ("AL", "2604", ".SHF", [20000, 20500, 21000, 21500]),
        ("NI", "2604", ".SHF", [120000, 125000, 130000, 135000]),
        ("SN", "2604", ".SHF", [250000, 260000, 270000, 280000]),
        ("SC", "2604", ".INE", [700, 725, 750, 775, 800]),
    ]
    fields = "rt_last,rt_pre_close,rt_vol,rt_bid1,rt_ask1,rt_imp_volatility,rt_delta,rt_gamma,rt_vega,rt_theta,rt_oi,rt_opt_oi"

    for product_code, month_code, suffix, strikes in products:
        print(f"\n===== {product_code} =====")
        for code in build_candidates(product_code, month_code, strikes, suffix):
            try:
                err, df = w.wsq(code, fields, usedf=True)
            except Exception as exc:
                print(code, "EXC", repr(exc))
                continue
            if err != 0:
                continue
            numeric = df.apply(lambda col: col.fillna(0).astype(float).abs().sum(), axis=0).sum()
            if numeric > 0:
                print(code)
                print(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
