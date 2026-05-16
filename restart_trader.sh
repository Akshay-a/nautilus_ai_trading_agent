#!/bin/bash
set -euo pipefail

# DeepSeek Trading Strategy Restart Script (Bybit Demo Paper Trading)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Restarting trading strategy..."

# Stop prior process (if any)
if [ -f trader.pid ]; then
  PID="$(cat trader.pid)"
  if ps -p "$PID" > /dev/null 2>&1; then
    echo "Stopping existing process (PID: $PID)..."
    kill "$PID"
    sleep 2
  fi
  rm -f trader.pid
fi

# Fallback process cleanup
pkill -f "python.*main_live.py" >/dev/null 2>&1 || true
sleep 1

# Activate project virtualenv if available
if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
fi

mkdir -p logs

# Safety defaults: force Bybit demo environment (no real money)
export BYBIT_TESTNET=false
export BYBIT_DEMO=true
export DRY_RUN=false
export AUTO_CONFIRM=true
export TIMEFRAME=1m
export TIMER_INTERVAL_SEC=60

echo "Starting new process in Bybit demo mode..."
nohup python main_live.py > "logs/trader_$(date +%Y%m%d_%H%M%S).log" 2>&1 &
echo $! > trader.pid

echo "Trading strategy restarted with PID: $(cat trader.pid)"
echo "Mode: BYBIT_DEMO=true, BYBIT_TESTNET=false, DRY_RUN=false, TIMEFRAME=1m, TIMER_INTERVAL_SEC=60"
echo "View logs: tail -f logs/trader_*.log"
echo "Stop trader: ./stop_trader.sh"
