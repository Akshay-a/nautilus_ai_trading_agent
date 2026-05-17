"""Backtest-only deterministic strategy variants for NautilusTrader."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from indicators.technical_manager import TechnicalIndicatorManager


VariantName = Literal["buy_and_hold", "rule_proxy", "recorded_llm_replay"]


@dataclass(frozen=True)
class ReplaySignal:
    ts_ns: int
    signal: str


class BacktestVariantStrategyConfig(StrategyConfig, frozen=True):
    """Config for deterministic backtest variants."""

    instrument_id: str
    bar_type: str
    variant: VariantName

    fixed_trade_usdt: float = 10_000.0
    min_trade_amount: float = 0.001
    warmup_bars: int = 50
    allow_short: bool = True

    replay_signals_csv: str = ""


class BacktestVariantStrategy(Strategy):
    """Thin deterministic strategy wrapper for variant backtesting."""

    def __init__(self, config: BacktestVariantStrategyConfig):
        super().__init__(config)

        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.variant = config.variant

        self.fixed_trade_usdt = float(config.fixed_trade_usdt)
        self.min_trade_amount = float(config.min_trade_amount)
        self.allow_short = bool(config.allow_short)

        self.instrument = None
        self.pending_target_side: str | None = None  # long / short / flat

        self.indicators = TechnicalIndicatorManager(
            sma_periods=[5, 20, 50],
            ema_periods=[12, 26],
            rsi_period=14,
            macd_fast=12,
            macd_slow=26,
            macd_signal=9,
            bb_period=20,
            bb_std=2.0,
            volume_ma_period=20,
            support_resistance_lookback=20,
        )

        self._replay_signals: list[ReplaySignal] = []
        self._replay_index = 0
        self._latest_replay_target: str = "flat"
        self._bars_seen = 0
        self._buy_hold_entered = False

        if self.variant == "recorded_llm_replay":
            self._replay_signals = self._load_replay_signals(config.replay_signals_csv)

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument unavailable: {self.instrument_id}")
            self.stop()
            return

        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self._bars_seen += 1
        current_price = float(bar.close)

        # Execute only the signal decided on the prior bar to remain causal.
        self._apply_target(self.pending_target_side, current_price)

        self.indicators.update(bar)
        self.pending_target_side = self._generate_target_side(bar, current_price)

    def _generate_target_side(self, bar: Bar, current_price: float) -> str | None:
        if self.variant == "buy_and_hold":
            return "long"

        if self.variant == "recorded_llm_replay":
            return self._target_from_replay(bar.ts_event)

        if self.variant == "rule_proxy":
            return self._target_from_rule_proxy(current_price)

        return None

    def _target_from_rule_proxy(self, current_price: float) -> str | None:
        if not self.indicators.is_initialized():
            return None

        t = self.indicators.get_technical_data(current_price)

        sma_fast = float(t.get("sma_5", current_price))
        sma_slow = float(t.get("sma_20", current_price))
        macd_hist = float(t.get("macd_histogram", 0.0))
        rsi = float(t.get("rsi", 50.0))

        if current_price > sma_slow and sma_fast >= sma_slow and macd_hist > 0 and rsi < 70:
            return "long"
        if self.allow_short and current_price < sma_slow and sma_fast <= sma_slow and macd_hist < 0 and rsi > 30:
            return "short"

        return "flat"

    def _target_from_replay(self, bar_ts_ns: int) -> str:
        while self._replay_index < len(self._replay_signals):
            item = self._replay_signals[self._replay_index]
            if item.ts_ns > bar_ts_ns:
                break

            if item.signal == "BUY":
                self._latest_replay_target = "long"
            elif item.signal == "SELL":
                self._latest_replay_target = "short"
            else:
                self._latest_replay_target = "flat"
            self._replay_index += 1

        return self._latest_replay_target

    def _load_replay_signals(self, csv_path: str) -> list[ReplaySignal]:
        path = Path(csv_path)
        if not csv_path or not path.exists():
            return []

        out: list[ReplaySignal] = []
        with path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    ts_ns = int(row["ts_ns"])
                except (TypeError, ValueError):
                    continue
                signal = str(row.get("signal", "HOLD")).strip().upper()
                if signal not in {"BUY", "SELL", "HOLD"}:
                    continue
                out.append(ReplaySignal(ts_ns=ts_ns, signal=signal))

        return sorted(out, key=lambda item: item.ts_ns)

    def _apply_target(self, target_side: str | None, current_price: float) -> None:
        if target_side is None:
            return

        position = self._get_open_position()
        if target_side == "flat":
            if position:
                current_side = "long" if position.side == PositionSide.LONG else "short"
                close_side = OrderSide.SELL if current_side == "long" else OrderSide.BUY
                self._submit_market(close_side, abs(float(position.quantity)), reduce_only=True)
            return

        target_qty = max(self.fixed_trade_usdt / max(current_price, 1e-9), self.min_trade_amount)
        target_qty = round(target_qty, 3)

        if position is None:
            side = OrderSide.BUY if target_side == "long" else OrderSide.SELL
            self._submit_market(side, target_qty, reduce_only=False)
            if self.variant == "buy_and_hold":
                self._buy_hold_entered = True
            return

        current_qty = abs(float(position.quantity))
        current_side = "long" if position.side == PositionSide.LONG else "short"

        if current_side == target_side:
            if self.variant == "buy_and_hold" and self._buy_hold_entered:
                return
            delta = target_qty - current_qty
            if abs(delta) < self.min_trade_amount:
                return
            if delta > 0:
                side = OrderSide.BUY if target_side == "long" else OrderSide.SELL
                self._submit_market(side, abs(delta), reduce_only=False)
            else:
                side = OrderSide.SELL if target_side == "long" else OrderSide.BUY
                self._submit_market(side, abs(delta), reduce_only=True)
            return

        close_side = OrderSide.SELL if current_side == "long" else OrderSide.BUY
        self._submit_market(close_side, current_qty, reduce_only=True)
        open_side = OrderSide.BUY if target_side == "long" else OrderSide.SELL
        self._submit_market(open_side, target_qty, reduce_only=False)

    def _get_open_position(self):
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if not positions:
            return None
        return positions[0]

    def _submit_market(self, side: OrderSide, quantity: float, reduce_only: bool) -> None:
        if quantity < self.min_trade_amount:
            return
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(quantity),
            time_in_force=TimeInForce.GTC,
            reduce_only=reduce_only,
        )
        self.submit_order(order)
