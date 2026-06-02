# LLM Decision Quality Review - 2026-06-02

## Scope

Primary evidence:

- `logs/llm_signal_audit_pid71511_full_session_20260601.md`
- `logs/llm_quant_dataset_pid71511_full_session_20260601.csv`
- `logs/deepseek_trader_2026-05-31_144126:554.json`

The generated audit covers one deployed session from `2026-05-31 14:45:13 UTC`
to `2026-06-01 07:17:47 UTC`, equivalent to `2026-06-01 00:45:13 AEST`
to `17:17:47 AEST`. The process continued after export, so later log evidence is
called out separately.

This is execution and decision forensics, not proof of strategy edge. Strategy
quality still requires a reproducible replay/backtest with fees, slippage, and
funding assumptions.

## Executive Decision

Do not increase live entry frequency yet.

The model is over-selective in ranges and often enters trends late after waiting
for volume confirmation. The user's prompt diagnosis is directionally correct.
However, the larger live risk is execution lifecycle correctness:

1. Locally emulated entry brackets can remain pending while exchange context
   reports `flat` and `open_orders=0`.
2. A later LLM cycle can submit a second entry bracket.
3. An old entry can fill after the LLM has changed to `NO_ACTION`.
4. Protective stops and trailing-stop replacements are locally emulated, not
   proven exchange-native protection.
5. `TEST_MODE=true` does not suppress order submission, and shutdown cancels
   orders without flattening exposure.

Prompt loosening before those invariants are fixed can increase unintended
exposure and loss clustering.

## What The Model Understood

The audited market was bearish but rotational: ETH moved from `$2,007.88` to
about `$1,988.82` (`-0.95%`) through repeated downtrend, range, and transition
states. The model generally understood:

- Shorting directly into nearby support often offers poor net reward.
- Low-volume pullbacks can be normal inside a trend.
- Countertrend longs need tighter evidence than passive bid depth alone.
- Friction matters because round-trip cost is about `13.1 bps` before larger
  adverse slippage.
- Prior thesis, invalidation, and structure should matter more than one noisy
  microstructure snapshot.

Observed decision mix:

| Metric | Value |
| --- | ---: |
| Decisions | 196 |
| HOLD | 187 (`95.4%`) |
| BUY | 3 |
| SELL | 6 |
| Naive next-bar directional accuracy on BUY/SELL | 3/9 (`33.3%`) |
| Realized PnL across eight closes | `-1105.25 USDT` |
| LLM latency median / p90 / max | `7.75s / 13.6s / 22.8s` |

## Good Decisions

### Breakdown Short That Worked

Signal `#20` sold the break below `$2,004`. The short initially moved against
the position, but the model kept the thesis intact and the bracket later closed
profitably for `+59.11 USDT`. This is the behavior to preserve: tolerate normal
pullback noise while invalidation remains intact.

### Fast Exit From Weak Countertrend Long

Signal `#172` bought a downtrend bounce using bid-heavy depth and low volume.
Signal `#173` exited one bar later when buying response failed. Price then moved
lower. The exit reduced downside, but the better lesson is to avoid treating
passive bid depth as bullish proof in the first place.

### Correct Read, Failed Lifecycle

Signal `#186` correctly recognized continuation below `$1,987.50`; price later
traded through its `$1,982.92` target. The entry did not fill immediately. It
remained active and filled 25 minutes later after the model had switched to
`NO_ACTION`. The market read was useful; the stale order lifecycle was not.

## Bad Decisions And Missed Participation

### Late Confirmation, Then Chasing

The model repeatedly waited for volume confirmation during bearish rotations,
then entered after a breakdown was extended and RSI was already oversold.
Signal `#133` sold at RSI `20.9` and was stopped after the rebound.

This is the central participation flaw: excessive confirmation delays entries,
then the same logic accepts a worse location once volume finally arrives.

### Premature Trend Exit

Signal `#183` shorted the break below `$1,989.60`. At signal `#184`, the model
exited because price bounced into `$1,991-$1,992`, despite reduced volume and a
strong downtrend. Price subsequently fell about `0.50%` to `$1,981.18`.

This supports a trend-management reframe: one pullback is not invalidation.
Require a sustained structure breach, exchange stop, or clear reversal response.

### Countertrend Depth Misread

Signal `#172` bought a strong-downtrend bounce because depth imbalance and queue
pressure were bid-heavy across windows. Price did not respond and the long was
closed for `-85.74 USDT`.

Passive bids at support may be absorption, short covering, spoofable liquidity,
or genuine demand. Without persistent aggressive buy flow and price acceptance,
they are neutral evidence.

