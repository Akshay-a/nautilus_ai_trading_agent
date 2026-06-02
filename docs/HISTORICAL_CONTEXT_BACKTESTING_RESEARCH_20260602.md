# Historical Context-Parity Backtesting Research

Date: 2026-06-02

## Bottom Line

If the goal is reliable backtests for an LLM-driven Bybit strategy that uses both TA and order book context, the best path is:

1. Use historical Bybit trades + order book deltas as the source of truth, not bars alone.
2. Replay those events through the same feature builders used in live (`TechnicalIndicatorManager`, `OrderBookManager`).
3. Call the real DeepSeek model during replay at the same decision boundaries as live.
4. Persist the exact decision context and model response for every replayed decision.

For this repo, the cleanest minimal architecture is:

- historical data source: `Tardis` first
- fallback / cost-down option: Bybit public archive downloads where available
- backtest engine: Nautilus-native replay for bars/trades/book events
- decision path: shared prompt/context code with live
- audit layer: flat CSV/Parquet artifacts for each LLM decision

CCXT / CCXT Pro should not be the core of this pipeline. It is useful for live market access and current snapshots, but not as a reliable historical order book replay layer.

## Current Repo State

### What already exists

- Live strategy subscribes to:
  - bars
  - order book deltas
  - trade ticks
- Live prompt includes:
  - kline context
  - technical indicators
  - microstructure summary
  - market-state gate context
  - position / risk context
- Existing backtesting layer is bar-only plus deterministic replay of logged BUY/SELL/HOLD outcomes.

Relevant files:

- Live decision loop: [strategy/deepseek_strategy.py](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/strategy/deepseek_strategy.py:484)
- Prompt construction: [utils/deepseek_client.py](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/utils/deepseek_client.py:678)
- Order book features: [indicators/orderbook_manager.py](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/indicators/orderbook_manager.py:1)
- Current bar-only fetch path: [backtesting/data_pipeline.py](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/backtesting/data_pipeline.py:1)
- Current deterministic replay strategy: [strategy/backtest_variants.py](/Users/akshayapsingi/Projects/nautilus_ai_trading_agent/strategy/backtest_variants.py:1)

### What is missing

The current backtesting path does not reconstruct the live decision context.

Missing pieces:

- no historical order book ingestion into Nautilus catalog
- no historical trade tick ingestion into Nautilus catalog
- no replay path that rebuilds `OrderBookManager` state before each bar-close decision
- no backtest mode that calls the real LLM using the same live prompt payload
- no persisted decision-context artifact containing the exact prompt inputs seen at replay time

### Branch status

`feature/backtesting-layer` is already merged into `main`. `main` is ahead by 4 commits and the feature branch is not ahead.

## External Findings

### 1. Bybit official APIs are not enough on their own

Bybit official docs currently expose:

- live WebSocket order book snapshot/delta streams
- current REST order book snapshot
- historical klines
- recent public trades, plus archived historical trades download

They do not document a first-class historical order book replay API in the same way they document klines and recent trades. So relying on official API docs alone is not enough for a robust historical L2 replay system.

Implication:

- bars-only from Bybit REST are insufficient
- current snapshot polling is insufficient
- official live WS docs are useful for schema parity, but not enough for backfill

## 2. Tardis is the strongest fit for this repo

Tardis is the best match because it gives:

- historical trades
- historical order book deltas / snapshots
- replay in exchange-native format
- Bybit coverage for derivatives and spot
- a NautilusTrader integration path already documented

This matters because your live code already thinks in terms of:

- exchange-native event flow
- Nautilus data objects
- event-driven feature updates

That means Tardis lets you preserve live parity with less custom glue than a DIY archive parser.

## 3. Bybit public archives are a viable fallback, but lower confidence

I found evidence that Bybit exposes public downloadable historical data through public archive listings and a history-data page. There are also community downloaders built around those archives.

This is useful if you want to avoid a paid vendor, but there are tradeoffs:

- docs are sparse
- format stability is less clearly governed than the API docs
- integration effort is higher
- you own reconstruction, validation, and gap handling

So this is a reasonable fallback, not the recommended primary path.

## 4. CCXT / CCXT Pro is the wrong backbone here

CCXT exposes current order book fetches and live order book watchers, but it is not a historical order book replay platform.

Use CCXT if you need:

- current snapshots
- exchange abstraction
- live wiring convenience

Do not use CCXT as the core historical microstructure research layer for this project.

## 5. Open-source projects worth lifting from

### Most relevant

1. `hftbacktest`
- strong reference for queue-aware execution, latency modelling, and order book replay
- useful if later you want more realistic fill simulation than standard bar-based backtests
- not the first integration target for this repo because your immediate problem is context parity, not advanced execution simulation

