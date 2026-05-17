# Autonomous Build Backlog

Purpose: single source of truth for what the hourly automation can build next after trader health is green.

Rules
- Always pick exactly one `PENDING` item per run.
- Smallest safe item first.
- Do not start a new item while `pending_live_verify` exists in `autonomous_state.md`.
- Every item needs explicit `Done when` + `Verify with`.

Status values
- `PENDING`
- `IN_PROGRESS`
- `DONE`
- `BLOCKED`

---

## B0 - Health and Safety (always-on guardrail)
Status: `DONE`
Done when:
- Trader process is up, status is fresh, and dashboard/status agree.
Verify with:
- `./check_strategy_status.sh`
- `python3 tools/serve_monitor_dashboard.py --print-json`

---

## B1 - Phase 2 hardening: persist longer feature history
Status: `PENDING`
Scope:
- Stop losing historical samples from `data/microstructure_features.csv` due overwrite.
- Preserve backward compatibility for current IC command.
Done when:
- CSV appends or rotates without truncating to last 500 rows.
- IC utility still works on output file path(s).
Verify with:
- Run trader for 2 timer cycles; row count strictly increases across cycles.
- `python3 -c "from indicators.orderbook_manager import OrderBookManager as O;print(O.compute_ic_from_csv('data/microstructure_features.csv')['n_rows'])"`
Kill criteria:
- Any change that breaks current status/dashboard or strategy startup.

---

## B2 - Phase 2.5 microstructure regime score (stat layer)
Status: `PENDING`
Scope:
- Distill selected microstructure features to one numeric regime score `[0,1]`.
- Keep inference lightweight (no heavy training pipeline in runtime loop).
Done when:
- Strategy logs regime score every timer cycle.
- Score is included in `microstructure` payload for downstream LLM gate.
Verify with:
- Runtime logs show score + component summary.
- No regression in strategy startup and timer analysis.
Kill criteria:
- Adds high-latency path or external dependencies for every timer tick.

---

## B3 - LLM gate before call
Status: `PENDING`
Scope:
- Skip LLM call when regime is neutral/no-edge.
Done when:
- Logs show gate pass/reject decision and reason.
- LLM calls reduced vs baseline with same runtime length.
Verify with:
- Compare number of `Calling DeepSeek AI` events before/after over same duration.
- Ensure trade behavior remains coherent and safe.
Kill criteria:
- Gate blocks all calls for long intervals without clear reason.

---

## B4 - Phase 3 bar-cadence validation utility
Status: `PENDING`
Scope:
- Add a utility/report to compute IC at bar horizons (1-bar/5-bar) using persisted data.
Done when:
- Report table produced and written to `progress_log.md`.
Verify with:
- Command exits 0 and prints table for each selected feature.
Kill criteria:
- Undefined timestamp alignment or silent leakage/lookahead.