### Range Participation Was Too Passive

The model used volume as a veto too often:

| Narrative pattern | Count |
| --- | ---: |
| Waited for volume in some form | 167/196 |
| Used high-friction framing | 89/196 |
| Flat range/chop HOLD with `RVOL < 0.5` | 65 |

Low RVOL is common inside ranges. It should not block a range fade when location,
invalidation, and net R:R are clear. It still matters as context: a sudden
volume change or failed price response is information.

## Critical Execution Findings

### Duplicate Pending Entry Exposure

At `2026-06-01 01:55 UTC`, the strategy submitted a locally emulated short
bracket at `$1,996.71`. Exchange risk context continued to report
`position=flat`, `open_orders=0`. At `02:00 UTC`, another short bracket was
submitted at `$1,993.83`. The older order filled at `02:04 UTC`, doubling the
short to `50.12 ETH`. The eventual close realized `-437.97 USDT`.

`strategy/deepseek_strategy.py` only blocks entry actions when a position is
already open. It does not enforce one active non-reduce entry intent while flat.
`_cleanup_oco_orphans()` cancels orphan reduce-only orders, not stale entry
parents.

### Stale Entry Filled Against Current LLM Decision

At `06:25 UTC`, signal `#186` created a short bracket. At `06:30 UTC`, the LLM
said the short entry was not taken and changed to `NO_ACTION`. The old entry
remained active and filled at `06:50 UTC`.

Flat exchange state is not enough. The strategy needs local intent state:
pending parent order, thesis id, age in bars, expiry, and cancellation reason.

### Protection Is Locally Emulated

Bracket entry, stop, and TP orders are created with `emulation_trigger`, and the
logs show `EMULATED[DEFAULT]`. The entry is released as a market order and fills
as taker liquidity. This has two implications:

- Current cost assumptions are reasonable; the entry is not reliably maker.
- Protection depends on the running process, emulator, and data feed. It should
  not be described as exchange-side protection until a kill-process test proves
  the remaining stop exists at Bybit.

Trailing-stop updates are also risky: the code cancels the old stop and submits
a new locally emulated stop. The replacement is not proven atomic or OCO-linked.

### Shutdown, Restart, And Recovery Gaps

`on_stop()` calls `cancel_all_orders()` but does not flatten positions. With
locally emulated protection, a stop or restart can leave exchange exposure
without an effective stop.

The strategy has no `on_save()` or `on_load()` lifecycle restoration for
pending entry parents, trailing-stop state, prior decision context, or position
health. Existing exchange exposure can be rediscovered, but its intended
protection state is not rebuilt.

Protective child rejection is only logged. There is no invariant check that an
open position still has active SL and TP protection, and no emergency recovery
path if protection fails.

### Safety Flags Are Ambiguous

`TEST_MODE=true` only changes console output in `main_live.py`. Actual order
suppression depends on `DRY_RUN=true` inside the strategy. The quick-test
launchers set smaller sizing and faster bars but do not force `DRY_RUN=true`.

Treat quick-test mode as capable of submitting orders until this is corrected.

### Additional Risk Controls Need Hardening

- Orphan cleanup decides from local cache state only. Reconciliation lag can
  make valid reduce-only protection look orphaned.
- While exposed, LLM re-analysis can remain suppressed indefinitely if sparse
  volume and microstructure buckets do not change. Add a maximum thesis age.
- Invalid LLM and structural brackets fall through to an unconditional
  symmetric `1%` fallback. Protection exists, but geometry is not necessarily
  justified by structure or net expectancy.
- Position sizing can raise a capped notional back to a minimum `$100`, and
  zero available balance is treated as missing balance context. A risk cap must
  never be overridden upward.

## Review Of Proposed Prompt Changes

