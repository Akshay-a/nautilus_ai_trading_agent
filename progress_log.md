# Progress Log – Order Book Microstructure Pipeline

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

> **Note:** IC values below are placeholders – they will be populated after the first live session accumulates ≥500 depth snapshots. Run the IC script above after at least 30 minutes of live data.

| Feature | vs fwd_ret_1 | vs fwd_ret_5 | Status |
|---------|-------------|-------------|--------|
| spread_bps | TBD | TBD | TBD |
| microprice | TBD | TBD | TBD |
| tob_imbalance | TBD | TBD | TBD |
| depth_imbalance | TBD | TBD | TBD |
| weighted_depth_imbalance | TBD | TBD | TBD |
| ofi | TBD | TBD | TBD |
| queue_pressure | TBD | TBD | TBD |
| trade_flow_imbalance | TBD | TBD | TBD |
| sweep_buy_count | TBD | TBD | TBD |
| sweep_sell_count | TBD | TBD | TBD |
| vwap_deviation_bps | TBD | TBD | TBD |
| spread_volatility | TBD | TBD | TBD |

### Files changed

| File | Purpose |
|------|---------|
| `indicators/orderbook_manager.py` | Full rewrite: aggressor fix, Phase 2a/2b/2c features, CSV dump, IC analysis |
| `strategy/deepseek_strategy.py` | Updated log lines for new features; added periodic CSV dump in `on_timer` |
| `progress_log.md` | This file |

### Validation commands

```bash
# Start live session (accumulate data)
source venv/bin/activate
python main_live.py

# After ≥30 min, check CSV was written
ls -la data/microstructure_features.csv

# Run IC analysis
python -c "
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

4. **Queue pressure** uses a fixed 10 bps window from best price. For tight-spread instruments this may capture only L1; for wide-spread instruments it may capture multiple levels. Can be tuned via `QUEUE_PRESSURE_BPS` class constant.

5. **IC threshold** at 0.02 is conservative. Academic microstructure papers typically see |IC| of 0.03–0.08 for these features at tick/L2 cadence on crypto.

---

## Phase 2.5 – Regime Score (planned, not yet built)

Distill surviving features into a single microstructure regime score using logistic regression or XGBoost. This reduces the LLM context to one compact signal rather than N raw features.

## Phase 3 – Feature Validation at Bar Cadence (planned)

Align features to bar timestamps, compute IC vs 1-bar and 5-bar forward returns, confirm features that survived tick-cadence IC also survive bar-cadence IC.

## Phase 4 – LLM Integration (planned)

Add surviving features to prompt builder. Gate: skip LLM call when microstructure state is neutral.

## Phase 5 – Risk / Execution Adaptation (planned)

Size with depth. Widen stops with spread. Use microprice for SL/TP anchoring.
