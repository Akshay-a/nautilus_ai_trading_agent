#!/usr/bin/env python3
"""
Extract LLM Signal Logs with Price Context
============================================
Parses deepseek_trader JSON log files, extracts every LLM decision,
pairs it with the triggering 5-min bar-close price (BEFORE) and the
next bar-close price (AFTER), and produces a chronologically sorted
markdown report suitable for downstream quant analysis & prompt tuning.

Usage:
    python scripts/extract_llm_signals.py [--days N] [--output PATH]

Output:  logs/llm_signal_audit.md  (default)
"""

import json
import re
import os
import sys
import glob
from datetime import datetime, timedelta, timezone
from collections import OrderedDict
from typing import Optional

# ─────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
DEFAULT_OUTPUT = os.path.join(LOG_DIR, "llm_signal_audit.md")
DEFAULT_DAYS = 14  # how far back to look

# ─────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────

BAR_CLOSE_RE = re.compile(
    r"📌 Bar-close @ (?P<bar_ts>\S+)\s+"
    r"px=\$(?P<price>[\d,.]+)\s+"
    r"trend=(?P<trend>\S+)\s+"
    r"rsi=(?P<rsi>[\d.]+)\s+"
    r"rvol=(?P<rvol>[\d.]+)\s+"
    r"ob_tfi=(?P<ob_tfi>[+\-\d.]+)\s+"
    r"regime=(?P<regime>\S+)"
)

LLM_JSON_RE = re.compile(r"🤖 LLM Response JSON: (.+)")
LLM_RAW_RE  = re.compile(r"🤖 DeepSeek Raw Response: (.+)")
LLM_CALL_RE = re.compile(r"Calling DeepSeek AI")


def parse_bar_close(msg: str) -> Optional[dict]:
    """Extract bar-close fields from log message."""
    m = BAR_CLOSE_RE.search(msg)
    if not m:
        return None
    return {
        "bar_ts":  m.group("bar_ts"),
        "price":   float(m.group("price").replace(",", "")),
        "trend":   m.group("trend"),
        "rsi":     float(m.group("rsi")),
        "rvol":    float(m.group("rvol")),
        "ob_tfi":  float(m.group("ob_tfi")),
        "regime":  m.group("regime"),
    }


def parse_llm_json(msg: str) -> Optional[dict]:
    """Extract the parsed LLM JSON from the 'LLM Response JSON' line."""
    m = LLM_JSON_RE.search(msg)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def log_file_timestamp(path: str) -> Optional[datetime]:
    """Extract session start datetime from filename like deepseek_trader_2026-05-29_120816:248.json"""
    base = os.path.basename(path)
    m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{6})", base)
    if not m:
        return None
    return datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y-%m-%d_%H%M%S").replace(tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────
# Main extraction
# ─────────────────────────────────────────────────────────

def extract_signals(log_path: str) -> list[dict]:
    """
    Walk through a single log file and produce a list of signal records.
    Each record = { bar_before, bar_after, llm_response, log_timestamp, session_file }
    """
    records = []
    bar_history = []  # chronological list of bar-close dicts
    pending_bar = None  # the bar that triggered the next LLM call

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
            ts  = entry.get("timestamp", "")

            # ── Bar-close ──
            bar = parse_bar_close(msg)
            if bar:
                bar_history.append(bar)
                pending_bar = bar
                continue

            # ── LLM call marker ──
            if LLM_CALL_RE.search(msg):
                # pending_bar is already set from the bar-close just above
                continue

            # ── LLM parsed response ──
            llm = parse_llm_json(msg)
            if llm:
                rec = {
                    "log_timestamp":  ts,
                    "session_file":   os.path.basename(log_path),
                    "llm":            llm,
                    "bar_before":     pending_bar,       # the bar that triggered this analysis
                    "bar_after":      None,              # filled in post-pass
                    "bar_index":      len(bar_history),  # current position in bar list
                }
                records.append(rec)
                pending_bar = None
                continue

    # ── Post-pass: fill bar_after (the NEXT bar-close after LLM response) ──
    for rec in records:
        idx = rec["bar_index"]
        if idx < len(bar_history):
            rec["bar_after"] = bar_history[idx]
        del rec["bar_index"]

    return records


