from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wind_mcp_server import WindMcpServer, json_safe


class FakeService:
    def wind_status(self):
        return {"status": "CONNECTED", "is_mock_mode": False}

    def list_products(self):
        return {"default_product_id": "IF", "products": [{"product_id": "IF"}]}

    def get_quote(self, product_id=None):
        return {
            "product_id": product_id or "IF",
            "quote": {"last": 5234.2, "timestamp": datetime(2026, 3, 18, 10, 0, 0)},
        }

    def get_option_chain(self, product_id=None, expiry_month_offset=None, limit=80):
        frame = pd.DataFrame([{"contract_code": "IO2603-C-5000.CFE", "last": 100.5}])
        return {"product_id": product_id or "IF", "row_count": len(frame), "option_chain": frame.to_dict(orient="records")}

    def get_intraday_bars(self, product_id=None, interval="1m", lookback_points=120):
        return {"product_id": product_id or "IF", "interval": interval, "row_count": lookback_points}

    def get_basis_curve(self, product_id=None):
        return {"product_id": product_id or "IF", "basis_curve": [{"bucket": "Current", "basis_now": 10.0}]}


def test_initialize() -> None:
    server = WindMcpServer(FakeService())
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pytest"}},
        }
    )
    assert response is not None
    assert response["result"]["serverInfo"]["name"] == "wind-mcp-server"
    assert response["result"]["capabilities"]["tools"]["listChanged"] is False


def test_tools_list_exposes_expected_tools() -> None:
    server = WindMcpServer(FakeService())
    response = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert response is not None
    tool_names = {tool["name"] for tool in response["result"]["tools"]}
    assert {"wind_status", "list_products", "get_quote", "get_option_chain", "get_intraday_bars", "get_basis_curve"} <= tool_names


def test_tools_call_returns_structured_content() -> None:
    server = WindMcpServer(FakeService())
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_quote", "arguments": {"product_id": "IH"}},
        }
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["product_id"] == "IH"
    assert "5234.2" in result["content"][0]["text"]


def test_json_safe_normalizes_timestamp() -> None:
    payload = {"ts": datetime(2026, 3, 18, 10, 0, 0)}
    normalized = json_safe(payload)
    assert normalized["ts"] == "2026-03-18T10:00:00"
