# TODO

## Plan (Prompt Autonomy Rebalance + External Research - 2026-05-29)
- [x] Run focused external research on autonomous LLM trading policy design with cost/friction and regime dependence.
- [x] Identify prompt-policy gaps versus current implementation (exit bias, threshold rigidity, win-rate trap risk).
- [x] Update `utils/deepseek_client.py` prompt sections to remove hard-coded exit criteria and make hold/reduce/exit more autonomous and structure-led.
- [x] Keep safety-critical constraints intact: Bybit source-of-truth and friction-awareness.
- [x] Re-run targeted tests and compile checks.
- [x] Document review notes and operator implications in this file.

## Review (Prompt Autonomy Rebalance + External Research - 2026-05-29)
- Research findings used:
  - Live/autonomous LLM trading benchmarks emphasize regime dependence and tradeoff between strict guardrails vs guided autonomy (`arXiv:2605.06024`, `arXiv:2512.10971`).
  - LLM agent behavior can follow trading styles when prompted with consistent instruction contracts (`arXiv:2504.10789`).
  - Momentum evidence supports allowing continuation/hold behavior instead of forcing rapid exits (`JFE 2012 Time Series Momentum`).
  - High-frequency alpha is highly execution-cost-sensitive, so friction remains a required anchor (`SSRN 1611623`, `SSRN 1678758`).
  - Bybit fee schedule confirms baseline taker-fee assumptions vary by tier but VIP0 perpetual taker remains 0.055% in the current help-center schedule (updated 2026-05-07).
- Prompt-policy gaps fixed:
  - Removed rigid exit-first hierarchy and fixed threshold wording from LLM prompt instructions.
  - Replaced with thesis-state workflow (`intact / weakening / invalidated`) and net-edge-after-friction framing.
  - Explicitly encouraged holding winners when structure remains supportive and using partial reductions before full exits.
- Safety constraints preserved:
  - Exchange position/order truth precedence retained (`BYBIT EXCHANGE POSITION/OPEN_ORDERS IS THE SOURCE OF TRUTH`).
  - Friction line and net edge language retained to avoid gross-positive/net-negative churn exits.
- Verification:
  - `python3 -m py_compile utils/deepseek_client.py` passed.
  - `python3 -m pytest tests/test_strategy_components.py -q` passed (`9 passed`).

## Plan (24h Log Forensics + Minimal Safety Fixes - 2026-05-29)
- [x] Complete 24-hour log taxonomy with event signatures, frequencies, and code-source mapping.
- [x] Reconstruct high-impact LLM decision chains (entry/exit/reject/mismatch/error windows) with timestamped evidence.
- [x] Cross-check `log_dashboard.py` filters against observed taxonomy and implement minimal missing trace filters.
- [x] Implement smallest safe state-awareness fix so Bybit flat state is treated as source-of-truth for decision context when local cache is stale.
- [x] Apply compact prompt improvement to reduce naive percentage exits by explicitly considering fees/spread/slippage and structure.
- [x] Verify changes with compile/lint-safe checks and dashboard parse run on latest logs.
- [x] Check active bot processes, report live vs demo mode, and provide safe restart/monitor commands.
- [x] Write review summary in this file with evidence-backed outcomes and residual risks.

## Review (24h Log Forensics + Minimal Safety Fixes - 2026-05-29)
- 24h forensic scope: `9437` JSON log events across 5 files; dominant components were `DeepSeekAIStrategy`, `ExecClient-BYBIT`, `Portfolio`, `ExecEngine`.
- Critical operational finding: two concurrent `main_live.py` processes were running (`PID 11522` and `PID 37618`), each writing separate `deepseek_trader_*.json` files with overlapping decision timestamps.
- State-mismatch finding: in `deepseek_trader_2026-05-27_233004:698.json`, there were `130` paired cycles where `Current Position: short ...` coexisted with `Bybit Risk Context ... position=flat 0`, causing repeated reduce-only rejects (`155`x `110017`).
- Decision-quality finding: multiple exits were triggered on tiny gains (e.g. `0.134%`) while realized PnL after fees could be negative (example close event at `2026-05-28T00:10:09Z` had `realized_return=0.00012` but `realized_pnl=-11.78 USDT`).
- Reliability finding: `33` fallback decisions caused by repeated `APIConnectionError` retries (`66` failed attempts logged).
- Implemented minimal changes:
  - `strategy/deepseek_strategy.py`: Bybit-flat truth override in `_merge_exchange_position_context` when `risk_context.ok=true` and no open orders, preventing stale local-position decisioning.
  - `utils/deepseek_client.py`: compact prompt rules now require friction-aware exits (fees/spread/slippage), discourage tiny-percentage-only exits, and reiterate exchange-state truth precedence.
  - `log_dashboard.py`: upgraded parser to support JSON logs + text logs, default 24h window, added event taxonomy trace list with filter/search controls for LLM chain, position/order state, mismatches, rejections, and failures.
  - `start_trader.sh`: single-process safety guard to prevent accidental dual `main_live.py` launches.
- Verification:
  - `python -m py_compile strategy/deepseek_strategy.py utils/deepseek_client.py log_dashboard.py` passed.
  - `bash -n start_trader.sh restart_trader.sh stop_trader.sh` passed.
  - `python log_dashboard.py logs/deepseek_trader_2026-05-27_233004:698.json 24` succeeded (`Events: 3115`, dashboard rendered).
  - Performed runtime process cleanup and restart: stopped tracked PID, terminated stale orphan PID, then started one fresh process (`PID 28247`) in demo mode.