2. `tardis-python` / `tardis-node`
- most direct example of replaying Bybit historical depth + trades
- matches the exact ingestion problem you need to solve

3. `nssanta/Bybit-Download-OrderBook-Trades-Klines`
- practical community downloader if you choose the Bybit-public-archive route
- useful as a fallback ingestion utility or a format reference

4. `mansoor-mamnoon/limit-order-book`
- good reference for deterministic reconstruction, resync, and validation patterns
- useful design reference, not the primary integration path

## Recommended Architecture

### Principle

Backtest the same information set the live bot had, not a cheaper approximation.

That means every replayed decision should be built from:

- bar-close data available at decision time
- trailing kline window
- technical indicators computed causally
- order book state reconstructed causally
- trade flow computed causally
- risk / position context consistent with replay state
- same prompt builder
- same model call

### Minimal architecture

#### Layer 1: raw historical store

Partition by UTC day and symbol:

- `trades`
- `orderbook`
- optional `book_ticker`
- optional `funding`, `liquidations`, `ticker`

Store both:

- exchange timestamp
- local capture / replay timestamp when provided by source

Do not partition by local timezone. Use UTC as the canonical storage clock.

#### Layer 2: Nautilus replay dataset

Build a replayable dataset that feeds:

- `TradeTick`
- `OrderBookDeltas` or compatible depth snapshots
- `Bar`

Important:

- if replaying L2, always warm up from `00:00 UTC` for that day when using Tardis-style daily snapshots
- if the user asks for a smaller evaluation window, prewarm from midnight and score only after the requested start

#### Layer 3: context-parity decision runner

Run a strategy/replay harness that:

- updates `TechnicalIndicatorManager`
- updates `OrderBookManager`
- triggers only on the same bar-close conditions as live
- calls the real DeepSeek model
- writes one row per decision with the full causal context

#### Layer 4: audit artifacts

For every replayed decision, persist:

- decision timestamp
- bar close timestamp
- kline slice hash or serialized payload
- technical payload JSON
- microstructure payload JSON
- market-state gate reason
- prompt hash
- prompt body or prompt artifact path
- raw LLM response JSON
- parsed action
- execution outcome
- forward returns / MFE / MAE

This is the reliability layer. Without it, you cannot trust replay claims after prompt or feature changes.

## Code Changes I Recommend

### Priority 1

1. Add historical order book + trade ingestion.
2. Add a backtest replay path that rebuilds live microstructure state.
3. Persist exact replay decision context to flat files.

### Priority 2

4. Refactor shared context assembly out of the live strategy so live and replay use the same builder.
5. Add a replay mode that disables live-only side effects:
   - Telegram
   - exchange risk refreshes
   - live-only order management assumptions

### Priority 3

6. Add funding-rate and liquidation context if you want stronger execution realism for multi-day or multi-week runs.
7. Add comparative regime labels for:
   - trend
   - chop
   - high vol
   - low vol
   - event/liquidation stress

## Practical Recommendation

### Best immediate path

Build the pipeline around Tardis + Nautilus first.

Reason:

- lowest engineering risk
- highest parity with live event flow
- already aligned with Nautilus
- already aligned with Bybit WS schemas
- faster to a trustworthy one-day / one-week / one-month replay

### Fallback path

If you want to minimize vendor spend:

- use Bybit public archives for order book / trades where available
- derive Nautilus replay data from those archives
- keep the rest of the architecture the same

But expect more validation work and more custom parsers.

### Not recommended

- CCXT / CCXT Pro as the main historical replay source
- bars-only backtests for LLM prompt changes that depend on microstructure
- a new prompt without storing the exact replay context row that produced each decision

## Verification Standard Before Trusting Results

You should not trust the backtests until all of the following are true:

1. Replay decisions can be exported with exact context rows.
2. A forward-only replay never uses future data.
3. Order book reconstruction is deterministic for the same raw day file.
4. One live day can be shadow-captured and replayed with materially matching prompt inputs.
5. Metrics are segmented by market regime, not only aggregated.

## Suggested Implementation Order

1. Ingest one week of BTCUSDT linear Bybit historical:
   - trades
   - orderbook.50
   - bars
2. Build a single-symbol replay harness that reproduces current live decision timing.
3. Persist `llm_context_replay.csv` or parquet.
4. Run real DeepSeek calls for 1 day, then 1 week.
5. Compare decisions across:
   - current live logs
   - deterministic replay of same window
6. Only then widen to 1 month and start prompt iteration studies.

## Recommendation

Proceed with a Tardis-backed historical replay pipeline and treat Bybit public archives as fallback supply.

The repo already has the decision logic and the feature builders. What it lacks is a proper historical microstructure ingestion layer and a context-parity replay harness. That is the work that actually unlocks trustworthy LLM backtesting here.
