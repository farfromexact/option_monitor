from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data.wind_client import WindClient, WindClientConfig
from utils.helpers import load_yaml

logger = logging.getLogger(__name__)
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


@dataclass(slots=True)
class JsonRpcError(Exception):
    code: int
    message: str
    data: Any | None = None


class WindService:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.settings = load_yaml(config_path)
        self.products: dict[str, dict[str, Any]] = self.settings["products"]
        self.default_product_id = str(self.settings["market"]["default_product_id"])
        self.wind_client = self._build_client(self.default_product_id)
        self.wind_client.connect()

    def _build_client(self, product_id: str) -> WindClient:
        wind_cfg = self.settings["wind"]
        return WindClient(
            WindClientConfig(
                product_id=product_id,
                product=self.products[product_id],
                expiry_month_offset=int(self.settings["market"]["expiry_month_offset"]),
                retry_count=int(wind_cfg["retry_count"]),
                retry_delay_seconds=float(wind_cfg["retry_delay_seconds"]),
                field_map={
                    "realtime_fields": wind_cfg["realtime_fields"],
                    "option_chain_fields": wind_cfg["option_chain_fields"],
                    "option_chain_date_format": wind_cfg["option_chain_date_format"],
                },
                use_mock_on_failure=bool(self.settings["app"]["use_mock_on_wind_failure"]),
            )
        )

    def _select_product(self, product_id: str | None, expiry_month_offset: int | None = None) -> tuple[str, dict[str, Any]]:
        resolved_product_id = (product_id or self.default_product_id).upper()
        if resolved_product_id not in self.products:
            raise ValueError(f"Unknown product_id: {resolved_product_id}")
        resolved_offset = self.settings["market"]["expiry_month_offset"] if expiry_month_offset is None else expiry_month_offset
        self.wind_client.update_market(
            product_id=resolved_product_id,
            product=self.products[resolved_product_id],
            expiry_month_offset=int(resolved_offset),
        )
        return resolved_product_id, self.products[resolved_product_id]

    def wind_status(self) -> dict[str, Any]:
        return {
            "status": self.wind_client.status_text(),
            "is_mock_mode": self.wind_client.is_mock_mode,
            "default_product_id": self.default_product_id,
            "connected_quote_code": self.wind_client.quote_code,
        }

    def list_products(self) -> dict[str, Any]:
        rows = []
        for product_id, product in self.products.items():
            rows.append(
                {
                    "product_id": product_id,
                    "display_name": product.get("display_name"),
                    "quote_code": product.get("quote_code"),
                    "asset_class": product.get("asset_class"),
                    "option_chain_enabled": bool(product.get("option_chain_enabled", False)),
                    "option_product_code": product.get("option_product_code"),
                }
            )
        return {"default_product_id": self.default_product_id, "products": rows}

    def get_quote(self, product_id: str | None = None) -> dict[str, Any]:
        resolved_product_id, product = self._select_product(product_id)
        return {
            "product_id": resolved_product_id,
            "product": {
                "display_name": product.get("display_name"),
                "quote_code": product.get("quote_code"),
                "asset_class": product.get("asset_class"),
            },
            "quote": self.wind_client.get_underlying_quote(),
            "wind_status": self.wind_status(),
        }

    def get_option_chain(
        self,
        product_id: str | None = None,
        expiry_month_offset: int | None = None,
        limit: int = 80,
    ) -> dict[str, Any]:
        resolved_product_id, product = self._select_product(product_id, expiry_month_offset)
        chain = self.wind_client.get_option_chain(expiry_month_offset)
        if limit > 0 and not chain.empty:
            chain = chain.head(limit).copy()
        return {
            "product_id": resolved_product_id,
            "product": {
                "display_name": product.get("display_name"),
                "quote_code": product.get("quote_code"),
                "asset_class": product.get("asset_class"),
                "option_chain_enabled": bool(product.get("option_chain_enabled", False)),
            },
            "row_count": int(len(chain)),
            "expiry_month_offset": self.wind_client.config.expiry_month_offset,
            "option_chain": dataframe_to_records(chain),
            "wind_status": self.wind_status(),
        }

    def get_intraday_bars(
        self,
        product_id: str | None = None,
        interval: str = "1m",
        lookback_points: int = 120,
    ) -> dict[str, Any]:
        resolved_product_id, product = self._select_product(product_id)
        bars = self.wind_client.get_intraday_bars(interval=interval, lookback_points=lookback_points)
        return {
            "product_id": resolved_product_id,
            "product": {
                "display_name": product.get("display_name"),
                "quote_code": product.get("quote_code"),
            },
            "interval": interval,
            "lookback_points": lookback_points,
            "row_count": int(len(bars)),
            "bars": dataframe_to_records(bars),
            "wind_status": self.wind_status(),
        }

    def get_basis_curve(self, product_id: str | None = None) -> dict[str, Any]:
        resolved_product_id, product = self._select_product(product_id)
        basis_curve = self.wind_client.get_index_future_basis_curve()
        return {
            "product_id": resolved_product_id,
            "product": {
                "display_name": product.get("display_name"),
                "quote_code": product.get("quote_code"),
                "asset_class": product.get("asset_class"),
            },
            "row_count": int(len(basis_curve)),
            "basis_curve": dataframe_to_records(basis_curve),
            "wind_status": self.wind_status(),
        }


