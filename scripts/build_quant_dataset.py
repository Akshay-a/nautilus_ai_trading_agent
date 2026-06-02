#!/usr/bin/env python3
"""
Build Quant-Ready LLM Decision Dataset
======================================
Aggregates every LLM trading decision from `deepseek_trader_*.json` logs into a
single, wide, analysis-ready CSV — one row per decision — joining:

  1. The exact feature snapshot the model saw (price, trend, RSI, rVol, trade
     flow imbalance, volatility regime, volume regime).
  2. The live position state at decision time (side / qty / unrealized PnL).
  3. The full LLM decision + free-text reasoning (thesis, invalidation, etc.).
  4. The execution outcome (executed / skipped LOW-conf / HOLD no-action).
  5. Multi-horizon FORWARD price action with path excursion (MFE/MAE) so the
     quant team can label signal quality properly instead of a noisy 1-bar check.

This is intentionally richer than `extract_llm_signals.py` (which produces a
human-readable markdown audit). This script targets an AI-driven quant team:
flat schema, pandas-friendly, self-documented via a companion data dictionary.

Usage:
    python scripts/build_quant_dataset.py [--days N] [--sessions N] [--output PATH]

Example (matches the markdown audit scope):
    python scripts/build_quant_dataset.py --days 2 --sessions 3

Outputs (default, in logs/):
    llm_quant_dataset.csv            <- the dataset
    llm_quant_dataset_dictionary.md  <- column docs + methodology + caveats
"""

import os
import re
import csv
import sys
import json
import glob
import argparse
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

# Reuse the battle-tested parsers from the markdown auditor to avoid drift.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_llm_signals import (  # noqa: E402
    LOG_DIR,
    parse_bar_close,
    parse_llm_json,
    log_file_timestamp,
)

# ─────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────
DEFAULT_CSV = os.path.join(LOG_DIR, "llm_quant_dataset.csv")
DEFAULT_DICT = os.path.join(LOG_DIR, "llm_quant_dataset_dictionary.md")
DEFAULT_DAYS = 14

# Forward horizons in BARS. Logs are bar-aligned (5-min default) so these map to
# 5 / 10 / 15 / 30 / 60 minutes for a 5m bar. The longest horizon also bounds the
# window used for path excursion (MFE/MAE).
HORIZONS = [1, 2, 3, 6, 12]
MAX_HORIZON = max(HORIZONS)

# ─────────────────────────────────────────────────────────
# Additional line parsers (not covered by extract_llm_signals)
# ─────────────────────────────────────────────────────────

# 🤖 LLM Context: px=2001.44 pos=flat qty=- upnl=- trend=strong_down
#                 rsi=28.24... rvol=1.34... vol_regime=normal tfi=-0.2281
LLM_CONTEXT_RE = re.compile(
    r"🤖 LLM Context:\s+"
    r"px=(?P<px>[\d.]+)\s+"
    r"pos=(?P<pos>\S+)\s+"
    r"qty=(?P<qty>\S+)\s+"
    r"upnl=(?P<upnl>\S+)\s+"
    r"trend=(?P<trend>\S+)\s+"
    r"rsi=(?P<rsi>[\deE.+-]+)\s+"
    r"rvol=(?P<rvol>[\deE.+-]+)\s+"
    r"vol_regime=(?P<vol_regime>\S+)\s+"
    r"tfi=(?P<tfi>[\deE.+-]+)"
)

# 🤖 Signal: HOLD | Confidence: MEDIUM | API time: 6.5s | Reason: ...
SIGNAL_SUMMARY_RE = re.compile(
    r"🤖 Signal:\s+(?P<signal>\S+)\s+\|\s+"
    r"Confidence:\s+(?P<conf>\S+)\s+\|\s+"
    r"API time:\s+(?P<api>[\d.]+)s"
)

