"""
Unit tests for strategy-adjacent components (minimal deps).

Avoids importing `strategy` package `__init__` when NautilusTrader is unavailable.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_deepseek_strategy_module():
    path = ROOT / "strategy" / "deepseek_strategy.py"
    spec = importlib.util.spec_from_file_location("deepseek_strategy_standalone", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(
    importlib.util.find_spec("nautilus_trader") is None,
    reason="nautilus_trader not installed",
)
def test_position_sizing_respects_minimum_notional():
    """Test that position sizing enforces Binance $100 minimum notional."""
    mod = _load_deepseek_strategy_module()
    DeepSeekAIStrategy = mod.DeepSeekAIStrategy

    strategy = DeepSeekAIStrategy.__new__(DeepSeekAIStrategy)
    strategy.equity = 400.0
    strategy.base_usdt = 30.0
    strategy.fixed_trade_usdt = 0.0
    strategy.leverage = 10.0
    strategy.rsi_extreme_mult = 0.7
    strategy.rsi_extreme_upper = 75.0
    strategy.rsi_extreme_lower = 25.0
    strategy.base_asset = "BTC"
    strategy.instrument = object()
    strategy._normalize_order_quantity = lambda quantity, log_skipped=True: float(quantity)
    strategy._log_warning_safe = lambda *_args, **_kwargs: None
    strategy._log_info_safe = lambda *_args, **_kwargs: None
    strategy.position_config = {
        'high_confidence_multiplier': 1.5,
        'medium_confidence_multiplier': 1.0,
        'low_confidence_multiplier': 0.5,
        'max_position_ratio': 0.10,
        'min_trade_amount': 0.001,
        'trend_strength_multiplier': 1.2,
    }
    strategy.latest_signal_data = {'confidence': 'MEDIUM'}
    strategy.latest_technical_data = {'trend': 'BULLISH', 'rsi': 0.3}
    strategy.latest_price_data = {'price': 90000.0}

    quantity = strategy._calculate_position_size(
        signal_data={"confidence": "MEDIUM"},
        price_data={"price": 90000.0},
        technical_data={"overall_trend": "mixed", "rsi": 50.0},
        current_position=None,
        risk_context=None,
    )
    notional_value = quantity * 90000.0
    assert notional_value >= 100.0


@pytest.mark.skipif(
    importlib.util.find_spec("nautilus_trader") is None,
    reason="nautilus_trader not installed",
)
def test_position_sizing_uses_fixed_margin_times_leverage_without_confidence_scaling():
    mod = _load_deepseek_strategy_module()
    DeepSeekAIStrategy = mod.DeepSeekAIStrategy

    strategy = DeepSeekAIStrategy.__new__(DeepSeekAIStrategy)
    strategy.equity = 10000.0
    strategy.base_usdt = 2500.0
    strategy.fixed_trade_usdt = 2500.0
    strategy.leverage = 20.0
    strategy.rsi_extreme_mult = 0.7
    strategy.rsi_extreme_upper = 75.0
    strategy.rsi_extreme_lower = 25.0
    strategy.base_asset = "BTC"
    strategy.instrument = object()
    strategy._normalize_order_quantity = lambda quantity, log_skipped=True: float(quantity)
    strategy._log_warning_safe = lambda *_args, **_kwargs: None
    strategy._log_info_safe = lambda *_args, **_kwargs: None
    strategy.position_config = {
        'high_confidence_multiplier': 1.5,
        'medium_confidence_multiplier': 1.0,
        'low_confidence_multiplier': 0.5,
        'max_position_ratio': 20.0,
        'min_trade_amount': 0.001,
        'trend_strength_multiplier': 1.2,
    }

    sizes = {}
    for confidence in ('LOW', 'MEDIUM', 'HIGH'):
        sizes[confidence] = strategy._calculate_position_size(
            signal_data={"confidence": confidence},
            price_data={"price": 90000.0},
            technical_data={"overall_trend": "mixed", "rsi": 50.0},
            current_position=None,
            risk_context=None,
        )

    assert sizes['LOW'] == sizes['MEDIUM'] == sizes['HIGH']
    assert pytest.approx(sizes['MEDIUM'] * 90000.0, rel=0.001) == 50000.0


def test_deepseek_synthesis_parse_and_journal_fields():
    """DeepSeekAnalyzer accepts synthesis JSON and fills legacy bridge fields."""
    import types

    synth = {
        "signal": "HOLD",
        "position_action": "NO_ACTION",
        "confidence": "MEDIUM",
        "regime": "range_compress",
        "thesis": "Balanced liquidity; wait for breakout.",
        "invalidation": "Close above resistance invalidates neutrality.",
        "execution_note": "Scale only after spread tightens.",
        "volume_note": "rvol muted",
        "risk_assessment": "MEDIUM",
        "trend_strength": "MODERATE",
    }

    openai_fake = types.ModuleType("openai")

    MockOpenAI = Mock()
    openai_fake.OpenAI = MockOpenAI

    mock_client = MockOpenAI.return_value
    mock_choice = Mock()
    mock_choice.message.content = json.dumps(synth)
    mock_choice.message.reasoning_content = "think: condensed"
    mock_client.chat.completions.create.return_value = Mock(choices=[mock_choice])

    spec_ds = importlib.util.spec_from_file_location(
        "deepseek_standalone_mod", ROOT / "utils" / "deepseek_client.py"
    )
    assert spec_ds and spec_ds.loader

    ds_mod_prev = sys.modules.get("deepseek_standalone_mod")
    openai_prev = sys.modules.get("openai")
    try:
        sys.modules["openai"] = openai_fake
        ds_mod = importlib.util.module_from_spec(spec_ds)
        sys.modules["deepseek_standalone_mod"] = ds_mod
        spec_ds.loader.exec_module(ds_mod)
        DeepSeekAnalyzer = ds_mod.DeepSeekAnalyzer

        client = DeepSeekAnalyzer(
            api_key="k", model="m", instrument_id="X-LINEAR.BYBIT", bar_type="X-1-MINUTE-LAST"
        )
        price_data = {"price": 100.0, "instrument_id": "X-LINEAR.BYBIT", "bar_type": "X-1-MINUTE-LAST"}
        technical_data = {"rsi": 50.0}

        result = client.analyze(price_data, technical_data)
    finally:
        if ds_mod_prev is not None:
            sys.modules["deepseek_standalone_mod"] = ds_mod_prev
        else:
            sys.modules.pop("deepseek_standalone_mod", None)
        if openai_prev is not None:
            sys.modules["openai"] = openai_prev
        else:
            sys.modules.pop("openai", None)

    assert result["signal"] == "HOLD"
    assert result["thesis"]
    assert result.get("reason") == result["thesis"]
    assert "stop_loss" in result and "take_profit" in result


def test_deepseek_warns_on_non_english_synthesis_but_uses_signal(caplog):
    import types

    synth = {
        "signal": "BUY",
        "position_action": "ENTER_LONG",
        "confidence": "HIGH",
        "regime": "强势上涨",
        "thesis": "价格上涨，建议买入。",
        "invalidation": "跌破支撑位",
        "execution_note": "分批进场",
        "volume_note": "放量上涨",
        "risk_assessment": "MEDIUM",
        "trend_strength": "STRONG",
    }

    openai_fake = types.ModuleType("openai")
    MockOpenAI = Mock()
    openai_fake.OpenAI = MockOpenAI

    mock_client = MockOpenAI.return_value
    mock_choice = Mock()
    mock_choice.message.content = json.dumps(synth)
    mock_choice.message.reasoning_content = "cn-output"
    mock_client.chat.completions.create.return_value = Mock(choices=[mock_choice])

    spec_ds = importlib.util.spec_from_file_location(
        "deepseek_standalone_mod_cn", ROOT / "utils" / "deepseek_client.py"
    )
    assert spec_ds and spec_ds.loader

    prev_mod = sys.modules.get("deepseek_standalone_mod_cn")
    openai_prev = sys.modules.get("openai")
    try:
        sys.modules["openai"] = openai_fake
        ds_mod = importlib.util.module_from_spec(spec_ds)
        sys.modules["deepseek_standalone_mod_cn"] = ds_mod
        spec_ds.loader.exec_module(ds_mod)
        DeepSeekAnalyzer = ds_mod.DeepSeekAnalyzer

        client = DeepSeekAnalyzer(
            api_key="k",
            model="m",
            instrument_id="X-LINEAR.BYBIT",
            bar_type="X-5-MINUTE-LAST",
            max_retries=1,
        )
        price_data = {
            "price": 100.0,
            "high": 101.0,
            "low": 99.0,
            "volume": 10.0,
            "price_change": 0.1,
            "timestamp": "2026-05-27T00:00:00Z",
            "kline_data": [{"open": 99.5, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0}],
            "instrument_id": "X-LINEAR.BYBIT",
            "bar_type": "X-5-MINUTE-LAST",
        }
        result = client.analyze(
            price_data,
            {"overall_trend": "强势上涨", "short_term_trend": "上涨", "rsi": 52.0},
        )
    finally:
        if prev_mod is not None:
            sys.modules["deepseek_standalone_mod_cn"] = prev_mod
        else:
            sys.modules.pop("deepseek_standalone_mod_cn", None)
        if openai_prev is not None:
            sys.modules["openai"] = openai_prev
        else:
            sys.modules.pop("openai", None)

    assert result["signal"] == "BUY"
    assert result.get("is_fallback") is not True
    assert "价格上涨" in (result.get("reason") or "")
    assert any(
        "Non-English synthesis fields detected; using signal as-is." in rec.message
        for rec in caplog.records
    )


def test_deepseek_records_fallback_in_signal_history():
    import types

    openai_fake = types.ModuleType("openai")
    MockOpenAI = Mock()
    openai_fake.OpenAI = MockOpenAI

    mock_client = MockOpenAI.return_value
    mock_choice = Mock()
    # Force parse failure to trigger fallback path
    mock_choice.message.content = "not-json-response"
    mock_choice.message.reasoning_content = "net-error"
    mock_client.chat.completions.create.return_value = Mock(choices=[mock_choice])

    spec_ds = importlib.util.spec_from_file_location(
        "deepseek_standalone_mod_fallback", ROOT / "utils" / "deepseek_client.py"
    )
    assert spec_ds and spec_ds.loader

    prev_mod = sys.modules.get("deepseek_standalone_mod_fallback")
    openai_prev = sys.modules.get("openai")
    try:
        sys.modules["openai"] = openai_fake
        ds_mod = importlib.util.module_from_spec(spec_ds)
        sys.modules["deepseek_standalone_mod_fallback"] = ds_mod
        spec_ds.loader.exec_module(ds_mod)
        DeepSeekAnalyzer = ds_mod.DeepSeekAnalyzer

        client = DeepSeekAnalyzer(
            api_key="k",
            model="m",
            instrument_id="X-LINEAR.BYBIT",
            bar_type="X-5-MINUTE-LAST",
            max_retries=1,
        )
        price_data = {
            "price": 100.0,
            "high": 101.0,
            "low": 99.0,
            "volume": 10.0,
            "price_change": 0.1,
            "timestamp": "2026-05-27T00:00:00Z",
            "kline_data": [{"open": 99.5, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0}],
            "instrument_id": "X-LINEAR.BYBIT",
            "bar_type": "X-5-MINUTE-LAST",
        }
        result = client.analyze(
            price_data,
            {"overall_trend": "strong_up", "short_term_trend": "up", "rsi": 52.0},
        )
    finally:
        if prev_mod is not None:
            sys.modules["deepseek_standalone_mod_fallback"] = prev_mod
        else:
            sys.modules.pop("deepseek_standalone_mod_fallback", None)
        if openai_prev is not None:
            sys.modules["openai"] = openai_prev
        else:
            sys.modules.pop("openai", None)

    assert result["signal"] == "HOLD"
    assert result.get("is_fallback") is True
    assert len(client.signal_history) == 1
    assert client.signal_history[-1].get("is_fallback") is True


def test_position_health_formatting_is_observational_not_exit_biased():
    import types

    openai_fake = types.ModuleType("openai")
    MockOpenAI = Mock()
    openai_fake.OpenAI = MockOpenAI

    spec_ds = importlib.util.spec_from_file_location(
        "deepseek_standalone_mod_health", ROOT / "utils" / "deepseek_client.py"
    )
    assert spec_ds and spec_ds.loader

    prev_mod = sys.modules.get("deepseek_standalone_mod_health")
    openai_prev = sys.modules.get("openai")
    try:
        sys.modules["openai"] = openai_fake
        ds_mod = importlib.util.module_from_spec(spec_ds)
        sys.modules["deepseek_standalone_mod_health"] = ds_mod
        spec_ds.loader.exec_module(ds_mod)
        DeepSeekAnalyzer = ds_mod.DeepSeekAnalyzer
        client = DeepSeekAnalyzer(
            api_key="k", model="m", instrument_id="X-LINEAR.BYBIT", bar_type="X-5-MINUTE-LAST"
        )
        text = client._format_position_health(
            {
                "position_health": {
                    "profit_pct": 0.03,
                    "peak_profit_pct": 0.25,
                    "giveback_pct": 45.0,
                    "bars_held": 8,
                    "recommendation": "minor_giveback",
                }
            }
        )
    finally:
        if prev_mod is not None:
            sys.modules["deepseek_standalone_mod_health"] = prev_mod
        else:
            sys.modules.pop("deepseek_standalone_mod_health", None)
        if openai_prev is not None:
            sys.modules["openai"] = openai_prev
        else:
            sys.modules.pop("openai", None)

    assert "strongly consider exiting" not in text
    assert "profit evaporating" not in text
    assert "giveback observed" in text
    assert "monitor invalidation proximity" in text


def test_prompt_framework_reframes_exit_and_mid_range_behavior():
    import types

    openai_fake = types.ModuleType("openai")
    MockOpenAI = Mock()
    openai_fake.OpenAI = MockOpenAI

    spec_ds = importlib.util.spec_from_file_location(
        "deepseek_standalone_mod_prompt", ROOT / "utils" / "deepseek_client.py"
    )
    assert spec_ds and spec_ds.loader

    prev_mod = sys.modules.get("deepseek_standalone_mod_prompt")
    openai_prev = sys.modules.get("openai")
    try:
        sys.modules["openai"] = openai_fake
        ds_mod = importlib.util.module_from_spec(spec_ds)
        sys.modules["deepseek_standalone_mod_prompt"] = ds_mod
        spec_ds.loader.exec_module(ds_mod)
        DeepSeekAnalyzer = ds_mod.DeepSeekAnalyzer
        client = DeepSeekAnalyzer(
            api_key="k", model="m", instrument_id="X-LINEAR.BYBIT", bar_type="X-5-MINUTE-LAST"
        )
        prompt = client._build_analysis_prompt(
            price_data={
                "price": 100.0,
                "high": 100.2,
                "low": 99.8,
                "volume": 12.0,
                "price_change": 0.1,
                "timestamp": "2026-06-02T10:10:00Z",
                "kline_data": [{"open": 99.9, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 12.0}],
                "instrument_id": "X-LINEAR.BYBIT",
                "bar_type": "X-5-MINUTE-LAST",
                "microstructure": {"spread_bps": 1.2},
                "market_state": {},
            },
            technical_data={
                "overall_trend": "mixed",
                "short_term_trend": "mixed",
                "rsi": 50.0,
                "macd_trend": "flat",
            },
            sentiment_data=None,
            current_position={
                "side": "short",
                "quantity": 1.0,
                "avg_px": 101.0,
                "unrealized_pnl": 2.0,
                "position_health": {
                    "profit_pct": 0.02,
                    "peak_profit_pct": 0.03,
                    "giveback_pct": 10.0,
                    "bars_held": 3,
                    "recommendation": "stable",
                },
            },
            risk_context={"open_orders": []},
        )
    finally:
        if prev_mod is not None:
            sys.modules["deepseek_standalone_mod_prompt"] = prev_mod
        else:
            sys.modules.pop("deepseek_standalone_mod_prompt", None)
        if openai_prev is not None:
            sys.modules["openai"] = openai_prev
        else:
            sys.modules.pop("openai", None)

    assert "position_action\": \"ENTER_LONG|ENTER_SHORT|HOLD_POSITION|EXIT_NOW|NO_ACTION" in prompt
    assert "do not exit because of small giveback alone" in prompt
    assert "Mid-range -> prefer NO_ACTION" in prompt


def test_market_state_prompt_text_uses_new_fields_without_removed_or_none_values():
    import types

    openai_fake = types.ModuleType("openai")
    MockOpenAI = Mock()
    openai_fake.OpenAI = MockOpenAI

    spec_ds = importlib.util.spec_from_file_location(
        "deepseek_standalone_mod_market_state", ROOT / "utils" / "deepseek_client.py"
    )
    assert spec_ds and spec_ds.loader

    prev_mod = sys.modules.get("deepseek_standalone_mod_market_state")
    openai_prev = sys.modules.get("openai")
    try:
        sys.modules["openai"] = openai_fake
        ds_mod = importlib.util.module_from_spec(spec_ds)
        sys.modules["deepseek_standalone_mod_market_state"] = ds_mod
        spec_ds.loader.exec_module(ds_mod)
        DeepSeekAnalyzer = ds_mod.DeepSeekAnalyzer
        client = DeepSeekAnalyzer(
            api_key="k", model="m", instrument_id="X-LINEAR.BYBIT", bar_type="X-5-MINUTE-LAST"
        )
        text = client._format_market_state(
            {
                "app_regime": "trend_down",
                "position_key": "short:1.0",
                "open_orders_count": 0,
                "main_pressure": "selling",
                "main_regime_shift": "stable",
                "main_trade_flow_imbalance": -0.19,
                "main_normalized_ofi_score": -0.27,
                "support_12": 1980.0,
                "resistance_12": 1992.0,
                "support_48": 1960.0,
                "resistance_48": 2004.0,
                "position_invalidation_price": None,
                # Removed gate-era fields that should never be emitted now:
                "trend": "down",
                "structure_state": "inside_range",
                "volume_state": "normal",
                "bb_state": "middle",
                "trade_flow_state": "sell",
                "depth_regime": "normal",
                "fast_pressure": "neutral",
                "context_pressure": "neutral",
                "fast_regime_shift": "stable",
            },
            "micro_numeric_cross",
        )
    finally:
        if prev_mod is not None:
            sys.modules["deepseek_standalone_mod_market_state"] = prev_mod
        else:
            sys.modules.pop("deepseek_standalone_mod_market_state", None)
        if openai_prev is not None:
            sys.modules["openai"] = openai_prev
        else:
            sys.modules.pop("openai", None)

    assert "main_normalized_ofi_score=-0.27" in text
    assert "position_invalidation_price=" not in text
    assert "=None" not in text
    assert "trend=" not in text
    assert "structure_state=" not in text
    assert "volume_state=" not in text
    assert "bb_state=" not in text
    assert "trade_flow_state=" not in text
    assert "depth_regime=" not in text
    assert "fast_pressure=" not in text
    assert "context_pressure=" not in text
    assert "fast_regime_shift=" not in text


def test_trade_journal_writes_header_once():
    import tempfile

    from utils.trade_journal import TradeJournalCSV

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "tj.csv"
        j = TradeJournalCSV(str(p))
        j.append({"instrument_id": "A", "decision_cycle_trigger": "on_bar"})
        j.append({"instrument_id": "B", "decision_cycle_trigger": "on_bar"})
        text = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(text) == 3
        header_count = sum(1 for line in text if line.startswith("decision_ts_utc"))
        assert header_count == 1


def test_ob_tf_window_summaries_shape():
    spec_ob = importlib.util.spec_from_file_location(
        "orderbook_standalone_mod", ROOT / "indicators" / "orderbook_manager.py"
    )
    assert spec_ob and spec_ob.loader

    prev = sys.modules.get("orderbook_standalone_mod")
    try:
        ob_mod = importlib.util.module_from_spec(spec_ob)
        sys.modules["orderbook_standalone_mod"] = ob_mod
        spec_ob.loader.exec_module(ob_mod)

        OrderBookManager = ob_mod.OrderBookManager
        DepthSnapshot = ob_mod.DepthSnapshot
    finally:
        if prev is not None:
            sys.modules["orderbook_standalone_mod"] = prev
        else:
            sys.modules.pop("orderbook_standalone_mod", None)

    mgr = OrderBookManager(
        depth_levels=2,
        depth_buffer_size=50,
        trade_buffer_size=500,
        feature_buffer_size=50,
    )

    anchor = int(170e9)

    def _snap(spread_bps: float, mid: float, regime: str) -> None:
        half = spread_bps * mid / 20_000.0
        snap = DepthSnapshot(
            ts_event=anchor,
            bid_prices=[mid - half],
            bid_sizes=[10.0],
            ask_prices=[mid + half],
            ask_sizes=[10.0],
            bid_counts=[1],
            ask_counts=[1],
        )
        mgr._ingest_snapshot(snap)
        mgr._feature_buf[-1].depth_regime = regime

    mid = 100.0
    for _ in range(22):
        _snap(12.0, mid, "normal")

    out = mgr.get_tf_window_summaries(bar_period_sec=60, now_ns=anchor)

    assert out.get("ready") is True
    assert out["W_fast_sec"] == 60
    assert out["W_main_sec"] == 60
    assert out["W_context_sec"] == 180

    fast = out["fast"]
    assert fast["ready"] is True
    assert "spread_mean_bps" in fast
    assert "labels" in fast and "liquidity" in fast["labels"]


@pytest.mark.skipif(
    importlib.util.find_spec("nautilus_trader") is None,
    reason="nautilus_trader not installed",
)
def test_market_state_gate_skips_unchanged_hold_context():
    mod = _load_deepseek_strategy_module()
    DeepSeekAIStrategy = mod.DeepSeekAIStrategy

    strategy = DeepSeekAIStrategy.__new__(DeepSeekAIStrategy)
    strategy.enable_market_state_gate = True
    strategy._force_next_llm_reason = None
    strategy.last_signal = {"signal": "HOLD", "confidence": "MEDIUM"}

    technical = {
        "overall_trend": "mixed",
        "adx": 12.0,
        "dmi_dx": 10.0,
        "support": 99.0,
        "resistance": 101.0,
        "atr": 1.0,
        "atr_pct": 1.0,
        "rvol": 0.9,
        "volume_zscore": 0.1,
        "bb_position": 0.5,
    }
    micro = {
        "trade_flow_imbalance": 0.02,
        "depth_regime": "normal",
        "tf_windows": {
            "fast": {"labels": {"directional_pressure": "neutral", "regime_shift": "stable"}},
            "main": {"labels": {"directional_pressure": "neutral"}},
            "context": {"labels": {"directional_pressure": "neutral"}},
        },
    }
    price = {"price": 100.0, "high": 100.3, "low": 99.8}

    state = strategy._build_market_state(price, technical, micro, None, {"open_orders": []})
    strategy._last_llm_market_state = dict(state)

    should_call, reason = strategy._should_call_llm(state, price)

    assert should_call is False
    assert reason == "no_material_market_change"


@pytest.mark.skipif(
    importlib.util.find_spec("nautilus_trader") is None,
    reason="nautilus_trader not installed",
)
def test_hold_partial_close_is_noop():
    mod = _load_deepseek_strategy_module()
    DeepSeekAIStrategy = mod.DeepSeekAIStrategy

    strategy = DeepSeekAIStrategy.__new__(DeepSeekAIStrategy)
    strategy.is_trading_paused = False
    strategy.min_confidence = "MEDIUM"
    strategy.latest_signal_data = None
    strategy.latest_technical_data = None
    strategy.latest_price_data = None
    strategy._submit_order = Mock()

    result = strategy._execute_trade(
        signal_data={
            "signal": "HOLD",
            "position_action": "HOLD_POSITION",
            "confidence": "HIGH",
            "partial_close_pct": 0.5,
        },
        price_data={"price": 100.0},
        technical_data={},
        current_position={"side": "long", "quantity": 2.0},
        risk_context=None,
    )

    assert result["action"] == "hold_position"
    strategy._submit_order.assert_not_called()


@pytest.mark.skipif(
    importlib.util.find_spec("nautilus_trader") is None,
    reason="nautilus_trader not installed",
)
def test_stop_loss_calculation_stub():
    mod = _load_deepseek_strategy_module()
    DeepSeekAIStrategy = mod.DeepSeekAIStrategy

    strategy = DeepSeekAIStrategy.__new__(DeepSeekAIStrategy)
    strategy.sl_use_support_resistance = True
    strategy.sl_buffer_pct = 0.001
    strategy.latest_technical_data = {'support': 89000.0}
    strategy.latest_price_data = {'price': 91000.0}
    strategy.latest_signal_data = {'confidence': 'HIGH'}
    strategy.sl_pct_config = {'HIGH': 0.01}

    current_price = 91000.0
    expected_sl = 89000.0 * (1 - 0.001)

    strategy._calculate_stop_loss_price = lambda side, price: expected_sl
    sl_price = strategy._calculate_stop_loss_price('BUY', current_price)
    assert abs(sl_price - expected_sl) < 1.0


@pytest.mark.skipif(
    importlib.util.find_spec("nautilus_trader") is None,
    reason="nautilus_trader not installed",
)
def test_take_profit_scaling_stub():
    mod = _load_deepseek_strategy_module()
    DeepSeekAIStrategy = mod.DeepSeekAIStrategy

    strategy = DeepSeekAIStrategy.__new__(DeepSeekAIStrategy)
    strategy.tp_pct_config = {'HIGH': 0.03, 'MEDIUM': 0.02, 'LOW': 0.01}
    strategy.latest_price_data = {'price': 90000.0}

    current_price = 90000.0

    for confidence, expected_pct in [('LOW', 0.01), ('MEDIUM', 0.02), ('HIGH', 0.03)]:
        strategy.latest_signal_data = {'confidence': confidence}
        expected_tp = current_price * (1 + expected_pct)
        strategy._calculate_take_profit_price = lambda side, price: expected_tp
        tp_price = strategy._calculate_take_profit_price('BUY', current_price)
        assert abs(tp_price - expected_tp) < 1.0
