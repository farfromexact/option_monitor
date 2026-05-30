from __future__ import annotations

FIELD_NOTES = {
    "realtime_fields": {
        "last": "Wind 实时最新价字段，默认 rt_last",
        "pre_close": "Wind 昨收字段，默认 rt_pre_close",
        "change": "Wind 涨跌额字段，默认 rt_chg",
        "pct_change": "Wind 涨跌幅字段，默认 rt_pct_chg",
        "volume": "Wind 成交量字段，默认 rt_vol",
        "bid1": "Wind 买一字段，默认 rt_bid1",
        "ask1": "Wind 卖一字段，默认 rt_ask1",
    },
    "option_chain_fields": {
        "contract_code": "期权合约代码",
        "contract_name": "期权简称",
        "option_type": "认购/认沽标识",
        "strike": "执行价",
        "expiry_date": "到期日",
        "last": "最新价",
        "change": "涨跌额",
        "pct_change": "涨跌幅",
        "volume": "成交量",
        "open_interest": "持仓量",
        "iv": "隐含波动率",
        "delta": "Delta",
        "gamma": "Gamma",
        "vega": "Vega",
        "theta": "Theta",
        "multiplier": "合约乘数",
    },
}
