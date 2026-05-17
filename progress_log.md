# Progress Log – Order Book Microstructure Pipeline

---

## Phase 4B – Dynamic Instrument Prompt Context + Stale-History Guard (2026-05-17)

### Problem addressed

When strategy instrument switched to ETH, `deepseek_client.py` still contained BTC-specific prompt context in system/user prompt text and position unit formatting. This could bias model reasoning and conflict with live strategy instrument state.

### Changes made

- `utils/deepseek_client.py`
  - Added dynamic instrument/timeframe context plumbing:
    - `instrument_id`, `bar_type` constructor inputs
    - context extractors for `pair_label`, `base_asset`, `quote_asset`, `venue`, `timeframe_label`
  - Replaced hardcoded system prompt context with `_build_system_prompt()` using active instrument/timeframe.
  - Replaced hardcoded header `BTC/USDT ...` with dynamic `{pair_label}` and `{timeframe_label}`.
  - Replaced hardcoded position size unit `BTC` with dynamic `{base_asset}` and dynamic P&L quote unit.
  - Added stale history guard:
    - on instrument/bar context change, clear `signal_history` and emit warning log.
  - Added quant prompt refinement:
    - microstructure execution filters for directional agreement, friction penalties, and conflict→HOLD bias.
- `strategy/deepseek_strategy.py`
  - Passes `instrument_id` + `bar_type` into `DeepSeekAnalyzer` on init.
  - Adds `instrument_id` + `bar_type` to `price_data` payload every analysis cycle.

### Validation

Commands:

```bash
python3 -m py_compile utils/deepseek_client.py strategy/deepseek_strategy.py
rg -n "BTCUSDT|BTC/USDT|\\bBTC\\b|Binance Futures|15-MINUTE TIMEFRAME" utils/deepseek_client.py
python3 - <<'PY'
from utils.deepseek_client import DeepSeekAnalyzer
a = DeepSeekAnalyzer(api_key='dummy', instrument_id='ETHUSDT-LINEAR.BYBIT', bar_type='ETHUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL')
a.signal_history = [{'signal':'SELL'}]
a._refresh_context_from_price_data({'instrument_id':'BTCUSDT-LINEAR.BYBIT','bar_type':'BTCUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL'})
print(a.pair_label, a.timeframe_label, len(a.signal_history))
PY
```

Results:
- Compile passed.
- No hardcoded BTC/BTCUSDT prompt-context references remain (only parser quote-token list includes `"BTC"`).
- Context-switch guard works and clears stale signal history:
  - warning log emitted: context changed + cleared `signal_history`.

---

## Phase 4A – Prompt-body Microstructure + ETH Demo Live Restart (2026-05-17)

### Scope completed

- Integrated order book microstructure into the actual `_build_analysis_prompt()` body in `utils/deepseek_client.py` while preserving `_build_prompt_payload()` as-is for audit logs.
- Added runtime marker per analysis call:
  - `🤖 Prompt microstructure section included: true|false`
- Updated prompt framework hierarchy weights to:
  - Technical 50 / Microstructure 15 / Sentiment 25 / Risk 10
- Kept strategy feed path unchanged and verified `price_data['microstructure']` remains attached before `deepseek.analyze(...)`.
- Switched `.env` instrument to `ETHUSDT-LINEAR.BYBIT` with demo live flags retained (`BYBIT_DEMO=true`, `BYBIT_TESTNET=false`, `DRY_RUN=false`).

### Files touched

- `utils/deepseek_client.py`
  - Injected microstructure section into `_build_analysis_prompt()`
  - Added `_has_microstructure_features()` and `_format_microstructure_data()`
  - Added inclusion marker log in `_analyze_with_retry()`
- `.env`
  - `INSTRUMENT_ID='ETHUSDT-LINEAR.BYBIT'`
- `tasks/todo.md`
  - Added and completed handoff checklist

### Verification commands and evidence

1) Static compile gate:

```bash
python3 -m py_compile utils/deepseek_client.py strategy/deepseek_strategy.py tools/serve_monitor_dashboard.py main_live.py
```

Result: pass (no errors).

