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
