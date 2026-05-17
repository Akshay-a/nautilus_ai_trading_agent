# Hourly Automation Goal (Read First Every Run)

## Primary Objective
Operate as a self-correcting and self-building agent:
1) keep the Bybit demo trader healthy,
2) verify prior shipped behavior under live conditions,
3) complete pending roadmap items incrementally.

## Hard Constraints
- Demo safety only: `BYBIT_DEMO=true`, `BYBIT_TESTNET=false`.
- Never reveal secrets from `.env`.
- Tiny scoped changes only.
- Must validate before every ship.
- If uncertain, diagnose first; do not guess.

## Priority Order (strict)
1. **Health first**: runtime correctness and safety.
2. **Verify previous ship**: confirm last automation change behaves correctly in live loop.
3. **Build next pending**: take one backlog item only when health is GREEN and no pending live-verify.

## Required Sources of Truth
- Runtime state: `check_strategy_status.sh` + dashboard JSON.
- Build backlog: `tasks/autonomous_backlog.md`.
- Run-state memory: `tasks/autonomous_state.md`.
- Human-readable run log: `hourly_update.md`.

## Success Criteria Per Hour
1. Health verdict is explicit (`GREEN`, `YELLOW`, `RED`).
2. Exactly one mode executed: `HEAL` or `VERIFY_PREV_SHIP` or `BUILD_NEXT`.
3. If code shipped: commit pushed and next run set to live-verify that commit.
4. Evidence captured in `hourly_update.md`.
