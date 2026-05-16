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

mkdir -p logs

nohup python main_live.py > "logs/trader_$(date +%Y%m%d_%H%M%S).log" 2>&1 &
echo $! > trader.pid

echo "Trading strategy started with PID: $(cat trader.pid)"
echo "Mode: BYBIT_DEMO=true, BYBIT_TESTNET=false, DRY_RUN=false"
echo "View logs: tail -f logs/trader_*.log"
echo "Stop trader: ./stop_trader.sh"
