#!/bin/bash
set -euo pipefail

# Start Bybit demo paper trading + local monitoring dashboard

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${DASHBOARD_PORT:-8787}"

./restart_trader.sh

# Stop existing dashboard if running
if [ -f "dashboard.pid" ]; then
  DASH_PID="$(cat dashboard.pid)"
  if ps -p "$DASH_PID" > /dev/null 2>&1; then
    kill "$DASH_PID" || true
    sleep 1
  fi
  rm -f dashboard.pid
fi

if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
fi

nohup python tools/serve_monitor_dashboard.py --host 0.0.0.0 --port "$PORT" \
  > "logs/dashboard_$(date +%Y%m%d_%H%M%S).log" 2>&1 &

echo $! > dashboard.pid

echo "Dashboard started with PID: $(cat dashboard.pid)"
echo "Open: http://localhost:$PORT"
echo "API:  http://localhost:$PORT/api/status"