- Residual risks:
  - Old overlapping sessions already polluted historical logs; keep per-run analysis scoped to one active log file whenever possible.
  - External API connectivity instability (`APIConnectionError`) remains environmental/provider-side and is not fully solved by this patch.

## Plan (TA Migration + 5m Cadence + Prompt Minimization - 2026-05-27)
- [x] Audit current TechnicalIndicatorManager for remaining hand-rolled computations and identify Nautilus-native replacements.
- [x] Delegate implementation via Cursor CLI: add ATR + ADX-style trend-strength features, switch remaining TA components to Nautilus indicators, keep TechnicalManager thin.
- [x] Delegate implementation via Cursor CLI: switch live defaults to 5m cadence (`TIMEFRAME=5m`, `TIMER_INTERVAL_SEC=300`) in production startup/config path.
- [x] Delegate implementation via Cursor CLI: minimize LLM prompt instructions and formatting overhead while preserving strict JSON schema.
- [x] Run compile + focused tests and dry runtime validation to verify no regressions in signal loop, indicator readiness, and journal output fields.
- [x] Produce quant-style verification summary and residual risks.

## Review (TA Migration + 5m Cadence + Prompt Minimization - 2026-05-27)
- Delegated implementation to Cursor CLI in two passes (primary change-set + targeted ADX field follow-up), then re-verified locally.
- Technical manager modernization:
  - Replaced manual Bollinger computation with Nautilus `BollingerBands`.
  - Added Nautilus `AverageTrueRange` and `DirectionalMovement`.
  - Added explicit volatility/trend-strength outputs: `atr`, `atr_pct`, `dmi_pos`, `dmi_neg`, `dmi_dx`, `adx`.
  - Kept manager as aggregation layer; preserved existing key outputs for compatibility.
- 5-minute trading cadence defaults:
  - `main_live.py` default `TIMEFRAME` changed to `5m`.
  - default `TIMER_INTERVAL_SEC` fallback changed to `300`.
  - `start_trader.sh` exports changed to `TIMEFRAME=5m`, `TIMER_INTERVAL_SEC=300`.
  - `configs/strategy_config.yaml` default `bar_type` changed to `...-5-MINUTE-...` and `timer_interval_sec: 300`.
- Prompt minimization:
  - System prompt simplified to compact role + strict JSON-only requirement.
  - User prompt significantly compressed (data-first, concise schema reminder, reduced behavioral forcing).
  - Added `atr/adx` into compact technical payload presented to LLM.
- Verification:
  - `python3 -m py_compile indicators/technical_manager.py utils/deepseek_client.py main_live.py strategy/deepseek_strategy.py` passed.
  - `pytest tests/test_strategy_components.py tests/test_integration_mock.py -q` passed (`9 passed`).
  - Runtime smoke boot confirmed 5m path:
    - `Sentiment fetcher initialized with timeframe: 5m`
    - `Pre-fetching ... interval=5m`
    - `SubscribeBars(...-5-MINUTE-LAST-EXTERNAL)`

## Plan (Live E2E Verification - LLM -> Decision -> Order Trigger - 2026-05-27)
- [x] Start trader process in the configured live demo mode and confirm process health.
- [x] Capture runtime logs proving outbound LLM call attempts and returned model decision payload.
- [x] Verify the strategy converts model output into a concrete trade decision path (BUY/SELL/HOLD + confidence).
- [x] Verify an order trigger event is emitted from that decision path (submit/skip/reject reason captured in logs).
- [x] Summarize evidence with exact timestamps and pass/fail per stage.

## Review (Live E2E Verification - LLM -> Decision -> Order Trigger - 2026-05-27)
- Executed foreground live run via `zsh -ic 'python main_live.py'` (startup through `RUNNING` confirmed).
- Bar-close decision cycles observed:
  - `2026-05-26T23:37:00Z`: `📌 Bar-close synth` -> `Calling DeepSeek AI (bar-aligned synthesis)` -> valid `LLM Response JSON` -> `🤖 Signal: HOLD | Confidence: LOW`.
  - `2026-05-26T23:38:00Z`: same full LLM path with valid JSON response and `HOLD | LOW`.
- Execution path verification:
  - Both cycles hit risk gate: `Signal confidence LOW below minimum MEDIUM, skipping trade`.
  - No `Submitted ... order` / `Submitted bracket order` lines were emitted in these validated cycles.
  - Trade journal rows confirm execution outcome as skipped (`execution_status=skipped`, note `confidence_below_min:LOW<MEDIUM`).
- Verdict for requested chain:
  - LLM calls flowing: `PASS`.
  - LLM decision produced: `PASS`.
  - Order triggered from decision: `FAIL (not triggered in this run window due to confidence gate)`.

