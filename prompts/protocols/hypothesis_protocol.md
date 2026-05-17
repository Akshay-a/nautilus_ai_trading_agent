# Hypothesis Protocol

> **Parent document:** [`../orchestrator.md`](../orchestrator.md)
> **Purpose:** How to form, validate, and kill hypotheses. Every component in the system starts as a hypothesis.

---

## Why Hypotheses Matter

In quant trading, every signal, every feature, every architectural decision is a bet. Untested bets lose money. This protocol ensures no component enters the system without proving its value against real data, and bad bets are killed at the cheapest possible stage.

---

## Hypothesis Format

Every hypothesis must be written down before any work begins. Use this exact format:

```
HYPOTHESIS: H-{sequential_id}
COMPONENT: {which layer / which feature / which design choice}
STATEMENT: {one sentence — what you believe to be true}
PREDICTION: {quantifiable outcome if hypothesis is correct}
KILL CRITERION: {specific condition that kills this hypothesis}
VALIDATION STAGE: {1: Paper | 2: Micro-backtest | 3: Full backtest | 4: Integration}
STATUS: {PROPOSED | PAPER_PASSED | MICRO_PASSED | BACKTEST_PASSED | INTEGRATED | KILLED}
```

### Example

```
HYPOTHESIS: H-03
COMPONENT: Feature Engineering — Order Flow Imbalance
STATEMENT: 60-second rolling OFI (buy volume minus sell volume) has directional 
           predictive power for 5-minute forward returns on BTCUSDT-PERP.
PREDICTION: >55% directional accuracy on 3-day out-of-sample test.
KILL CRITERION: <52% accuracy, OR signal is >80% correlated with existing RSI feature 
                (redundant information).
VALIDATION STAGE: Stage 2 (Micro-backtest)
STATUS: PROPOSED
```

---

## The 4-Stage Funnel

### Stage 1: Paper Validation

**Cost:** ~1 Researcher sub-agent call
**Goal:** Does the hypothesis have a theoretical basis?

What the Researcher investigates:
- Is there academic evidence for this signal? (order flow imbalance: Cont, Kukanov & Stoikov 2014; Kyle 1985)
- What's the expected signal-to-noise ratio in crypto markets?
- Are there known failure modes? (e.g., OFI breaks down in highly manipulated markets with wash trading)
- Is the feature independent of what we already have, or is it a noisy proxy for RSI/momentum?

**Pass if:** Theoretical basis exists, expected signal strength is reasonable, and the feature provides information not already captured.
**Kill if:** No theoretical basis, or the feature is provably redundant with existing features.

### Stage 2: Micro-Backtest

**Cost:** ~3 sub-agent calls (Builder + Backtester + optional Researcher)
**Goal:** Does the signal exist in real data?

Steps:
1. Builder creates a minimal implementation (<200 lines, single file)
2. Backtester runs it on 1-3 days of historical data for the primary pair
3. Check: does the signal distribution match the Stage 1 prediction?

**Pass if:** Signal exists with the predicted characteristics. Doesn't need to be profitable yet — just needs to show predictive information.
**Kill if:** Signal is absent, distribution is random, or accuracy is below the kill criterion.

### Stage 3: Full Backtest

**Cost:** ~2 sub-agent calls (Backtester + Reviewer)
**Goal:** Does this component improve the system's trading performance?

Steps:
1. Backtester runs on 30+ days with realistic costs (slippage, fees, funding)
2. Out-of-sample validation (20d train / 10d test)
3. Compare against baseline (system without this component)
4. Reviewer checks for overfitting, look-ahead bias, curve fitting

**Pass if:** Measurable improvement over baseline on at least one primary metric (Sharpe or win rate) without degrading others.
**Kill if:** No improvement, or improvement only appears in-sample (overfitting).

### Stage 4: Integration

**Cost:** ~5 sub-agent calls (Builder + Backtester + Reviewer + potential iteration)
**Goal:** Does the component improve the FULL system when combined with all other active components?

Steps:
1. Builder integrates the component into the main architecture
2. Backtester runs full system backtest
3. Reviewer audits integration for coupling, over-engineering, correctness
4. Compare full-system metrics: before integration vs. after

**Pass if:** System-level metrics improve or hold steady. The component adds value in the ensemble.
**Kill if:** System-level metrics degrade (interaction effects — the component may conflict with other signals). Revert the integration.

---

## Parallel Hypothesis Testing

Independent hypotheses can (and should) run through the funnel in parallel. Two hypotheses are independent if:
- They operate on different data sources (e.g., OFI from trades vs. funding rate from REST)
- They don't share state or buffers
- Validating one doesn't affect the other's data

Example: "Does OFI have predictive power?" and "Does funding rate z-score have predictive power?" are independent — run Stages 1 and 2 for both simultaneously.

---

## Hypothesis Graveyard

Every killed hypothesis goes to the Graveyard section of `plan.md`. The Graveyard is valuable — it prevents future orchestrators from re-investigating dead ends. Include:
- Hypothesis ID and statement
- What stage it died at
- Why it was killed (with data if available)
- Whether it's worth revisiting under different conditions (e.g., "might work on ETH but not BTC")

---

## Anti-Patterns to Avoid

1. **Zombie hypotheses:** A hypothesis that should be dead but keeps getting "one more iteration." If it didn't pass Stage 2, a tweak is unlikely to save it. Kill it and try a different signal.
2. **Kitchen-sink features:** Adding features because "they might help." Every feature must independently pass Stage 2. If it doesn't show signal alone, it won't help in combination.
3. **Hypothesis-free building:** "Let's just build the microstructure manager and see what happens." No. Each feature in that manager is a separate hypothesis with separate validation.
4. **Confirmation bias:** You want H-03 to work because you spent 3 sub-agent calls on it. Use a Reviewer agent to check. Your judgment is compromised after investment.
