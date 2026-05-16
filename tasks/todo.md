# TODO

## Plan (Autonomous Self-Heal + Self-Build Loop)
- [x] Audit existing hourly automation prompt/goal artifacts in `tasks/`.
- [x] Define centralized backlog with explicit done/verify/kill criteria.
- [x] Define persistent automation state file so each hourly run knows whether to heal, verify previous ship, or build next.
- [x] Replace hourly prompt with deterministic state-machine execution logic.
- [x] Add compact context-pack guidance to reduce context drift and over-reading.

## Review (Autonomous Self-Heal + Self-Build Loop)
- Added centralized backlog: `tasks/autonomous_backlog.md`.
- Added persistent run-state memory: `tasks/autonomous_state.md`.
- Rewrote automation prompt to strict flow:
  - `HEAL` if unhealthy.
  - `VERIFY_PREV_SHIP` if previous commit is awaiting live verification.
  - `BUILD_NEXT` only when healthy and no pending verification.
- Added context-engineering pack: `tasks/autonomous_context.md` with read budget, truth sources, and verification ladder.
- Updated `tasks/automation_goal.md` so priority is explicit: health -> verify previous ship -> build next pending.

## Review (Autonomous Run 2026-05-17 02:00 AEST)
- Health gate initially failed on status correctness: `process.running=true` could appear with dead/non-trader PIDs.
- HEAL fix applied in `tools/serve_monitor_dashboard.py`:
  - PID validation now checks `ps -o command=` and accepts only `python ... main_live.py`.
  - `pgrep` fallback now validates each candidate PID before marking running.
- Post-fix verification:
  - `python3 tools/serve_monitor_dashboard.py --print-json` reports accurate process state.
  - Fresh runtime evidence captured from active cycle: `SELL` signal, LLM prompt/response, and `📊 Position Sizing: Fixed:$10000.00 ...`.
- Mandatory trader behavior audit:
  - No direction flip observed yet (previous signal was `null`), so close/reverse classification is pending next signal transition.

## Review (Autonomous Run 2026-05-17 02:00 AEST - Follow-up HEAL)
- Health gate is RED in this sandbox run because process checks cannot use `ps`/`pgrep` and detached strategy processes are short-lived here.
- HEAL fix applied in `tools/serve_monitor_dashboard.py`:
  - Replaced process detection dependency on `ps`/`pgrep` with portable PID liveness probe via `os.kill(pid, 0)`.
  - Removed fallback process scanning that relied on blocked commands.
- Validation evidence:
  - `python3 -m py_compile tools/serve_monitor_dashboard.py` passed.
  - `./check_strategy_status.sh` + `python3 tools/serve_monitor_dashboard.py --print-json` ran successfully and consistently report RED due non-running trader process.
  - Trader startup logs confirm demo-safe flags and fresh startup attempts (`BYBIT_DEMO=true`, `BYBIT_TESTNET=false`, `DRY_RUN=false`), but process exits before sustained runtime.
- Mandatory trader behavior audit:
  - Last signal in parsed runtime snapshot: `HOLD`, open position `SHORT 0.128 @ 78264.5`.
  - No BUY/SELL direction flip event occurred in this run window, so close-only vs close-and-reverse classification remains pending next live transition.

## Plan (Phase 2 Audit + Validation)
- [x] Inspect commit `0fc4f31` and audit Phase 2 feature code for logic/performance defects.
- [x] Remove slop/high-risk defects in `indicators/orderbook_manager.py` before trusting validation output.
- [x] Run live demo validation and confirm order book + microstructure logs + CSV feature dump.
- [x] Run IC analysis from generated CSV and classify keep/drop features using |IC| >= 0.02.
- [x] Update `progress_log.md` with measured IC values and explicit risks.

## Review (Phase 2 Audit + Validation)
- Fixed three concrete defects in `indicators/orderbook_manager.py`:
  - Trade-window computation now runs in one pass (instead of 3 passes per depth update).
  - Sweep threshold lookback now correctly uses most recent trades (`recent[:lookback]`).
  - Spearman rank-IC now handles tied values correctly (average tie ranks).
- Fixed feature degeneracy where `queue_pressure` was identical to `depth_imbalance` on BTCUSDT:
  - `queue_pressure` now caps to top 3 levels while retaining the bps distance filter.
- Live demo validation succeeded:
  - Subscriptions active for order book deltas + trade ticks.
  - OB logs included Phase 2 fields (`qp`, sweep counts, regime).
  - Timer-cycle microstructure logs and CSV dumps were emitted.
- IC validation completed from `data/microstructure_features.csv`:
  - `Rows: 500` (latest ring-buffer snapshot set).
  - No full feature drops at |IC| >= 0.02 (some horizon-specific weak values remain).
- Residual operational risk remains: CSV dump currently overwrites from the in-memory ring buffer (`feature_buffer_size=500`), so historical data is truncated each cycle.

## Plan
- [x] Migrate `main_live.py` from Binance adapters/config to Bybit adapters/config.
- [x] Switch default instrument/bar type to Bybit linear perpetual format.
- [x] Migrate warmup historical bar prefetch from Binance REST to Bybit v5 REST.
- [x] Add true `DRY_RUN` guard for order submission paths.
- [x] Update `.env.template` for Bybit credentials and env flags.
- [x] Execute runtime smoke test for Bybit + dry-run behavior.

