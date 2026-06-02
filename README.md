# Nautilus AI Trading Agent

An experimental autonomous trading agent that lets an LLM make BUY / SELL / HOLD decisions from technical indicators, live position context, and order book microstructure.

This is a solo developer research project. The goal is to understand how far a language model can go when it is placed inside a real trading loop with structured market data, risk constraints, and execution plumbing.

This is not financial advice, not a profitable-strategy claim, and not something to run with real money without serious review, backtesting, and risk controls.

## Acknowledgements

This project is a fork and extension of [Patrick-code-Bot/nautilus_AItrader](https://github.com/Patrick-code-Bot/nautilus_AItrader).

The original repository did the heavy lifting for the core trading infrastructure: NautilusTrader strategy structure, DeepSeek integration, exchange execution flow, risk management components, Telegram hooks, and operational scripts. This fork builds on that foundation and focuses on adding live order book ingestion, microstructure features, Bybit demo/live wiring, monitoring, and backtesting utilities.

Additional credit:

- [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) for the event-driven trading framework.
- [DeepSeek](https://www.deepseek.com/) for the LLM API used by the strategy.
- Bybit market data and execution APIs for the current demo/live integration target.

## What This Fork Adds

The original project already had a working LLM-driven trading strategy. This fork changes the experiment from "LLM reads candles and indicators" to "LLM reads candles, indicators, position state, and market microstructure."

Main additions:

- Bybit linear perpetual integration for live data and demo/live execution.
- L2 order book subscription and trade tick ingestion.
- Raw order book feature pipeline.
- Derived microstructure signals:
  - spread and spread volatility
  - top-of-book imbalance
  - depth imbalance
  - queue pressure
  - EMA order-flow imbalance
  - trade-flow imbalance
  - sweep buy/sell counts
  - VWAP deviation
  - depth regime classification
- Microstructure-aware LLM prompt section.
- Structured LLM prompt/response logs for auditability.
- Local monitoring dashboard from JSON logs.
- Demo-safe launcher scripts.
- Nautilus-native backtesting utilities for rule-proxy and replay variants.

## Current Experiment

The current live experiment uses:

- Exchange: Bybit linear perpetuals.
- Default instrument: `BTCUSDT-LINEAR.BYBIT` or overridden via `INSTRUMENT_ID`.
- Common demo instrument used during testing: `ETHUSDT-LINEAR.BYBIT`.
- LLM provider: DeepSeek.
- Default model: `deepseek-reasoner`.
- Decision loop: **5-minute bars** by default (`TIMEFRAME=5m`); LLM calls are further gated by market-state changes.
- Position sizing: **2500 USDT fixed margin** per protected entry (`fixed_trade_usdt`), **20x leverage** => **50000 USDT target notional**.
- Safety mode: `DRY_RUN=true` skips order submission; `BYBIT_DEMO=true` for demo trading.

The LLM receives a compact market payload plus a detailed prompt containing:

- recent K-line data
- technical indicators
- support/resistance
- current position and unrealized PnL
- order book microstructure
- previous signal
- risk management instructions

It must return strict JSON:

```json
{
  "signal": "BUY|SELL|HOLD",
  "position_action": "ENTER_LONG|ENTER_SHORT|HOLD_POSITION|EXIT_NOW|NO_ACTION",
  "confidence": "HIGH|MEDIUM|LOW",
  "reason": "analysis text",
  "stop_loss": 0,
  "take_profit": 0,
  "trend_strength": "STRONG|MODERATE|WEAK",
  "risk_assessment": "LOW|MEDIUM|HIGH"
}
```

## Architecture

```text
Bybit market data
  -> NautilusTrader data engine
  -> bars + L2 order book + trade ticks
  -> technical indicators + microstructure features
  -> DeepSeekAnalyzer prompt
  -> BUY / SELL / HOLD JSON signal
  -> DeepSeekAIStrategy risk checks
  -> order submission or DRY_RUN simulation
  -> JSON logs + dashboard + optional Telegram
```

Important files:

```text
main_live.py                         Live runtime entrypoint
configs/strategy_config.yaml         Strategy defaults and risk settings
strategy/deepseek_strategy.py        Main Nautilus strategy and execution logic
utils/deepseek_client.py             LLM prompt construction, API call, JSON parsing
indicators/technical_manager.py      Technical indicator calculations
indicators/orderbook_manager.py      Order book storage and microstructure features
tools/serve_monitor_dashboard.py     Local dashboard and status JSON API
tools/fetch_bybit_bars.py            Historical Bybit bar fetcher
tools/run_backtest.py                Nautilus-native backtest runner
backtesting/                         Backtest data, replay, metrics helpers
logs/                                Nautilus JSON logs
data/microstructure_features.csv     Runtime feature dump
```

## Setup

Use Python 3.10 if possible. NautilusTrader support can be sensitive to Python versions.

```bash
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.template .env
chmod 600 .env
```

Edit `.env`:

```bash
BYBIT_API_KEY=your_key
BYBIT_API_SECRET=your_secret
BYBIT_DEMO=true
BYBIT_TESTNET=false
DRY_RUN=true
INSTRUMENT_ID=ETHUSDT-LINEAR.BYBIT

DEEPSEEK_API_KEY=your_deepseek_key

TIMEFRAME=5m
TIMER_INTERVAL_SEC=300
FIXED_TRADE_USDT=2500
LEVERAGE=20
MIN_CONFIDENCE_TO_TRADE=MEDIUM
ENABLE_MARKET_STATE_GATE=true
```

Use `DRY_RUN=true` until you have verified the runtime path. Use `BYBIT_DEMO=true` for demo trading where supported by your credentials.

## Running

Foreground:

```bash
source venv/bin/activate
python main_live.py
```

Demo helper:

```bash
./start_paper_demo.sh
```

Status:

```bash
./check_strategy_status.sh
```

Stop:

```bash
./stop_trader.sh
```

Dashboard:

```bash
python tools/serve_monitor_dashboard.py --port 8080
```

Then open:

```text
http://localhost:8080
```

The dashboard reads existing JSON logs. It does not change trading behavior.

## Configuration

Primary config is in [configs/strategy_config.yaml](configs/strategy_config.yaml).

Key sections:

- `strategy.instrument_id`: default trading instrument.
- `strategy.bar_type`: Nautilus bar type.
- `strategy.position_management.fixed_trade_usdt`: fixed margin capital per protected entry (default **2500 USDT**; notional = margin x leverage).
- `strategy.leverage`: reference leverage (default **20x**; must match exchange account setting).
- `strategy.deepseek.model`: model name sent to DeepSeek.
- `strategy.deepseek.kline_context_bars`: number of bars sent to the LLM.
- `strategy.orderbook`: depth/trade buffers and microstructure settings.
- `strategy.decision_layer.enable_market_state_gate`: skips LLM calls while the previous decision remains valid and regime/structure/microstructure are unchanged.
- `strategy.timer_interval_sec`: maintenance cadence; LLM analysis is further gated by market-state changes.
- `strategy.warmup_bars`: historical bars fetched at startup.
- `strategy.risk`: confidence filters, structural SL/TP, `min_entry_rr`, OCO, and trailing stop. Partial TP is disabled by default for move-capture mode.

### Execution model (v1)

| Flow | Order type |
|------|------------|
| New entry (flat) | LIMIT post-only bracket + mandatory SL/TP |
| LLM `EXIT_NOW` | MARKET reduce-only close; no automatic reversal |
| Invalid LLM levels | Structural SL/TP, then symmetric 1% fallback |
| Bracket submission failure / missing price | Entry blocked |

New entries never fall back to an unprotected market order. Post-only LIMIT entries may not fill on every signal — track open orders on demo before trusting fill rate.

Environment variables in `.env` override several YAML values. Check [main_live.py](main_live.py) before assuming a config key is final.

## Order Book Features

The microstructure pipeline lives in [indicators/orderbook_manager.py](indicators/orderbook_manager.py).

It maintains ring buffers for:

- managed order book snapshots
- trade ticks
- computed feature rows

The strategy periodically emits:

```text
data/microstructure_features.csv
```

This file is useful for quick inspection and IC analysis, but it is currently a rolling in-memory snapshot, not a full historical data lake.

## Backtesting

Backtesting is intentionally separated from the live LLM path.

Fetch bars:

```bash
python tools/fetch_bybit_bars.py fetch \
  --start 2025-01-01T00:00:00Z \
  --end 2025-07-01T00:00:00Z \
  --catalog-path data/catalog \
  --symbol BTCUSDT \
  --instrument-id BTCUSDT-LINEAR.BYBIT \
  --bar-type BTCUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL \
  --interval-minutes 15
```

Validate catalog:

```bash
python tools/fetch_bybit_bars.py validate \
  --catalog-path data/catalog \
  --instrument-id BTCUSDT-LINEAR.BYBIT \
  --bar-type BTCUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL \
  --interval-minutes 15
```

Run variants:

```bash
python tools/run_backtest.py --config configs/backtest_config.yaml
```

Backtest outputs are written under:

```text
backtest_results/
```

Current variants include:

- `buy_and_hold`
- `rule_proxy`
- `recorded_llm_replay`

Do not treat dry-run logs or demo fills as evidence of trading edge. Strategy quality requires reproducible out-of-sample backtests with fees, slippage, and realistic execution assumptions.

## Monitoring Logs

Useful log checks:

```bash
ls -lt logs/deepseek_trader_*.json | head
rg "Calling DeepSeek AI for analysis" logs/deepseek_trader_*.json
rg "LLM Prompt Payload|LLM Response JSON|Signal:" logs/deepseek_trader_*.json
rg "OrderFilled|PositionOpened|PositionChanged|PositionClosed" logs/deepseek_trader_*.json
```

The project logs prompt payloads and parsed LLM response JSON so each decision can be audited after the fact.

## Safety Notes

Assume this code can lose money.

Known risks:

- LLM output can be malformed, delayed, or wrong.
- Prompt instructions do not guarantee risk-aware behavior.
- Market microstructure signals can be noisy and regime-dependent.
- Exchange APIs, websocket feeds, and order state can desync.
- Demo fills do not prove live execution quality.
- Backtests can overfit if variants are tuned after seeing results.
- Fees and slippage can dominate high-frequency LLM decision loops.

Practical safety defaults:

- Start with `DRY_RUN=true`.
- Use demo credentials before mainnet.
- Keep `FIXED_TRADE_USDT` small while testing.
- Run the dashboard and inspect logs during every experiment.
- Reconcile account state directly on the exchange.
- Never commit `.env` or API credentials.

## Roadmap

Near-term work:

- Improve persisted feature history instead of rolling CSV dumps only.
- Add cleaner trade/PnL reporting from Nautilus events.
- Add more deterministic replay modes for LLM decisions.
- Add cost tracking for API calls and token usage.
- Compare DeepSeek models and other LLMs under the same prompt/data payload.
- Tighten backtest/live parity for microstructure inputs.

## License

This fork inherits the licensing constraints of the upstream project and dependencies. Review the upstream repository and dependency licenses before commercial use.

## Disclaimer

This repository is for research and engineering experimentation. It is not investment advice, not a trading recommendation, and not a guarantee of profitability. You are responsible for your own keys, accounts, capital, and risk.
