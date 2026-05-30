from __future__ import annotations

from pprint import pprint


def main() -> int:
    from WindPy import w

    result = w.start(waitTime=60)
    print("start_err", getattr(result, "ErrorCode", None))
    print("connected", w.isconnected())

    checks = [
        ("IH main", "IH00.CFE", "rt_last,rt_pre_close,rt_vol"),
        ("IF main", "IF00.CFE", "rt_last,rt_pre_close,rt_vol"),
        ("IM main", "IM00.CFE", "rt_last,rt_pre_close,rt_vol"),
        ("HO option", "HO2603-C-3000.CFE", "rt_last,rt_pre_close,rt_vol,rt_open_interest"),
        ("IO option", "IO2603-P-4700.CFE", "rt_last,rt_pre_close,rt_vol,rt_open_interest"),
        ("MO option", "MO2603-P-8100.CFE", "rt_last,rt_pre_close,rt_vol,rt_open_interest"),
    ]

    for label, code, fields in checks:
        print(f"\n[{label}] {code}")
        try:
            wsq_result = w.wsq(code, fields, usedf=True)
            pprint(wsq_result)
        except Exception as exc:
            print("wsq_exc", repr(exc))

    static_checks = [
        ("HO static", "HO2603-C-3000.CFE", "w_info_strikeprice,w_info_exercisingend,s_info_tunit"),
        ("IO static", "IO2603-P-4700.CFE", "w_info_strikeprice,w_info_exercisingend,s_info_tunit"),
        ("MO static", "MO2603-P-8100.CFE", "w_info_strikeprice,w_info_exercisingend,s_info_tunit"),
    ]
    option = "tradeDate=s_trade_date(windcode,now(),0)"
    for label, code, fields in static_checks:
        print(f"\n[{label}] {code}")
        try:
            wss_result = w.wss(code, fields, option, usedf=True)
            pprint(wss_result)
        except Exception as exc:
            print("wss_exc", repr(exc))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