def collect_all_signals(days: int = DEFAULT_DAYS, max_sessions: int = 0) -> list[dict]:
    """Scan all recent log files and return sorted signals.

    Args:
        days: How many days back to scan.
        max_sessions: If > 0, keep only the last N session files that
                      contain at least one LLM signal.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pattern = os.path.join(LOG_DIR, "deepseek_trader_*.json")
    all_files = sorted(glob.glob(pattern))

    # First pass: identify files in the date window that have signals
    session_results: list[tuple[str, list[dict]]] = []
    for fp in all_files:
        file_ts = log_file_timestamp(fp)
        if file_ts and file_ts < cutoff:
            continue
        signals = extract_signals(fp)
        if signals:
            session_results.append((fp, signals))

    # Trim to last N sessions if requested
    if max_sessions > 0 and len(session_results) > max_sessions:
        skipped = len(session_results) - max_sessions
        session_results = session_results[-max_sessions:]
        print(f"  ⏭  Skipped {skipped} older session(s), keeping last {max_sessions}\n")

    all_signals = []
    for fp, signals in session_results:
        all_signals.extend(signals)
        print(f"  ✓ {os.path.basename(fp):50s}  →  {len(signals):>4d} LLM signals")

    # Sort by log timestamp
    all_signals.sort(key=lambda r: r["log_timestamp"])
    return all_signals


# ─────────────────────────────────────────────────────────
# Markdown report generation
# ─────────────────────────────────────────────────────────

SIGNAL_EMOJI = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪", "CLOSE": "🟡"}
CONFIDENCE_EMOJI = {"HIGH": "🔥", "MEDIUM": "⚡", "LOW": "💤"}

def fmt_price(p: Optional[float]) -> str:
    return f"${p:,.2f}" if p else "—"


def fmt_delta(before: Optional[float], after: Optional[float]) -> str:
    if before is None or after is None:
        return "—"
    delta = after - before
    pct   = (delta / before) * 100
    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    color = "green" if delta > 0 else ("red" if delta < 0 else "gray")
    return f"{arrow} {delta:+.2f} ({pct:+.3f}%)"


def generate_report(signals: list[dict], output_path: str):
    """Write the markdown audit report."""

    # ── Statistics ──
    total     = len(signals)
    buys      = sum(1 for s in signals if s["llm"].get("signal") == "BUY")
    sells     = sum(1 for s in signals if s["llm"].get("signal") == "SELL")
    holds     = sum(1 for s in signals if s["llm"].get("signal") == "HOLD")
    closes    = sum(1 for s in signals if s["llm"].get("signal") == "CLOSE")
    high_conf = sum(1 for s in signals if s["llm"].get("confidence") == "HIGH")
    med_conf  = sum(1 for s in signals if s["llm"].get("confidence") == "MEDIUM")
    low_conf  = sum(1 for s in signals if s["llm"].get("confidence") == "LOW")

    # ── Regime distribution ──
    regime_counts: dict[str, int] = {}
    for s in signals:
        r = s["llm"].get("regime", "unknown")
        regime_counts[r] = regime_counts.get(r, 0) + 1
    regime_sorted = sorted(regime_counts.items(), key=lambda x: -x[1])

    # ── Price move tracking for actionable signals ──
    correct_direction = 0
    wrong_direction   = 0
    no_data           = 0
    for s in signals:
        sig  = s["llm"].get("signal")
        if sig not in ("BUY", "SELL"):
            continue
        bb = s.get("bar_before")
        ba = s.get("bar_after")
        if not bb or not ba:
            no_data += 1
            continue
        delta = ba["price"] - bb["price"]
        if (sig == "BUY" and delta > 0) or (sig == "SELL" and delta < 0):
            correct_direction += 1
        else:
            wrong_direction += 1

    # ── Session breakdown ──
    sessions: dict[str, list] = {}
    for s in signals:
        sf = s["session_file"]
        sessions.setdefault(sf, []).append(s)

    # ── Build report ──
    lines = []
    w = lines.append

    w("# 🤖 LLM Signal Audit — DeepSeek AI Trading Agent")
    w("")
    w(f"> **Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    w(f"> **Total Signals**: {total}  ")
    w(f"> **Sessions Analyzed**: {len(sessions)}  ")
    if signals:
        ts_first = signals[0]["log_timestamp"][:19].replace("T", " ")
        ts_last  = signals[-1]["log_timestamp"][:19].replace("T", " ")
        w(f"> **Date Range**: `{ts_first}` → `{ts_last}`  ")
    w("")
    w("---")
    w("")

    # ── Summary table ──
    w("## 📊 Signal Distribution Summary")
    w("")
    w("| Metric | Count | % |")
    w("|--------|------:|---:|")
    w(f"| 🟢 BUY  | {buys} | {buys/total*100:.1f}% |")
    w(f"| 🔴 SELL | {sells} | {sells/total*100:.1f}% |")
    w(f"| ⚪ HOLD | {holds} | {holds/total*100:.1f}% |")
    w(f"| 🟡 CLOSE | {closes} | {closes/total*100:.1f}% |")
    w(f"| **Total** | **{total}** | **100%** |")
    w("")

    w("### Confidence Breakdown")
    w("")
    w("| Confidence | Count | % |")
    w("|------------|------:|---:|")
    w(f"| 🔥 HIGH   | {high_conf} | {high_conf/total*100:.1f}% |")
    w(f"| ⚡ MEDIUM  | {med_conf} | {med_conf/total*100:.1f}% |")
    w(f"| 💤 LOW    | {low_conf} | {low_conf/total*100:.1f}% |")
    w("")

    # ── Directional accuracy ──
    actionable = correct_direction + wrong_direction
    if actionable > 0:
        w("### 🎯 Directional Accuracy (Actionable Signals Only)")
        w("")
        w(f"- **Correct direction (next bar)**: {correct_direction}/{actionable} "
          f"({correct_direction/actionable*100:.1f}%)")
        w(f"- **Wrong direction (next bar)**: {wrong_direction}/{actionable} "
          f"({wrong_direction/actionable*100:.1f}%)")
        if no_data:
            w(f"- Missing price data: {no_data}")
        w("")
        w("> ⚠️ This is a *naive* 1-bar directional check. It does not account for stop-loss, "
          "take-profit, or multi-bar thesis. Use for signal quality screening only.")
        w("")

    # ── Regime distribution ──
    w("### 🌐 Regime Distribution")
    w("")
    w("| Regime | Count |")
    w("|--------|------:|")
    for regime, cnt in regime_sorted[:15]:
        w(f"| `{regime}` | {cnt} |")
    w("")

    w("---")
    w("")

    # ── Session-by-session detail ──
    w("## 📋 Signal Log — Chronological (All Sessions)")
    w("")
    w("Each entry shows:")
    w("- **Bar BEFORE**: The 5-min candle close that triggered the LLM call")
    w("- **LLM Decision**: Full signal JSON with thesis, invalidation, and notes")
    w("- **Bar AFTER**: The next 5-min candle close (price reaction)")
    w("- **Δ Price**: Change from bar-before to bar-after")
    w("")

    for session_file, session_signals in sessions.items():
        w(f"### 📁 Session: `{session_file}`")
        w(f"*{len(session_signals)} signals*")
        w("")

        for i, s in enumerate(session_signals, 1):
            llm    = s["llm"]
            bb     = s.get("bar_before")
            ba     = s.get("bar_after")
            sig    = llm.get("signal", "?")
            conf   = llm.get("confidence", "?")
            sig_e  = SIGNAL_EMOJI.get(sig, "❓")
            conf_e = CONFIDENCE_EMOJI.get(conf, "")

            # Header
            ts_display = s["log_timestamp"][:19].replace("T", " ")
            w(f"#### {sig_e} Signal #{i}: **{sig}** ({conf} {conf_e}) — `{ts_display} UTC`")
            w("")

            # Bar context table
            w("| | Timestamp | Price | Trend | RSI | rVol | OB-TFI | Regime |")
            w("|---|-----------|------:|-------|----:|-----:|-------:|--------|")
            if bb:
                w(f"| **BEFORE** | `{bb['bar_ts']}` | {fmt_price(bb['price'])} | "
                  f"{bb['trend']} | {bb['rsi']:.1f} | {bb['rvol']:.2f} | {bb['ob_tfi']:+.2f} | {bb['regime']} |")
            else:
                w("| **BEFORE** | — | — | — | — | — | — | — |")
            if ba:
                w(f"| **AFTER**  | `{ba['bar_ts']}` | {fmt_price(ba['price'])} | "
                  f"{ba['trend']} | {ba['rsi']:.1f} | {ba['rvol']:.2f} | {ba['ob_tfi']:+.2f} | {ba['regime']} |")
            else:
                w("| **AFTER**  | — | — | — | — | — | — | — |")

            # Price delta
            before_px = bb["price"] if bb else None
            after_px  = ba["price"] if ba else None
            w(f"| **Δ Price** | | **{fmt_delta(before_px, after_px)}** | | | | | |")
            w("")

            # LLM Decision details
            w("**LLM Decision:**")
            w("")
            w(f"- **Regime**: `{llm.get('regime', '—')}`")
            w(f"- **Thesis**: {llm.get('thesis', '—')}")
            w(f"- **Invalidation**: {llm.get('invalidation', '—')}")
            w(f"- **Execution Note**: {llm.get('execution_note', '—')}")
            w(f"- **Volume Note**: {llm.get('volume_note', '—')}")
            w(f"- **Risk Assessment**: {llm.get('risk_assessment', '—')}")
            w(f"- **Trend Strength**: {llm.get('trend_strength', '—')}")
            partial = llm.get('partial_close_pct')
            if partial is not None:
                w(f"- **Partial Close %**: {partial}")
            entry_px = llm.get('entry_price')
            if entry_px:
                w(f"- **Entry Price**: {entry_px}")
            sl = llm.get('stop_loss')
            if sl:
                w(f"- **Stop Loss**: {sl}")
            tp = llm.get('take_profit')
            if tp:
                w(f"- **Take Profit**: {tp}")
            w("")
            w("---")
            w("")

    # ── Appendix: raw JSON dump for downstream pipelines ──
    w("## 📦 Appendix: Machine-Readable JSON Dump")
    w("")
    w("The following JSON array can be consumed by downstream quant pipelines for further analysis:")
    w("")
    w("```json")

    json_dump = []
    for s in signals:
        entry = {
            "timestamp":    s["log_timestamp"],
            "session":      s["session_file"],
            "signal":       s["llm"].get("signal"),
            "confidence":   s["llm"].get("confidence"),
            "regime":       s["llm"].get("regime"),
            "thesis":       s["llm"].get("thesis"),
            "invalidation": s["llm"].get("invalidation"),
            "execution_note": s["llm"].get("execution_note"),
            "volume_note":  s["llm"].get("volume_note"),
            "risk_assessment": s["llm"].get("risk_assessment"),
            "trend_strength": s["llm"].get("trend_strength"),
            "partial_close_pct": s["llm"].get("partial_close_pct"),
            "entry_price":  s["llm"].get("entry_price"),
            "stop_loss":    s["llm"].get("stop_loss"),
            "take_profit":  s["llm"].get("take_profit"),
        }
        bb = s.get("bar_before")
        ba = s.get("bar_after")
        if bb:
            entry["bar_before"] = {
                "timestamp": bb["bar_ts"],
                "price":     bb["price"],
                "trend":     bb["trend"],
                "rsi":       bb["rsi"],
                "rvol":      bb["rvol"],
                "ob_tfi":    bb["ob_tfi"],
                "regime":    bb["regime"],
            }
        if ba:
            entry["bar_after"] = {
                "timestamp": ba["bar_ts"],
                "price":     ba["price"],
                "trend":     ba["trend"],
                "rsi":       ba["rsi"],
                "rvol":      ba["rvol"],
                "ob_tfi":    ba["ob_tfi"],
                "regime":    ba["regime"],
            }
            if bb:
                delta = ba["price"] - bb["price"]
                entry["price_delta"]     = round(delta, 2)
                entry["price_delta_pct"] = round((delta / bb["price"]) * 100, 4)
        json_dump.append(entry)

    w(json.dumps(json_dump, indent=2))
    w("```")
    w("")

    # Write
    with open(output_path, "w", encoding="utf-8") as out:
        out.write("\n".join(lines))

    print(f"\n✅ Report written to: {output_path}")
    print(f"   Total signals: {total}")
    print(f"   Sessions: {len(sessions)}")
    if actionable > 0:
        print(f"   Directional accuracy: {correct_direction}/{actionable} "
              f"({correct_direction/actionable*100:.1f}%)")


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract LLM signals with price context")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="How many days back to scan")
    parser.add_argument("--sessions", type=int, default=0,
                        help="Keep only the last N sessions (files with signals). 0 = all.")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT, help="Output file path")
    args = parser.parse_args()

    scope = f"last {args.sessions} sessions" if args.sessions else f"last {args.days} days"
    print(f"🔍 Scanning logs — {scope}...")
    print(f"   Log directory: {LOG_DIR}\n")

    signals = collect_all_signals(days=args.days, max_sessions=args.sessions)
    if not signals:
        print("❌ No LLM signals found in the specified date range.")
        sys.exit(1)

    generate_report(signals, args.output)


if __name__ == "__main__":
    main()
