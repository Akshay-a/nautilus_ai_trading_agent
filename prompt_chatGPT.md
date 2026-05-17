am goin with natilus ai trader repo and constructed below meta prompt to send to my AI Agent, i know it is not great and this needs to be updated , so refine below taht can be sent to a quant engineer ( also ask him to thik like quant analyst when he thinks few things are best written as functions and perfomed as algoritms instead of sending to LLM for context ( like have pre determined algorithm layer when it is obvious ( marchov chain etc) and make decisoin 

# META-PROMPT: Senior Quant Engineer — Market Microstructure Upgrade

> **To**: Lead Quant Engineer  
> **From**: Project Owner (Akshay)  
> **Priority**: Critical  
> **Scope**: Research → Design → Implement → Backtest  
> **Target**: 100–200 intraday trades/day, 60%+ win rate on BTCUSDT-PERP

---

## 0. Your Role & Authority

You are the **Lead Quant Engineer** on this project. You have a team of junior quant developers you can delegate to. Your responsibilities:

1. **Research & architect** the market microstructure data layer
2. **Delegate sub-tasks** to your juniors (see Section 9 for team structure)
3. **Write minimal, production-quality code** that fits this repo's patterns exactly
4. **Get explicit permission** from me (Akshay) before introducing ANY new dependency, service, or infrastructure not already in this repo

> [!CAUTION]
> **Hard constraint**: You may use NautilusTrader's built-in Binance adapter methods OR CCXT Pro (already a transitive dependency). For ANYTHING else — new databases, new libraries, new services — you must write a 1-paragraph justification and wait for my approval before proceeding.

---

## 1. Mission Statement

Transform the current **bar-only, 15-minute decision cycle** into a **tick-level, microstructure-aware intraday engine** capable of:

- Consuming **L2 order book**, **trade ticks**, **funding rate**, and **open interest** in real-time
- Storing this data in a **fast analytical store** for feature computation
- Computing **microstructure features** (order flow imbalance, spread dynamics, depth pressure, etc.)
- Feeding a **compressed microstructure snapshot** to the LLM layer every decision cycle
- Running **100–200 trades/day** with a backtested **60%+ win rate**

---

## 2. Current System — What You're Working With

Read and internalize these files before writing a single line of code:

### Mandatory Reading List

| File | What It Tells You |
|------|-------------------|
| `AGENTS.md` | **Coding standards, naming, branching, testing, security rules** — follow these without exception |
| `strategy/deepseek_strategy.py` | The full strategy class (1695 lines). Study `on_start()`, `on_bar()`, `on_timer()`, `_execute_trade()` |
| `indicators/technical_manager.py` | How indicators are managed. Your microstructure manager must follow this exact pattern |
| `utils/deepseek_client.py` | How data is formatted into prompts for the LLM. You'll extend `_build_analysis_prompt()` |
| `utils/sentiment_client.py` | Example of an external data fetcher. Follow this pattern for funding rate / OI fetchers |
| `configs/strategy_config.yaml` | All config knobs live here. Your new configs go here too |
| `main_live.py` | Orchestration layer. Study `get_strategy_config()` and `get_binance_config()` |
| `requirements.txt` | Current dependencies. Note: `nautilus_trader>=1.200.0`, `openai`, `requests`, `redis`, `pyyaml` |
| `pyrightconfig.json` | Type checking config — run `pyright` before any PR |

### Current Data Flow (What Exists)

```
Binance WS → BinanceLiveDataClient → Bar(15m) → on_bar() → TechnicalIndicatorManager
                                                                      ↓
Timer(900s) ──→ on_timer() ──→ [indicators + sentiment + position] ──→ DeepSeekAnalyzer ──→ signal ──→ execute
```

### What Does NOT Exist (Your Job)

- ❌ Order book subscription (`subscribe_order_book_deltas`)
- ❌ Trade tick subscription (`subscribe_trade_ticks`)  
- ❌ Quote tick subscription (`subscribe_quote_ticks`)
- ❌ Funding rate fetcher
- ❌ Open interest fetcher
- ❌ Any microstructure feature computation
- ❌ Any time-series analytical store (no ClickHouse, no QuestDB, nothing)
- ❌ Any backtesting harness

---

## 3. Research Phase — What You Must Investigate

### 3.1 Order Book (L2) Integration

**Research questions:**

1. What is the exact NautilusTrader API for subscribing to L2 order book data on Binance Futures?
   - `self.subscribe_order_book_deltas(instrument_id, book_type=BookType.L2_MBP, depth=10)`?
   - What callback does this trigger? `on_order_book_deltas()` or `on_order_book()`?
   - What is the message rate? (Binance sends ~100ms updates for depth streams)
   
2. What is the data structure of a `OrderBookDelta` or `OrderBook` object in NautilusTrader?
   - How do you access bids/asks at each level?
   - How do you compute mid-price, spread, weighted mid-price?

3. What depth should we subscribe to? (5, 10, 20 levels)
   - Trade-off: more levels = more data but more noise
   - Recommendation needed for BTCUSDT-PERP specifically

4. **Alternative**: Should we use CCXT Pro's `watch_order_book()` instead of NautilusTrader's adapter? Evaluate pros/cons.

### 3.2 Trade Ticks (Time & Sales)

**Research questions:**

1. NautilusTrader API: `self.subscribe_trade_ticks(instrument_id)` → `on_trade_tick(tick)` — confirm this works with Binance Futures adapter
2. What fields are on a `TradeTick`? (price, size, aggressor side, timestamp)
3. At what rate do trade ticks arrive for BTCUSDT? (can be 50–500/sec during volatile periods)
4. How to efficiently compute:
   - **Order flow imbalance** (buy volume − sell volume over N-second window)
   - **Trade intensity** (trades per second)
   - **Large trade detection** (trades > X percentile of recent sizes)
   - **VWAP** from raw trades

### 3.3 Funding Rate

**Research questions:**

1. Binance Futures REST endpoint: `GET /fapi/v1/fundingRate` — what's the polling frequency? (funding settles every 8h but the *predicted* rate changes continuously)
2. Binance WS stream: `<symbol>@markPrice` provides `fundingRate` and `nextFundingTime` — should we use this instead of REST?
3. How does funding rate signal:
   - Crowded positioning (high positive = too many longs)
   - Mean-reversion opportunities
   - Carry cost for position sizing

### 3.4 Open Interest

**Research questions:**

1. REST endpoint: `GET /fapi/v1/openInterest` — poll every 1–5 minutes?
2. WS alternative: Does Binance stream OI changes?
3. How to compute:
   - **OI delta** (change in OI over last N minutes)
   - **OI + price divergence** (price up + OI down = weak rally)
   - **OI rate of change** for momentum signals

### 3.5 Time-Series Storage

**Research questions — evaluate these options:**

| Option | Pros | Cons | Fits This Repo? |
|--------|------|------|-----------------|
| **In-memory ring buffers** (Python deque/numpy) | Zero infra, fast, simple | Lost on restart, limited history | ✅ Best fit |
| **Redis TimeSeries** | Already in `requirements.txt`, fast writes | Not designed for analytics | ✅ Already a dependency |
| **QuestDB** | Purpose-built TSDB, SQL interface, very fast ingestion | New dependency, needs Docker | ⚠️ Needs approval |
| **ClickHouse** | Powerful analytics, columnar | Heavy, overkill for single-instrument | ❌ Too heavy |
| **DuckDB** | In-process, analytical SQL, zero infra | Single-threaded writes | ⚠️ Needs approval |
| **Parquet files** | Simple, portable, works with pandas | Not real-time queryable | ⚠️ Partial fit |

**Your recommendation must justify the choice.** My preference is: start with in-memory (deque/numpy arrays) for live trading, and Parquet/DuckDB for backtesting historical data. But convince me if you disagree.

### 3.6 LLM Prompt Engineering for Microstructure

**Critical research area.** The current DeepSeek prompt in `utils/deepseek_client.py` receives only bar-level data. You need to design:

1. **What microstructure features to include in the prompt** (not raw data — the LLM can't process 10,000 order book snapshots)
2. **Compression format** — how to distill 15 minutes of tick data into a ~500-token summary
3. **Feature candidates** to pass to the LLM:

```
ORDER FLOW SNAPSHOT (last 15 minutes):
├─ Net Order Flow: +$2.3M (buyers dominating)
├─ Trade Imbalance: 62% buy / 38% sell
├─ Large Trade Count: 14 (>$50K each), bias: 9 BUY / 5 SELL
├─ VWAP: $104,231.50 (price above VWAP = bullish)
├─ Trade Intensity: 127 trades/min (1.8x normal)
└─ Aggressor Flip: Sellers dominated 5 min ago, buyers now

ORDER BOOK PRESSURE:
├─ Bid Depth (10 levels): $4.2M
├─ Ask Depth (10 levels): $2.8M
├─ Imbalance Ratio: 0.60 (bid-heavy = bullish pressure)
├─ Spread: 0.01% ($10.42)
├─ Best Bid Wall: $104,200 (850 BTC stacked)
└─ Book Skew: +0.33 (weighted toward bids)

DERIVATIVES CONTEXT:
├─ Funding Rate: +0.0100% (longs paying shorts, slightly crowded)
├─ Predicted Next Funding: +0.0085% (declining)
├─ Open Interest: $18.2B (+2.3% last 4h)
├─ OI + Price: Both rising → New money entering longs
└─ OI Delta Velocity: Accelerating (+$150M/hr)
```

4. **How to weight microstructure vs existing signals** — propose a new weighting:
   - Technical Analysis: X%
   - Market Microstructure: Y%
   - Sentiment: Z%
   - Position Context: W%

### 3.7 Intraday Trading Architecture

**Research the decision cycle change:**

Current: 1 decision every 15 minutes (4 trades/day max)
Target: 100–200 trades/day → roughly **1 decision every 4–7 minutes**

Questions:
1. What bar timeframe? (1m bars? 3m bars? Or tick-based triggers?)
2. Should the timer interval shrink to 60–120 seconds?
3. Should we trigger decisions on **microstructure events** rather than timers? (e.g., order flow imbalance crosses threshold → immediate decision)
4. How to avoid overloading the DeepSeek API? (rate limits, cost, latency)
   - Option A: Use DeepSeek for high-level bias (every 5–15 min), use pure quantitative rules for individual entries
   - Option B: Use a local/smaller model for rapid decisions
   - Option C: Pre-compute microstructure score, only call DeepSeek when score crosses threshold
5. **Entry vs exit strategy**: Fast entries on microstructure signals, trailing stops for exits?

---

## 4. Implementation Constraints — NON-NEGOTIABLE

### 4.1 Code Standards (from AGENTS.md)

- **PEP 8**, 4-space indent, type hints on every function signature
- **Docstrings**: NautilusTrader/NumPy style (see `main_live.py` as reference)
- **Naming**: `snake_case` functions/modules, `PascalCase` classes, `UPPER_SNAKE_CASE` env vars, `lower_snake_case` YAML keys
- **Imports**: Keep aligned with `pyrightconfig.json` extra paths: `strategy/`, `utils/`, `indicators/`
- **Run `pyright` (basic mode)** before submitting any PR

### 4.2 Architecture Rules

1. **New data fetchers** → `utils/` directory (follow `sentiment_client.py` pattern)
2. **New indicator/feature managers** → `indicators/` directory (follow `technical_manager.py` pattern)
3. **New config knobs** → `configs/strategy_config.yaml` (with sanitized defaults)
4. **New env vars** → document in `.env.template` and `README.md`
5. **New dependencies** → add to `requirements.txt` with version pin, and **get my approval first**
6. **Never commit secrets** — `.env` stays local with `chmod 600`

### 4.3 File Placement

```
nautilus_ai_trading_agent/
├── strategy/
│   └── deepseek_strategy.py          # MODIFY: add new subscriptions, callbacks, microstructure integration
├── indicators/
│   ├── technical_manager.py          # DO NOT MODIFY (unless extending, not replacing)
│   └── microstructure_manager.py     # NEW: order flow, book pressure, derivatives features
├── utils/
│   ├── deepseek_client.py            # MODIFY: extend prompt with microstructure section
│   ├── sentiment_client.py           # DO NOT MODIFY
│   ├── funding_rate_client.py        # NEW: funding rate fetcher
│   └── open_interest_client.py       # NEW: open interest fetcher
├── configs/
│   └── strategy_config.yaml          # MODIFY: add microstructure config section
├── tests/
│   └── test_microstructure.py        # NEW: unit tests for feature computation
└── backtesting/
    ├── data_collector.py             # NEW: collect historical tick/book data for backtesting
    ├── backtest_runner.py            # NEW: backtesting harness
    └── results/                      # NEW: backtest results output
```

### 4.4 What Requires My Permission

| Action | Approval Needed? |
|--------|-----------------|
| Use NautilusTrader's built-in `subscribe_order_book_deltas()` | ✅ No — it's in-house |
| Use NautilusTrader's built-in `subscribe_trade_ticks()` | ✅ No — it's in-house |
| Use `requests` to call Binance REST API (like `_prefetch_historical_bars` already does) | ✅ No — already used |
| Use CCXT Pro for WebSocket streams | ✅ No — transitive dependency |
| Use Redis (already in `requirements.txt`) for caching | ✅ No — already a dependency |
| Add QuestDB, ClickHouse, or DuckDB | ❌ **Yes — justify first** |
| Add any new pip package | ❌ **Yes — justify first** |
| Change the DeepSeek model or add a second LLM | ❌ **Yes — justify first** |
| Reduce timer interval below 60 seconds | ❌ **Yes — justify first** |
| Change bar type from `15-MINUTE` to something else | ❌ **Yes — justify first** |

---

## 5. Deliverables — Phased Approach

### Phase 1: Data Layer (Week 1)

| # | Deliverable | Owner | Acceptance Criteria |
|---|-------------|-------|-------------------|
| 1.1 | `indicators/microstructure_manager.py` — ring-buffer based feature manager | Junior Dev A | Passes `pyright`, has docstrings, follows `technical_manager.py` pattern |
| 1.2 | Order book subscription in `deepseek_strategy.py` (`on_order_book_deltas` callback) | You (Lead) | Logs bid/ask spread and depth every 10 seconds in quick-test mode |
| 1.3 | Trade tick subscription (`on_trade_tick` callback) | Junior Dev A | Logs trade flow imbalance every 60 seconds |
| 1.4 | `utils/funding_rate_client.py` | Junior Dev B | Follows `sentiment_client.py` pattern exactly. Fetches from Binance REST |
| 1.5 | `utils/open_interest_client.py` | Junior Dev B | Same pattern. Polls every 5 minutes |
| 1.6 | Config additions in `strategy_config.yaml` | You (Lead) | All new knobs documented with comments |

### Phase 2: Feature Engineering (Week 2)

| # | Deliverable | Owner | Acceptance Criteria |
|---|-------------|-------|-------------------|
| 2.1 | Order flow imbalance feature (rolling window) | Junior Dev A | Validated against manual calculation on 1h of data |
| 2.2 | Book pressure / depth imbalance feature | Junior Dev A | Unit tested |
| 2.3 | Funding rate signal processing | Junior Dev B | Correct sign interpretation, historical average comparison |
| 2.4 | OI delta + OI-price divergence features | Junior Dev B | Unit tested |
| 2.5 | Microstructure snapshot formatter (for LLM prompt) | You (Lead) | Compressed to <500 tokens, human-readable |
| 2.6 | Extended DeepSeek prompt in `deepseek_client.py` | You (Lead) | Includes microstructure section, updated signal weighting |

### Phase 3: Intraday Strategy Logic (Week 3)

| # | Deliverable | Owner | Acceptance Criteria |
|---|-------------|-------|-------------------|
| 3.1 | Timer interval reduction + event-driven trigger design | You (Lead) | Documented trade-off analysis |
| 3.2 | Entry signal logic (microstructure + LLM hybrid) | You (Lead) | Clear decision tree documented |
| 3.3 | Exit logic (tighter trailing stops for intraday) | Junior Dev A | Configurable via YAML |
| 3.4 | Position sizing adjustments for high-frequency | Junior Dev B | Max drawdown bounded |
| 3.5 | Quick-test validation (`run_quick_test.py` on 1m bars) | All | Clean startup + 5 signal cycles logged |

### Phase 4: Backtesting (Week 4)

| # | Deliverable | Owner | Acceptance Criteria |
|---|-------------|-------|-------------------|
| 4.1 | Historical data collector (tick + book snapshots) | Junior Dev B | 7 days of BTCUSDT data collected |
| 4.2 | Backtesting harness using NautilusTrader's `BacktestEngine` | Junior Dev A | Runs without errors on collected data |
| 4.3 | Win rate analysis across parameter sweeps | You (Lead) | Report showing parameter → win rate mapping |
| 4.4 | **Gate check: 60%+ win rate achieved?** | You (Lead) | If no: iterate on features/logic. If yes: proceed to paper trading |

---

## 6. Backtesting Requirements

### Target Metrics

| Metric | Target | Minimum Acceptable |
|--------|--------|--------------------|
| Win Rate | 65%+ | 60% |
| Profit Factor | >1.5 | >1.2 |
| Sharpe Ratio (annualized) | >2.0 | >1.5 |
| Max Drawdown | <5% | <10% |
| Avg Trades/Day | 100–200 | 50 |
| Avg Holding Period | 5–30 min | 2–60 min |
| Avg Win / Avg Loss ratio | >1.0 | >0.8 |

### Backtesting Rules

1. Use **NautilusTrader's `BacktestEngine`** (already part of the framework — no new dependencies)
2. Test on **at least 30 days** of historical data
3. Include **realistic slippage** (0.01% per trade for BTCUSDT)
4. Include **trading fees** (Binance Futures: 0.02% maker, 0.04% taker)
5. Include **funding rate costs** in PnL calculation
6. **Out-of-sample validation**: Train on 20 days, test on 10 days (no peeking)
7. Report results with **equity curves**, **drawdown charts**, and **trade distribution histograms**

---

## 7. Risk Controls for Intraday

Implement these safety rails before going live:

```yaml
# configs/strategy_config.yaml — new section
intraday_risk:
  max_trades_per_day: 200
  max_trades_per_hour: 30
  max_consecutive_losses: 5          # Pause after 5 consecutive losses
  daily_loss_limit_pct: 3.0          # Stop trading if daily loss > 3% of equity
  hourly_loss_limit_pct: 1.0         # Cool down if hourly loss > 1%
  min_time_between_trades_sec: 30    # Minimum 30s between entries
  max_open_positions: 1              # Single instrument, single position
  kill_switch_drawdown_pct: 5.0      # Emergency stop at 5% drawdown
```

---

## 8. LLM Prompt Design Specification

### Current Prompt Weighting (as-is)

```
Technical Analysis:  60%
Market Sentiment:    30%
Position Context:    10%
```

### Proposed New Weighting (research and validate)

```
Technical Analysis:       30%   (still important, but less dominant)
Market Microstructure:    40%   (the new edge — order flow, book pressure)
Market Sentiment:         15%   (keep but reduce weight)
Derivatives Context:      10%   (funding rate + OI)
Position Context:          5%   (current exposure)
```

### Prompt Extension Template

Add this section to `_build_analysis_prompt()` in `deepseek_client.py`:

```
【MARKET MICROSTRUCTURE — REAL-TIME ORDER FLOW】

📊 Order Flow Analysis (last {window} minutes):
├─ Net Order Flow: {net_flow_sign}${abs(net_flow):,.0f}
├─ Buy/Sell Ratio: {buy_pct:.0f}% buy / {sell_pct:.0f}% sell
├─ Large Trades (>{large_threshold}): {large_count} ({large_buy} BUY / {large_sell} SELL)
├─ VWAP: ${vwap:,.2f} (price {'above' if price > vwap else 'below'} VWAP)
├─ Trade Intensity: {trades_per_min:.0f}/min ({intensity_ratio:.1f}x normal)
└─ Flow Momentum: {'Accelerating' if flow_accel > 0 else 'Decelerating'}

📖 Order Book Pressure:
├─ Bid Depth (10 levels): ${bid_depth:,.0f}
├─ Ask Depth (10 levels): ${ask_depth:,.0f}
├─ Depth Imbalance: {depth_imbalance:.2f} ({'bid-heavy' if > 0.5 else 'ask-heavy'})
├─ Spread: {spread_bps:.1f} bps (${spread_usd:,.2f})
└─ Book Skew: {book_skew:+.2f}

💰 Derivatives Context:
├─ Funding Rate: {funding_rate:+.4f}% ({funding_interpretation})
├─ Next Funding In: {next_funding_hours:.1f}h
├─ Open Interest: ${oi_billions:.1f}B ({oi_change_pct:+.1f}% last 4h)
└─ OI + Price Signal: {oi_price_signal}
```

---

## 9. Team Structure & Delegation

### You (Lead Quant Engineer)

- Own the architecture decisions
- Write the `microstructure_manager.py` core class
- Design and validate the LLM prompt extension
- Review all code from juniors before PR
- Run final backtesting analysis
- Present results to me with recommendation

### Junior Dev A — Data & Features

- Implement order book and trade tick callbacks
- Build ring-buffer data structures for tick storage
- Implement order flow imbalance and book pressure features
- Build the backtesting harness
- Write unit tests for all feature calculations

### Junior Dev B — External Data & Risk

- Build `funding_rate_client.py` and `open_interest_client.py`
- Implement OI features and funding rate signal processing
- Implement intraday risk controls (Section 7)
- Build the historical data collector for backtesting
- Write integration tests

### Delegation Rules

1. Every junior PR must be reviewed by you before merge
2. Every junior must run `pyright` and `python run_quick_test.py` before submitting
3. Juniors must follow the exact file placement in Section 4.3
4. Juniors must NOT add new dependencies without your approval (and you need mine)
5. All commits follow `<type>: <summary>` format per `GIT_WORKFLOW.md`

---

## 10. Definition of Done

This project is complete when:

- [ ] L2 order book data streaming and processed into features
- [ ] Trade tick data streaming and processed into order flow metrics
- [ ] Funding rate fetched and integrated into decision cycle
- [ ] Open interest fetched and integrated into decision cycle
- [ ] `MicrostructureManager` class created following `TechnicalIndicatorManager` pattern
- [ ] DeepSeek prompt extended with microstructure section
- [ ] Intraday timer/trigger system implemented (100–200 trades/day capable)
- [ ] Risk controls implemented (daily loss limit, max trades, kill switch)
- [ ] Backtesting harness built using NautilusTrader's `BacktestEngine`
- [ ] **60%+ win rate demonstrated on 30-day backtest with out-of-sample validation**
- [ ] All code passes `pyright` basic mode
- [ ] All new configs documented in `strategy_config.yaml` with comments
- [ ] All new env vars documented in `.env.template`
- [ ] Quick-test mode validates clean startup + signal cycles
- [ ] PR submitted with log snippets and backtest results attached

---

## 11. First Steps — Start Here

1. **Read every file** in the Mandatory Reading List (Section 2)
2. **Research** NautilusTrader's order book and trade tick APIs (check their docs/source)
3. **Write a 1-page research summary** covering Sections 3.1–3.7 with your recommendations
4. **Present the summary to me** for approval before writing any code
5. **Branch off `main`** as `feature/microstructure-data-layer` and begin Phase 1

> [!IMPORTANT]
> Do NOT start coding until your research summary is approved. I want to validate your approach on storage choice, event-driven vs timer-driven decisions, and LLM prompt design BEFORE any implementation work begins.