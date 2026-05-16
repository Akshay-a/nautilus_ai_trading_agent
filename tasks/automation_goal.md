# Hourly Automation Goal (Read First Every Run)

## Primary Objective
Keep the Bybit demo trader running continuously, observable, and self-correcting with minimal-risk code changes.

## Hard Constraints
- Demo safety only: `BYBIT_DEMO=true`, `BYBIT_TESTNET=false`.
- Do not reveal secrets from `.env`.
- Prefer tiny fixes over complex architecture.
- Every change must be validated before commit.
- If uncertain, diagnose and pause changes instead of guessing.

## What Must Be True Each Hour
1. Process is running and logs are fresh.
2. Dashboard and `check_strategy_status.sh` agree on key state.
3. Last signal, position state, and freshness are coherent.
4. If signal flips direction, closure/reversal behavior is explicitly verified.
5. Warnings/errors are triaged and either fixed or documented.

## Directional Metrics
- Keep status staleness low (`log_timestamp_utc` fresh).
- Keep logs concise and trader-readable.
- Avoid repeated regressions by documenting discoveries in `hourly_update.md`.
