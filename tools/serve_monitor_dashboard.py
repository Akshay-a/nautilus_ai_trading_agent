#!/usr/bin/env python3
"""
Lightweight monitoring dashboard for DeepSeek strategy.

Reads existing Nautilus JSON logs and exposes:
- GET /          -> simple HTML dashboard
- GET /api/status -> JSON snapshot for automation
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from glob import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
ENV_FILE = ROOT / ".env"
PID_FILE = ROOT / "trader.pid"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.bybit_account_context import BybitAccountContextFetcher

RE_SIGNAL = re.compile(
    r"🤖 Signal:\s*(BUY|SELL|HOLD)\s*\|\s*Confidence:\s*(LOW|MEDIUM|HIGH)\s*\|\s*API time:\s*([0-9.]+)s\s*\|\s*Reason:\s*(.*)"
)
RE_PRICE = re.compile(r"Current Price:\s*\$([0-9,]+(?:\.[0-9]+)?)")
RE_RSI = re.compile(r"RSI:\s*([0-9]+(?:\.[0-9]+)?)")
RE_POSITION_OPENED = re.compile(
    r"🟢 Position opened:\s*(LONG|SHORT)\s*([0-9.]+)\s*@\s*([0-9.]+)"
)
RE_POSITION_CLOSED = re.compile(r"🔴 Position closed:\s*(LONG|SHORT)\s*P&L:\s*([+-]?[0-9.]+)\s*USDT")
RE_WARMUP = re.compile(r"Received\s+([0-9]+)\s+warmup bars")
RE_POSITION_NET = re.compile(r"net_position=([+-]?[0-9]+(?:\.[0-9]+)?)")
RE_POSITION_AVG = re.compile(r"Position avg_px verified .*internal=([0-9]+(?:\.[0-9]+)?)")
RE_LLM_PROMPT_PAYLOAD = re.compile(r"🤖 LLM Prompt Payload:\s*(\{.*\})$")
RE_LLM_RESPONSE_JSON = re.compile(r"🤖 LLM Response JSON:\s*(\{.*\})$")
RE_LLM_RAW_RESPONSE = re.compile(r"🤖 DeepSeek Raw Response:\s*(.+)", re.DOTALL)
RE_BAR_CLOSE = re.compile(
    r"📌 Bar-close @ (?P<bar_ts>\S+)\s+"
    r"px=\$(?P<price>[\d,.]+)\s+"
    r"trend=(?P<trend>\S+)\s+"
    r"rsi=(?P<rsi>[\d.]+)\s+"
    r"rvol=(?P<rvol>[\d.]+)\s+"
    r"ob_tfi=(?P<ob_tfi>[+\-\d.]+)\s+"
    r"regime=(?P<ob_regime>\S+)"
)
RE_POSITION_CURRENT = re.compile(
    r"Current Position:\s*(?P<side>long|short)\s*(?P<qty>[0-9.]+)(?:\s+\w+)?\s*@\s*\$"
    r"(?P<entry>[0-9,]+(?:\.[0-9]+)?)"
    r"(?:\s+uPnL=(?P<upnl>[+-]?[0-9.]+))?"
    r"(?:\s+health=(?P<health>[a-z_]+))?",
    re.IGNORECASE,
)

_LLM_SYNTHESIS_KEYS = (
    "signal",
    "confidence",
    "position_action",
    "regime",
    "playbook",
    "thesis",
    "hold_reason",
    "setup_type",
    "thesis_state",
    "prior_trigger_status",
    "watch_trigger",
    "watch_trigger_price",
    "watch_trigger_direction",
    "watch_trigger_expiry_bars",
    "invalidation",
    "invalidation_price",
    "execution_note",
    "volume_note",
    "risk_assessment",
    "trend_strength",
    "target_r",
)


@dataclass
class PositionState:
    side: str
    quantity: float
    entry_price: Optional[float]
    unrealized_pnl: Optional[float] = None
    health: Optional[str] = None


def _latest_json_log() -> Optional[Path]:
    """
    Return the log file most recently modified (not just alphabetically last).

    Nautilus rotates files as:
      deepseek_trader_TIMESTAMP.json   <- current active file
      deepseek_trader_TIMESTAMP.json.1 <- first backup
      deepseek_trader_TIMESTAMP.json.2 <- second backup

    After rotation the SAME base name is reused for the active file, so
    alphabetical sort is unreliable.  Sort by mtime instead.
    """
    # Include both .json and .json.N backup files
    candidates = glob(str(LOG_DIR / "deepseek_trader_*.json"))
    candidates += glob(str(LOG_DIR / "deepseek_trader_*.json.[0-9]*"))
    if not candidates:
        return None
    # Pick the file modified most recently
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return Path(candidates[0])


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        values[key] = val
    return values


def _collect_bybit_exchange_context(env_cfg: Dict[str, str], instrument_id: str) -> Dict[str, Any]:
    """
    Fetch direct read-only Bybit context for portfolio/order/trade visibility.

    The dashboard should make exchange/log mismatches obvious instead of hiding
    behind log-derived state only.
    """
    fetcher = BybitAccountContextFetcher.from_env(
        instrument_id=instrument_id,
        env=env_cfg,
    )
    if fetcher is None:
        return {
            "ok": False,
            "errors": [{"error": "BYBIT_API_KEY/BYBIT_API_SECRET not configured"}],
            "wallet": None,
            "position": None,
            "open_orders": [],
            "recent_executions": [],
            "recent_closed_pnl": [],
            "recent_trade_summary": {},
        }

    try:
        return fetcher.fetch()
    except Exception as exc:
        return {
            "ok": False,
            "source": "bybit_v5",
            "mode": {
                "demo": fetcher.demo,
                "testnet": fetcher.testnet,
                "endpoint": fetcher.base_url,
            },
            "instrument_id": instrument_id,
            "symbol": fetcher.symbol,
            "errors": [{"error": f"{type(exc).__name__}: {exc}"}],
            "wallet": None,
            "position": None,
            "open_orders": [],
            "recent_executions": [],
            "recent_closed_pnl": [],
            "recent_trade_summary": {},
        }


def _strategy_process_state() -> Dict[str, Any]:
    def _is_pid_alive(candidate_pid: int) -> bool:
        """Portable liveness probe that does not depend on `ps`/`pgrep`."""
        try:
            os.kill(candidate_pid, 0)
            return True
        except Exception:
            return False

    running = False
    pid: Optional[int] = None

    if PID_FILE.exists():
        try:
            candidate = int(PID_FILE.read_text(encoding="utf-8").strip())
            if _is_pid_alive(candidate):
                pid = candidate
                running = True
        except Exception:
            pid = None

    return {"running": running, "pid": pid}


def _llm_synthesis_snapshot(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract present LLM synthesis fields; omit empty values for backward compatibility."""
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in _LLM_SYNTHESIS_KEYS:
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        out[key] = val
    if "thesis" not in out:
        reason = data.get("reason")
        if isinstance(reason, str) and reason.strip():
            out["thesis"] = reason.strip()
    return out


