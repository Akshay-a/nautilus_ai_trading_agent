"""
Focused tests for exchange-aware position and risk context behavior.
"""

import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nautilus_trader.model.enums import PositionSide

from strategy.deepseek_strategy import DeepSeekAIStrategy


class FakeInstrument:
    """Minimal instrument stub with a 0.01 size increment."""

    size_increment = 0.01

    def make_qty(self, value):
        rounded = round(float(value), 2)
        if rounded <= 0:
            raise ValueError("rounded to zero")
        return rounded


class FakeBar:
    close = 100.0


def _strategy_stub() -> Any:
    strategy = SimpleNamespace()
    strategy.instrument = FakeInstrument()
    strategy.base_asset = "ETH"
    strategy.equity = 100000.0
    strategy.leverage = 10.0
    strategy.fixed_trade_usdt = 1000.0
    strategy.base_usdt = 1000.0
    strategy.position_config = {
        "high_confidence_multiplier": 1.5,
        "medium_confidence_multiplier": 1.0,
        "low_confidence_multiplier": 0.5,
        "max_position_ratio": 1.0,
        "trend_strength_multiplier": 1.0,
        "min_trade_amount": 0.001,
        "adjustment_threshold": 0.001,
    }
    strategy.rsi_extreme_upper = 75.0
    strategy.rsi_extreme_lower = 25.0
    strategy.rsi_extreme_mult = 0.7
    for name in (
        "_get_current_position_data",
        "_position_adjustment_threshold",
        "_calculate_position_size",
        "_account_equity_for_sizing",
        "_available_balance_for_sizing",
        "_normalize_order_quantity",
        "_log_info_safe",
        "_log_warning_safe",
    ):
        setattr(strategy, name, MethodType(getattr(DeepSeekAIStrategy, name), strategy))
    return strategy


def test_current_position_aggregates_multiple_open_positions():
    strategy = _strategy_stub()
    strategy.indicator_manager = Mock(recent_bars=[FakeBar()])
    strategy.instrument_id = "ETHUSDT-LINEAR.BYBIT"

    external_position = Mock(
        is_open=True,
        side=PositionSide.SHORT,
        quantity=4.57,
        avg_px_open=2195.27,
        id="ETHUSDT-LINEAR.BYBIT-EXTERNAL",
    )
    external_position.unrealized_pnl.return_value = 300.0

    strategy_position = Mock(
        is_open=True,
        side=PositionSide.SHORT,
        quantity=4.70,
        avg_px_open=2128.67,
        id="ETHUSDT-LINEAR.BYBIT-DeepSeekAIStrategy-000",
    )
    strategy_position.unrealized_pnl.return_value = -8.0

    strategy.cache = Mock()
    strategy.cache.positions_open.return_value = [external_position, strategy_position]

    position = strategy._get_current_position_data()

    assert position is not None
    assert position["side"] == "short"
    assert round(position["quantity"], 2) == 9.27
    assert position["position_count"] == 2
    assert position["source"] == "nautilus_aggregate"
    assert position["unrealized_pnl"] == 292.0


def test_sub_increment_order_quantity_is_skipped():
    strategy = _strategy_stub()

    assert strategy._position_adjustment_threshold() == 0.01
    assert strategy._normalize_order_quantity(0.004) is None
    assert strategy._normalize_order_quantity(0.011) == 0.01


def test_position_size_varies_by_confidence_with_fixed_trade_base():
    strategy = _strategy_stub()
    price_data = {"price": 2000.0}
    technical_data = {"overall_trend": "neutral", "rsi": 50.0}

    low = strategy._calculate_position_size(
        {"confidence": "LOW"},
        price_data,
        technical_data,
        current_position=None,
    )
    medium = strategy._calculate_position_size(
        {"confidence": "MEDIUM"},
        price_data,
        technical_data,
        current_position=None,
    )
    high = strategy._calculate_position_size(
        {"confidence": "HIGH"},
        price_data,
        technical_data,
        current_position=None,
    )

    assert low < medium < high
