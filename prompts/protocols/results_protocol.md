# Results Protocol

> **Parent document:** [`../orchestrator.md`](../orchestrator.md)
> **Purpose:** How backtest results and research artifacts are stored, named, and used for comparison.

---

## Directory Structure

```
backtest_results/
  {YYYY-MM-DD_HH-MM}_{hypothesis_id}_{short_name}/
    summary.md              # Human-readable report (always present)
    results.json            # Machine-readable metrics (always present)
    config_snapshot.yaml    # Exact config used to reproduce this run
    equity_curve.csv        # If applicable — timestamp, equity columns
    trades.csv              # If applicable — entry/exit/pnl per trade
```

### Naming Convention

- Timestamp: `2026-05-14_11-30`
- Hypothesis ID: `H-03` (from plan.md)
- Short name: `ofi_micro_backtest` (descriptive, snake_case)
- Full folder: `2026-05-14_11-30_H-03_ofi_micro_backtest/`

---

## summary.md Template

Every backtest result MUST have a `summary.md`. This is the primary artifact a human (or a future orchestrator) reads to understand what was tested and what happened.

```markdown
# Backtest Summary: {hypothesis_id} — {short description}

## Hypothesis
{paste the full hypothesis from plan.md}

## Test Configuration
- **Instrument:** {e.g., BTCUSDT-PERP.BINANCE}
- **Date range:** {start} to {end}
- **Data type:** {bars / ticks / book snapshots}
- **Slippage:** {e.g., 0.01% per trade}
- **Fees:** {e.g., maker 0.02%, taker 0.04%}
- **Funding included:** {yes/no}
- **OOS split:** {e.g., 20d train / 10d test}

## Results

| Metric | In-Sample | Out-of-Sample | Baseline | Target |
|--------|-----------|---------------|----------|--------|
| Win Rate | | | | |
| Profit Factor | | | | |
| Sharpe (ann.) | | | | |
| Max Drawdown | | | | |
| Total Trades | | | | |
| Avg Hold Time | | | | |

## Prediction vs Reality
- **Predicted:** {what the hypothesis predicted}
- **Actual:** {what happened}
- **Delta:** {how far off the prediction was}

## Verdict
**{PASS / KILL / INCONCLUSIVE}**

{2-3 sentences explaining the verdict}

## Red Flags
{Any concerns — overfitting, low trade count, high variance, etc.}

## Next Steps
{What should happen next based on this result}
```

---

## results.json Schema

```json
{
  "hypothesis_id": "H-03",
  "component": "ofi_60s_rolling",
  "instrument": "BTCUSDT-PERP.BINANCE",
  "date_range": {
    "start": "2026-04-01",
    "end": "2026-04-30"
  },
  "oos_split_date": "2026-04-21",
  "metrics": {
    "in_sample": {
      "win_rate": 0.58,
      "profit_factor": 1.35,
      "sharpe_annual": 1.8,
      "max_drawdown_pct": 4.2,
      "total_trades": 412,
      "avg_hold_minutes": 18.5
    },
    "out_of_sample": {
      "win_rate": 0.55,
      "profit_factor": 1.18,
      "sharpe_annual": 1.4,
      "max_drawdown_pct": 6.1,
      "total_trades": 187,
      "avg_hold_minutes": 21.3
    },
    "baseline": {
      "win_rate": 0.52,
      "profit_factor": 1.05,
      "sharpe_annual": 0.9,
      "max_drawdown_pct": 8.3,
      "total_trades": 95,
      "avg_hold_minutes": 45.0
    }
  },
  "verdict": "PASS",
  "timestamp": "2026-05-14T11:30:00Z"
}
```

---

## How Results Are Used

### By the Current Orchestrator
- After every stage gate, check the latest results against the hypothesis prediction
- Update `plan.md` with the outcome
- If PASS: advance hypothesis to next stage
- If KILL: move hypothesis to Graveyard, pick next highest-value hypothesis
- Track the "Current Best Metrics" table in `plan.md` by pointing to the latest full-system backtest result

### By a Future Orchestrator
- On session start, scan `backtest_results/` to understand what's been tested
- Read summaries to avoid re-investigating killed hypotheses
- Use baseline metrics from previous runs as starting point for comparison
- The Graveyard in `plan.md` and the result summaries together form a complete audit trail

### By the Human (Akshay)
- `summary.md` files are the primary review artifact
- Each one answers: what was tested, what happened, does it work
- The collection of summaries tells the story of the research program

---

## Comparison Rules

When comparing two backtest results:
1. **Same instrument and date range.** Never compare BTCUSDT results to ETHUSDT results, or April results to May results.
2. **Same cost model.** Slippage and fee assumptions must match.
3. **Out-of-sample only for verdicts.** In-sample results are for debugging, not for deciding if something works.
4. **Statistical significance.** A 0.5% improvement in win rate on 50 trades is noise. Require at least 100 trades in OOS for any verdict.
5. **Multiple metrics.** A component that improves win rate but doubles max drawdown is not an improvement. Look at the full picture.

---

## Plan Failure Protocol

If the orchestrator determines the overall plan is failing (3+ layer-level dead ends with no viable path forward):

1. Delete `plan.md`
2. Create `backtest_results/plan_failure_{timestamp}/postmortem.md` containing:
   - What the plan was
   - What was tried at each layer
   - Why each approach failed (with links to relevant result folders)
   - What a fresh orchestrator should consider
   - Any architectural constraints that should be revisited
3. Create `HANDOFF.md` at the repo root with the context handoff (see orchestrator.md Section 7)
