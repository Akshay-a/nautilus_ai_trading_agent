"""Performance metric and integrity utilities for backtest runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


ANNUAL_15M_PERIODS = 365 * 24 * 4


@dataclass(frozen=True)
class MetricsBundle:
    """Computed metrics plus derived artifacts and warnings."""

    metrics: dict[str, Any]
    adjusted_equity_curve: pd.DataFrame
    warnings: list[str]


def hash_metrics_payload(payload: dict[str, Any]) -> str:
    """Stable SHA256 hash for deterministic metric payload checks."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _to_float_safe(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, np.number)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("_", "")
    token = text.split()[0]
    try:
        return float(token)
    except ValueError:
        return None


def _coerce_account_df(account_df: pd.DataFrame) -> pd.DataFrame:
    if account_df is None or account_df.empty:
        return pd.DataFrame(columns=["timestamp", "equity"])

    out = account_df.copy()
    out = out.reset_index().rename(columns={"index": "timestamp"})
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    out["equity"] = out["total"].apply(_to_float_safe)
    out = out.dropna(subset=["equity"]).sort_values("timestamp")
    return out[["timestamp", "equity"]]


def _coerce_fills_df(fills_df: pd.DataFrame) -> pd.DataFrame:
    if fills_df is None or fills_df.empty:
        return pd.DataFrame(columns=["timestamp", "notional"])

    out = fills_df.copy().reset_index()
    out["timestamp"] = pd.to_datetime(out["ts_event"], utc=True)
    out["qty"] = out["last_qty"].apply(_to_float_safe)
    out["px"] = out["last_px"].apply(_to_float_safe)
    out["notional"] = (out["qty"].abs() * out["px"].abs()).fillna(0.0)
    out = out.sort_values("timestamp")
    return out[["timestamp", "notional"]]


def _coerce_positions_df(positions_df: pd.DataFrame) -> pd.DataFrame:
    if positions_df is None or positions_df.empty:
        return pd.DataFrame(columns=["ts_opened", "ts_closed", "duration_ns", "realized_pnl", "quantity", "avg_px_open", "avg_px_close"])

    out = positions_df.copy().reset_index(drop=False)
    out["ts_opened"] = pd.to_datetime(out["ts_opened"], utc=True, errors="coerce")
    out["ts_closed"] = pd.to_datetime(out["ts_closed"], utc=True, errors="coerce")
    out["duration_ns"] = pd.to_numeric(out["duration_ns"].apply(_to_float_safe), errors="coerce").fillna(0.0)
    out["realized_pnl_num"] = pd.to_numeric(
        out["realized_pnl"].apply(_to_float_safe),
        errors="coerce",
    ).fillna(0.0)
    out["quantity_num"] = pd.to_numeric(out["peak_qty"].apply(_to_float_safe), errors="coerce").fillna(0.0)
    out["avg_px_open_num"] = pd.to_numeric(
        out["avg_px_open"].apply(_to_float_safe),
        errors="coerce",
    ).fillna(0.0)
    out["avg_px_close_num"] = pd.to_numeric(
        out["avg_px_close"].apply(_to_float_safe),
        errors="coerce",
    ).fillna(0.0)
    return out


