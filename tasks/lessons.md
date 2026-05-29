# Lessons

## 2026-05-15
- User correction: orchestration prompts were too ambitious and not grounded in incremental runability.
- Rule: always prioritize an executable bootstrap path first (env -> run -> dry-run) before adding advanced orchestration layers.
- Rule: never claim strategy quality without a reproducible backtesting harness that outputs core metrics (win/loss, Sharpe, drawdown, PF).

## 2026-05-16
- User correction: monitoring output should be a simple browser UI on a port, not just terminal commands.
- Rule: when asked for "track performance in real time", deliver an always-on endpoint/UI that surfaces key run metrics from existing logs/events before proposing richer enhancements.

## 2026-05-17
- User correction: prompt/inference context must not contain hardcoded BTC references after instrument switch.
- Rule: every model-facing prompt field (system prompt, header, units, timeframe, prior-signal context) must derive from active runtime instrument metadata, never literals.
- Rule: when instrument or timeframe context changes, clear model-side signal history to prevent stale cross-instrument carryover.
- Rule: extend the same anti-hardcoding discipline to backtest infra; instrument construction must derive base/quote/settlement from config/symbol inputs, not BTC/USDT literals.

## 2026-05-27
- User correction: for decision audit persistence, prefer flat files that are immediately analysis-friendly; avoid introducing new DB dependencies when a CSV suffices.
- Rule: when the user prioritizes lightweight analytics workflows, default to append-only CSV with explicit schema and stable column names over JSONL or database-backed storage.

## 2026-05-27 (Scalp Control Logic)
- User correction: avoid over-engineered deterministic execution state machines when the intended design is LLM-first discretionary behavior.
- Rule: prefer prompt + context instrumentation (`position_health`, market state) over hardcoded multi-threshold trade overrides unless explicitly requested.
- Rule: keep safety nets minimal and clearly bounded (single emergency breaker), and avoid adding config knobs/state that operators must tune without proven necessity.
- Rule: for language-format drift in LLM narrative fields, warn and continue unless execution-critical fields are invalid; do not add costly retry loops by default.

## 2026-05-29 (Prompt Exit Bias)
- User correction: prompt policy remained too exit-prescriptive and constrained upside capture.
- Rule: avoid fixed profit-target/giveback ladders in LLM instructions unless there is direct log evidence for that exact threshold.
- Rule: prioritize thesis-validity + market-structure + net-edge framing (hold/reduce/exit) over percentage-only exit rules.
- Rule: explicitly preserve HOLD/NO_ACTION as a valid high-quality outcome when evidence is mixed, to reduce churn.