2) Restart commands executed:

```bash
./stop_trader.sh || true
INSTRUMENT_ID=ETHUSDT-LINEAR.BYBIT BYBIT_DEMO=true BYBIT_TESTNET=false DRY_RUN=false AUTO_CONFIRM=true TIMEFRAME=1m TIMER_INTERVAL_SEC=60 ./start_paper_demo.sh
```

Because launcher PID exited quickly, run was continued with the same required env flags directly:

```bash
INSTRUMENT_ID=ETHUSDT-LINEAR.BYBIT BYBIT_DEMO=true BYBIT_TESTNET=false DRY_RUN=false AUTO_CONFIRM=true TIMEFRAME=1m TIMER_INTERVAL_SEC=60 python main_live.py
```

3) Runtime/status gate:

```bash
./check_strategy_status.sh
python3 tools/serve_monitor_dashboard.py --print-json | rg -n "running|instrument_id|dry_run|bybit_demo"
```

Key lines:
- `"running": true`
- `"bybit_demo": true`
- `"dry_run": false`
- `"instrument_id": "ETHUSDT-LINEAR.BYBIT"`

4) Log evidence gate:

```bash
LATEST=$(ls -1t logs/deepseek_trader_*.json* | head -n 1)
tail -n 400 "$LATEST" | rg -n "Loaded instrument|ETHUSDT-LINEAR.BYBIT|📊 Microstructure|🤖 Prompt microstructure section included|🤖 LLM Prompt Payload|🤖 Signal:"
```

Observed in `logs/deepseek_trader_2026-05-17_085410:973.json`:
- `Loaded instrument: ETHUSDT-LINEAR.BYBIT`
- repeated `📊 Microstructure: ...`
- repeated `🤖 LLM Prompt Payload: ...`
- repeated `🤖 Prompt microstructure section included: true`
- repeated `🤖 Signal: ...`

5) Reasoning-usage check:

Output from latest session:
- `SIGNAL_COUNT=5`
- `OB_TERM_MENTION_COUNT=5`
- `OB_TERM_MENTION_PCT=100.0`
- 5 recent examples extracted with timestamp/signal/reason excerpt.

6) Regression scan:

```bash
tail -n 500 "$LATEST" | rg -n "JSON parse failed|Analysis attempt .* failed|fallback"
```

Findings in latest session:
- 1 parse failure event (single cycle) followed by retry:
  - `JSON parse failed ...`
  - `Attempt 1 returned fallback, retrying...`
- No repeated escalation beyond that single fallback-retry instance.

---

## Phase 1 – Data Ingestion (complete, commit `b54ee1f`)

### What changed

| File | Purpose |
|------|---------|
| `indicators/orderbook_manager.py` | New `OrderBookManager` class – ring buffers for depth/trades, basic feature computation |
| `indicators/__init__.py` | Re-export `OrderBookManager` |
| `strategy/deepseek_strategy.py` | Subscribe to `order_book_deltas` + `trade_ticks`; callbacks `on_order_book_deltas`, `on_order_book_depth`, `on_trade_tick`; log microstructure summary in `on_timer` |
| `configs/strategy_config.yaml` | New `orderbook:` config section |
| `main_live.py` | Wire YAML orderbook config → strategy config |

### Validation

```bash
source venv/bin/activate
env -u BYBIT_API_KEY -u BYBIT_API_SECRET python main_live.py
```

Confirmed in live logs:
- `Subscribed to order book deltas for BTCUSDT-LINEAR.BYBIT`
- `Subscribed to trade ticks for BTCUSDT-LINEAR.BYBIT`
- Periodic `📊 OrderBook #N: bid=... ask=... spread=...bps ...` lines printing

### Open risks
- If Bybit adapter does not support `subscribe_order_book_deltas`, warning fires and system falls back to bar-only mode (graceful degradation).
- AggressorSide `str()` was using string comparison (`"BUYER"`) which can break when Nautilus returns integer representation (`"1"`). **Fixed in Phase 2.**

---

## Phase 2 – Feature Engineering & Validation (this commit)

### Bug fix: AggressorSide parsing