# 📤 Submitted SELL market order: 6 ETH (reduce_only=True)
ORDER_SUBMIT_RE = re.compile(
    r"📤 Submitted (?P<side>BUY|SELL) market order:\s*(?P<qty>[\d.]+)\s+\S+\s+"
    r"\(reduce_only=(?P<ro>True|False)\)"
)
# ✅ Order filled: SELL 6.00 @ 1998.59 (ID: ...)
ORDER_FILL_RE = re.compile(
    r"✅ Order filled:\s*(?P<side>BUY|SELL)\s+(?P<qty>[\d.]+)\s*@\s*(?P<px>[\d.]+)"
)
# 🔴 / 🟢 Position closed: FLAT P&L: -23.40 USDT
POS_CLOSED_RE = re.compile(r"Position closed:\s*FLAT\s*P&L:\s*(?P<pnl>[-\d.]+)\s*USDT")
# 🟢 Position opened: LONG 6.00 @ 2000.29
POS_OPENED_RE = re.compile(
    r"Position opened:\s*(?P<side>LONG|SHORT)\s+(?P<qty>[\d.]+)\s*@\s*(?P<px>[\d.]+)"
)
# 📊 Position Sizing: ... (notional: $12003.06, equity_basis=$175616.97)
SIZING_RE = re.compile(
    r"\(notional:\s*\$(?P<notional>[\d,.]+),\s*equity_basis=\$(?P<equity>[\d,.]+)\)"
)
# 🎯 Creating bracket order ... Stop Loss: $1,991.78 ... Take Profit: $2,010.xx
BRACKET_SL_RE = re.compile(r"Stop Loss:\s*\$(?P<sl>[\d,.]+)")
BRACKET_TP_RE = re.compile(r"Take Profit:\s*\$(?P<tp>[\d,.]+)")
BRACKET_SOURCE_RE = re.compile(r"levels_source=(?P<source>\S+)")


