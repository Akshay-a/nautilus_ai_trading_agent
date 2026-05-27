"""
CSV trade journal writer for strategy decision audit trails.

The journal is append-only and uses a stable column schema so it can be
analyzed directly with pandas or spreadsheets.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from typing import Any, Dict, Iterable


class TradeJournalCSV:
    """Append-only CSV journal with a fixed schema."""

    FIELDNAMES = [
        "decision_ts_utc",
        "decision_ts",
        "strategy_ts",
        "instrument_id",
        "bar_type",
        "bar_ts_event",
        "bar_ts_init",
        "signal",
        "confidence",
        "trend_strength",
        "risk_assessment",
        "is_fallback",
        "reason",
        "reasoning_content",
        "llm_model",
        "llm_api_seconds",
        "current_price",
        "period_high",
        "period_low",
        "bar_volume",
        "price_change_pct",
        "overall_trend",
        "short_term_trend",
        "medium_term_trend",
        "rsi",
        "macd",
        "macd_signal",
        "macd_histogram",
        "volume_ratio",
        "support",
        "resistance",
        "ob_spread_bps",
        "ob_spread_volatility",
        "ob_tob_imbalance",
        "ob_depth_imbalance",
        "ob_ema_ofi",
        "ob_queue_pressure",
        "ob_trade_flow_imbalance",
        "ob_vwap_deviation_bps",
        "ob_sweep_buy_count",
        "ob_sweep_sell_count",
        "ob_depth_regime",
        "position_before_side",
        "position_before_qty",
        "position_before_avg_px",
        "position_before_upnl",
        "position_before_source",
        "risk_total_equity",
        "risk_total_available_balance",
        "risk_open_orders_count",
        "risk_recent_realized_pnl_5",
        "execution_status",
        "execution_action",
        "execution_target_side",
        "execution_target_quantity",
        "execution_note",
        "technical_snapshot_json",
        "microstructure_snapshot_json",
        "risk_context_json",
        "position_before_json",
        "position_after_json",
        "bar_close_ts_utc",
        "bar_close_ts",
        "execution_ts_utc",
        "execution_ts",
        "latency_ms",
        "decision_cycle_trigger",
        "llm_market_regime",
        "thesis",
        "invalidation",
        "llm_execution_note",
        "volume_note",
        "rvol",
        "volume_zscore",
        "volume_trend_slope",
        "directional_volume_confirmation",
        "technical_volume_regime",
        "ob_window_fast_json",
        "ob_window_main_json",
        "ob_window_context_json",
        "signal_json",
    ]

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def append(self, row: Dict[str, Any]) -> None:
        """Append one row to the journal, creating header on first write."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

        with self._lock:
            file_exists = os.path.exists(self.path) and os.path.getsize(self.path) > 0
            with open(self.path, "a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=self.FIELDNAMES)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(self._normalize_row(row, self.FIELDNAMES))

    @staticmethod
    def _normalize_row(row: Dict[str, Any], columns: Iterable[str]) -> Dict[str, Any]:
        work = dict(row)
        # Timing aliases mirror *_utc canonical fields when not supplied explicitly.
        if not work.get("decision_ts") and work.get("decision_ts_utc"):
            work["decision_ts"] = work["decision_ts_utc"]
        if not work.get("bar_close_ts") and work.get("bar_close_ts_utc"):
            work["bar_close_ts"] = work["bar_close_ts_utc"]
        if not work.get("execution_ts") and work.get("execution_ts_utc"):
            work["execution_ts"] = work["execution_ts_utc"]

        normalized: Dict[str, Any] = {}
        for col in columns:
            normalized[col] = TradeJournalCSV._to_cell(work.get(col))
        return normalized

    @staticmethod
    def _to_cell(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value
