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
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from glob import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
ENV_FILE = ROOT / ".env"
PID_FILE = ROOT / "trader.pid"

RE_SIGNAL = re.compile(
    r"🤖 Signal:\s*(BUY|SELL|HOLD)\s*\|\s*Confidence:\s*(LOW|MEDIUM|HIGH)\s*\|\s*API time:\s*([0-9.]+)s\s*\|\s*Reason:\s*(.*)"
)
RE_PRICE = re.compile(r"Current Price:\s*\$([0-9,]+(?:\.[0-9]+)?)")
RE_RSI = re.compile(r"RSI:\s*([0-9]+(?:\.[0-9]+)?)")
RE_POSITION_CURRENT = re.compile(
    r"Current Position:\s*(long|short)\s*([0-9.]+)\s*@\s*\$([0-9,]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)
RE_POSITION_OPENED = re.compile(
    r"🟢 Position opened:\s*(LONG|SHORT)\s*([0-9.]+)\s*@\s*([0-9.]+)"
)
RE_POSITION_CLOSED = re.compile(r"🔴 Position closed:\s*(LONG|SHORT)\s*P&L:\s*([+-]?[0-9.]+)\s*USDT")
RE_WARMUP = re.compile(r"Received\s+([0-9]+)\s+warmup bars")


@dataclass
class PositionState:
    side: str
    quantity: float
    entry_price: float


def _latest_json_log() -> Optional[Path]:
    candidates = sorted(glob(str(LOG_DIR / "deepseek_trader_*.json")))
    return Path(candidates[-1]) if candidates else None


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


def _strategy_process_state() -> Dict[str, Any]:
    running = False
    pid: Optional[int] = None

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            check = subprocess.run(["ps", "-p", str(pid)], capture_output=True, text=True)
            running = check.returncode == 0 and str(pid) in check.stdout
        except Exception:
            pid = None

    if not running:
        pgrep = subprocess.run(["pgrep", "-f", "python.*main_live.py"], capture_output=True, text=True)
        if pgrep.returncode == 0 and pgrep.stdout.strip():
            first_pid = pgrep.stdout.strip().splitlines()[0]
            try:
                pid = int(first_pid)
            except ValueError:
                pid = None
            running = True

    return {"running": running, "pid": pid}


def _parse_log_metrics(log_path: Optional[Path]) -> Dict[str, Any]:
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
    }
    if log_path is None or not log_path.exists():
        return metrics

    position: Optional[PositionState] = None

    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
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

            m_signal = RE_SIGNAL.search(msg)
            if m_signal:
                signal, confidence, api_sec, reason = m_signal.groups()
                metrics["deepseek_calls"] += 1
                metrics["last_signal"] = {"signal": signal, "confidence": confidence}
                metrics["last_signal_api_sec"] = float(api_sec)
                metrics["last_signal_reason"] = reason.strip()

            if "DRY RUN" in msg:
                metrics["dry_run_events"] += 1

            m_pos_cur = RE_POSITION_CURRENT.search(msg)
            if m_pos_cur:
                side, qty, entry_px = m_pos_cur.groups()
                position = PositionState(side=side.upper(), quantity=float(qty), entry_price=float(entry_px.replace(",", "")))

            m_pos_open = RE_POSITION_OPENED.search(msg)
            if m_pos_open:
                side, qty, entry_px = m_pos_open.groups()
                position = PositionState(side=side.upper(), quantity=float(qty), entry_price=float(entry_px))

            m_pos_closed = RE_POSITION_CLOSED.search(msg)
            if m_pos_closed:
                _, pnl = m_pos_closed.groups()
                metrics["closed_trades"] += 1
                metrics["realized_pnl_usdt"] += float(pnl)
                position = None

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

    return metrics


def collect_status() -> Dict[str, Any]:
    env_cfg = _parse_env_file(ENV_FILE)
    log_path = _latest_json_log()
    proc = _strategy_process_state()
    metrics = _parse_log_metrics(log_path)

    now_utc = datetime.now(timezone.utc).isoformat()
    mode = {
        "bybit_demo": env_cfg.get("BYBIT_DEMO", "").lower() == "true",
        "bybit_testnet": env_cfg.get("BYBIT_TESTNET", "").lower() == "true",
        "dry_run": env_cfg.get("DRY_RUN", "").lower() == "true",
        "instrument_id": env_cfg.get("INSTRUMENT_ID", "BTCUSDT-LINEAR.BYBIT"),
    }

    status = {
        "now_utc": now_utc,
        "process": proc,
        "mode": mode,
        "metrics": metrics,
    }
    return status


def _render_html(status: Dict[str, Any]) -> str:
    mode = status["mode"]
    proc = status["process"]
    m = status["metrics"]
    sig = m["last_signal"] or {"signal": "N/A", "confidence": "N/A"}
    pos = m["open_position"]

    pos_html = (
        f"<b>{pos['side']}</b> {pos['quantity']:.6f} BTC @ ${pos['entry_price']:,.2f}"
        if pos
        else "No open position"
    )
    last_price = f"${m['last_price']:,.2f}" if isinstance(m["last_price"], (int, float)) else "N/A"
    last_rsi = f"{m['last_rsi']:.2f}" if isinstance(m["last_rsi"], (int, float)) else "N/A"
    pnl = f"{m['realized_pnl_usdt']:.2f}"
    running = "RUNNING" if proc["running"] else "STOPPED"

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
        <div class="small">API time: {m["last_signal_api_sec"] if m["last_signal_api_sec"] is not None else "N/A"}s</div>
      </div>
      <div class="card">
        <div class="k">Open Position</div>
        <div class="v">{pos_html}</div>
      </div>
      <div class="card">
        <div class="k">Session Performance</div>
        <div class="v">{pnl} USDT</div>
        <div class="small">Closed trades: {m["closed_trades"]} | DeepSeek calls: {m["deepseek_calls"]}</div>
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
    const warnList = document.getElementById("warns");
    const eventList = document.getElementById("events");
    warnList.innerHTML = warns.slice(-8).reverse().map(x => `<li>[${{x.level}}] ${{x.message}}</li>`).join("") || "<li>None</li>";
    eventList.innerHTML = events.slice(-8).reverse().map(x => `<li>${{x.message}}</li>`).join("") || "<li>None</li>";
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