## Review
- Code compiles with `python3 -m py_compile main_live.py strategy/deepseek_strategy.py`.
- Runtime smoke test is blocked in this workspace because `.env` is absent and `BYBIT_API_KEY/BYBIT_API_SECRET` are not set.
- Additional runtime blocker: only Python 3.13 is available here; project docs expect Python 3.10 for NautilusTrader runtime.
- Installed dependencies with `python3 -m pip install -r requirements.txt` (NautilusTrader 1.226.0 available on this machine).
- Smoke run result with dummy keys + `DRY_RUN=true`: Bybit data/exec clients built and strategy initialized in dry-run mode; exec authentication failed with HTTP 401 as expected for invalid credentials.
- Validation run with real env + `BYBIT_TESTNET=false`, `TIMEFRAME=1m`, `TIMER_INTERVAL_SEC=20`, `DRY_RUN=true`:
  - Strategy loaded instrument and pre-fetched 200 warmup bars from Bybit.
  - DeepSeek was called 7 times in one session.
  - Generated first actionable signal `SELL (MEDIUM)` and hit dry-run execution path:
    `🧪 DRY RUN: Simulated bracket order SELL 0.002 BTC (entry + SL + TP not submitted)`.

## Plan (Paper Demo + Dashboard)
- [x] Fix startup/restart/status scripts to use the current repo path instead of hardcoded `/home/ubuntu/...`.
- [x] Enforce Bybit demo-safe defaults in launch scripts (`BYBIT_DEMO=true`, `BYBIT_TESTNET=false`, `DRY_RUN=false`).
- [x] Add a local HTTP dashboard script that only reads existing strategy JSON logs and exposes a monitoring UI + JSON API.
- [x] Add a one-command launcher for demo trading plus dashboard.
- [x] Verify script syntax and dashboard JSON output locally.

## Review (Paper Demo + Dashboard)
- Updated `start_trader.sh` and `restart_trader.sh` to be repo-relative and force demo-safe Bybit mode.
- Updated `check_strategy_status.sh` to use dashboard parser output (`--print-json`) instead of stale grep patterns.
- Added `tools/serve_monitor_dashboard.py`:
  - Serves `GET /` and `GET /api/status` on a chosen port.
  - Displays process status, safety flags, last price/trend/RSI, last LLM signal, open position, session realized P&L, warning/error stream, and key recent events.
  - Uses only existing log output (`logs/deepseek_trader_*.json`), no strategy logic changes.
- Added `start_paper_demo.sh` to start strategy (demo mode) + dashboard together.
- Updated `stop_trader.sh` to also stop the dashboard when `dashboard.pid` exists.

## Plan (Status Accuracy + LLM Auditability + Log Optimization)
- [x] Verify the claimed rotation/status fixes against current runtime logs and parser behavior.
- [x] Make status parser resilient before first timer cycle by inferring open position from reconciliation logs.
- [x] Add LLM conversation observability in dashboard JSON/HTML (prompt payload + response + final signal).
- [x] Add additional order-book log throttling based on wall-clock time to prevent noisy log growth.
- [x] Make warmup bars configurable from YAML/env and wire through runtime config.
- [x] Make LLM K-line context depth configurable (instead of fixed 10 bars).
- [x] Run compile/runtime validation for modified files and confirm `--print-json` includes new fields.

## Review (Status Accuracy + LLM Auditability + Log Optimization)
- Confirmed root cause of your `null` snapshot: first timer cycle had not executed yet in that earlier run, so price/signal fields were legitimately empty; plus pre-cycle open position was not always inferred from reconciliation logs.
- Enhanced `tools/serve_monitor_dashboard.py`:
  - Parses `net_position` + `avg_px verified` reconciliation lines to derive open position earlier.
  - Adds `metrics.llm_conversations` in `/api/status` and dashboard UI.
  - Supports both new structured logs (`LLM Prompt Payload`, `LLM Response JSON`) and fallback from existing `DeepSeek Raw Response` lines.
- Enhanced `utils/deepseek_client.py`:
  - Logs concise structured prompt payload (`🤖 LLM Prompt Payload`) per AI call.
  - Logs parsed JSON response (`🤖 LLM Response JSON`) for clean replay/debugging.
- Enhanced `strategy/deepseek_strategy.py` and `main_live.py` config surface:
  - Added `orderbook_log_min_seconds` (wall-clock throttle).
  - Added `warmup_bars` (startup history depth configurable by YAML/env).
  - Added `llm_kline_context_bars` (bars sent into LLM prompt context).
  - Strategy now calls warmup with configured value instead of hardcoded 200.
- Updated `configs/strategy_config.yaml` defaults:
  - `orderbook.log_min_seconds: 60`
  - `warmup_bars: 500`
  - `deepseek.kline_context_bars: 20`
- Validation:
  - `python -m py_compile main_live.py strategy/deepseek_strategy.py utils/deepseek_client.py tools/serve_monitor_dashboard.py` passed.
  - `python tools/serve_monitor_dashboard.py --print-json` now includes `llm_conversations` and accurate live metrics.
