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
- mode: `BUILD_NEXT`
- last_run_utc: `2026-05-21T01:47:11Z`
- last_health: `GREEN`
- last_shipped_commit: `b6d1f37`
- pending_live_verify: `yes`
- pending_live_verify_target: `b6d1f37`
- active_backlog_item: `B1`
- notes: `Built B1: feature CSV now appends unseen timestamps; next run must live-verify b6d1f37`