def _apply_slippage_overlay(
    equity_df: pd.DataFrame,
    fills_df: pd.DataFrame,
    slippage_bps_per_side: float,
) -> pd.DataFrame:
    """
    Apply deterministic per-side slippage overlay to equity curve.

    Costs are accrued at fill timestamps and forward-filled into account events.
    """
    if equity_df.empty:
        return equity_df.copy()

    adjusted = equity_df.copy()
    adjusted["slippage_cost"] = 0.0

    fills = _coerce_fills_df(fills_df)
    if fills.empty or slippage_bps_per_side <= 0:
        adjusted["equity_adjusted"] = adjusted["equity"]
        return adjusted

    fills["slippage_cost"] = fills["notional"] * (slippage_bps_per_side / 10_000.0)
    fills["cum_slippage_cost"] = fills["slippage_cost"].cumsum()

    merged = pd.merge_asof(
        adjusted.sort_values("timestamp"),
        fills[["timestamp", "cum_slippage_cost"]].sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    merged["cum_slippage_cost"] = merged["cum_slippage_cost"].fillna(0.0)
    merged["slippage_cost"] = merged["cum_slippage_cost"]
    merged["equity_adjusted"] = merged["equity"] - merged["cum_slippage_cost"]
    return merged[["timestamp", "equity", "equity_adjusted", "slippage_cost"]]


def _compute_exposure_ratio(
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    positions_df: pd.DataFrame,
    fills_df: pd.DataFrame,
) -> float:
    if period_end <= period_start:
        return 0.0
    period_ns = (period_end - period_start).total_seconds() * 1_000_000_000
    if period_ns <= 0:
        return 0.0

    pos = _coerce_positions_df(positions_df)
    total_ns = 0.0
    if not pos.empty:
        total_ns = float(pos["duration_ns"].sum())

    if total_ns > 0:
        ratio = total_ns / period_ns
        return float(max(0.0, min(ratio, 1.0)))

    # Fallback for open positions not represented in closed-position durations.
    if fills_df is None or fills_df.empty:
        return 0.0

    fills = fills_df.copy().reset_index(drop=False)
    fills["timestamp"] = pd.to_datetime(fills["ts_event"], utc=True, errors="coerce")
    fills["qty"] = pd.to_numeric(fills["last_qty"].apply(_to_float_safe), errors="coerce").fillna(0.0)
    fills["signed_qty"] = fills.apply(
        lambda row: row["qty"] if str(row.get("order_side", "")).upper() == "BUY" else -row["qty"],
        axis=1,
    )
    fills = fills.dropna(subset=["timestamp"]).sort_values("timestamp")

    if fills.empty:
        return 0.0

    last_ts = period_start
    net_qty = 0.0
    active_ns = 0.0

    for _, row in fills.iterrows():
        ts = row["timestamp"]
        if ts < period_start:
            net_qty += float(row["signed_qty"])
            continue
        if ts > period_end:
            break

        if abs(net_qty) > 1e-12:
            active_ns += (ts - last_ts).total_seconds() * 1_000_000_000
        last_ts = ts
        net_qty += float(row["signed_qty"])

    if abs(net_qty) > 1e-12 and last_ts < period_end:
        active_ns += (period_end - last_ts).total_seconds() * 1_000_000_000

    ratio = active_ns / period_ns
    return float(max(0.0, min(ratio, 1.0)))


def compute_performance_metrics(
    account_df: pd.DataFrame,
    fills_df: pd.DataFrame,
    positions_df: pd.DataFrame,
    initial_equity: float,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    slippage_bps_per_side: float,
) -> MetricsBundle:
    """Compute reporting metrics from native Nautilus reports with cost overlay."""
    warnings: list[str] = []

    eq = _coerce_account_df(account_df)
    adjusted_eq = _apply_slippage_overlay(eq, fills_df, slippage_bps_per_side)
    if adjusted_eq.empty:
        metrics = {
            "net_return": None,
            "max_drawdown": None,
            "sharpe_annualized": None,
            "profit_factor": None,
            "win_rate": None,
            "total_trades": 0,
            "avg_hold_time_minutes": None,
            "turnover": 0.0,
            "exposure": 0.0,
            "slippage_bps_per_side": slippage_bps_per_side,
        }
        warnings.append("No account curve data returned by backtest engine")
        return MetricsBundle(metrics=metrics, adjusted_equity_curve=adjusted_eq, warnings=warnings)

    start_equity = float(adjusted_eq["equity_adjusted"].iloc[0])
    end_equity = float(adjusted_eq["equity_adjusted"].iloc[-1])
    if start_equity <= 0:
        start_equity = max(initial_equity, 1e-9)

    net_return = (end_equity / start_equity) - 1.0

    adjusted_eq["running_max"] = adjusted_eq["equity_adjusted"].cummax()
    adjusted_eq["drawdown"] = (adjusted_eq["equity_adjusted"] / adjusted_eq["running_max"]) - 1.0
    max_drawdown = float(adjusted_eq["drawdown"].min()) if not adjusted_eq.empty else 0.0

    adjusted_eq["ret"] = adjusted_eq["equity_adjusted"].pct_change().replace([np.inf, -np.inf], np.nan)
    returns = adjusted_eq["ret"].dropna()
    if len(returns) > 1 and returns.std(ddof=1) > 0:
        sharpe = (returns.mean() / returns.std(ddof=1)) * np.sqrt(ANNUAL_15M_PERIODS)
    else:
        sharpe = np.nan

    positions = _coerce_positions_df(positions_df)
    if positions.empty:
        trade_pnls = np.array([], dtype=float)
        total_trades = 0
        avg_hold_mins = np.nan
    else:
        turnover_per_trade = (
            positions["quantity_num"].abs() * (positions["avg_px_open_num"].abs() + positions["avg_px_close_num"].abs())
        )
        slippage_per_trade = turnover_per_trade * (slippage_bps_per_side / 10_000.0)
        trade_pnls = (positions["realized_pnl_num"] - slippage_per_trade).to_numpy(dtype=float)
        total_trades = int(len(trade_pnls))
        avg_hold_mins = float((positions["duration_ns"].mean() / 1_000_000_000) / 60.0)

    if total_trades > 0:
        wins = trade_pnls[trade_pnls > 0]
        losses = trade_pnls[trade_pnls < 0]
        win_rate = float(len(wins) / total_trades)
        if losses.size > 0:
            profit_factor = float(wins.sum() / abs(losses.sum())) if wins.size > 0 else 0.0
        else:
            profit_factor = float("inf") if wins.size > 0 else np.nan
    else:
        win_rate = np.nan
        profit_factor = np.nan

    fills = _coerce_fills_df(fills_df)
    turnover = float(fills["notional"].sum() / max(initial_equity, 1e-9)) if not fills.empty else 0.0
    exposure = _compute_exposure_ratio(period_start, period_end, positions_df, fills_df)

    metrics = {
        "net_return": float(net_return),
        "max_drawdown": float(max_drawdown),
        "sharpe_annualized": None if np.isnan(sharpe) else float(sharpe),
        "profit_factor": None if np.isnan(profit_factor) else float(profit_factor),
        "win_rate": None if np.isnan(win_rate) else float(win_rate),
        "total_trades": total_trades,
        "avg_hold_time_minutes": None if np.isnan(avg_hold_mins) else float(avg_hold_mins),
        "turnover": float(turnover),
        "exposure": float(exposure),
        "slippage_bps_per_side": float(slippage_bps_per_side),
        "start_equity": float(start_equity),
        "end_equity": float(end_equity),
    }

    if metrics["sharpe_annualized"] is not None and abs(metrics["sharpe_annualized"]) > 5:
        warnings.append("Suspicious Sharpe magnitude (>5) detected")
    if total_trades < 5:
        warnings.append("Too few trades (<5) for stable inference")
    if metrics["max_drawdown"] < -0.8:
        warnings.append("Severe drawdown (>80%) detected")

    return MetricsBundle(metrics=metrics, adjusted_equity_curve=adjusted_eq, warnings=warnings)


def viability_verdict(oos_metrics: dict[str, Any]) -> str:
    """Simple viability classification for OOS performance."""
    if not oos_metrics:
        return "INCONCLUSIVE"

    net_return = oos_metrics.get("net_return")
    max_dd = oos_metrics.get("max_drawdown")
    total_trades = oos_metrics.get("total_trades") or 0
    sharpe = oos_metrics.get("sharpe_annualized")

    if total_trades < 5:
        return "INCONCLUSIVE"
    if net_return is None or max_dd is None:
        return "INCONCLUSIVE"

    if net_return <= 0 or max_dd < -0.35:
        return "KILL"

    if net_return > 0 and (sharpe is None or sharpe > 0):
        return "PASS"

    return "INCONCLUSIVE"