## Review (Live Re-Validation After zshrc Typo Fix - 2026-05-27)
- Interactive shell check (`zsh -ic`) now confirms all required vars resolve: `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `DEEPSEEK_API_KEY`.
- Fresh live run executed with `zsh -ic 'python main_live.py'`:
  - Strategy reached `RUNNING`.
  - Bar-close trigger fired at `2026-05-26T23:00:00Z` and `2026-05-26T23:01:00Z`.
  - Timer remained ops-only: `⏲️ Ops timer: maintenance (no standalone LLM call).`
- Journal validation from latest rows in `logs/trade_journal.csv`:
  - `decision_cycle_trigger=on_bar`
  - timing fields populated (`bar_close_ts`, `decision_ts`, `execution_ts`, `latency_ms`)
  - volume fields populated (`rvol`, `volume_zscore`, `volume_trend_slope`, `technical_volume_regime`, `directional_volume_confirmation`)
  - OB window payloads populated (`ob_window_fast_json`, `ob_window_main_json`, `ob_window_context_json`)
  - fallback reasoning captured (`reasoning_content=fallback_default_no_model_output`)
- Remaining blocker unchanged: DeepSeek API responses still return `402 Insufficient Balance`, so strategy falls back to `HOLD/LOW`.
- Shell warning still present from `.zshrc`: missing sourced file `/Users/akshayapsingi/.openclaw/completions/openclaw.zsh`.

## Review (Live Re-Validation After zshrc Update - 2026-05-27)
- Re-validated using interactive shell (`zsh -ic`) after user refreshed `~/.zshrc`.
- Runtime `python main_live.py` live run proved env/key wiring + strategy startup path:
  - Bybit clients initialized and strategy reached `RUNNING`.
  - Bar-aligned trigger fired (`📌 Bar-close synth @ ...`).
  - Timer remained ops-only (`⏲️ Ops timer: maintenance (no standalone LLM call).`).
  - DeepSeek call path active but still returns `402 Insufficient Balance`; fallback held (`HOLD/LOW`).
- Latest journal row in `logs/trade_journal.csv` validated:
  - `decision_cycle_trigger=on_bar`
  - populated timing (`bar_close_ts`, `decision_ts`, `execution_ts`, `latency_ms`)
  - populated volume (`rvol`, `volume_zscore`, `volume_trend_slope`, `technical_volume_regime`, `directional_volume_confirmation`)
  - populated OB windows (`ob_window_fast_json`, `ob_window_main_json`, `ob_window_context_json`)
  - fallback reasoning capture present (`reasoning_content=fallback_default_no_model_output`)
- Operational caveat:
  - In this Codex execution environment, detached `nohup` children are reclaimed after command return, so `start_trader.sh` appears to return an ephemeral PID with an empty new log. Foreground live run is healthy.
  - `.zshrc` still logs `exprt` typo and missing `/Users/akshayapsingi/.openclaw/completions/openclaw.zsh`.

## Plan (Live Deploy Validation - 2026-05-27)
- [x] Validate runtime prerequisites after user refreshed zsh env.
- [x] Restart trader in live mode and verify process health after bootstrap.
- [x] Verify bar-aligned decision loop and ops-only timer behavior from fresh logs.
- [x] Verify latest journal row includes timing, reasoning, OB-window, and volume-regime fields.

## Review (Live Deploy Validation - 2026-05-27)
- Deployment executed with `zsh -ic './start_trader.sh'` (fresh process `PID 67595`).
- Runtime health confirmed after 75s hold:
  - process alive (`python main_live.py`).
  - bar-close cycle emitted (`📌 Bar-close synth ...`).
  - timer emitted ops-only message (`⏲️ Ops timer: maintenance (no standalone LLM call)`).
- LLM calls still returning `402 Insufficient Balance` from DeepSeek API, strategy correctly falls back to `HOLD LOW`.
- Journal integrity confirmed from latest row in `logs/trade_journal.csv`:
  - `decision_cycle_trigger=on_bar`
  - timing fields populated (`bar_close_ts`, `decision_ts`, `execution_ts`, `latency_ms`)
  - `reasoning_content=fallback_default_no_model_output`
  - volume + OB window fields populated (`rvol`, `volume_zscore`, `volume_trend_slope`, `technical_volume_regime`, `ob_window_*_json`).
- Noted shell issue from user zsh startup:
  - `.zshrc:20` has `exprt` typo (should be `export`).
  - missing sourced file warning for `/Users/akshayapsingi/.openclaw/completions/openclaw.zsh`.

## Plan (Production Upgrade + Validation Cycle - 2026-05-27)
- [x] Recon baseline behavior and schema gaps for trigger flow, prompt architecture, OB windows, and journal fields.
- [x] Delegate implementation to Cursor: move decision trigger to `on_bar`, keep `on_timer` ops-only, and preserve non-signal maintenance behavior.
- [x] Delegate implementation to Cursor: add timeframe-aware OB windows (`W_fast=60s`, `W_main=TF`, `W_context=3xTF`) with compact regime labels for LLM + CSV.
- [x] Delegate implementation to Cursor: add volume regime features (`rvol`, `volume_zscore`, `volume_trend_slope`, directional confirmation, regime labels).
- [x] Delegate implementation to Cursor: refactor DeepSeek prompt/schema from weighted rules-engine style to synthesis-engine style with concise output fields.
- [x] Delegate implementation to Cursor: harden trade journal row contract (single decision row per cycle, robust `reasoning_content`, latency/timing + compact JSON snapshots).
- [x] Run compile + focused tests and capture deterministic pass/fail output.
- [x] Run dry-run validation (5m + 15m mode) with evidence pack: bar-aligned triggers, CSV append/header behavior, OB window population, volume fields, reasoning capture, and latency.
- [x] Produce quant verdict (`PASS|CONDITIONAL PASS|FAIL`) with residual risks and next action.
- [x] Produce execution-minimal autonomous prompt (~35% shorter), save it to automation target, and verify it executes correctly.

## Check-In (Production Upgrade + Validation Cycle - 2026-05-27)
- Bar-close `_run_bar_close_decision_cycle` wired from `on_bar`; `on_timer` is ops-only (risk refresh, trailing, OCO, OB CSV append).
- Analyzer prompt/schema migrated to synthesis format; fallback now emits non-empty `reasoning_content` markers.
- OB windows + labels and volume regime fields are present in runtime payloads and journal rows.
- `python3 -m py_compile` passes for updated strategy/prompt/journal/indicator/tests modules.
- `pytest tests/test_strategy_components.py tests/test_integration_mock.py` passes: `9 passed`.

## Review (Production Upgrade + Validation Cycle - 2026-05-27)
- Runtime evidence captured from `logs/stage_validation_1m_20260527_012539.log` and `logs/stage_validation_1m_b_20260527_013108.log` shows:
  - `on_bar` drives decisions (`📌 Bar-close synth ...`, followed by `Calling DeepSeek AI (bar-aligned synthesis)...`).
  - `on_timer` is maintenance-only (`⏲️ Ops timer: maintenance (no standalone LLM call).`).
- CSV evidence:
  - `logs/trade_journal_scope_1m.csv` and `logs/trade_journal_scope_1m_b.csv` each created with a single header and append rows only.
  - Timing fields populated: `bar_close_ts_utc/bar_close_ts`, `decision_ts_utc/decision_ts`, `execution_ts_utc/execution_ts`, `latency_ms`.
  - Volume fields populated: `rvol`, `volume_zscore`, `volume_trend_slope`, `directional_volume_confirmation`, `technical_volume_regime`.
  - OB windows populated in each row: `ob_window_fast_json`, `ob_window_main_json`, `ob_window_context_json`.
  - Fallback reasoning capture verified as non-empty (`fallback_default_no_model_output`).
- Timeframe window policy validation:
  - Synthetic manager check confirms 5m -> `W_fast/W_main/W_context = 60/300/900`.
  - Synthetic manager check confirms 15m -> `W_fast/W_main/W_context = 60/900/2700`.
- Automation:
  - Added execution-minimal run prompt file: `/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/tasks/autonomous_daily_prompt_minimal.md`.
  - Created automation `nautilus-daily-validation-minimal` (daily heartbeat) and successfully rendered it via `view`.

## Plan (Desk Summary - Current Data / Features / LLM Perception - 2026-05-22)
- [x] Trace the current runtime cadence and active data path from strategy timer, bar feed, and order-book subscriptions.
- [x] Extract the exact feature sets from technical, microstructure, sentiment, and risk-context layers.
- [x] Summarize how the LLM currently receives and weights each information source, including known limitations around timeframe alignment.
- [x] Record the desk-facing summary for sharing.

## Review (Desk Summary - Current Data / Features / LLM Perception - 2026-05-22)
- Runtime cadence confirmed from code:
  - `main_live.py` defaults `TIMEFRAME` to `1m` when env is unset, while YAML still documents a 15-minute bar setup.
  - Timer loop is currently configured at 60 seconds.
  - Order-book deltas and trade ticks are event-driven and continuous.
- LLM input path confirmed:
  - `on_timer()` builds `price_data`, `technical_data`, `microstructure`, `sentiment`, `current_position`, and `risk_context`, then calls `deepseek.analyze(...)`.
  - Prompt payload audit log is compact; full prompt contains richer formatted sections and explicit heuristic weights.
- Feature inventory confirmed:
  - Technical: SMA/EMA, RSI, MACD, Bollinger, support/resistance, volume ratio, trend labels, recent K-lines.
  - Microstructure: spread, microprice, TOB/depth imbalance, weighted depth imbalance, OFI/EMA OFI, queue pressure, trade-flow imbalance, sweeps, VWAP deviation, spread volatility, depth regime.
  - Risk: wallet, exchange position, open orders, recent executions/closed P&L summary.
- Important desk caveats:
  - LLM weights are prompt instructions, not a calibrated statistical ensemble.
  - Sentiment fetch currently defaults to `BTC` unless separately wired to active instrument.
  - OB features are short-horizon/event-cadence; they are not yet bar-aligned 15-minute aggregates.

## Plan (Capability Review - Multi-Coin / Multi-Timeframe / OB Reuse - 2026-05-22)
- [x] Inspect live config and entrypoint for instrument/timeframe parameterization.
- [x] Verify whether order-book subscriptions and features are active in the live strategy path.
- [x] Determine whether the current architecture supports single-run reuse only or true concurrent multi-coin / multi-timeframe scanning.
- [x] Record the conclusion and concrete next-step recommendation.

## Review (Capability Review - Multi-Coin / Multi-Timeframe / OB Reuse - 2026-05-22)
- Current live path already accepts a dynamic `INSTRUMENT_ID` and derives `bar_type` from `TIMEFRAME`, so the same strategy can be reused for different coin/timeframe pairs without changing core logic.
- Order-book support is live, not stubbed: the strategy subscribes to order-book deltas and trade ticks, computes microstructure features through `OrderBookManager`, logs summaries, and injects the resulting feature set into the LLM prompt payload.
- The present architecture is single-context per strategy instance: one `instrument_id`, one `bar_type`, one technical-indicator state, one order-book state, and one risk/execution context.
- `TradingNodeConfig` is currently built with a single strategy config entry, so a thin utility class that just passes coin + timeframe is enough for sequential reuse, but not enough by itself for true concurrent market scanning/trading across many symbols/timeframes.
- Important behavior nuance: the repo currently decouples bar timeframe from decision cadence. With 15-minute bars and a 60-second timer, the system can re-evaluate the same 15-minute bar while order-book features evolve intrabar.
- Recommended next step if multi-asset analysis is the goal: extract a pure `MarketAnalysisContextBuilder` / `AnalysisRunner` layer that accepts `(instrument_id, timeframe)` and returns technical + microstructure + AI analysis without owning execution. Keep execution strategy instances separate from scanner instances.

## Plan (Autonomous Run - B1 Feature History Persistence - 2026-05-21)
- [x] Run health gate and mandatory trader behavior audit from status + dashboard + fresh logs.
- [x] Execute `BUILD_NEXT` for backlog item B1 and patch feature persistence to avoid CSV truncation.
- [x] Validate append behavior and IC compatibility, then record autonomous run evidence/state updates.

## Review (Autonomous Run - B1 Feature History Persistence - 2026-05-21)
- Health gate: GREEN (`process.running=true` inferred from fresh logs, latest timestamp progressing, status snapshot coherent, demo-safety flags intact).
- Mandatory trader behavior audit:
  - Signal changed. Did position actually flip? `No opposite-side BUY/SELL reversal event observed; position remained SHORT while signals oscillated SELL/HOLD.`
  - Did we close-only or close-and-reverse? `Neither triggered this run window (no BUY-on-SHORT or SELL-on-LONG transition).`
  - Did resulting position notional match sizing policy? `Yes; logs continue to show fixed sizing near $10k notional while holding short inventory.`
- Implemented B1 in [`/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/indicators/orderbook_manager.py`](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/indicators/orderbook_manager.py):
  - `dump_features_csv` now appends unseen rows by timestamp and avoids file truncation.
- Validation:
  - `python3 -m py_compile indicators/orderbook_manager.py` passed.
  - Synthetic append proof (same serializer path): line count grew `11 -> 16`; IC utility remained functional with `n_rows=15`.
  - `python3 -c "from indicators.orderbook_manager import OrderBookManager as O;print(O.compute_ic_from_csv('data/microstructure_features.csv')['n_rows'])"` returned `500` on current live file (compatible schema retained).

## Plan (Exchange Risk Context + Position Reconciliation - 2026-05-20)
- [x] Add a read-only Bybit account context helper for wallet, open positions, open orders, recent executions, and closed P&L.
- [x] Fix strategy position awareness to aggregate all open Nautilus positions for the active instrument instead of using the first cache position.
- [x] Feed compact account/order/trade risk context into the DeepSeek prompt payload and human prompt.
- [x] Make position sizing respect actual account context and instrument quantity increments so tiny adjustment orders are skipped safely.
- [x] Extend the dashboard with direct Bybit exchange portfolio/trade context alongside log-derived Nautilus state.
- [x] Run compile and focused behavior verification, then capture results here.

## Review (Exchange Risk Context + Position Reconciliation - 2026-05-20)
- Added `utils/bybit_account_context.py` for signed read-only Bybit V5 GETs: wallet, active position, open orders, executions, and closed P&L.
- Fixed strategy position context to aggregate multiple Nautilus open positions for the same instrument and override/fallback to Bybit context when Nautilus is missing or materially stale.
- Added LLM risk context: wallet equity/available balance, exchange position, open orders, and last 5 closed-trade outcomes/P&L.
- Changed sizing so `fixed_trade_usdt` is now the base target, while confidence/trend/RSI still scale the final notional; caps use exchange equity/available balance when present.
- Added instrument-increment normalization so sub-`0.01 ETH` adjustment noise is skipped before Nautilus rejects it.
- Extended dashboard JSON/HTML with direct Bybit portfolio, exchange position, open orders, executions, and closed P&L.
- Validation:
  - `python -m py_compile utils/__init__.py utils/bybit_account_context.py utils/deepseek_client.py strategy/deepseek_strategy.py tools/serve_monitor_dashboard.py tests/test_risk_context.py` passed.
  - `python -m pytest tests/test_risk_context.py -q` passed: 3 tests.
  - Dashboard direct Bybit snapshot succeeded and showed the live mismatch clearly: log/Nautilus-derived position around `4.69 ETH` short, exchange position around `9.26 ETH` short, open orders `0`, last 5 closed P&L about `-61.22 USDT`.
  - `npx --yes pyright --pythonpath "$(which python)" ...` ran; remaining failures are pre-existing strategy typing issues in `strategy/deepseek_strategy.py` (optional Nautilus instrument/orderbook/telegram fields and SMA config typing), with the repo config still pointing at `/home/ubuntu/deepseek_venv`.

## Plan (README Reframe - Solo AI Trading Agent Experiment - 2026-05-17)
- [x] Audit current README, repo remotes, and local docs for original upstream attribution.
- [x] Rewrite README around this fork as a solo developer experiment, with clear scope and safety disclaimers.
- [x] Highlight the added Bybit order book pipeline, raw features, derived microstructure signals, LLM decision loop, dashboard, and backtesting utilities.
- [x] Add proper acknowledgement to the original `Patrick-code-Bot/nautilus_AItrader` repo and NautilusTrader.
- [x] Verify README consistency for stale Binance/BTC-only claims and update this task with review notes.

## Review (README Reframe - Solo AI Trading Agent Experiment - 2026-05-17)
- Replaced the upstream-style 1,800-line README with a focused 324-line README for this fork.
- Added explicit acknowledgement that this project forks and extends `Patrick-code-Bot/nautilus_AItrader`.
- Reframed the project as a solo developer research experiment, not a production-ready or profitable strategy claim.
- Documented current fork-specific work: Bybit integration, L2 order book ingestion, microstructure feature pipeline, LLM prompt audit logs, dashboard, and Nautilus-native backtesting tools.
- Verification: searched the new README for stale Binance/BTC-PERP/upstream versioning claims; remaining BTC references are current Bybit defaults/backtest examples only.

## Plan (Dynamic Instrument Context + Prompt Refinement - 2026-05-17)
- [x] Remove hardcoded BTC/BTCUSDT references from DeepSeek system/user prompt context.
- [x] Make instrument, venue, pair label, base asset unit, and timeframe labels dynamic in `DeepSeekAnalyzer`.
- [x] Guard against stale `signal_history` across instrument switches by resetting history when context changes.
- [x] Pass explicit instrument metadata from strategy into analyzer input payload.
- [x] Add quant-oriented prompt refinements for microstructure-aware confidence handling without changing output JSON schema.
- [x] Run static compile verification and confirm references via targeted grep.
- [x] Update `tasks/todo.md` and `progress_log.md` with outcomes.

## Plan (Microstructure Prompt Integration + ETH Demo Live Restart - 2026-05-17)
- [x] Verify mandatory context files and confirm existing microstructure feed path into `deepseek.analyze`.
- [x] Implement prompt-body microstructure section in `utils/deepseek_client.py` while keeping `_build_prompt_payload()` and JSON schema/parsing unchanged.
- [x] Add explicit runtime marker `🤖 Prompt microstructure section included: true|false` to prove prompt inclusion per analysis call.
- [x] Keep strategy feed path unchanged and verify `price_data['microstructure']` attachment in `strategy/deepseek_strategy.py`.
- [x] Update `.env` to deterministic ETH demo live settings (`INSTRUMENT_ID=ETHUSDT-LINEAR.BYBIT`, `BYBIT_DEMO=true`, `BYBIT_TESTNET=false`, `DRY_RUN=false`).
- [x] Run static compile checks for target files.
- [x] Restart via required command (`stop_trader` + `start_paper_demo.sh` with ETH/demo/live flags).
- [x] Run runtime/status checks and log evidence checks from latest logs after several analysis cycles.
- [x] Run reasoning-usage and regression checks (OB-term mention rate + parse/fallback scan).
- [x] Update `progress_log.md` with concrete evidence and outcomes.

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

## Review (Autonomous Run 2026-05-17 02:00 AEST - HEAL)
- Health gate before fix: RED due `process.running=false` when sandbox restrictions blocked reliable PID probing, despite fresh strategy heartbeat logs.
- HEAL fix applied in `tools/serve_monitor_dashboard.py`:
  - Added timestamp freshness helper and fallback that infers `process.running=true` when all conditions hold:
    - `strategy_running_log=true`
    - fresh `log_timestamp_utc` (<= 180s)
    - active runtime activity (`analysis_cycles` or `deepseek_calls` > 0)
  - Marks inferred state explicitly via `process.inferred_from_logs=true`.
- Validation evidence:
  - `python3 -m py_compile tools/serve_monitor_dashboard.py` passed.
  - `./check_strategy_status.sh` now reports `process.running=true` with `inferred_from_logs=true`.
  - `python3 tools/serve_monitor_dashboard.py --print-json` reports coherent fresh runtime snapshot:
    - last signal `SELL`
    - open position `SHORT 0.128 @ 78264.5`
    - latest log timestamp `2026-05-16T16:37:58.683030000Z`
- Mandatory trader behavior audit:
  - Signal changed. Did position actually flip? `No flip observed in latest window (HOLD↔SELL while position remained SHORT).`
  - Did we close-only or close-and-reverse? `Neither observed this run; no BUY/SELL opposite-side transition event in parsed logs.`
  - Did resulting position notional match sizing policy? `Yes; logs show fixed sizing line ~ $10k notional (0.128 BTC).`

## Review (Autonomous Run 2026-05-17 11:00 AEST - VERIFY_PREV_SHIP)
- Verified commit `62d5133` behavior from fresh runtime snapshot:
  - `process.running=true` with `inferred_from_logs=true`
  - fresh `log_timestamp_utc` (`2026-05-17T01:24:56.808390000Z`)
  - coherent state: last signal `SELL`, open position `SHORT 0.128 @ 78261.41`, deepseek calls increasing.
- Mandatory trader behavior audit:
  - Signal changed. Did position actually flip? `No BUY↔SELL direction flip in latest window; signal oscillated HOLD/SELL while net side stayed SHORT.`
  - Did we close-only or close-and-reverse? `Not triggered this hour due no opposite-direction transition.`
  - Did resulting position notional match sizing policy? `Yes; 0.128 BTC at ~77.9k ≈ $10k target notional.`

## Plan (Backtesting Layer - Nautilus Native Only - 2026-05-17)
- [x] Audit existing Nautilus usage and create minimal reusable backtest adapter module (no custom replay/fill/portfolio engine).
- [x] Add backtest config file `configs/backtest_config.yaml` with venue, instrument, date range, split, cost assumptions, and variant toggles.
- [x] Implement historical bars ingestion script for Bybit BTCUSDT 15m -> Nautilus `ParquetDataCatalog` using `BarDataWrangler`.
- [x] Implement catalog validation command to report bar count, UTC date coverage, and missing 15m intervals.
- [x] Implement deterministic analyzer modes for backtest only:
- [x] `recorded_llm_replay` (timestamp-aligned signal replay from `logs/deepseek_trader_*.json*`, no external API calls).
- [x] `rule_proxy` (deterministic technical-only decision proxy).
- [x] Add mode/config plumbing that keeps live LLM path untouched.
- [x] Implement single backtest CLI runner `tools/run_backtest.py --config ...` using Nautilus `BacktestEngine`/`BacktestNode` + catalog data.
- [x] Implement variant execution for `buy_and_hold`, `rule_proxy`, `recorded_llm_replay`; skip with explicit reason when inputs are insufficient.
- [x] Implement split protocol (time-ordered 70/30) and mandatory OOS reporting.
- [x] Implement results export to `backtest_results/{timestamp}_{run_id}/`:
- [x] config snapshot
- [x] metrics (JSON + CSV)
- [x] trade list
- [x] equity curve
- [x] run log (assumptions, warnings, insufficiency reasons)
- [x] Add cost sensitivity reruns for slippage 1/2/5 bps and include configured fees.
- [x] Add determinism hash + sanity flags + viability verdict (`PASS` / `KILL` / `INCONCLUSIVE`) based on OOS metrics.
- [x] Run verification commands and capture outputs in review section below.

## Review (Backtesting Layer - Nautilus Native Only - 2026-05-17)
- Implemented files:
  - `backtesting/` module (`data_pipeline.py`, `instruments.py`, `replay.py`, `metrics.py`, `__init__.py`)
  - `strategy/backtest_variants.py`
  - `tools/fetch_bybit_bars.py`
  - `tools/run_backtest.py`
  - `configs/backtest_config.yaml`
  - `strategy/__init__.py` updated for backtest variant exports.
- Verification commands + key outputs:
  - `python3 -m py_compile backtesting/__init__.py backtesting/instruments.py backtesting/replay.py backtesting/metrics.py backtesting/data_pipeline.py strategy/backtest_variants.py tools/fetch_bybit_bars.py tools/run_backtest.py`
    - Result: pass.
  - `python3 tools/fetch_bybit_bars.py fetch --start 2025-01-01T00:00:00Z --end 2025-07-01T00:00:00Z --catalog-path data/catalog --symbol BTCUSDT --instrument-id BTCUSDT-LINEAR.BYBIT --bar-type BTCUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL --interval-minutes 15 --maker-fee 0.0002 --taker-fee 0.00055`
    - Result: 17,377 bars written, coverage 2025-01-01 to 2025-07-01, missing intervals 0.
  - `python3 tools/fetch_bybit_bars.py validate --catalog-path data/catalog --bar-type BTCUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL --interval-minutes 15`
    - Result: bars 17,377; expected bars 17,377; missing intervals 0.
  - `python3 tools/run_backtest.py --config configs/backtest_config.yaml`
    - Result: artifacts written to `backtest_results/20260517T092226Z_btc15m_native_backtest/`
    - Metrics rows: 12 (variants x split x slippage scenarios)
    - Replay variant skipped with explicit reason: no timestamp overlap between replay logs and configured backtest window.
    - Viability verdict: `KILL`.
    - Determinism hash: `c46c88fb5a839617d18ace784e6279335831ca8a23c25fac9c6365bfc7f9c5b0`.
  - `python3 tools/run_backtest.py --config configs/backtest_config.yaml` (repeat)
    - Result: identical determinism hash `c46c88fb5a839617d18ace784e6279335831ca8a23c25fac9c6365bfc7f9c5b0`.

## Plan (IC-Driven Decision Layer Research - 2026-05-24)
- [x] Inspect the current live decision path, prompt weighting, sentiment semantics, and position sizing logic.
- [x] Read the attached IC article and extract the useful ideas around weak-signal combination, independence, and uncertainty-aware sizing.
- [x] Synthesize concrete signal candidates, statistical evaluation methods, and an implementation order suitable for the current 1m + order-book runtime.

## Review (IC-Driven Decision Layer Research - 2026-05-24)
- Current architecture still mixes jobs that should be separated:
  - `utils/deepseek_client.py` hard-codes static prompt weights for technical, microstructure, sentiment, and risk.
  - `strategy/deepseek_strategy.py` still sizes off confidence/trend/RSI multipliers instead of a stop-distance risk budget.
  - `utils/sentiment_client.py` remains BTC-oriented and the live strategy currently calls it without passing the active instrument symbol.
- The attached article is directionally useful on three points:
  - weak signals can compound if they are genuinely independent,
  - correlation between signals matters more than raw feature count,
  - sizing should shrink when edge uncertainty is unstable.
- Most useful application to this repo:
  - treat each technical, microstructure, sentiment, and higher-timeframe feature as a measurable weak signal,
  - compute forward-return IC by horizon and regime,
  - penalize correlated features and aggregate surviving signals into a pre-LLM decision-layer score,
  - keep the LLM as a conditional explainer or tie-breaker, not the primary weight allocator.
- Recommended build order:
  - fix sentiment scope/confidence semantics,
  - add regime labels and conditional IC logging,
  - add a feature-research pipeline beyond the current microstructure-only CSV dump,
  - build a small ensemble score from decorrelated weak signals,
  - externalize stop-based sizing and uncertainty throttles.

## Plan (Decision Audit Trail - CSV Journal + Reasoning Capture - 2026-05-27)
- [x] Capture DeepSeek `reasoning_content` safely in analyzer output metadata.
- [x] Add an append-only CSV trade journal writer with stable schema for downstream analysis.
- [x] Wire strategy decision loop to persist one decision row per model call with market/context snapshots.
- [x] Run compile verification and record implementation review notes.

## Review (Decision Audit Trail - CSV Journal + Reasoning Capture - 2026-05-27)
- Added new utility: `utils/trade_journal.py`
  - `TradeJournalCSV` provides append-only CSV persistence with a fixed column schema.
  - Handles header creation and JSON-serialization for nested snapshot fields.
- Updated `utils/deepseek_client.py`:
  - Captures `reasoning_content` from `response.choices[0].message.reasoning_content` when present.
  - Adds `reasoning_content` and `llm_model` into the returned `signal_data`.
  - Extends fallback payload to include empty `reasoning_content` + `llm_model`.
- Updated `strategy/deepseek_strategy.py`:
  - Added trade-journal init via env:
    - `TRADE_JOURNAL_ENABLED` (default true)
    - `TRADE_JOURNAL_CSV_PATH` (default `logs/trade_journal.csv`)
  - Added bar timestamps (`bar_ts_event`, `bar_ts_init`) into `price_data`.
  - Adds measured API latency (`llm_api_seconds`) onto each signal.
  - `_execute_trade(...)` now returns structured execution summaries (`status/action/note/...`) for journaling.
  - Added `_append_trade_journal_row(...)` to write one CSV row per LLM decision cycle with:
    - signal/confidence/reason/reasoning
    - market + technical + microstructure snapshots
    - risk context summary
    - position before/after snapshots
    - execution intent summary
- Verification:
  - `python3 -m py_compile utils/deepseek_client.py utils/trade_journal.py strategy/deepseek_strategy.py` passed.

## Plan (Scalp Profit-Taking + Prompt/Log Audit from Latest Session - 2026-05-27)
- [x] Reconstruct the latest session directly from `logs/deepseek_trader_2026-05-27_002731:811.json` and validate trade-by-trade flow, including reversals, adds, and missed profit exits.
- [x] Audit exact LLM payload quality (trend labels, volume/OB semantics, position context richness, language drift) and identify interpretation failure points.
- [x] Implement deterministic scalp behavior guardrails in strategy execution (profit capture and retrace-aware add/hold controls) so outcomes are not prompt-only.
- [x] Tighten LLM prompt contract for scalp posture (English-only, profit-protection hierarchy, exit-first when giveback grows) and reduce ambiguous instructions.
- [x] Reduce noisy log payloads while preserving decision-audit value; ensure future 1-3h runs stay readable.
- [x] Run compile + focused tests and provide a validated review with residual risk notes.

## Review (Scalp Profit-Taking + Prompt/Log Audit from Latest Session - 2026-05-27)
- Session reconstruction + payload audit from `logs/deepseek_trader_2026-05-27_002731:811.json` confirmed:
  - trend labels were Chinese in that run (`强势上涨/强势下跌/震荡整理`) and no `position_health` context reached the model.
  - model language drift occurred (4 signal reasons with CJK text).
  - profitable positions were repeatedly held through giveback, and same-direction add attempts happened during retraces.
- Implemented strategy guardrails in `strategy/deepseek_strategy.py`:
  - added deterministic scalp profit guard config fields to `DeepSeekAIStrategyConfig`.
  - replaced hardcoded giveback block with `_apply_scalp_profit_guard(...)` supporting:
    - full exit on deep giveback,
    - partial reduce on moderate giveback,
    - audit journaling for guard-triggered actions.
  - added no-add-on-retrace protection in `_manage_existing_position(...)`.
  - added short same-direction re-entry cooldown guard after forced full exits.
  - aligned `position_health.recommendation` thresholds to scalp guard settings.
- Implemented prompt/log hardening in `utils/deepseek_client.py`:
  - normalize legacy Chinese trend labels before prompt construction.
  - reject non-English synthesis payloads and force retry/fallback path.
  - reduce log noise: INFO now emits compact context summary; full payload moved to DEBUG.
  - compact `previous_signal` in payload logs to avoid recursive reasoning-content bloat.
  - tightened scalp prompt framing for exit-first behavior on >30% giveback.
- Improved volume semantics in `indicators/technical_manager.py`:
  - replaced `weak_volume_drift` with directional labels:
    - `up_move_weak_volume`
    - `down_move_weak_volume`
- Verification:
  - `python3 -m py_compile utils/deepseek_client.py strategy/deepseek_strategy.py indicators/technical_manager.py` passed.
  - `pytest tests/test_strategy_components.py -q` passed (`8 passed`).

## Plan (Scalp Logic Simplification After User Review - 2026-05-27)
- [x] Remove deterministic scalp state-machine guardrails from strategy execution and keep only minimal emergency giveback protection.
- [x] Remove re-entry cooldown and no-add hard blocks so position management authority remains with LLM signals.
- [x] Simplify non-English handling to warning-only (no fallback/retry penalty) while keeping trend-label normalization.
- [x] Restore prompt scalp target wording to `0.3-0.8%`.
- [x] Update/adjust tests and re-run compile + focused test suite.
- [x] Capture the correction pattern in `tasks/lessons.md`.

## Review (Scalp Logic Simplification After User Review - 2026-05-27)
- Removed deterministic override logic from [`strategy/deepseek_strategy.py`](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/strategy/deepseek_strategy.py):
  - deleted extra scalp config knobs and runtime state for partial/full guardrails.
  - deleted `_apply_scalp_profit_guard`, `_set_reentry_guard`, `_is_reentry_blocked`.
  - removed no-add guard in `_manage_existing_position`.
  - removed re-entry cooldown block in `_execute_trade`.
  - retained the minimal emergency full giveback exit block (60% giveback with peak > $5).
- Simplified non-English policy in [`utils/deepseek_client.py`](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/utils/deepseek_client.py):
  - still normalizes incoming Chinese trend labels.
  - now warns on non-English synthesis text and uses the signal as-is (no forced fallback/retry loop).
  - kept compact payload summary at INFO and full payload at DEBUG.
- Restored prompt scalp target range wording to `0.3-0.8%`.
- Kept directional volume semantics update (`up_move_weak_volume` / `down_move_weak_volume`) in [`indicators/technical_manager.py`](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/indicators/technical_manager.py).
- Updated test coverage:
  - adjusted non-English behavior test to validate warning-only path in [`tests/test_strategy_components.py`](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/tests/test_strategy_components.py).
- Verification:
  - `python3 -m py_compile utils/deepseek_client.py strategy/deepseek_strategy.py indicators/technical_manager.py tests/test_strategy_components.py` passed.
  - `pytest tests/test_strategy_components.py -q` passed (`8 passed`).