**Problem:** `str(tick.aggressor_side).upper() in ("BUYER", "BUY")` fails when the Rust-backed enum renders as `"1"`.

**Fix:** New `_is_buyer()` helper that tries `int(aggressor_side) == 1` first, falls back to string check. Located at module level in `indicators/orderbook_manager.py`.

### 2a – Spread / Depth / Microprice

| Feature | Formula | Range |
|---------|---------|-------|
| `spread_bps` | `(best_ask − best_bid) / mid × 10000` | [0, ∞) |
| `microprice` | `(bid × ask_sz + ask × bid_sz) / (bid_sz + ask_sz)` | ≈ mid |
| `tob_imbalance` | `(bid_sz − ask_sz) / (bid_sz + ask_sz)` at L1 | [−1, +1] |
| `depth_imbalance` | `(Σbid_sz − Σask_sz) / (Σbid_sz + Σask_sz)` across 10 levels | [−1, +1] |
| `weighted_depth_imbalance` | Same but weights `w_k = exp(−0.3k)` | [−1, +1] |
| `avg_bid_orders_per_level` | `mean(bid_counts)` | [0, ∞) |
| `avg_ask_orders_per_level` | `mean(ask_counts)` | [0, ∞) |
| `spread_volatility` | `pstdev(spread_bps)` over last 60 depth updates | [0, ∞) |
| `depth_regime` | total_depth / rolling_median: thin (<0.5×), normal, thick (>1.5×) | categorical |

### 2b – OFI / Queue Pressure

| Feature | Formula | Range |
|---------|---------|-------|
| `ofi` | Cont-Kukanov-Stoikov (2014): `ΔBidQty − ΔAskQty` accounting for price-level changes | (−∞, +∞) |
| `queue_pressure` | `(near_bid_depth − near_ask_depth) / total` within 10 bps of best price | [−1, +1] |
| `ema_ofi` | EMA(ofi, α=0.05) | smoothed OFI |

**OFI detail:**
```
if curr_bid_px > prev_bid_px:  Δbid = curr_bid_sz
elif curr_bid_px == prev_bid_px:  Δbid = curr_bid_sz − prev_bid_sz
else:  Δbid = −prev_bid_sz

(mirror for ask side)
OFI = Δbid − Δask
```

### 2c – Trade Flow / Sweeps / VWAP / Regime

| Feature | Formula | Range |
|---------|---------|-------|
| `trade_flow_imbalance` | `(buy_vol − sell_vol) / (buy_vol + sell_vol)` in 5-min window | [−1, +1] |
| `sweep_buy_count` / `sweep_sell_count` | Trades with `size > 5 × median(recent_sizes)` | [0, ∞) |
| `sweep_buy_volume` / `sweep_sell_volume` | Total volume in sweep trades | [0, ∞) |
| `recent_vwap` | `Σ(price × size) / Σ(size)` over trade window | ≈ mid |
| `vwap_deviation_bps` | `(mid − vwap) / mid × 10000` | (−∞, +∞) |

### Feature dump

On each `on_timer` cycle (every 15 min by default), the strategy calls:
```python
orderbook_manager.dump_features_csv("data/microstructure_features.csv", fwd_bars=(1, 5))
```

This writes all buffered feature vectors with forward-return columns `fwd_ret_1` and `fwd_ret_5` (computed from mid-price series at depth-update cadence).

**Output path:** `data/microstructure_features.csv`

### IC analysis

Run from Python (or the strategy itself on shutdown):
```python
from indicators.orderbook_manager import OrderBookManager
report = OrderBookManager.compute_ic_from_csv("data/microstructure_features.csv")
print("Kept:", report["kept"])
print("Dropped:", report["dropped"])
for row in report["ic_table"]:
    print(f"  {row['feature']:30s} vs {row['return_col']:12s}  IC={row['ic']:+.4f}  {'✓' if row['kept'] else '✗'}")
```

**Method:** Spearman rank-IC. Threshold: |IC| ≥ 0.02. Features below threshold across *all* return horizons are marked dropped.

### IC table

