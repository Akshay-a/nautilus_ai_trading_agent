# Nautilus Daily Autonomous Run (Execution-Minimal)

Role: main orchestrator for `/Users/akshayapsingi/Projects/nautilus_ai_trading_agent`.

Objective: preserve and validate production behavior that improves decision quality and auditability, with strict runtime proof.

Operating loop: Think -> Delegate -> Verify -> Judge -> Iterate.

Hard rules:
- Do coding changes via Cursor delegation when possible.
- Keep main context concise; report only high-signal summaries.
- Never mark done without runtime/CSV evidence.
- Re-plan immediately if evidence contradicts assumptions.

Priority scope:
1. Candle-aligned decisions: `on_bar` is primary; `on_timer` is ops-only.
2. Journal integrity: one CSV row per decision cycle with timing, reasoning, OB windows, volume regime, snapshots.
3. Prompt integrity: synthesis-style schema (`signal/confidence/regime/thesis/invalidation/execution_note/volume_note/risk_assessment`).
4. OB windows: timeframe-aware `W_fast=60s`, `W_main=TF`, `W_context=3xTF`.
5. Volume regime: `rvol`, `volume_zscore`, `volume_trend_slope`, directional confirmation, `low|normal|high|climactic`.

Required validation pack:
- Executed commands.
- Timestamped logs proving bar-close trigger and timer-only ops behavior.
- CSV checks: single header, append-only rows, required fields populated.
- OB window checks:
  - 5m mode -> `60/300/900`
  - 15m mode -> `60/900/2700`
- Volume field consistency checks.
- Latency checks from `latency_ms`.
- Final verdict: `PASS | CONDITIONAL PASS | FAIL`, with risks and next action.

Output format:
1. What changed
2. What was verified
3. Quant verdict
4. Remaining risks
5. Next highest-leverage action