| Proposal | Decision | Reason |
| --- | --- | --- |
| Reframe low RVOL in ranges | Keep, revise wording | Say low absolute RVOL is normal and not a veto. Keep volume changes and price response as evidence. |
| Treat bid-heavy book near downtrend support as neutral | Keep | Make passive depth neutral by default unless aggressive flow persists and price accepts higher levels. |
| Replace AND-gate with `7/12` score | Defer live use | The current flat-entry wording behaves like an AND-gate. A hand-written score adds false precision unless logged and calibrated in replay first. |
| Use only last `48-96` bars for regime | Revise | Use a local execution regime from `48-96` bars and a separate higher-timeframe context regime. Do not erase higher-timeframe structure. |
| Remove friction discouragement | Keep, revise wording | Remove adjectives such as “friction is high.” Keep numeric costs and require net executable R:R after fees, spread, slippage, and latency drift. |
| Remove chance multiplier and numeric probability language | Keep | The current prompt no longer needs numeric win probabilities. Confidence should remain qualitative and enum-validated. |
| Add trailing stop and half exit after `>1R` stall | Defer | Current evidence is premature exits, not failure to partial runners. Partial exits are disabled and trailing-stop mechanics need hardening first. |
| Include recent realized PnL without masking | Keep, revise wording | Treat short-run PnL as review context, not an entry-frequency or sizing signal. Loss clusters may still reveal execution failure or regime mismatch. |
| Add `3-4` prior states | Keep, narrow | Add compact lifecycle memory, not verbose narrative history. Preserve entry thesis plus material updates while allowing fresh regime classification. |

## Recommended Direction

### P0 - Execution Invariants Before Prompt Changes

1. Force `DRY_RUN=true` in quick tests and make startup mode unambiguous.
2. Define shutdown behavior: flatten safely or preserve verified native
   exchange protection before canceling anything.
3. Enforce exactly one active non-reduce entry intent per instrument.
4. Track locally emulated parents, not only Bybit `open_orders`.
5. Cancel or replace a pending entry when its thesis changes, it expires after a
   bounded bar count, price drift destroys net R:R, or the LLM returns
   `NO_ACTION`.
6. Revalidate current quote, slippage, stop geometry, and target room immediately
   before release or fill.
7. Separate journal events: decision, parent submitted, parent canceled, entry
   fill, TP fill, SL fill, LLM exit, and stale-intent prevention.
8. Prove protection under process termination, restart, child rejection, and
   local-cache lag. Prefer native exchange stop protection where possible.
9. Make risk caps monotonic: later fallbacks may reduce or block size, never
   increase capped exposure.

### P1 - Prompt Reframe In Shadow Mode

Use a compact regime contract:

- `RANGE`: location and invalidation dominate. Low RVOL is neutral. Trade-flow
  persistence and price response matter more than passive depth.
- `TREND`: pullbacks are normal. Exit only after sustained structure failure,
  exchange stop, or clear reversal response. Do not exit solely because a small
  unrealized profit retraced.
- `PASSIVE DEPTH`: neutral by default. Upgrade only with persistence, aggressor
  flow, OFI, and price acceptance.
- `FRICTION`: report numeric cost and latency-adjusted net R:R without cautionary
  adjectives.

Do not deploy a `7/12` score as a live gate yet. First journal advisory factor
scores and compare them against net outcomes on a locked replay corpus.

### P2 - State Continuity And Research Data

Add a compact lifecycle context:

- immutable entry thesis and invalidation;
- entry zone, actual fill, stop, TP, and pending-intent age;
- last two material thesis updates;
- MFE, MAE, current PnL in `R`, bars held, and net cost paid;
- last execution event and whether it matched the LLM action.

Add funding, open-interest change, and liquidation-flow data as research-only
regime context first. They are currently absent. For a 5-minute scalp system,
OI change and liquidation bursts may help distinguish continuation from
exhaustion; funding is slower context, not an immediate trigger.

## Verification Plan

1. Add focused tests for one-pending-entry enforcement, stale-intent expiry,
   `NO_ACTION` cancellation, duplicate-bracket prevention, and process-kill stop
   persistence.
2. Add tests for `TEST_MODE`, forced quick-test `DRY_RUN`, shutdown with
   exposure, restart recovery, child rejection recovery, cache-lag cleanup,
   trailing-stop relinking, and zero-balance sizing.
3. Fix the false-positive DeepSeek parse test: it omits `timestamp`, falls back,
   and still passes assertions.
4. Replay the audited payloads with current and revised prompts using a locked
   prompt version and model id.
5. Compare net PnL, trade count, entry latency drift, MFE/MAE, stop rate,
   duplicate-intent prevention, and regime-specific outcomes.
6. Only after execution invariants pass, run the revised prompt in demo shadow
   mode before allowing it to submit orders.

Current read-only targeted verification by the explorer reported `29 passed,
1 failed`. The failure is a stale `tests/test_rounding_fix.py` fixture missing
`fixed_trade_usdt`; it is not caused by this documentation review.

## Supplementary Post-Export Evidence

After the audit cutoff, the same running demo process opened two more longs and
closed them for `-127.26 USDT` and `-132.97 USDT`. No trailing-stop activation
was observed. The latest Bybit risk-context log reported flat exposure, but that
does not prove there are no locally emulated pending entry parents.