> **Live validation run:** 2026-05-16 (demo account, ~2 minutes, ~3.5k depth updates observed; CSV captures latest 500 due ring buffer size).

| Feature | vs fwd_ret_1 | vs fwd_ret_5 | Status |
|---------|-------------|-------------|--------|
| spread_bps | +0.048075 | +0.114323 | KEEP |
| microprice | -0.020925 | -0.050027 | KEEP |
| tob_imbalance | +0.106795 | +0.171691 | KEEP |
| depth_imbalance | +0.070424 | +0.050726 | KEEP |
| weighted_depth_imbalance | +0.091801 | +0.123290 | KEEP |
| ofi | +0.113024 | +0.118236 | KEEP |
| queue_pressure | +0.106791 | +0.181769 | KEEP |
| trade_flow_imbalance | +0.051143 | +0.086400 | KEEP |
| sweep_buy_count | +0.047617 | +0.078359 | KEEP |
| sweep_sell_count | +0.043751 | +0.075463 | KEEP |
| vwap_deviation_bps | -0.018740 | -0.032214 | KEEP (fails @1 only) |
| spread_volatility | -0.026151 | -0.045875 | KEEP |

### Files changed

| File | Purpose |
|------|---------|
| `indicators/orderbook_manager.py` | Full rewrite: aggressor fix, Phase 2a/2b/2c features, CSV dump, IC analysis |
| `strategy/deepseek_strategy.py` | Updated log lines for new features; added periodic CSV dump in `on_timer` |
| `progress_log.md` | This file |

### Validation commands

```bash
# Start live session (accumulate data)
python3 main_live.py

# After ≥30 min, check CSV was written
ls -la data/microstructure_features.csv

# Run IC analysis
python3 -c "
from indicators.orderbook_manager import OrderBookManager
r = OrderBookManager.compute_ic_from_csv('data/microstructure_features.csv')
print(f'Rows: {r[\"n_rows\"]}')
print(f'Kept: {r[\"kept\"]}')
print(f'Dropped: {r[\"dropped\"]}')
for row in r['ic_table']:
    print(f'  {row[\"feature\"]:30s} {row[\"return_col\"]:12s}  IC={row[\"ic\"]:+.6f}  {\"KEEP\" if row[\"kept\"] else \"DROP\"}')"
```

### Open risks / assumptions

1. **Forward returns are at depth-update cadence, not bar cadence.** This means `fwd_ret_1` is the return over ~1 depth update interval (50-200ms for Bybit), not 1 bar (15 min). This is intentional: we want to validate whether microstructure features predict *short-term* price movement, which is what they're designed for. Bar-aligned IC will be computed in Phase 3.

2. **Sweep detection uses a fixed 5× median multiplier.** This may need tuning per instrument. BTCUSDT-LINEAR on Bybit has different trade size distributions than altcoins.

3. **Depth regime classification** uses a 120-snapshot rolling median. During startup (first ~120 snapshots), it defaults to "normal".

4. **CSV persistence currently overwrites** the output on each timer cycle from the in-memory ring buffer. With `feature_buffer_size: 500`, historical data older than the last 500 snapshots is dropped from disk each dump.

5. **Queue pressure now caps to top 3 levels** (plus 10 bps filter) to avoid collapsing into a duplicate of full depth imbalance.

6. **IC threshold** at 0.02 is conservative. Academic microstructure papers typically see |IC| of 0.03–0.08 for these features at tick/L2 cadence on crypto.

---

## Phase 2.5 – Regime Score (planned, not yet built)

Distill surviving features into a single microstructure regime score using logistic regression or XGBoost. This reduces the LLM context to one compact signal rather than N raw features.

## Phase 3 – Feature Validation at Bar Cadence (planned)

Align features to bar timestamps, compute IC vs 1-bar and 5-bar forward returns, confirm features that survived tick-cadence IC also survive bar-cadence IC.

## Phase 4 – LLM Integration (planned)

Add surviving features to prompt builder. Gate: skip LLM call when microstructure state is neutral.

## Phase 5 – Risk / Execution Adaptation (planned)

Size with depth. Widen stops with spread. Use microprice for SL/TP anchoring.
