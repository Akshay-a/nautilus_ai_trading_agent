# Autonomous Context Pack

Use this file to minimize context drift in hourly runs.

## Read Budget
- Always read fully:
  - `tasks/automation_goal.md`
  - `tasks/autonomous_state.md`
  - `tasks/autonomous_backlog.md`
  - `hourly_update.md` (last 20 lines)
- Read on demand:
  - `tasks/todo.md` (latest relevant plan/review block only)
  - `progress_log.md` (sections tied to active backlog item)

## Runtime Truth Sources
- `./check_strategy_status.sh`
- `python3 tools/serve_monitor_dashboard.py --print-json`
- Latest log file under `logs/deepseek_trader_*.json`

## Build Scope Rules
- Touch only files needed for the selected backlog item.
- Avoid global refactors.
- Keep one commit per run.

## Verification Ladder
1. Static check: `python3 -m py_compile <modified_py_files>`
2. Functional check: backlog-item specific command(s)
3. Runtime coherence: status + dashboard freshness + signal/position consistency

## Shipping Contract
- If all verification checks pass:
  - commit
  - push
  - set `pending_live_verify: yes` in `autonomous_state.md`
- Next run must execute live verification before any new build.

