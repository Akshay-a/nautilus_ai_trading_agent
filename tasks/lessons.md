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

## 2026-05-31 (Move-Capture Design Simplicity)
- User correction: the proposed redesign added too many deterministic gates and audit fields before the bot has a clean directional trading layer.
- Rule: for early move-capture redesigns, prefer a simple market-structure contract (range vs trend, ATR/volatility-aware TP/SL, maker orders, event-triggered re-analysis) over multi-threshold state machines.
- Rule: do not over-instrument before the behavior is clean; keep logs sufficient for a 3-day review, then add fields only where the analysis is blocked.
- Rule: prompt context should emphasize weekly/daily volatility, ATR, local/high-timeframe structure, and longer OB windows instead of tiny fixed profit-taking rules.

## 2026-05-31 (RR Geometry Before Live Entries)
- User correction: fixed percent TP with widened structural SL can create poor R:R, and re-analysis needs prior thesis/invalidation continuity.
- Rule: before enabling move-capture entries, compute TP/SL geometry explicitly and block entries when structural target distance does not justify invalidation risk.
- Rule: every LLM re-analysis prompt must include prior action, prior thesis, prior invalidation, prior regime, and bars since the prior LLM decision.
- Rule: when fixed sizing/leverage is requested, make the prompt and execution config explicit about notional, leverage, and what 1R means; do not leave sizing semantics implicit.

## 2026-06-01 (Deterministic Protection Before LLM Reanalysis)
- User correction: LLM entries must always carry TP/SL protection, and ordinary open-position reanalysis should wait for microstructure or volume changes.
- Rule: never depend on an asynchronous LLM call as the hard loss-control mechanism; attach exchange-side SL and TP protection before opening exposure.
- Rule: when LLM levels are missing or invalid, use bounded deterministic bracket fallbacks and block the entry if the protected bracket cannot be submitted.
- Rule: keep open-position LLM wakeups sparse; lifecycle events and price-only movement should not create analysis churn when deterministic bracket protection is active.

## 2026-06-01 (Deployment Scope Over Rolling Window)
- User correction: when requesting analysis after the latest deployment, scope exports to the active process session rather than forcing an approximate rolling-hour cutoff.
- Rule: resolve PID -> process start -> matching JSON log first, then export the entire session unless the user explicitly requires a time slice.

## 2026-06-02 (Verify Exchange Position Before Reporting)
- User correction: the monitor summary exposed a stale open-position field after the exchange position had already closed.
- Rule: before reporting current exposure, verify the latest Bybit risk context `position` field and recent executions; treat monitor-derived open-position summaries as stale when they conflict with exchange-backed context.

## 2026-06-03 (Project Update Writing Must Preserve Core Domain)
- User correction: a project update draft omitted the central trading/experiment framing and underplayed the value of the conversations that came from sharing the work publicly.
- Rule: when writing public updates for this repo, explicitly name the work as a trading experiment and restate the core question being tested so the post does not read like a generic builder update.
- Rule: if the user highlights relationship or learning outcomes from sharing the project, include that angle directly instead of treating it as implied context.

## 2026-06-03 (Public Dashboard Assets Need Correct Scope)
- User correction: a social-share dashboard used a single-session export and exposed internal source naming, which understated profitable closes and looked too implementation-specific for LinkedIn.
- Rule: for public-facing artifacts in this repo, verify whether an aggregate dataset already exists before defaulting to the latest session export.
- Rule: remove internal filenames, vendor-specific source labels, and other implementation references from screenshots unless the user explicitly wants them shown.

## 2026-06-05 (Isolate Layers Before Rebuilding)
- User correction: the right path is to isolate execution and LLM layers inside the current repo, not default to a greenfield architecture rewrite.
- Rule: when live behavior is wrong but the platform integration is still usable, prefer a scoped v2 path or layer isolation inside the repo before proposing a separate project.
- Rule: fix application-layer ownership conflicts first (forced re-analysis, conflicting exit owners, state handoff) before blaming prompt quality alone.

## 2026-06-05 (Gate Before Prompt Aggression)
- User correction: high hold-rate diagnosis must separate gate-produced holds from LLM-produced holds; prompt aggressiveness is not the first lever when the gate is the dominant bottleneck.
- Rule: before making the model more aggressive, verify how much of the inactivity is created by the decision shell versus the prompt, using existing journal fields or adding the minimum telemetry needed.
- Rule: make continuity machine-readable before asking the model to participate more; narrative `watch_trigger` text is not enough for reliable re-arm logic.
- Rule: when flat-state participation is too low, loosen flat re-arm and trigger adjudication first, while keeping in-position wakeups tight so the fix does not reintroduce churn.
