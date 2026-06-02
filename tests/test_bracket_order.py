"""Unit tests for DeepSeek bracket order helpers."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import Mock
import enum
import sys
import types
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ensure_nautilus_stub() -> None:
    """Create minimal nautilus_trader stubs so strategy module can import."""
    base = types.ModuleType("nautilus_trader")
    sys.modules["nautilus_trader"] = base

    config_mod = types.ModuleType("nautilus_trader.config")
    class StrategyConfig:  # noqa: D401 - minimal stub
        """Stub StrategyConfig."""
        def __init_subclass__(cls, **kwargs: Any) -> None:
            return
    config_mod.StrategyConfig = StrategyConfig
    sys.modules["nautilus_trader.config"] = config_mod

    trading_mod = types.ModuleType("nautilus_trader.trading")
    sys.modules["nautilus_trader.trading"] = trading_mod

    trade_strategy_mod = types.ModuleType("nautilus_trader.trading.strategy")
    class Strategy:
        def __init__(self, config: StrategyConfig | None = None) -> None:
            self.config = config
    trade_strategy_mod.Strategy = Strategy
    sys.modules["nautilus_trader.trading.strategy"] = trade_strategy_mod

    model_mod = types.ModuleType("nautilus_trader.model")
    sys.modules["nautilus_trader.model"] = model_mod

    data_mod = types.ModuleType("nautilus_trader.model.data")
    class Bar:
        def __init__(self, open_price, high, low, close, volume):
            self.open = open_price
            self.high = high
            self.low = low
            self.close = close
            self.volume = volume
    class BarType:
        @classmethod
        def from_str(cls, value: str) -> str:
            return value
    data_mod.Bar = Bar
    data_mod.BarType = BarType
    sys.modules["nautilus_trader.model.data"] = data_mod

    enums_mod = types.ModuleType("nautilus_trader.model.enums")
    OrderSide = enum.Enum("OrderSide", "BUY SELL")
    TimeInForce = enum.Enum("TimeInForce", "GTC FOK IOC")
    PositionSide = enum.Enum("PositionSide", "LONG SHORT")
    PriceType = enum.Enum("PriceType", "LAST MARK")
    TriggerType = enum.Enum("TriggerType", "DEFAULT LAST INDEX MARK")
    OrderType = enum.Enum("OrderType", "MARKET LIMIT STOP_MARKET")
    enums_mod.OrderSide = OrderSide
    enums_mod.TimeInForce = TimeInForce
    enums_mod.PositionSide = PositionSide
    enums_mod.PriceType = PriceType
    enums_mod.TriggerType = TriggerType
    enums_mod.OrderType = OrderType
    sys.modules["nautilus_trader.model.enums"] = enums_mod

    identifiers_mod = types.ModuleType("nautilus_trader.model.identifiers")
    class InstrumentId(str):
        @classmethod
        def from_str(cls, value: str) -> "InstrumentId":
            return cls(value)
    identifiers_mod.InstrumentId = InstrumentId
    sys.modules["nautilus_trader.model.identifiers"] = identifiers_mod

    instruments_mod = types.ModuleType("nautilus_trader.model.instruments")
    class Instrument:
        def make_qty(self, quantity: float) -> Decimal:
            return Decimal(str(quantity))
        def make_price(self, price: float) -> Decimal:
            return Decimal(str(price))
    instruments_mod.Instrument = Instrument
    sys.modules["nautilus_trader.model.instruments"] = instruments_mod

    position_mod = types.ModuleType("nautilus_trader.model.position")
    class Position:
        pass
    position_mod.Position = Position
    sys.modules["nautilus_trader.model.position"] = position_mod

    orders_mod = types.ModuleType("nautilus_trader.model.orders")
    class MarketOrder:
        pass
    orders_mod.MarketOrder = MarketOrder
    sys.modules["nautilus_trader.model.orders"] = orders_mod

    indicators_mod = types.ModuleType("nautilus_trader.indicators")

    class _Indicator:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.value = 0.0
            self.initialized = False

        def update_raw(self, value: float) -> None:
            self.value = value
            self.initialized = True

    class SimpleMovingAverage(_Indicator):
        pass

    class ExponentialMovingAverage(_Indicator):
        pass

    class RelativeStrengthIndex(_Indicator):
        pass

    class MovingAverageConvergenceDivergence(_Indicator):
        pass

    class AverageTrueRange(_Indicator):
        pass

    class BollingerBands(_Indicator):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.upper = 0.0
            self.middle = 0.0
            self.lower = 0.0

    class DirectionalMovement(_Indicator):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.pos = 0.0
            self.neg = 0.0

    MovingAverageType = enum.Enum("MovingAverageType", "SIMPLE WILDER")

    indicators_mod.SimpleMovingAverage = SimpleMovingAverage
    indicators_mod.ExponentialMovingAverage = ExponentialMovingAverage
    indicators_mod.RelativeStrengthIndex = RelativeStrengthIndex
    indicators_mod.MovingAverageConvergenceDivergence = MovingAverageConvergenceDivergence
    indicators_mod.AverageTrueRange = AverageTrueRange
    indicators_mod.BollingerBands = BollingerBands
    indicators_mod.DirectionalMovement = DirectionalMovement
    indicators_mod.MovingAverageType = MovingAverageType
    sys.modules["nautilus_trader.indicators"] = indicators_mod

    openai_mod = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create_response)
            )

        def _create_response(self, *args: Any, **kwargs: Any) -> Any:
            content = (
                '{\"signal\":\"HOLD\",\"confidence\":\"LOW\",\"reason\":\"stub\",'
                '\"stop_loss\":0,\"take_profit\":0}'
            )
            message = types.SimpleNamespace(content=content)
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    openai_mod.OpenAI = OpenAI
    sys.modules["openai"] = openai_mod


_ensure_nautilus_stub()
sys.modules.pop("strategy.deepseek_strategy", None)

from nautilus_trader.model.enums import OrderSide, OrderType  # type: ignore
from strategy.deepseek_strategy import DeepSeekAIStrategy


class DummyInstrument:
    def make_qty(self, quantity: float) -> Decimal:
        return Decimal(str(quantity))

    def make_price(self, price: float) -> Decimal:
        return Decimal(str(price))


class DummyOrderList:
    def __init__(self) -> None:
        self.orders = [SimpleNamespace(order_type=OrderType.STOP_MARKET, client_order_id="SL-order")]
        self.id = "order-list-001"


class DummyOrderFactory:
    def __init__(self) -> None:
        self.kwargs: Dict[str, Any] | None = None

    def bracket(self, **kwargs: Any) -> DummyOrderList:
        self.kwargs = kwargs
        return DummyOrderList()


class DummyCache:
    def __init__(self, bars: List[Any]) -> None:
        self._bars = bars

    def bars(self, bar_type: Any) -> List[Any]:
        return self._bars


class DummyLogger:
    def info(self, *args: Any, **kwargs: Any) -> None:
        pass

    def warning(self, *args: Any, **kwargs: Any) -> None:
        pass

    def error(self, *args: Any, **kwargs: Any) -> None:
        pass

    def debug(self, *args: Any, **kwargs: Any) -> None:
        pass


def _make_strategy_stub() -> DeepSeekAIStrategy:
    strategy = DeepSeekAIStrategy.__new__(DeepSeekAIStrategy)
    strategy.position_config = {
        "min_trade_amount": 0.001,
        "adjustment_threshold": 0.0,
    }
    strategy.enable_auto_sl_tp = True
    strategy.sl_use_support_resistance = True
    strategy.sl_buffer_pct = 0.001
    strategy.tp_pct_config = {"HIGH": 0.03, "MEDIUM": 0.02, "LOW": 0.01}
    strategy.min_entry_rr = 0.5
    strategy.default_target_r = 1.0
    strategy.max_target_r = 3.0
    strategy.latest_signal_data = {"confidence": "HIGH"}
    strategy.latest_technical_data = {"support": 950.0, "resistance": 1050.0}
    strategy.latest_price_data = {"price": 1000.0}
    strategy.indicator_manager = SimpleNamespace(recent_bars=[])
    strategy.cache = DummyCache([])
    strategy.bar_type = "BTC-BARS"
    strategy.order_factory = DummyOrderFactory()
    strategy.submit_order_list = Mock()
    strategy._submit_order = Mock()
    strategy.instrument = DummyInstrument()
    strategy.instrument_id = "BTCUSDT-PERP.BINANCE"
    strategy.base_asset = "BTC"
    strategy.dry_run = False
    strategy.enable_trailing_stop = False
    strategy.trailing_stop_state = {}
    strategy.log = DummyLogger()
    return strategy


def test_submit_bracket_order_uses_latest_price_data() -> None:
    strategy = _make_strategy_stub()

    strategy._submit_bracket_order(OrderSide.BUY, 0.01)

    assert strategy.order_factory.kwargs is not None, "Bracket call should occur"
    tp_price = strategy.order_factory.kwargs["tp_price"]
    sl_trigger = strategy.order_factory.kwargs["sl_trigger_price"]

    assert strategy.order_factory.kwargs["entry_order_type"] == OrderType.LIMIT
    assert strategy.order_factory.kwargs["entry_price"] == Decimal("1000.0")
    assert strategy.order_factory.kwargs["entry_post_only"] is True
    assert tp_price == Decimal("1050.0")
    assert sl_trigger == Decimal("949.05")
    strategy.submit_order_list.assert_called_once()
    strategy._submit_order.assert_not_called()


def test_submit_bracket_order_falls_back_when_price_missing() -> None:
    strategy = _make_strategy_stub()
    strategy.latest_price_data = {}
    strategy.indicator_manager.recent_bars = []
    strategy.cache = DummyCache([])

    result = strategy._submit_bracket_order(OrderSide.SELL, 0.02)

    assert result["status"] == "skipped"
    assert result["note"] == "entry_price_missing_entry_blocked"
    strategy._submit_order.assert_not_called()
    assert strategy.order_factory.kwargs is None


def test_submit_bracket_order_uses_llm_levels_first() -> None:
    strategy = _make_strategy_stub()
    strategy.latest_signal_data.update({"stop_loss": 990.0, "take_profit": 1020.0})

    result = strategy._submit_bracket_order(OrderSide.BUY, 0.01)

    assert result["bracket_plan"]["levels_source"] == "llm"
    assert strategy.order_factory.kwargs["sl_trigger_price"] == Decimal("990.0")
    assert strategy.order_factory.kwargs["tp_price"] == Decimal("1020.0")


def test_submit_bracket_order_falls_back_to_symmetric_one_pct() -> None:
    strategy = _make_strategy_stub()
    strategy.latest_technical_data = {"support": 970.0, "resistance": 1010.0}

    result = strategy._submit_bracket_order(OrderSide.BUY, 0.01)

    assert result["status"] == "submitted"
    assert result["bracket_plan"]["levels_source"] == "fallback_1pct"
    assert strategy.order_factory.kwargs["sl_trigger_price"] == Decimal("990.0")
    assert strategy.order_factory.kwargs["tp_price"] == Decimal("1010.0")
    strategy.submit_order_list.assert_called_once()
    strategy._submit_order.assert_not_called()


def test_submit_bracket_order_failure_never_opens_unprotected_market_order() -> None:
    strategy = _make_strategy_stub()
    strategy.order_factory.bracket = Mock(side_effect=RuntimeError("venue error"))

    result = strategy._submit_bracket_order(OrderSide.BUY, 0.01)

    assert result["status"] == "skipped"
    assert result["note"] == "protected_bracket_submission_failed:RuntimeError"
    strategy._submit_order.assert_not_called()


def _configure_execution_stub(strategy: DeepSeekAIStrategy) -> None:
    strategy.is_trading_paused = False
    strategy.min_confidence = "MEDIUM"
    strategy._log_info_safe = Mock()
    strategy._log_warning_safe = Mock()


def test_exit_now_submits_one_reduce_only_close_without_reversal() -> None:
    strategy = _make_strategy_stub()
    _configure_execution_stub(strategy)

    result = strategy._execute_trade(
        signal_data={"signal": "BUY", "position_action": "EXIT_NOW", "confidence": "LOW"},
        price_data={"price": 1000.0},
        technical_data={},
        current_position={"side": "short", "quantity": 2.5},
    )

    assert result["action"] == "exit_now"
    strategy._submit_order.assert_called_once_with(
        side=OrderSide.BUY,
        quantity=2.5,
        reduce_only=True,
    )


def test_entry_action_while_exposed_is_noop() -> None:
    strategy = _make_strategy_stub()
    _configure_execution_stub(strategy)

    result = strategy._execute_trade(
        signal_data={"signal": "SELL", "position_action": "ENTER_SHORT", "confidence": "HIGH"},
        price_data={"price": 1000.0},
        technical_data={},
        current_position={"side": "long", "quantity": 2.5},
    )

    assert result["note"] == "entry_action_while_exposed:ENTER_SHORT"
    strategy._submit_order.assert_not_called()


def _baseline_market_state() -> Dict[str, Any]:
    return {
        "price": 1000.0,
        "atr": 10.0,
        "position_key": "flat:0.0",
        "open_orders_count": 0,
        "has_pending_intent": False,
        "app_regime": "trend_down",
        "main_pressure": "neutral",
        "main_regime_shift": "stable",
        "main_trade_flow_imbalance": 0.05,
        "main_normalized_ofi_score": 0.05,
        "position_invalidation_price": None,
        "support_12": 990.0,
        "resistance_12": 1010.0,
        "support_48": 970.0,
        "resistance_48": 1030.0,
        "support": 990.0,
        "resistance": 1010.0,
    }


def test_market_state_gate_ignores_pressure_label_drift_without_anchor_or_numeric_cross() -> None:
    strategy = _make_strategy_stub()
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "position_action": "HOLD_POSITION"}
    previous = _baseline_market_state()
    strategy._last_llm_market_state = previous
    changed = dict(previous, main_pressure="buying", app_regime="range")

    should_call, reason = strategy._should_call_llm(changed, {"price": 1000.0, "high": 1002.0, "low": 999.0})

    assert should_call is False
    assert reason == "no_material_market_change"


def test_market_state_gate_ignores_small_price_drift_inside_same_band() -> None:
    strategy = _make_strategy_stub()
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "position_action": "NO_ACTION"}
    previous = _baseline_market_state()
    strategy._last_llm_market_state = previous

    should_call, reason = strategy._should_call_llm(
        dict(previous, price=1006.0),
        {"price": 1006.0, "high": 1007.0, "low": 1005.0},
    )

    assert should_call is False
    assert reason == "no_material_market_change"


def test_market_state_gate_wakes_on_structure_cross_with_cross_buffer() -> None:
    strategy = _make_strategy_stub()
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "position_action": "NO_ACTION"}
    previous = _baseline_market_state()
    strategy._last_llm_market_state = previous

    should_call, reason = strategy._should_call_llm(
        dict(previous, price=1012.0),
        {"price": 1012.0, "high": 1013.0, "low": 1009.0},
    )

    assert should_call is True
    assert reason == "structure_cross"


def test_market_state_gate_wakes_on_trade_flow_numeric_cross_outside_neutral() -> None:
    strategy = _make_strategy_stub()
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "position_action": "NO_ACTION"}
    previous = _baseline_market_state()
    strategy._last_llm_market_state = previous

    should_call, reason = strategy._should_call_llm(
        dict(previous, main_trade_flow_imbalance=0.22),
        {"price": 1000.0, "high": 1001.0, "low": 999.0},
    )

    assert should_call is True
    assert reason == "micro_numeric_cross"


def test_market_state_gate_wakes_on_normalized_ofi_numeric_cross_outside_neutral() -> None:
    strategy = _make_strategy_stub()
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "position_action": "NO_ACTION"}
    previous = _baseline_market_state()
    strategy._last_llm_market_state = previous

    should_call, reason = strategy._should_call_llm(
        dict(previous, main_normalized_ofi_score=-0.25),
        {"price": 1000.0, "high": 1001.0, "low": 999.0},
    )

    assert should_call is True
    assert reason == "micro_numeric_cross"


def test_market_state_build_extracts_main_ofi_and_triggers_numeric_cross() -> None:
    strategy = _make_strategy_stub()
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "position_action": "NO_ACTION"}

    technical = {
        "overall_trend": "mixed",
        "adx": 14.0,
        "dmi_dx": 9.0,
        "atr": 10.0,
        "support_12": 990.0,
        "resistance_12": 1010.0,
        "support_48": 970.0,
        "resistance_48": 1030.0,
        "support": 990.0,
        "resistance": 1010.0,
    }
    base_micro = {
        "trade_flow_imbalance": 0.04,
        "tf_windows": {
            "ready": True,
            "W_fast_sec": 60,
            "W_main_sec": 300,
            "W_context_sec": 900,
            "main": {
                "trade_flow_imbalance": 0.05,
                "normalized_ofi_score": 0.10,
                "labels": {
                    "directional_pressure": "neutral",
                    "regime_shift": "stable",
                },
            },
        },
    }
    price = {"price": 1000.0, "high": 1001.0, "low": 999.0}
    prev_state = strategy._build_market_state(
        price_data=price,
        technical_data=technical,
        microstructure_data=base_micro,
        current_position=None,
        risk_context={"open_orders": []},
    )
    assert prev_state["main_normalized_ofi_score"] == pytest.approx(0.10, rel=1e-9)
    strategy._last_llm_market_state = dict(prev_state)

    crossed_micro = {
        **base_micro,
        "tf_windows": {
            **base_micro["tf_windows"],
            "main": {
                **base_micro["tf_windows"]["main"],
                "normalized_ofi_score": 0.26,
            },
        },
    }
    current_state = strategy._build_market_state(
        price_data=price,
        technical_data=technical,
        microstructure_data=crossed_micro,
        current_position=None,
        risk_context={"open_orders": []},
    )
    should_call, reason = strategy._should_call_llm(current_state, price)

    assert should_call is True
    assert reason == "micro_numeric_cross"


def test_in_position_giveback_does_not_wake_without_invalidation_threat() -> None:
    strategy = _make_strategy_stub()
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "position_action": "HOLD_POSITION"}
    previous = dict(
        _baseline_market_state(),
        position_key="short:1.0",
        position_invalidation_price=1020.0,
        resistance_12=1050.0,
        resistance_48=1080.0,
    )
    strategy._last_llm_market_state = previous

    should_call, reason = strategy._should_call_llm(
        dict(previous, price=1005.0, app_regime="range"),
        {"price": 1005.0, "high": 1006.0, "low": 1004.0},
    )

    assert should_call is False
    assert reason == "no_material_market_change"


def test_in_position_invalidation_proximity_wakes_llm() -> None:
    strategy = _make_strategy_stub()
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "position_action": "HOLD_POSITION"}
    previous = dict(
        _baseline_market_state(),
        position_key="short:1.0",
        position_invalidation_price=1020.0,
        resistance_12=1050.0,
        resistance_48=1080.0,
    )
    strategy._last_llm_market_state = previous

    should_call, reason = strategy._should_call_llm(
        dict(previous, price=1018.8),
        {"price": 1018.8, "high": 1019.0, "low": 1017.5},
    )

    assert should_call is True
    assert reason == "position_invalidation_threat"


def test_removed_legacy_wakeup_triggers_absent_from_strategy_source() -> None:
    source = (Path(__file__).resolve().parents[1] / "strategy" / "deepseek_strategy.py").read_text(
        encoding="utf-8"
    )
    assert "price_moved_half_atr_since_last_llm" not in source
    assert "main_vwap_cross" not in source


def test_cleanup_oco_orphans_cancels_reduce_only_orders_when_flat() -> None:
    strategy = _make_strategy_stub()
    reduce_only = SimpleNamespace(is_reduce_only=True, client_order_id="SL-order")
    non_reduce = SimpleNamespace(is_reduce_only=False, client_order_id="entry-order")
    strategy.cache = SimpleNamespace(
        positions_open=Mock(return_value=[]),
        orders_open=Mock(return_value=[reduce_only, non_reduce]),
    )
    strategy.cancel_order = Mock()

    strategy._cleanup_oco_orphans()

    strategy.cancel_order.assert_called_once_with(reduce_only)


if __name__ == "__main__":
    test_submit_bracket_order_uses_latest_price_data()
    test_submit_bracket_order_falls_back_when_price_missing()
    print("✅ bracket order tests passed")
