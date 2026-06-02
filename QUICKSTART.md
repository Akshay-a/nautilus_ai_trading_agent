# Quick Start — Bybit Demo / Paper Trade

Get the move-capture agent running on **Bybit demo** in a few minutes. This fork uses **DeepSeek + NautilusTrader + Bybit linear perpetuals**, not Binance.

---

## Prerequisites

```text
Python 3.10+
Bybit demo API key + secret (BYBIT_DEMO=true)
DeepSeek API key with balance
Basic terminal familiarity
```

---

## Install

```bash
cd /path/to/nautilus_ai_trading_agent
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.template .env
chmod 600 .env
```

---

## Configure `.env` (paper / demo defaults)

```bash
# Exchange — demo first
BYBIT_API_KEY=your_demo_key
BYBIT_API_SECRET=your_demo_secret
BYBIT_DEMO=true
BYBIT_TESTNET=false
INSTRUMENT_ID=BTCUSDT-LINEAR.BYBIT

# AI
DEEPSEEK_API_KEY=your_deepseek_key

# Safety — use DRY_RUN=true until startup looks clean, then false for demo fills
DRY_RUN=false

# Move-capture sizing (matches configs/strategy_config.yaml)
EQUITY=100000
LEVERAGE=20
FIXED_TRADE_USDT=2500
TIMEFRAME=5m
TIMER_INTERVAL_SEC=300
MIN_CONFIDENCE_TO_TRADE=MEDIUM
ENABLE_MARKET_STATE_GATE=true
```

Set leverage on the **Bybit demo account** to match (`20x` cross margin recommended for paper).

---

## Key YAML defaults

From [configs/strategy_config.yaml](configs/strategy_config.yaml):

| Parameter | Default | Notes |
|-----------|---------|-------|
| `equity` | 100000 | Reference capital for caps |
| `leverage` | 20 | Must match exchange setting |
| `fixed_trade_usdt` | 2500 | Fixed margin per protected entry; notional = margin x leverage |
| `bar_type` | 5-MINUTE | Align with `TIMEFRAME=5m` |
| `timer_interval_sec` | 300 | Ops/maintenance cadence |
| `decision_layer.enable_market_state_gate` | true | Skips LLM when state unchanged |
| `risk.min_entry_rr` | 0.5 | Blocks entries without structural TP room |
| `risk.tp_*_confidence_pct` | 1% / 2% / 3% | Fallback caps; live TP uses structural levels |
| `risk.enable_partial_tp` | false | No deterministic scale-outs in v1 |
| `risk.trailing_activation_pct` | 0.01 | Trailing only after +1% MFE |

Environment variables override YAML via [main_live.py](main_live.py).

---

## Run

```bash
# Foreground (recommended first run)
source venv/bin/activate
python main_live.py

# Or helper scripts
./start_trader.sh
./check_strategy_status.sh
tail -f logs/trader*.log
```

Expected startup:

```text
Strategy RUNNING
SubscribeBars(...-5-MINUTE-...)
Bar-close synthesis or Market-state gate lines every 5m
```

---

## Execution model (what to expect)

| Flow | Order type | Notes |
|------|------------|-------|
| **New entry** | LIMIT post-only bracket | Entry + mandatory SL + TP; levels use LLM, then structure, then 1% fallback |
| **LLM `EXIT_NOW`** | MARKET reduce-only | Close-only; never opens an opposite position |
| **Bracket submission failure** | No order | Entry is blocked; naked exposure is never opened |
| **Partial TP** | Disabled | LLM partial_close ignored |

Paper-trade validation: confirm bracket submissions, fill rate on LIMIT entries, and that `HOLD` never submits reduce-only orders.

---

## Paper-trade validation checklist (first 1–3 days)

**Startup**

- [ ] Single `main_live.py` process only
- [ ] `BYBIT_DEMO=true`, credentials valid
- [ ] Indicators initialize after warmup (~500 bars fetched)

**Gate**

- [ ] Most bars: `Market-state gate: previous LLM decision remains valid`
- [ ] Journal rows with `decision_cycle_trigger=market_state_gate`, no orders
- [ ] While flat, LLM may fire on broader structure/trend/volatility/price/microstructure/volume triggers
- [ ] While exposed, LLM fires only on material microstructure or volume changes

**Entries**

- [ ] `Creating bracket order` with `levels_source=llm|structural|fallback_1pct` and logged R:R
- [ ] Bracket submission failure blocks the entry
- [ ] Track LIMIT fill rate — signal BUY/SELL without position = unfilled entry

**Churn fixes**

- [ ] No partial close on HOLD
- [ ] Price-only drift and giveback do not wake the LLM while exposed

**Thesis continuity**

- [ ] LLM prompts include `PRIOR_DECISION` with `prior_thesis` when called

**Red flags — stop**

- [ ] HOLD submits reduce-only orders
- [ ] Any naked MARKET entry
- [ ] DeepSeek `402` / persistent API errors
- [ ] Nautilus vs Bybit position mismatch

---

## Monitor

```bash
# Signals and gate
grep -E "Market-state gate|Calling DeepSeek|🤖 Signal:" logs/trader*.log | tail -30

# Brackets and blocks
grep -E "Creating bracket order|Entry blocked|Submitted bracket" logs/trader*.log | tail -20

# Trades
grep -E "Order filled|Position opened|Position closed" logs/trader*.log | tail -20

# Dashboard (reads JSON logs only)
python tools/serve_monitor_dashboard.py --port 8080
```

---

## Troubleshooting

**Indicators not initialized** — wait for warmup bars (check logs for bar count).

**Order quantity below minimum** — raise `FIXED_TRADE_USDT` or check instrument min size.

**LLM every bar (no gate)** — verify `ENABLE_MARKET_STATE_GATE=true` and prior decision exists; first run always calls LLM.

**No fills after BUY signal** — post-only LIMIT at bar close may not cross; normal for v1; watch open orders on Bybit demo.

**DeepSeek insufficient balance** — top up API balance; strategy falls back to HOLD/LOW.

---

## Tests before paper run

```bash
python3 -m py_compile strategy/deepseek_strategy.py utils/deepseek_client.py main_live.py
pytest tests/test_strategy_components.py tests/test_integration_mock.py tests/test_bracket_order.py -q
git diff --check
```

---

## Final checklist

```text
✅ .env configured (BYBIT_DEMO=true, DRY_RUN reviewed)
✅ Demo account leverage = 20x (or matches LEVERAGE env)
✅ fixed_trade_usdt = 2500 USDT understood
✅ Market-state gate enabled
✅ Tests pass
✅ Monitoring path ready (logs + optional dashboard)
✅ Reversal/add MARKET limitation understood
```

---

*Quick Start — move-capture / Bybit demo — updated 2026-05-31*