def dataframe_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    normalized = frame.replace({np.nan: None})
    records = normalized.to_dict(orient="records")
    return [json_safe(record) for record in records]


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isnan(value):
            return None
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


class WindMcpServer:
    def __init__(self, service: WindService) -> None:
        self.service = service
        self.tool_handlers: dict[str, Any] = {
            "wind_status": lambda arguments: self.service.wind_status(),
            "list_products": lambda arguments: self.service.list_products(),
            "get_quote": lambda arguments: self.service.get_quote(product_id=arguments.get("product_id")),
            "get_option_chain": lambda arguments: self.service.get_option_chain(
                product_id=arguments.get("product_id"),
                expiry_month_offset=arguments.get("expiry_month_offset"),
                limit=int(arguments.get("limit", 80)),
            ),
            "get_intraday_bars": lambda arguments: self.service.get_intraday_bars(
                product_id=arguments.get("product_id"),
                interval=str(arguments.get("interval", "1m")),
                lookback_points=int(arguments.get("lookback_points", 120)),
            ),
            "get_basis_curve": lambda arguments: self.service.get_basis_curve(product_id=arguments.get("product_id")),
        }

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        params = message.get("params", {})
        request_id = message.get("id")

        if method == "notifications/initialized":
            return None
        if method == "ping":
            return self._success(request_id, {})
        if method == "initialize":
            client_protocol = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
            return self._success(
                request_id,
                {
                    "protocolVersion": client_protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "wind-mcp-server", "version": "0.1.0"},
                    "instructions": "Wind market data tools backed by the local WindPy client.",
                },
            )
        if method == "tools/list":
            return self._success(request_id, {"tools": self._tool_definitions()})
        if method == "tools/call":
            return self._success(request_id, self._call_tool(params))
        if request_id is None:
            return None
        raise JsonRpcError(code=-32601, message=f"Method not found: {method}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if tool_name not in self.tool_handlers:
            raise JsonRpcError(code=-32602, message=f"Unknown tool: {tool_name}")
        try:
            payload = self.tool_handlers[tool_name](arguments)
        except Exception as exc:
            logger.exception("Tool call failed name=%s", tool_name)
            return {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "structuredContent": {"tool": tool_name, "error": str(exc)},
                "isError": True,
            }
        return {
            "content": [{"type": "text", "text": json.dumps(json_safe(payload), ensure_ascii=False, indent=2)}],
            "structuredContent": json_safe(payload),
            "isError": False,
        }

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "wind_status",
                "description": "Return the current Wind connection status and whether the server is in mock mode.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "list_products",
                "description": "List the configured Wind products from config/settings.yaml.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_quote",
                "description": "Fetch the latest underlying quote for a configured product such as IF, IH, IM, AU or CU.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "Configured product id, for example IF or AU."}
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_option_chain",
                "description": "Fetch the live option chain for a configured product and expiry offset.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "Configured product id."},
                        "expiry_month_offset": {"type": "integer", "minimum": 0, "description": "0 for front month, 1 for next month."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum number of rows to return."},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_intraday_bars",
                "description": "Fetch intraday OHLCV bars for a configured product.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "Configured product id."},
                        "interval": {"type": "string", "enum": ["1m", "5m", "tick"], "description": "Bar interval."},
                        "lookback_points": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Number of recent rows to return."},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_basis_curve",
                "description": "Fetch the current basis curve for index futures products.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "Configured index futures product id such as IH, IF or IM."}
                    },
                    "additionalProperties": False,
                },
            },
        ]

    @staticmethod
    def _success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def read_message(stdin: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stdin.readline()
        if not line:
            return None
        if line == b"\r\n":
            break
        decoded = line.decode("utf-8").strip()
        if ":" not in decoded:
            raise ValueError(f"Invalid header line: {decoded}")
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    content_length = int(headers["content-length"])
    body = stdin.read(content_length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(stdout: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    stdout.write(header)
    stdout.write(body)
    stdout.flush()


def write_error(stdout: Any, request_id: Any, error: JsonRpcError) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": error.code, "message": error.message},
    }
    if error.data is not None:
        payload["error"]["data"] = error.data
    write_message(stdout, payload)


def serve(config_path: Path) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = WindMcpServer(WindService(config_path=config_path))
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        try:
            message = read_message(stdin)
            if message is None:
                return 0
            response = server.handle_message(message)
            if response is not None:
                write_message(stdout, response)
        except JsonRpcError as exc:
            write_error(stdout, locals().get("message", {}).get("id") if isinstance(locals().get("message"), dict) else None, exc)
        except Exception as exc:
            logger.exception("Unhandled MCP server error")
            write_error(stdout, locals().get("message", {}).get("id") if isinstance(locals().get("message"), dict) else None, JsonRpcError(-32000, str(exc)))


def main() -> int:
    root = Path(__file__).resolve().parent
    return serve(root / "config" / "settings.yaml")


if __name__ == "__main__":
    raise SystemExit(main())
