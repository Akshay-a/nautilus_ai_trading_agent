"""
Read-only Bybit V5 account context for risk-aware strategy prompts.

This module only performs signed GET requests. It is intentionally separate
from order execution so dashboard/prompt context cannot mutate exchange state.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests


def _float_or_none(value: Any) -> Optional[float]:
    """Return a float for exchange string numbers, or None when unavailable."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    """Return an int for exchange timestamp strings, or None when unavailable."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ms_to_iso(value: Any) -> Optional[str]:
    """Convert exchange millisecond timestamp to ISO-8601 UTC."""
    millis = _int_or_none(value)
    if millis is None:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class BybitAccountContextFetcher:
    """Fetch compact read-only account, order, execution, and P&L context."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        instrument_id: str,
        demo: bool = False,
        testnet: bool = False,
        recv_window_ms: int = 5000,
        timeout_sec: int = 10,
        logger: Any = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.instrument_id = instrument_id
        self.symbol = self._symbol_from_instrument(instrument_id)
        self.demo = demo
        self.testnet = testnet
        self.recv_window_ms = recv_window_ms
        self.timeout_sec = timeout_sec
        self.logger = logger
        self.base_url = self._base_url(demo=demo, testnet=testnet)

    @classmethod
    def from_env(
        cls,
        instrument_id: str,
        logger: Any = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Optional["BybitAccountContextFetcher"]:
        """Create a fetcher from environment-style values, returning None if keys are absent."""
        values = env if env is not None else os.environ
        api_key = (values.get("BYBIT_API_KEY") or "").strip()
        api_secret = (values.get("BYBIT_API_SECRET") or "").strip()
        if not api_key or not api_secret:
            return None

        return cls(
            api_key=api_key,
            api_secret=api_secret,
            instrument_id=instrument_id,
            demo=(values.get("BYBIT_DEMO") or "false").strip().lower() == "true",
            testnet=(values.get("BYBIT_TESTNET") or "false").strip().lower() == "true",
            logger=logger,
        )

    @staticmethod
    def _base_url(demo: bool, testnet: bool) -> str:
        if testnet:
            return "https://api-testnet.bybit.com"
        if demo:
            return "https://api-demo.bybit.com"
        return "https://api.bybit.com"

    @staticmethod
    def _symbol_from_instrument(instrument_id: str) -> str:
        return str(instrument_id).split("-")[0].split(".")[0]

    def _log_warning(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.warning(message)
                return
            except Exception:
                pass

    def _signed_get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        query = urllib.parse.urlencode(params)
        timestamp_ms = str(int(time.time() * 1000))
        recv_window = str(self.recv_window_ms)
        payload = timestamp_ms + self.api_key + recv_window + query
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": timestamp_ms,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
        }
        response = requests.get(
            self.base_url + path,
            params=params,
            headers=headers,
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = payload.get("result") or {}
        rows = result.get("list") or []
        return rows if isinstance(rows, list) else []

    def _fetch_endpoint(
        self,
        name: str,
        path: str,
        params: Dict[str, Any],
        errors: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        try:
            payload = self._signed_get(path=path, params=params)
        except Exception as exc:
            errors.append({"endpoint": name, "error": f"{type(exc).__name__}: {exc}"})
            return []

        ret_code = payload.get("retCode")
        if ret_code != 0:
            errors.append(
                {
                    "endpoint": name,
                    "retCode": ret_code,
                    "retMsg": payload.get("retMsg"),
                }
            )
            return []
        return self._rows(payload)

    def fetch(self) -> Dict[str, Any]:
        """Fetch and normalize account context for one linear perpetual symbol."""
        errors: List[Dict[str, Any]] = []
        wallet_rows = self._fetch_endpoint(
            "wallet",
            "/v5/account/wallet-balance",
            {"accountType": "UNIFIED"},
            errors,
        )
        position_rows = self._fetch_endpoint(
            "positions",
            "/v5/position/list",
            {"category": "linear", "symbol": self.symbol},
            errors,
        )
        order_rows = self._fetch_endpoint(
            "open_orders",
            "/v5/order/realtime",
            {"category": "linear", "symbol": self.symbol, "openOnly": "0", "limit": "50"},
            errors,
        )
        execution_rows = self._fetch_endpoint(
            "executions",
            "/v5/execution/list",
            {"category": "linear", "symbol": self.symbol, "limit": "20"},
            errors,
        )
        closed_pnl_rows = self._fetch_endpoint(
            "closed_pnl",
            "/v5/position/closed-pnl",
            {"category": "linear", "symbol": self.symbol, "limit": "10"},
            errors,
        )

        wallet = self._normalize_wallet(wallet_rows)
        position = self._normalize_position(position_rows)
        open_orders = self._normalize_open_orders(order_rows)
        recent_executions = self._normalize_executions(execution_rows)
        recent_closed_pnl = self._normalize_closed_pnl(closed_pnl_rows)
        trade_summary = self._build_trade_summary(recent_closed_pnl)

        if errors:
            self._log_warning(f"⚠️ Bybit account context partial failure: {errors[:2]}")

        return {
            "source": "bybit_v5",
            "ok": not errors,
            "errors": errors,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "mode": {
                "demo": self.demo,
                "testnet": self.testnet,
                "endpoint": self.base_url,
            },
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "wallet": wallet,
            "position": position,
            "open_orders": open_orders,
            "recent_executions": recent_executions,
            "recent_closed_pnl": recent_closed_pnl,
            "recent_trade_summary": trade_summary,
        }

    def _normalize_wallet(self, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not rows:
            return None
        row = rows[0]
        coins = {
            coin.get("coin"): coin
            for coin in (row.get("coin") or [])
            if isinstance(coin, dict)
        }
        usdt = coins.get("USDT") or {}
        return {
            "total_equity": _float_or_none(row.get("totalEquity")),
            "total_wallet_balance": _float_or_none(row.get("totalWalletBalance")),
            "total_available_balance": _float_or_none(row.get("totalAvailableBalance")),
            "total_initial_margin": _float_or_none(row.get("totalInitialMargin")),
            "total_maintenance_margin": _float_or_none(row.get("totalMaintenanceMargin")),
            "usdt_equity": _float_or_none(usdt.get("equity")),
            "usdt_wallet_balance": _float_or_none(usdt.get("walletBalance")),
            "usdt_available_to_withdraw": _float_or_none(usdt.get("availableToWithdraw")),
        }

    def _normalize_position(self, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for row in rows:
            size = _float_or_none(row.get("size")) or 0.0
            if size == 0:
                continue
            venue_side = row.get("side")
            side = "short" if venue_side == "Sell" else "long" if venue_side == "Buy" else str(venue_side or "").lower()
            signed_qty = -size if side == "short" else size
            avg_price = _float_or_none(row.get("avgPrice"))
            mark_price = _float_or_none(row.get("markPrice"))
            return {
                "symbol": row.get("symbol"),
                "side": side,
                "quantity": size,
                "signed_quantity": signed_qty,
                "avg_price": avg_price,
                "mark_price": mark_price,
                "position_value": _float_or_none(row.get("positionValue")),
                "unrealized_pnl": _float_or_none(row.get("unrealisedPnl")),
                "leverage": _float_or_none(row.get("leverage")),
                "liq_price": _float_or_none(row.get("liqPrice")),
                "initial_margin": _float_or_none(row.get("positionIM")),
                "maintenance_margin": _float_or_none(row.get("positionMM")),
            }
        return None

    def _normalize_open_orders(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        closed_statuses = {"Filled", "Cancelled", "Rejected", "Deactivated"}
        orders: List[Dict[str, Any]] = []
        for row in rows:
            if row.get("orderStatus") in closed_statuses:
                continue
            orders.append(
                {
                    "symbol": row.get("symbol"),
                    "side": str(row.get("side") or "").lower(),
                    "order_type": row.get("orderType"),
                    "quantity": _float_or_none(row.get("qty")),
                    "price": _float_or_none(row.get("price")),
                    "trigger_price": _float_or_none(row.get("triggerPrice")),
                    "reduce_only": bool(row.get("reduceOnly")),
                    "status": row.get("orderStatus"),
                    "created_time": _ms_to_iso(row.get("createdTime")),
                    "updated_time": _ms_to_iso(row.get("updatedTime")),
                }
            )
        return orders

    def _normalize_executions(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for row in rows[:10]:
            normalized.append(
                {
                    "symbol": row.get("symbol"),
                    "side": str(row.get("side") or "").lower(),
                    "exec_type": row.get("execType"),
                    "quantity": _float_or_none(row.get("execQty")),
                    "price": _float_or_none(row.get("execPrice")),
                    "value": _float_or_none(row.get("execValue")),
                    "fee": _float_or_none(row.get("execFee")),
                    "closed_size": _float_or_none(row.get("closedSize")),
                    "mark_price": _float_or_none(row.get("markPrice")),
                    "time": _ms_to_iso(row.get("execTime")),
                }
            )
        return normalized

    def _normalize_closed_pnl(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for row in rows[:10]:
            closed_pnl = _float_or_none(row.get("closedPnl"))
            normalized.append(
                {
                    "symbol": row.get("symbol"),
                    "side": str(row.get("side") or "").lower(),
                    "quantity": _float_or_none(row.get("qty")),
                    "avg_entry_price": _float_or_none(row.get("avgEntryPrice")),
                    "avg_exit_price": _float_or_none(row.get("avgExitPrice")),
                    "closed_pnl": closed_pnl,
                    "entry_value": _float_or_none(row.get("cumEntryValue")),
                    "exit_value": _float_or_none(row.get("cumExitValue")),
                    "created_time": _ms_to_iso(row.get("createdTime")),
                    "updated_time": _ms_to_iso(row.get("updatedTime")),
                    "outcome": "win" if (closed_pnl or 0.0) > 0 else "loss" if (closed_pnl or 0.0) < 0 else "flat",
                }
            )
        return normalized

    def _build_trade_summary(self, closed_pnl_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        last_five = closed_pnl_rows[:5]
        pnl_values = [row.get("closed_pnl") or 0.0 for row in last_five]
        wins = sum(1 for value in pnl_values if value > 0)
        losses = sum(1 for value in pnl_values if value < 0)
        count = len(last_five)
        return {
            "last_5_count": count,
            "last_5_realized_pnl": sum(pnl_values),
            "last_5_wins": wins,
            "last_5_losses": losses,
            "last_5_win_rate": (wins / count) if count else None,
            "last_5_outcomes": [
                {
                    "side": row.get("side"),
                    "quantity": row.get("quantity"),
                    "closed_pnl": row.get("closed_pnl"),
                    "outcome": row.get("outcome"),
                    "updated_time": row.get("updated_time"),
                }
                for row in last_five
            ],
        }
