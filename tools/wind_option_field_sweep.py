from __future__ import annotations

from collections import OrderedDict


def main() -> int:
    from WindPy import w

    result = w.start(waitTime=60)
    print("start_err", getattr(result, "ErrorCode", None))
    print("connected", w.isconnected())

    sample_codes = [
        "HO2603-C-2950.CFE",
        "IO2603-P-4700.CFE",
        "MO2603-P-8100.CFE",
    ]

    field_groups = OrderedDict(
        [
            (
                "quote_fields",
                [
                    ("wsq", "rt_last"),
                    ("wsq", "rt_pre_close"),
                    ("wsq", "rt_vol"),
                    ("wsq", "rt_open_interest"),
                    ("wsq", "oi"),
                    ("wsq", "rt_bid1"),
                    ("wsq", "rt_ask1"),
                ],
            ),
            (
                "static_fields",
                [
                    ("wss", "sec_name"),
                    ("wss", "s_info_name"),
                    ("wss", "exe_price"),
                    ("wss", "exercise_price"),
                    ("wss", "strike_price"),
                    ("wss", "w_info_strikeprice"),
                    ("wss", "exe_enddate"),
                    ("wss", "expiredate"),
                    ("wss", "lasttradingdate"),
                    ("wss", "w_info_exercisingend"),
                    ("wss", "contractmultiplier"),
                    ("wss", "s_info_tunit"),
                    ("wss", "multiplier"),
                    ("wss", "oi"),
                    ("wss", "open_interest"),
                    ("wss", "position"),
                ],
            ),
            (
                "greek_fields",
                [
                    ("wss", "delta"),
                    ("wss", "gamma"),
                    ("wss", "vega"),
                    ("wss", "theta"),
                    ("wss", "us_impliedvol"),
                    ("wss", "implied_volatility"),
                    ("wss", "iv"),
                    ("wss", "optioniv"),
                    ("wss", "imp_vol"),
                ],
            ),
        ]
    )

    option = "tradeDate=s_trade_date(windcode,now(),0)"
    for code in sample_codes:
        print(f"\n===== {code} =====")
        for group_name, field_specs in field_groups.items():
            print(f"\n[{group_name}]")
            for source, field in field_specs:
                try:
                    if source == "wsq":
                        res = w.wsq(code, field, usedf=True)
                    else:
                        res = w.wss(code, field, option, usedf=True)
                    err, df = res
                    print(f"{source.upper():3} {field:24} -> {err}")
                    if err == 0:
                        print(df)
                except Exception as exc:
                    print(f"{source.upper():3} {field:24} -> EXC {exc!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