def _to_float(value: Any) -> Optional[float]:
    """Permissive float coercion that maps placeholders ('-', '') to None."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "None", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_llm_context(msg: str) -> Optional[dict]:
    """Extract the compact context one-liner the strategy logs each cycle."""
    m = LLM_CONTEXT_RE.search(msg)
    if not m:
        return None
    pos_raw = m.group("pos")
    return {
        "ctx_price": _to_float(m.group("px")),
        "position_side": "flat" if pos_raw.lower() == "flat" else pos_raw.upper(),
        "position_qty": _to_float(m.group("qty")),
        "position_upnl": _to_float(m.group("upnl")),
        "ctx_trend": m.group("trend"),
        "ctx_rsi": _to_float(m.group("rsi")),
        "ctx_rvol": _to_float(m.group("rvol")),
        "volume_regime": m.group("vol_regime"),
        "trade_flow_imbalance": _to_float(m.group("tfi")),
    }


# ─────────────────────────────────────────────────────────
# Per-session extraction (state machine over chronological lines)
# ─────────────────────────────────────────────────────────

def extract_decisions(log_path: str) -> list[dict]:
    """
    Walk one session log and produce decision records.

    Each record is paired with:
      - the triggering bar-close (features) via `bar_index` into `bar_history`
      - the live context line (position state) preceding the call
      - the api latency + execution outcome lines following the call
    """
    bars: list[dict] = []          # chronological bar-close snapshots (close px etc.)
    pending_bar: Optional[dict] = None
    pending_ctx: Optional[dict] = None
    records: list[dict] = []

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = entry.get("message", "")
            ts = entry.get("timestamp", "")

            # ── Bar-close (features snapshot) ──
            bar = parse_bar_close(msg)
            if bar:
                bars.append(bar)
                pending_bar = bar
                pending_ctx = None  # new bar invalidates stale context
                continue

            # ── Context one-liner (position state) ──
            ctx = parse_llm_context(msg)
            if ctx:
                pending_ctx = ctx
                continue

            # ── Parsed LLM decision JSON ──
            llm = parse_llm_json(msg)
            if llm is not None:
                records.append({
                    "decision_ts": ts,
                    "bar_index": len(bars) - 1,   # triggering bar position
                    "bar": pending_bar,
                    "ctx": pending_ctx,
                    "llm": llm,
                    "api_time_s": None,
                    "execution_status": "unknown",
                    "order_submitted": False,
                    "reduce_only": None,
                    "order_qty": None,
                    "fill_price": None,
                    "realized_pnl": None,
                    "is_entry": False,
                    "entry_fill_price": None,
                    "bracket_sl": None,
                    "bracket_tp": None,
                    "bracket_levels_source": None,
                    "sized_notional_usd": None,
                    "equity_at_decision_usd": None,
                })
                continue

            if not records:
                continue
            cur = records[-1]

            # ── API latency / execution outcome lines (attach to latest decision) ──
            sig = SIGNAL_SUMMARY_RE.search(msg)
            if sig:
                cur["api_time_s"] = _to_float(sig.group("api"))
                continue

            sub = ORDER_SUBMIT_RE.search(msg)
            if sub:
                cur["order_submitted"] = True
                cur["execution_status"] = "executed"
                cur["order_qty"] = _to_float(sub.group("qty"))
                cur["reduce_only"] = sub.group("ro") == "True"
                continue

            fill = ORDER_FILL_RE.search(msg)
            if fill:
                cur["fill_price"] = _to_float(fill.group("px"))
                cur["execution_status"] = "executed"
                continue

            closed = POS_CLOSED_RE.search(msg)
            if closed:
                cur["realized_pnl"] = _to_float(closed.group("pnl"))
                continue

            opened = POS_OPENED_RE.search(msg)
            if opened:
                cur["is_entry"] = True
                cur["execution_status"] = "executed"
                cur["entry_fill_price"] = _to_float(opened.group("px"))
                cur["reduce_only"] = False
                continue

            if "Position Sizing" in msg:
                cur["execution_status"] = "executed"
                cur["is_entry"] = True
                szm = SIZING_RE.search(msg)
                if szm:
                    cur["sized_notional_usd"] = _to_float(szm.group("notional").replace(",", ""))
                    cur["equity_at_decision_usd"] = _to_float(szm.group("equity").replace(",", ""))
            elif "Creating bracket order" in msg:
                slm = BRACKET_SL_RE.search(msg)
                tpm = BRACKET_TP_RE.search(msg)
                source_match = BRACKET_SOURCE_RE.search(msg)
                if slm:
                    cur["bracket_sl"] = _to_float(slm.group("sl").replace(",", ""))
                if tpm:
                    cur["bracket_tp"] = _to_float(tpm.group("tp").replace(",", ""))
                if source_match:
                    cur["bracket_levels_source"] = source_match.group("source")
            elif "skipping trade" in msg:
                cur["execution_status"] = "skipped_low_conf"
            elif "No action taken" in msg:
                cur["execution_status"] = "hold_no_action"

    # Attach the within-session bar series so forward returns can be computed.
    for rec in records:
        rec["_bars"] = bars
    return records


# ─────────────────────────────────────────────────────────
# Row assembly (features + decision + forward price action)
# ─────────────────────────────────────────────────────────

def _signed_correct(signal: str, fwd_ret_pct: Optional[float]) -> Optional[int]:
    """1 if forward move agreed with an actionable signal, 0 if not, None if N/A."""
    if fwd_ret_pct is None or signal not in ("BUY", "SELL"):
        return None
    if signal == "BUY":
        return 1 if fwd_ret_pct > 0 else 0
    return 1 if fwd_ret_pct < 0 else 0


def build_row(rec: dict, prev_signal: Optional[str], consecutive: int) -> dict:
    """Flatten one decision record into a single CSV row."""
    bar = rec.get("bar") or {}
    ctx = rec.get("ctx") or {}
    llm = rec.get("llm") or {}
    bars = rec.get("_bars") or []
    idx = rec.get("bar_index", -1)

    entry_px = bar.get("price")
    if entry_px is None:
        entry_px = ctx.get("ctx_price")

    signal = llm.get("signal")

    row: dict[str, Any] = {
        # ── Identity ──
        "session_file": rec.get("session_file"),
        "decision_ts": (rec.get("decision_ts") or "")[:19].replace("T", " "),
        "bar_ts": (bar.get("bar_ts") or ""),
        "decision_idx": rec.get("decision_idx"),

        # ── Features the model saw ──
        "price": entry_px,
        "trend": bar.get("trend") or ctx.get("ctx_trend"),
        "rsi": bar.get("rsi") if bar.get("rsi") is not None else ctx.get("ctx_rsi"),
        "rvol": bar.get("rvol") if bar.get("rvol") is not None else ctx.get("ctx_rvol"),
        "trade_flow_imbalance": ctx.get("trade_flow_imbalance", bar.get("ob_tfi")),
        "volatility_regime": bar.get("regime"),
        "volume_regime": ctx.get("volume_regime"),

        # ── Position state at decision ──
        "position_side": ctx.get("position_side"),
        "position_qty": ctx.get("position_qty"),
        "position_upnl": ctx.get("position_upnl"),

        # ── LLM decision ──
        "signal": signal,
        "position_action": llm.get("position_action"),
        "confidence": llm.get("confidence"),
        "llm_regime": llm.get("regime"),
        "trend_strength": llm.get("trend_strength"),
        "risk_assessment": llm.get("risk_assessment"),
        "partial_close_pct": llm.get("partial_close_pct"),
        "stop_loss": llm.get("stop_loss"),
        "take_profit": llm.get("take_profit"),
        "is_fallback": bool(llm.get("is_fallback", False)),
        "api_time_s": rec.get("api_time_s"),

        # ── LLM free-text reasoning (prompt tuning) ──
        "thesis": _clean(llm.get("thesis")),
        "invalidation": _clean(llm.get("invalidation")),
        "execution_note": _clean(llm.get("execution_note")),
        "volume_note": _clean(llm.get("volume_note")),

        # ── Execution outcome ──
        "execution_status": rec.get("execution_status"),
        "trade_executed": rec.get("execution_status") == "executed",
        "order_intent": _order_intent(rec, signal),
        "reduce_only": rec.get("reduce_only"),
        "order_qty": rec.get("order_qty"),
        "fill_price": rec.get("fill_price"),
        "entry_fill_price": rec.get("entry_fill_price"),
        "bracket_sl": rec.get("bracket_sl"),
        "bracket_tp": rec.get("bracket_tp"),
        "bracket_levels_source": rec.get("bracket_levels_source"),
        "sized_notional_usd": rec.get("sized_notional_usd"),
        "equity_at_decision_usd": rec.get("equity_at_decision_usd"),
        "realized_pnl": rec.get("realized_pnl"),

        # ── Behavior ──
        "signal_changed": (prev_signal is not None and signal != prev_signal),
        "consecutive_same_signal": consecutive,
    }

    # ── Forward price action (close-based) ──
    fwd_closes: list[float] = []
    for h in range(1, MAX_HORIZON + 1):
        j = idx + h
        if 0 <= idx and j < len(bars):
            fwd_closes.append(bars[j]["price"])
        else:
            break
    row["bars_forward_available"] = len(fwd_closes)

    for h in HORIZONS:
        ret_key = f"fwd_ret_{h}b_pct"
        dir_key = f"correct_dir_{h}b"
        if entry_px and len(fwd_closes) >= h:
            fwd_px = fwd_closes[h - 1]
            ret = (fwd_px - entry_px) / entry_px * 100.0
            row[ret_key] = round(ret, 4)
            row[dir_key] = _signed_correct(signal, ret)
        else:
            row[ret_key] = None
            row[dir_key] = None

    # Path excursion over the available window (close-based proxy).
    if entry_px and fwd_closes:
        fwd_max = max(fwd_closes)
        fwd_min = min(fwd_closes)
        max_pct = (fwd_max - entry_px) / entry_px * 100.0
        min_pct = (fwd_min - entry_px) / entry_px * 100.0
        row["fwd_max_pct"] = round(max_pct, 4)
        row["fwd_min_pct"] = round(min_pct, 4)
        if signal == "BUY":
            row["mfe_pct"] = round(max_pct, 4)
            row["mae_pct"] = round(min_pct, 4)
        elif signal == "SELL":
            row["mfe_pct"] = round(-min_pct, 4)
            row["mae_pct"] = round(-max_pct, 4)
        else:
            row["mfe_pct"] = None
            row["mae_pct"] = None
    else:
        row["fwd_max_pct"] = None
        row["fwd_min_pct"] = None
        row["mfe_pct"] = None
        row["mae_pct"] = None

    return row


def _order_intent(rec: dict, signal: Optional[str]) -> str:
    """
    Classify what an order (if any) in this cycle represents.

      entry      -> new/added exposure (reduce_only=False)
      llm_exit   -> LLM-directed close/flip (BUY/SELL with reduce_only=True)
      auto_exit  -> protective bracket TP/SL fill during a HOLD cycle
      ""         -> no order this cycle
    """
    if rec.get("is_entry"):
        return "entry"
    if not rec.get("order_submitted") and rec.get("fill_price") is None:
        return ""
    if signal in ("BUY", "SELL"):
        return "llm_exit"
    return "auto_exit"


def _clean(text: Any) -> str:
    """Collapse whitespace/newlines in free-text so CSV cells stay single-line."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


