#!/bin/bash
set -euo pipefail

# DeepSeek Trading Strategy Startup Script (Bybit Demo Paper Trading)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate project virtualenv if available
if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
fi

# Safety defaults: force Bybit demo environment (no real money)
export BYBIT_TESTNET=false
export BYBIT_DEMO=true
export DRY_RUN=false
export AUTO_CONFIRM=true
export TIMEFRAME=5m
export TIMER_INTERVAL_SEC=300

mkdir -p logs

existing_pids="$(pgrep -f "python.*main_live.py" || true)"
if [ -n "$existing_pids" ]; then
  echo "⚠️ Refusing to start: existing main_live.py process(es) detected:"
  echo "$existing_pids"
  echo "Use ./restart_trader.sh for a safe single-process restart."
  exit 1
fi

log_file="logs/trader_$(date +%Y%m%d_%H%M%S).log"
nohup python -u main_live.py > "$log_file" 2>&1 &
trader_pid=$!
echo "$trader_pid" > trader.pid

sleep 2
if ! kill -0 "$trader_pid" 2>/dev/null; then
  rm -f trader.pid
  echo "Trading strategy failed to remain running. Review: $log_file"
  exit 1
fi

echo "Trading strategy started with PID: $(cat trader.pid)"
echo "Mode: BYBIT_DEMO=true, BYBIT_TESTNET=false, DRY_RUN=false, TIMEFRAME=5m, TIMER_INTERVAL_SEC=300"
echo "View logs: tail -f $log_file"
echo "Stop trader: ./stop_trader.sh"