def _merge_current_llm(metrics: Dict[str, Any], patch: Dict[str, Any]) -> None:
    current = metrics.get("current_llm")
    if not isinstance(current, dict):
        current = {}
    current.update(patch)
    metrics["current_llm"] = current


def _position_alignment(
    log_pos: Optional[Dict[str, Any]],
    ex_pos: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare strategy-log position vs exchange snapshot."""
    log_side = str((log_pos or {}).get("side") or "").upper()
    ex_side = str((ex_pos or {}).get("side") or "").upper()
    log_qty = (log_pos or {}).get("quantity")
    ex_qty = (ex_pos or {}).get("quantity")

    log_open = bool(log_side and isinstance(log_qty, (int, float)) and log_qty > 0)
    ex_open = bool(ex_side and isinstance(ex_qty, (int, float)) and ex_qty > 0)

    if not log_open and not ex_open:
        state = "flat"
    elif log_open and not ex_open:
        state = "log_only"
    elif ex_open and not log_open:
        state = "exchange_only"
    elif log_side == ex_side:
        state = "aligned"
    else:
        state = "side_mismatch"

    return {
        "state": state,
        "log_open": log_open,
        "exchange_open": ex_open,
        "log_side": log_side or None,
        "exchange_side": ex_side or None,
    }


def _session_base_name(path_str: str) -> str:
    """
    Normalize rotated and non-rotated log path into one session key.

    Examples:
    - deepseek_trader_x.json   -> deepseek_trader_x.json
    - deepseek_trader_x.json.1 -> deepseek_trader_x.json
    """
    p = Path(path_str)
    if p.suffix.lstrip(".").isdigit():
        return str(p.with_suffix(""))
    return str(p)


def _session_log_files(target_pid: Optional[int] = None) -> List[Path]:
    """
    Return all log files for the current session, oldest → newest.

    Nautilus rotates files in-place: when a file exceeds max_size it is
    renamed to .json.1 (.json.2, etc.) and the SAME base name is used for
    fresh output.  We therefore collect the base file + its numbered
    backups, then sort by mtime (oldest first) so we replay events in
    chronological order.
    """
    candidates = glob(str(LOG_DIR / "deepseek_trader_*.json"))
    candidates += glob(str(LOG_DIR / "deepseek_trader_*.json.[0-9]*"))
    if not candidates:
        return []

    sessions: Dict[str, List[str]] = {}
    for p in candidates:
        key = _session_base_name(p)
        sessions.setdefault(key, []).append(p)

    def _sorted_session_files(files: List[str]) -> List[Path]:
        files = sorted(files, key=lambda p: os.path.getmtime(p))
        return [Path(p) for p in files]

    # If we know active PID, pin to the session whose startup banner includes that PID.
    if target_pid is not None:
        pid_token = f"PID: {target_pid}"
        for files in sessions.values():
            for f in _sorted_session_files(files):
                try:
                    with f.open("r", encoding="utf-8", errors="replace") as fh:
                        for idx, line in enumerate(fh):
                            if pid_token in line:
                                return _sorted_session_files(files)
                            if idx > 1200:
                                break
                except Exception:
                    continue

    # Fallback: newest session by latest file mtime.
    newest_session = max(
        sessions.values(),
        key=lambda files: max(os.path.getmtime(p) for p in files),
    )
    return _sorted_session_files(newest_session)


def _parse_log_metrics(log_path: Optional[Path], target_pid: Optional[int] = None) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {
        "log_file": str(log_path) if log_path else None,
        "log_timestamp_utc": None,
        "strategy_started": False,
        "strategy_running_log": False,
        "warmup_bars": None,
        "analysis_cycles": 0,
        "deepseek_calls": 0,
        "last_signal": None,
        "last_signal_reason": None,
        "last_signal_api_sec": None,
        "last_price": None,
        "last_trend": None,
        "last_rsi": None,
        "open_position": None,
        "closed_trades": 0,
        "realized_pnl_usdt": 0.0,
        "dry_run_events": 0,
        "recent_events": [],
        "recent_warnings_errors": [],
        "llm_conversations": [],
        "bar_close": None,
        "current_llm": None,
    }

    # Read all rotated files for this session in chronological order
    all_files = _session_log_files(target_pid=target_pid)
    if not all_files:
        if log_path is not None and log_path.exists():
            all_files = [log_path]
        else:
            return metrics

    metrics["log_file"] = str(all_files[-1])  # show active file in UI

    position: Optional[PositionState] = None
    last_reconciled_avg_px: Optional[float] = None
    pending_prompt: Optional[Dict[str, Any]] = None

    for file_path in all_files:
        if not file_path.exists():
            continue
        with file_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp")
                msg = entry.get("message", "")
                level = entry.get("level", "INFO")

                if ts:
                    metrics["log_timestamp_utc"] = ts

                if "Strategy started successfully" in msg:
                    metrics["strategy_started"] = True
                if msg == "RUNNING":
                    metrics["strategy_running_log"] = True

                m_warmup = RE_WARMUP.search(msg)
                if m_warmup:
                    metrics["warmup_bars"] = int(m_warmup.group(1))
                # Also catch the actual prefetch log format
                m_prefetch = re.search(r"Pre-fetched\s+([0-9]+)\s+bars", msg)
                if m_prefetch:
                    metrics["warmup_bars"] = int(m_prefetch.group(1))

                if "Running periodic analysis..." in msg:
                    metrics["analysis_cycles"] += 1

                m_price = RE_PRICE.search(msg)
                if m_price:
                    metrics["last_price"] = float(m_price.group(1).replace(",", ""))

                if msg.startswith("Overall Trend:"):
                    metrics["last_trend"] = msg.split(":", 1)[1].strip()

                m_rsi = RE_RSI.search(msg)
                if m_rsi:
                    metrics["last_rsi"] = float(m_rsi.group(1))

                m_bar = RE_BAR_CLOSE.search(msg)
                if m_bar:
                    metrics["bar_close"] = {
                        "bar_ts": m_bar.group("bar_ts"),
                        "price": float(m_bar.group("price").replace(",", "")),
                        "trend": m_bar.group("trend"),
                        "rsi": float(m_bar.group("rsi")),
                        "rvol": float(m_bar.group("rvol")),
                        "ob_tfi": float(m_bar.group("ob_tfi")),
                        "ob_regime": m_bar.group("ob_regime"),
                    }

                m_signal = RE_SIGNAL.search(msg)
                if m_signal:
                    signal, confidence, api_sec, reason = m_signal.groups()
                    metrics["deepseek_calls"] += 1
                    metrics["last_signal"] = {"signal": signal, "confidence": confidence}
                    metrics["last_signal_api_sec"] = float(api_sec)
                    metrics["last_signal_reason"] = reason.strip()
                    _merge_current_llm(
                        metrics,
                        {
                            "signal": signal,
                            "confidence": confidence,
                            "api_time_sec": float(api_sec),
                            "ts_signal": ts,
                        },
                    )
                    if reason.strip():
                        cur = metrics.get("current_llm") or {}
                        if not cur.get("thesis"):
                            _merge_current_llm(metrics, {"thesis": reason.strip()})
                    if not metrics["llm_conversations"] or metrics["llm_conversations"][-1].get("signal"):
                        metrics["llm_conversations"].append(
                            {"ts_prompt": None, "prompt_payload": None, "ts_response": ts}
                        )
                    metrics["llm_conversations"][-1]["signal"] = metrics["last_signal"]
                    metrics["llm_conversations"][-1]["api_time_sec"] = metrics["last_signal_api_sec"]
                    metrics["llm_conversations"][-1]["reason"] = metrics["last_signal_reason"]
                    if len(metrics["llm_conversations"]) > 12:
                        metrics["llm_conversations"] = metrics["llm_conversations"][-12:]

                if "DRY RUN" in msg:
                    metrics["dry_run_events"] += 1

                m_pos_cur = RE_POSITION_CURRENT.search(msg)
                if m_pos_cur:
                    gd = m_pos_cur.groupdict()
                    upnl_raw = gd.get("upnl")
                    position = PositionState(
                        side=gd["side"].upper(),
                        quantity=float(gd["qty"]),
                        entry_price=float(gd["entry"].replace(",", "")),
                        unrealized_pnl=float(upnl_raw) if upnl_raw else None,
                        health=gd.get("health"),
                    )

                m_pos_open = RE_POSITION_OPENED.search(msg)
                if m_pos_open:
                    side, qty, entry_px = m_pos_open.groups()
                    position = PositionState(
                        side=side.upper(),
                        quantity=float(qty),
                        entry_price=float(entry_px),
                    )

                m_pos_net = RE_POSITION_NET.search(msg)
                if m_pos_net:
                    net_qty = float(m_pos_net.group(1))
                    if net_qty == 0:
                        position = None
                    else:
                        position = PositionState(
                            side="LONG" if net_qty > 0 else "SHORT",
                            quantity=abs(net_qty),
                            entry_price=last_reconciled_avg_px,
                        )

                m_pos_avg = RE_POSITION_AVG.search(msg)
                if m_pos_avg:
                    last_reconciled_avg_px = float(m_pos_avg.group(1))
                    if position is not None:
                        position.entry_price = last_reconciled_avg_px

                m_pos_closed = RE_POSITION_CLOSED.search(msg)
                if m_pos_closed:
                    _, pnl = m_pos_closed.groups()
                    metrics["closed_trades"] += 1
                    metrics["realized_pnl_usdt"] += float(pnl)
                    position = None

                m_prompt = RE_LLM_PROMPT_PAYLOAD.search(msg)
                if m_prompt:
                    try:
                        payload = json.loads(m_prompt.group(1))
                    except json.JSONDecodeError:
                        payload = {"raw": m_prompt.group(1)}
                    pending_prompt = {
                        "ts_prompt": ts,
                        "prompt_payload": payload,
                    }

                m_resp_json = RE_LLM_RESPONSE_JSON.search(msg)
                if m_resp_json:
                    try:
                        resp_json = json.loads(m_resp_json.group(1))
                    except json.JSONDecodeError:
                        resp_json = {"raw": m_resp_json.group(1)}
                    if isinstance(resp_json, dict):
                        _merge_current_llm(
                            metrics,
                            {**_llm_synthesis_snapshot(resp_json), "ts_response": ts},
                        )
                    convo = pending_prompt or {"ts_prompt": None, "prompt_payload": None}
                    convo["ts_response"] = ts
                    convo["response_json"] = resp_json
                    metrics["llm_conversations"].append(convo)
                    pending_prompt = None
                    if len(metrics["llm_conversations"]) > 12:
                        metrics["llm_conversations"] = metrics["llm_conversations"][-12:]

                m_resp_raw = RE_LLM_RAW_RESPONSE.search(msg)
                if m_resp_raw:
                    if not metrics["llm_conversations"] or metrics["llm_conversations"][-1].get("response_raw"):
                        convo = pending_prompt or {"ts_prompt": None, "prompt_payload": None}
                        convo["ts_response"] = ts
                        metrics["llm_conversations"].append(convo)
                        pending_prompt = None
                    metrics["llm_conversations"][-1]["response_raw"] = m_resp_raw.group(1)
                    if len(metrics["llm_conversations"]) > 12:
                        metrics["llm_conversations"] = metrics["llm_conversations"][-12:]

                important = (
                    "Signal:" in msg
                    or "Current Price:" in msg
                    or "Position opened:" in msg
                    or "Position closed:" in msg
                    or "DRY RUN" in msg
                    or "Order rejected" in msg
                    or "Warning:" in msg
                )
                if important:
                    metrics["recent_events"].append({"ts": ts, "level": level, "message": msg})
                    if len(metrics["recent_events"]) > 25:
                        metrics["recent_events"] = metrics["recent_events"][-25:]

                if level in {"WARN", "ERROR"}:
                    metrics["recent_warnings_errors"].append({"ts": ts, "level": level, "message": msg})
                    if len(metrics["recent_warnings_errors"]) > 10:
                        metrics["recent_warnings_errors"] = metrics["recent_warnings_errors"][-10:]

    if position:
        metrics["open_position"] = {
            "side": position.side,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
        }
        if position.unrealized_pnl is not None:
            metrics["open_position"]["unrealized_pnl"] = position.unrealized_pnl
        if position.health:
            metrics["open_position"]["health"] = position.health

    if metrics.get("current_llm") == {}:
        metrics["current_llm"] = None
    elif isinstance(metrics.get("current_llm"), dict):
        current_llm = metrics["current_llm"]
        if metrics.get("last_signal") is None:
            signal = current_llm.get("signal")
            confidence = current_llm.get("confidence")
            if signal or confidence:
                metrics["last_signal"] = {
                    "signal": signal or "N/A",
                    "confidence": confidence or "N/A",
                }
        if not metrics.get("last_signal_reason"):
            thesis = current_llm.get("thesis")
            if isinstance(thesis, str) and thesis.strip():
                metrics["last_signal_reason"] = thesis

    if metrics.get("deepseek_calls", 0) == 0:
        metrics["deepseek_calls"] = sum(
            1
            for convo in metrics.get("llm_conversations", [])
            if convo.get("response_json") or convo.get("response_raw")
        )

    return metrics


def _is_fresh_log_timestamp(ts: Optional[str], now_utc: datetime, max_age_seconds: int = 180) -> bool:
    if not ts:
        return False
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False
    return now_utc - parsed <= timedelta(seconds=max_age_seconds)


def collect_status() -> Dict[str, Any]:
    env_cfg = _parse_env_file(ENV_FILE)
    log_path = _latest_json_log()  # still used as fallback
    proc = _strategy_process_state()
    metrics = _parse_log_metrics(log_path, target_pid=proc.get("pid"))

    now_dt = datetime.now(timezone.utc)
    now_utc = now_dt.isoformat()
    if not proc["running"]:
        if (
            metrics.get("strategy_running_log")
            and _is_fresh_log_timestamp(metrics.get("log_timestamp_utc"), now_dt)
            and (
                int(metrics.get("analysis_cycles") or 0) > 0
                or int(metrics.get("deepseek_calls") or 0) > 0
            )
        ):
            proc["running"] = True
            proc["pid"] = None
            proc["inferred_from_logs"] = True
    mode = {
        "bybit_demo": env_cfg.get("BYBIT_DEMO", "").lower() == "true",
        "bybit_testnet": env_cfg.get("BYBIT_TESTNET", "").lower() == "true",
        "dry_run": env_cfg.get("DRY_RUN", "").lower() == "true",
        "instrument_id": env_cfg.get("INSTRUMENT_ID", "BTCUSDT-LINEAR.BYBIT"),
    }
    exchange = _collect_bybit_exchange_context(env_cfg, mode["instrument_id"])

    status = {
        "now_utc": now_utc,
        "process": proc,
        "mode": mode,
        "metrics": metrics,
        "exchange": exchange,
        "position_alignment": _position_alignment(
            metrics.get("open_position"),
            exchange.get("position"),
        ),
    }
    return status


def _fmt_llm_field(llm: Optional[Dict[str, Any]], key: str, default: str = "N/A") -> str:
    if not isinstance(llm, dict):
        return default
    val = llm.get(key)
    if val is None:
        return default
    if isinstance(val, str) and not val.strip():
        return default
    return str(val)


def _base_asset_label(instrument_id: str, exchange_symbol: Optional[str]) -> str:
    if exchange_symbol and isinstance(exchange_symbol, str):
        if exchange_symbol.endswith("USDT") and len(exchange_symbol) > 4:
            return exchange_symbol[:-4]
        if exchange_symbol.endswith("USD") and len(exchange_symbol) > 3:
            return exchange_symbol[:-3]
        return exchange_symbol
    head = str(instrument_id or "").split("-", 1)[0]
    for suffix in ("USDT", "USD", "USDC"):
        if head.endswith(suffix) and len(head) > len(suffix):
            return head[: -len(suffix)]
    return head or "ASSET"


def _render_html(status: Dict[str, Any]) -> str:
    mode = status["mode"]
    proc = status["process"]
    m = status["metrics"]
    ex = status.get("exchange") or {}
    sig = m["last_signal"] or {"signal": "N/A", "confidence": "N/A"}
    pos = m["open_position"]
    llm = m.get("current_llm")
    bar = m.get("bar_close") or {}
    align = status.get("position_alignment") or {}
    ex_wallet = ex.get("wallet") or {}
    ex_position = ex.get("position")
    ex_trade_summary = ex.get("recent_trade_summary") or {}
    asset_label = _base_asset_label(mode.get("instrument_id", ""), ex.get("symbol"))

    def _pos_line(p: Optional[Dict[str, Any]], asset_label: str) -> str:
        if not p:
            return "No open position"
        side = str(p.get("side") or "").upper()
        qty = p.get("quantity")
        entry = p.get("entry_price") or p.get("avg_price")
        parts = [f"<b>{side}</b>"]
        if isinstance(qty, (int, float)):
            parts.append(f"{qty:.6f} {asset_label}")
        if isinstance(entry, (int, float)):
            parts.append(f"@ ${entry:,.2f}")
        upnl = p.get("unrealized_pnl")
        if isinstance(upnl, (int, float)):
            parts.append(f"| uPnL {upnl:+.2f}")
        health = p.get("health")
        if health:
            parts.append(f"| {health}")
        return " ".join(parts)

    pos_html = _pos_line(pos, asset_label)
    align_state = align.get("state") or "unknown"
    align_class = "ok" if align_state == "aligned" else ("bad" if align_state in {"side_mismatch", "log_only", "exchange_only"} else "")
    align_label = {
        "flat": "Both flat",
        "aligned": "Log + exchange aligned",
        "log_only": "Log shows position; exchange flat",
        "exchange_only": "Exchange position; log flat",
        "side_mismatch": "Side mismatch",
    }.get(align_state, align_state)

    llm_regime = _fmt_llm_field(llm, "regime")
    llm_playbook = _fmt_llm_field(llm, "playbook")
    llm_action = _fmt_llm_field(llm, "position_action")
    ob_regime = bar.get("ob_regime") or "N/A"
    bar_trend = bar.get("trend") or "N/A"
    thesis = _fmt_llm_field(llm, "thesis", m.get("last_signal_reason") or "N/A")
    hold_reason = _fmt_llm_field(llm, "hold_reason")
    setup_type = _fmt_llm_field(llm, "setup_type")
    watch_trigger = _fmt_llm_field(llm, "watch_trigger")
    wtp = llm.get("watch_trigger_price") if isinstance(llm, dict) else None
    wtd = llm.get("watch_trigger_direction") if isinstance(llm, dict) else None
    wte = llm.get("watch_trigger_expiry_bars") if isinstance(llm, dict) else None
    if watch_trigger == "N/A" and isinstance(wtp, (int, float)):
        watch_trigger = f"price={wtp}"
        if wtd:
            watch_trigger += f" dir={wtd}"
        if wte:
            watch_trigger += f" expiry_bars={wte}"
    invalidation = _fmt_llm_field(llm, "invalidation")
    inv_price = llm.get("invalidation_price") if isinstance(llm, dict) else None
    if invalidation == "N/A" and isinstance(inv_price, (int, float)):
        invalidation = f"${inv_price:,.2f}"
    last_price = f"${m['last_price']:,.2f}" if isinstance(m["last_price"], (int, float)) else "N/A"
    last_rsi = f"{m['last_rsi']:.2f}" if isinstance(m["last_rsi"], (int, float)) else "N/A"
    pnl = f"{m['realized_pnl_usdt']:.2f}"
    running = "RUNNING" if proc["running"] else "STOPPED"
    ex_pos_html = (
        f"<b>{str(ex_position.get('side', '')).upper()}</b> {ex_position.get('quantity')} "
        f"{(ex.get('symbol') or '')} @ ${ex_position.get('avg_price'):,.2f} "
        f"| uPnL {ex_position.get('unrealized_pnl')}"
        if ex_position and isinstance(ex_position.get("avg_price"), (int, float))
        else "No exchange position"
    )
    ex_equity = ex_wallet.get("total_equity")
    ex_available = ex_wallet.get("total_available_balance")
    ex_margin = ex_wallet.get("total_initial_margin")
    ex_pnl_5 = ex_trade_summary.get("last_5_realized_pnl")

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DeepSeek Strategy Dashboard</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --card: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --good: #0f766e;
      --warn: #b45309;
      --bad: #b91c1c;
      --line: #e5e7eb;
    }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family: "IBM Plex Sans", "Segoe UI", sans-serif; }}
    .wrap {{ max-width: 1100px; margin: 20px auto; padding: 0 14px; }}
    .top {{ display:flex; justify-content:space-between; gap:10px; align-items:center; margin-bottom:14px; }}
    .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:12px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px; }}
    .k {{ color: var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    .v {{ margin-top:6px; font-size:18px; font-weight:600; }}
    .small {{ color: var(--muted); font-size:12px; }}
    .pill {{ display:inline-block; font-size:12px; padding:4px 8px; border-radius:999px; border:1px solid var(--line); margin-right:6px; }}
    .ok {{ color:var(--good); border-color:#99f6e4; background:#ecfeff; }}
    .bad {{ color:var(--bad); border-color:#fecaca; background:#fef2f2; }}
    ul {{ margin:8px 0 0; padding-left:18px; }}
    li {{ margin:4px 0; }}
    code {{ background:#f3f4f6; padding:2px 5px; border-radius:6px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h2 style="margin:0">DeepSeek Strategy Monitor</h2>
      <div class="small">Updated: <span id="now">{status["now_utc"]}</span></div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="k">Process</div>
        <div class="v">{running}</div>
        <div class="small">PID: {proc["pid"] if proc["pid"] else "N/A"}</div>
      </div>
      <div class="card">
        <div class="k">Safety Mode</div>
        <div class="v">
          <span class="pill {'ok' if mode['bybit_demo'] else 'bad'}">BYBIT_DEMO={str(mode['bybit_demo']).lower()}</span>
          <span class="pill">{'BYBIT_TESTNET=' + str(mode['bybit_testnet']).lower()}</span>
          <span class="pill">{'DRY_RUN=' + str(mode['dry_run']).lower()}</span>
        </div>
        <div class="small">Instrument: {mode["instrument_id"]}</div>
      </div>
      <div class="card">
        <div class="k">Market Snapshot</div>
        <div class="v">{last_price}</div>
        <div class="small">Trend: {m["last_trend"] or "N/A"} | RSI: {last_rsi}</div>
      </div>
      <div class="card">
        <div class="k">Last LLM Signal</div>
        <div class="v">{sig["signal"]} ({sig["confidence"]})</div>
        <div class="small">Action: {llm_action} | API: {m["last_signal_api_sec"] if m["last_signal_api_sec"] is not None else "N/A"}s</div>
      </div>
      <div class="card">
        <div class="k">Regime Identification</div>
        <div class="v">{llm_regime}</div>
        <div class="small">Playbook: {llm_playbook} | OB bar regime: {ob_regime} | Bar trend: {bar_trend}</div>
      </div>
      <div class="card">
        <div class="k">Strategy Log Position</div>
        <div class="v">{pos_html}</div>
        <div class="small"><span class="pill {align_class}">{align_label}</span></div>
      </div>
      <div class="card">
        <div class="k">Session Performance</div>
        <div class="v">{pnl} USDT</div>
        <div class="small">Closed trades: {m["closed_trades"]} | DeepSeek calls: {m["deepseek_calls"]}</div>
      </div>
      <div class="card">
        <div class="k">Bybit Portfolio</div>
        <div class="v">{f'${ex_equity:,.2f}' if isinstance(ex_equity, (int, float)) else 'N/A'}</div>
        <div class="small">Available: {f'${ex_available:,.2f}' if isinstance(ex_available, (int, float)) else 'N/A'} | Initial margin: {f'${ex_margin:,.2f}' if isinstance(ex_margin, (int, float)) else 'N/A'}</div>
      </div>
      <div class="card">
        <div class="k">Exchange Position (Bybit)</div>
        <div class="v">{ex_pos_html}</div>
        <div class="small">Open orders: {len(ex.get("open_orders") or [])} | Last 5 P&L: {f'{ex_pnl_5:.2f} USDT' if isinstance(ex_pnl_5, (int, float)) else 'N/A'}</div>
      </div>
    </div>

    <div class="card" style="margin-top:12px">
      <div class="k">Current LLM Thinking</div>
      <div class="small" style="margin-top:8px;line-height:1.5">
        <div><b>Thesis:</b> {thesis}</div>
        <div><b>Hold reason:</b> {hold_reason}</div>
        <div><b>Setup:</b> {setup_type} | <b>Watch trigger:</b> {watch_trigger}</div>
        <div><b>Invalidation:</b> {invalidation}</div>
      </div>
    </div>

    <div class="grid" style="margin-top:12px">
      <div class="card">
        <div class="k">Recent Warnings/Errors</div>
        <ul id="warns"></ul>
      </div>
      <div class="card">
        <div class="k">Recent Important Events</div>
        <ul id="events"></ul>
      </div>
      <div class="card">
        <div class="k">Bybit Recent Closed P&L</div>
        <ul id="closed_pnl"></ul>
      </div>
    </div>

    <div class="card" style="margin-top:12px">
      <div class="k">LLM Conversation (Recent)</div>
      <div id="llm_convos" class="small"></div>
    </div>

    <div class="card" style="margin-top:12px">
      <div class="small">
        API endpoint: <code>/api/status</code><br>
        Source log: <code>{m["log_file"] or "N/A"}</code>
      </div>
    </div>
  </div>
  <script>
    const status = {json.dumps(status)};
    const warns = status.metrics.recent_warnings_errors || [];
    const events = status.metrics.recent_events || [];
    const llmConvos = status.metrics.llm_conversations || [];
    const closedPnl = (status.exchange && status.exchange.recent_closed_pnl) || [];
    const warnList = document.getElementById("warns");
    const eventList = document.getElementById("events");
    const pnlList = document.getElementById("closed_pnl");
    const llmConvosEl = document.getElementById("llm_convos");
    warnList.innerHTML = warns.slice(-8).reverse().map(x => `<li>[${{x.level}}] ${{x.message}}</li>`).join("") || "<li>None</li>";
    eventList.innerHTML = events.slice(-8).reverse().map(x => `<li>${{x.message}}</li>`).join("") || "<li>None</li>";
    pnlList.innerHTML = closedPnl.slice(0, 5).map(x => `<li>${{x.outcome}} ${{x.side}} ${{x.quantity}} | ${{x.closed_pnl}} USDT</li>`).join("") || "<li>None</li>";
    const synthKeys = ["regime","playbook","position_action","thesis","hold_reason","setup_type","watch_trigger","invalidation"];
    const fmtSynth = (r) => {{
      if (!r || typeof r !== "object") return "";
      return synthKeys.filter(k => r[k]).map(k => `<div><b>${{k}}:</b> ${{r[k]}}</div>`).join("");
    }};
    llmConvosEl.innerHTML = llmConvos.slice(-5).reverse().map(c => {{
      const prompt = c.prompt_payload ? JSON.stringify(c.prompt_payload) : "N/A";
      const response = c.response_json ? JSON.stringify(c.response_json) : (c.response_raw || "N/A");
      const signal = c.signal ? `${{c.signal.signal}} (${{c.signal.confidence}})` : "N/A";
      const synth = fmtSynth(c.response_json);
      return `
        <div style="border:1px solid #e5e7eb; border-radius:8px; padding:8px; margin:8px 0;">
          <div><b>Signal:</b> ${{signal}} | <b>API:</b> ${{c.api_time_sec ?? "N/A"}}s</div>
          ${{synth}}
          <div><b>Prompt payload:</b> <code>${{prompt}}</code></div>
          <div><b>Response:</b> <code>${{response}}</code></div>
        </div>
      `;
    }}).join("") || "<div>None</div>";
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        status = collect_status()
        if self.path == "/api/status":
            body = json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/", "/index.html"):
            body = _render_html(status).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve DeepSeek strategy monitoring dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default: 8787)")
    parser.add_argument("--print-json", action="store_true", help="Print one JSON status snapshot and exit")
    args = parser.parse_args()

    if args.print_json:
        print(json.dumps(collect_status(), ensure_ascii=False, indent=2))
        return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Dashboard listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