# Column order (stable schema for downstream pipelines).
COLUMNS = [
    "session_file", "decision_ts", "bar_ts", "decision_idx",
    "price", "trend", "rsi", "rvol", "trade_flow_imbalance",
    "volatility_regime", "volume_regime",
    "position_side", "position_qty", "position_upnl",
    "signal", "position_action", "confidence", "llm_regime", "trend_strength", "risk_assessment",
    "partial_close_pct", "stop_loss", "take_profit", "is_fallback", "api_time_s",
    "execution_status", "trade_executed", "order_intent", "reduce_only",
    "order_qty", "fill_price", "entry_fill_price", "bracket_sl", "bracket_tp", "bracket_levels_source",
    "sized_notional_usd", "equity_at_decision_usd", "realized_pnl",
    "signal_changed", "consecutive_same_signal",
    "bars_forward_available",
    *[f"fwd_ret_{h}b_pct" for h in HORIZONS],
    *[f"correct_dir_{h}b" for h in HORIZONS],
    "fwd_max_pct", "fwd_min_pct", "mfe_pct", "mae_pct",
    "thesis", "invalidation", "execution_note", "volume_note",
]


# ─────────────────────────────────────────────────────────
# Collection across sessions
# ─────────────────────────────────────────────────────────

def collect_rows(days: int, max_sessions: int) -> tuple[list[dict], list[str]]:
    """Return (rows, session_files_used) honoring the day/session window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pattern = os.path.join(LOG_DIR, "deepseek_trader_*.json")
    all_files = sorted(glob.glob(pattern))

    session_results: list[tuple[str, list[dict]]] = []
    for fp in all_files:
        file_ts = log_file_timestamp(fp)
        if file_ts and file_ts < cutoff:
            continue
        decisions = extract_decisions(fp)
        if decisions:
            session_results.append((fp, decisions))

    if max_sessions > 0 and len(session_results) > max_sessions:
        skipped = len(session_results) - max_sessions
        session_results = session_results[-max_sessions:]
        print(f"  ⏭  Skipped {skipped} older session(s), keeping last {max_sessions}\n")

    rows: list[dict] = []
    used: list[str] = []
    for fp, decisions in session_results:
        name = os.path.basename(fp)
        used.append(name)
        prev_signal: Optional[str] = None
        consecutive = 0
        for i, rec in enumerate(decisions, 1):
            rec["session_file"] = name
            rec["decision_idx"] = i
            signal = (rec.get("llm") or {}).get("signal")
            if signal == prev_signal:
                consecutive += 1
            else:
                consecutive = 1
            rows.append(build_row(rec, prev_signal, consecutive))
            prev_signal = signal
        print(f"  ✓ {name:50s}  →  {len(decisions):>4d} decisions")
    return rows, used


# ─────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────

def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def write_dictionary(path: str, rows: list[dict], used: list[str]) -> None:
    """Emit a self-documenting data dictionary for the quant team."""
    n = len(rows)
    docs = [
        ("session_file", "Source log file (one trading session)."),
        ("decision_ts", "UTC timestamp when the LLM decision was logged."),
        ("bar_ts", "Close time of the 5-min bar that triggered the decision."),
        ("decision_idx", "1-based decision index within the session (chronological)."),
        ("price", "Bar-close price the model saw (decision entry reference)."),
        ("trend", "Composite trend label (strong_up/up/mixed/down/strong_down)."),
        ("rsi", "RSI at decision time."),
        ("rvol", "Relative volume (current vs rolling average)."),
        ("trade_flow_imbalance", "Signed trade-flow imbalance (+buy / -sell pressure)."),
        ("volatility_regime", "Volatility/market regime label (e.g. normal)."),
        ("volume_regime", "Volume regime label (e.g. normal/elevated)."),
        ("position_side", "Live position at decision: flat / LONG / SHORT."),
        ("position_qty", "Position size in base asset (None if flat)."),
        ("position_upnl", "Unrealized PnL of the open position (None if flat)."),
        ("signal", "LLM action: BUY / SELL / HOLD."),
        ("position_action", "Execution contract: ENTER_LONG / ENTER_SHORT / HOLD_POSITION / EXIT_NOW / NO_ACTION."),
        ("confidence", "LLM confidence: HIGH / MEDIUM / LOW."),
        ("llm_regime", "Free-text regime label the LLM assigned."),
        ("trend_strength", "LLM trend strength: STRONG / MODERATE / WEAK."),
        ("risk_assessment", "LLM risk read: LOW / MEDIUM / HIGH."),
        ("partial_close_pct", "Requested partial close fraction (0..1), if any."),
        ("stop_loss", "LLM stop_loss field (often a compat placeholder, not the live bracket)."),
        ("take_profit", "LLM take_profit field (compat placeholder; bracket is strategy-computed)."),
        ("is_fallback", "True if this was a conservative fallback (model failed/parse error)."),
        ("api_time_s", "DeepSeek API round-trip latency in seconds."),
        ("execution_status", "executed / skipped_low_conf / hold_no_action / unknown."),
        ("trade_executed", "True when any order filled during this decision cycle."),
        ("order_intent", "entry / llm_exit / auto_exit (bracket TP-SL on a HOLD) / '' none. "
                         "Use this to separate LLM-directed trades from protective auto-exits."),
        ("reduce_only", "True if the submitted order only reduced/closed exposure (exit/flip)."),
        ("order_qty", "Quantity (base asset) of the market order submitted (exits/flips)."),
        ("fill_price", "Actual average fill price of the order (real slippage reference)."),
        ("entry_fill_price", "Actual fill price when this decision OPENED a position (entries)."),
        ("bracket_sl", "Strategy-set bracket Stop-Loss price for an entry (the REAL SL, not the LLM placeholder)."),
        ("bracket_tp", "Strategy-set bracket Take-Profit price for an entry (the REAL TP)."),
        ("bracket_levels_source", "Selected protected bracket source: llm / structural / fallback_1pct."),
        ("sized_notional_usd", "Notional USD size committed on an entry (position sizing output)."),
        ("equity_at_decision_usd", "Account equity basis used for sizing at decision time."),
        ("realized_pnl", "Realized PnL (USDT) booked when a position closed in this cycle."),
        ("signal_changed", "True if signal differs from the previous decision (churn study)."),
        ("consecutive_same_signal", "Run length of the current signal up to this row."),
        ("bars_forward_available", f"How many forward bars existed (cap {MAX_HORIZON}); <h means horizon is right-censored."),
    ]
    for h in HORIZONS:
        docs.append((f"fwd_ret_{h}b_pct", f"Close-to-close % return {h} bar(s) after the decision."))
    for h in HORIZONS:
        docs.append((f"correct_dir_{h}b", f"1/0 if the {h}-bar move agreed with an actionable signal; blank for HOLD."))
    docs += [
        ("fwd_max_pct", f"Max close-based % gain over the next {MAX_HORIZON} bars (path peak)."),
        ("fwd_min_pct", f"Max close-based % drop over the next {MAX_HORIZON} bars (path trough)."),
        ("mfe_pct", "Max Favorable Excursion aligned to signal direction (actionable only)."),
        ("mae_pct", "Max Adverse Excursion aligned to signal direction (actionable only)."),
        ("thesis", "LLM reasoning for the decision (free text)."),
        ("invalidation", "What the LLM said would prove the thesis wrong."),
        ("execution_note", "LLM scaling/spread/friction-aware execution note."),
        ("volume_note", "LLM volume context note."),
    ]

    lines = [
        "# LLM Quant Dataset — Data Dictionary",
        "",
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"> Rows (decisions): {n}  ",
        f"> Sessions: {len(used)}  ",
        "",
        "## Sessions included",
        "",
    ]
    for s in used:
        lines.append(f"- `{s}`")
    lines += [
        "",
        "## Columns",
        "",
        "| Column | Description |",
        "|--------|-------------|",
    ]
    for col, desc in docs:
        lines.append(f"| `{col}` | {desc} |")

    lines += [
        "",
        "## Methodology & caveats",
        "",
        "- **One row = one LLM decision**, joining the feature snapshot the model saw, "
        "the live position state, the full decision + reasoning, the execution outcome, "
        "and multi-horizon forward price action.",
        "- **Forward returns are close-to-close** within the same session. Bars are "
        "5-min by default, so horizons 1/2/3/6/12 ≈ 5/10/15/30/60 minutes.",
        "- **MFE/MAE are close-based proxies.** Bar-close logs do not carry intrabar "
        "high/low, so true excursions (and stop/target touches) are not captured here. "
        "Treat these as lower-bound excursion estimates.",
        "- **Right-censoring:** rows near session end have fewer forward bars; check "
        "`bars_forward_available` before trusting longer-horizon columns.",
        "- **Directional accuracy is a screen, not a backtest.** It ignores fees, spread, "
        "slippage, holding time, and the actual bracket SL/TP. Round-trip friction is "
        "~13 bps; net edge requires forward moves clearly above that.",
        "- **Forward returns are not friction-adjusted.** Apply your own cost model "
        "(≈11 bps fees + live spread + buffer) before scoring net edge.",
        "- For deeper microstructure features (OFI, depth, multi-TF windows, full klines) "
        "raise the strategy log level to DEBUG to capture the full `LLM Prompt Payload`; "
        "INFO logs only expose the headline features represented here.",
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ─────────────────────────────────────────────────────────
# Console summary (sufficiency check)
# ─────────────────────────────────────────────────────────

def print_summary(rows: list[dict]) -> None:
    n = len(rows)
    if not n:
        return

    def count(pred) -> int:
        return sum(1 for r in rows if pred(r))

    print("\n" + "=" * 60)
    print("📊 DATASET SUMMARY")
    print("=" * 60)
    print(f"Total decisions : {n}")

    # Signal mix
    for sig in ("BUY", "SELL", "HOLD"):
        c = count(lambda r, s=sig: r["signal"] == s)
        print(f"  {sig:4s}: {c:4d} ({c/n*100:5.1f}%)")
    fb = count(lambda r: r["is_fallback"])
    print(f"  fallback rows: {fb}")

    # Execution (separate LLM-directed orders from protective auto-exits)
    entries = count(lambda r: r["order_intent"] == "entry")
    llm_exits = count(lambda r: r["order_intent"] == "llm_exit")
    auto_exits = count(lambda r: r["order_intent"] == "auto_exit")
    skipped = count(lambda r: r["execution_status"] == "skipped_low_conf")
    print(f"\nOrders filled   : entries={entries}  llm_exits={llm_exits}  "
          f"auto_exits(bracket)={auto_exits}")
    print(f"LOW-conf skips  : {skipped}")

    # Realized PnL on closes (actual booked outcomes)
    pnls = [r["realized_pnl"] for r in rows if r["realized_pnl"] is not None]
    if pnls:
        wins = sum(1 for p in pnls if p > 0)
        print(f"Closed positions: {len(pnls)}  |  net realized PnL: {sum(pnls):+.2f} USDT  "
              f"|  win rate: {wins}/{len(pnls)} ({wins/len(pnls)*100:.0f}%)")

    # Latency
    apis = [r["api_time_s"] for r in rows if r["api_time_s"] is not None]
    if apis:
        print(f"API latency     : avg {sum(apis)/len(apis):.1f}s  "
              f"min {min(apis):.1f}s  max {max(apis):.1f}s")

    # Forward-return coverage and directional accuracy by horizon
    print("\nDirectional accuracy (actionable BUY/SELL) by horizon:")
    actionable = [r for r in rows if r["signal"] in ("BUY", "SELL")]
    for h in HORIZONS:
        key = f"correct_dir_{h}b"
        vals = [r[key] for r in actionable if r[key] is not None]
        if vals:
            acc = sum(vals) / len(vals) * 100
            print(f"  {h:2d}-bar: {sum(vals):3d}/{len(vals):3d} correct ({acc:5.1f}%)")
        else:
            print(f"  {h:2d}-bar: no data")

    # Mean forward return by signal at primary horizon (3 bars)
    print("\nMean fwd_ret_3b_pct by signal (gross, pre-cost):")
    for sig in ("BUY", "SELL", "HOLD"):
        vals = [r["fwd_ret_3b_pct"] for r in rows
                if r["signal"] == sig and r["fwd_ret_3b_pct"] is not None]
        if vals:
            print(f"  {sig:4s}: {sum(vals)/len(vals):+.4f}%  (n={len(vals)})")

    # Confidence calibration (3-bar accuracy on actionable)
    print("\nConfidence calibration (3-bar dir accuracy, actionable):")
    for conf in ("HIGH", "MEDIUM", "LOW"):
        vals = [r["correct_dir_3b"] for r in actionable
                if r["confidence"] == conf and r["correct_dir_3b"] is not None]
        if vals:
            print(f"  {conf:6s}: {sum(vals)/len(vals)*100:5.1f}%  (n={len(vals)})")
    print("=" * 60)


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a wide, quant-ready CSV of LLM decisions + forward price action."
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help="How many days back to scan (default 14).")
    parser.add_argument("--sessions", type=int, default=0,
                        help="Keep only the last N sessions with decisions (0 = all).")
    parser.add_argument("--output", type=str, default=DEFAULT_CSV,
                        help="Output CSV path.")
    parser.add_argument("--dict", type=str, default=None,
                        help="Data-dictionary markdown path (default: alongside CSV).")
    args = parser.parse_args()

    dict_path = args.dict or (os.path.splitext(args.output)[0] + "_dictionary.md")

    scope = f"last {args.sessions} sessions" if args.sessions else f"last {args.days} days"
    print(f"🔍 Building quant dataset — {scope}")
    print(f"   Log directory: {LOG_DIR}\n")

    rows, used = collect_rows(days=args.days, max_sessions=args.sessions)
    if not rows:
        print("❌ No LLM decisions found in the specified window.")
        sys.exit(1)

    write_csv(rows, args.output)
    write_dictionary(dict_path, rows, used)

    print_summary(rows)
    print(f"\n✅ Dataset written : {args.output}  ({len(rows)} rows × {len(COLUMNS)} cols)")
    print(f"✅ Data dictionary : {dict_path}")


if __name__ == "__main__":
    main()
