# Autonomous State

This file is owned by the hourly automation. Update it every run.

## State Schema
- `mode`: `HEAL` | `VERIFY_PREV_SHIP` | `BUILD_NEXT`
- `last_run_utc`: ISO timestamp
- `last_health`: `GREEN` | `YELLOW` | `RED`
- `last_shipped_commit`: short hash or `none`
- `pending_live_verify`: `yes` | `no`
- `pending_live_verify_target`: commit hash or `none`
- `active_backlog_item`: backlog id or `none`
- `notes`: one short line

## Current
- mode: `HEAL`
- last_run_utc: `2026-05-16T16:35:56Z`
- last_health: `RED`
- last_shipped_commit: `0e26328`
- pending_live_verify: `yes`
- pending_live_verify_target: `0e26328`
- active_backlog_item: `none`
- notes: `HEAL run: replaced ps/pgrep process probe with kill(0); runtime process still exits quickly in sandbox so health remains RED`
