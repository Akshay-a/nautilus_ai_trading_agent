"""
Live Trading Entrypoint for DeepSeek AI Strategy

Runs the DeepSeek AI strategy on Bybit linear perpetuals with live market data.
"""

import os
import asyncio
import yaml
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit import BybitLiveExecClientFactory
from nautilus_trader.adapters.bybit import BybitProductType
from nautilus_trader.adapters.bybit.config import BybitDataClientConfig, BybitExecClientConfig
try:
    # Newer Nautilus versions
    from nautilus_trader.adapters.bybit import BybitEnvironment
except Exception:  # pragma: no cover - compatibility across Nautilus versions
    BybitEnvironment = None
from nautilus_trader.config import InstrumentProviderConfig, LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId, InstrumentId
from nautilus_trader.trading.config import ImportableStrategyConfig

from strategy.deepseek_strategy import DeepSeekAIStrategy, DeepSeekAIStrategyConfig


# Load environment variables from local .env and override inherited shell vars.
# This avoids stale OS-level secrets causing key/secret mismatches at runtime.
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)


def load_yaml_config() -> dict:
    """
    Load strategy configuration from YAML file.
    
    Returns
    -------
    dict
        Configuration dictionary from YAML
    """
    config_path = Path(__file__).parent / "configs" / "strategy_config.yaml"
    if config_path.exists():
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    return {}


def get_env_float(key: str, default: str) -> float:
    """
    Safely get float environment variable, removing any inline comments.
    """
    value = os.getenv(key, default)
    # Remove inline comments (anything after #)
    if '#' in value:
        value = value.split('#')[0]
    # Strip whitespace
    value = value.strip()
    return float(value)


def get_env_str(key: str, default: str) -> str:
    """
    Safely get string environment variable, removing any inline comments.
    """
    value = os.getenv(key, default)
    # Remove inline comments (anything after #)
    if '#' in value:
        value = value.split('#')[0]
    # Strip whitespace
    return value.strip()


def get_env_int(key: str, default: str) -> int:
    """
    Safely get integer environment variable, removing any inline comments.
    """
    value = os.getenv(key, default)
    # Remove inline comments (anything after #)
    if '#' in value:
        value = value.split('#')[0]
    # Strip whitespace
    value = value.strip()
    return int(value)


def get_strategy_config() -> DeepSeekAIStrategyConfig:
    """
    Build strategy configuration from environment variables.

    Returns
    -------
    DeepSeekAIStrategyConfig
        Strategy configuration
    """
    # Get API keys
    deepseek_api_key = get_env_str('DEEPSEEK_API_KEY', '')
    if not deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY not found in environment variables")

    # Load YAML config
    yaml_config = load_yaml_config()
    strategy_yaml = yaml_config.get('strategy', {})
    
    # Get strategy parameters from env or use defaults
    equity = get_env_float('EQUITY', str(strategy_yaml.get('equity', '400')))
    leverage = get_env_float('LEVERAGE', str(strategy_yaml.get('leverage', '10')))
    base_position = get_env_float('BASE_POSITION_USDT', str(strategy_yaml.get('position_management', {}).get('base_usdt_amount', '30')))
    timeframe = get_env_str('TIMEFRAME', '1m')  # Fast loop default for demo iteration
    instrument_id = get_env_str(
        'INSTRUMENT_ID',
        str(strategy_yaml.get('instrument_id', 'BTCUSDT-LINEAR.BYBIT')),
    )
    
    # Debug output
    print(f"[CONFIG] Equity: {equity}")
    print(f"[CONFIG] Base Position: {base_position}")
    print(f"[CONFIG] Timeframe: {timeframe}")

    # Map timeframe to bar aggregation
    timeframe_map = {
        '1m': 'MINUTE',
        '5m': 'MINUTE',
        '15m': 'MINUTE',
        '1h': 'HOUR',
        '4h': 'HOUR',
        '1d': 'DAY',
    }

    # Parse timeframe
    print(f"DEBUG: timeframe = '{timeframe}'")  # Debug
    if timeframe == '1m':
        bar_spec = '1-MINUTE-LAST'
    elif timeframe == '5m':
        bar_spec = '5-MINUTE-LAST'
    elif timeframe == '15m':
        bar_spec = '15-MINUTE-LAST'
    elif timeframe == '1h':
        bar_spec = '1-HOUR-LAST'
    else:
        bar_spec = '15-MINUTE-LAST'  # Default
    
    print(f"DEBUG: bar_spec = '{bar_spec}'")  # Debug
    final_bar_type = f"{instrument_id}-{bar_spec}-EXTERNAL"
    print(f"DEBUG: final_bar_type = '{final_bar_type}'")  # Debug

    return DeepSeekAIStrategyConfig(
        instrument_id=instrument_id,
        bar_type=final_bar_type,

        # Capital
        equity=equity,
        leverage=leverage,

        # Position sizing
        base_usdt_amount=base_position,
        high_confidence_multiplier=get_env_float('HIGH_CONFIDENCE_MULTIPLIER', '1.5'),
        medium_confidence_multiplier=get_env_float('MEDIUM_CONFIDENCE_MULTIPLIER', '1.0'),
        low_confidence_multiplier=get_env_float('LOW_CONFIDENCE_MULTIPLIER', '0.5'),
        max_position_ratio=get_env_float('MAX_POSITION_RATIO', '0.10'),
        trend_strength_multiplier=get_env_float('TREND_STRENGTH_MULTIPLIER', '1.2'),
        min_trade_amount=0.001,

        # Technical indicators - Production mode (standard periods)
        # Use reduced periods only for 1m bars (for testing)
        sma_periods=[3, 7, 15] if timeframe == '1m' else [5, 20, 50],
        rsi_period=7 if timeframe == '1m' else 14,
        macd_fast=5 if timeframe == '1m' else 12,
        macd_slow=10 if timeframe == '1m' else 26,
        bb_period=10 if timeframe == '1m' else 20,
        bb_std=2.0,

        # AI
        deepseek_api_key=deepseek_api_key,
        deepseek_model="deepseek-reasoner",
        deepseek_temperature=0.1,
        deepseek_max_retries=2,
        llm_kline_context_bars=get_env_int(
            'LLM_KLINE_CONTEXT_BARS',
            str(strategy_yaml.get('deepseek', {}).get('kline_context_bars', 10)),
        ),

        # Sentiment
        sentiment_enabled=True,
        sentiment_lookback_hours=4,
        # Set sentiment timeframe based on bar timeframe (default to 15m)
        sentiment_timeframe="1m" if timeframe == "1m" else ("5m" if timeframe == "5m" else "15m"),

        # Risk
        min_confidence_to_trade=get_env_str('MIN_CONFIDENCE_TO_TRADE', 'MEDIUM'),
        allow_reversals=True,
        require_high_confidence_for_reversal=False,
        rsi_extreme_threshold_upper=75.0,
        rsi_extreme_threshold_lower=25.0,
        rsi_extreme_multiplier=0.7,

        # Execution
        position_adjustment_threshold=0.001,

        # Order Book / Microstructure
        enable_orderbook=strategy_yaml.get('orderbook', {}).get('enabled', True),
        orderbook_depth_levels=strategy_yaml.get('orderbook', {}).get('depth_levels', 10),
        orderbook_depth_buffer_size=strategy_yaml.get('orderbook', {}).get('depth_buffer_size', 300),
        orderbook_trade_buffer_size=strategy_yaml.get('orderbook', {}).get('trade_buffer_size', 5000),
        orderbook_feature_buffer_size=strategy_yaml.get('orderbook', {}).get('feature_buffer_size', 500),
        orderbook_ema_alpha=strategy_yaml.get('orderbook', {}).get('ema_alpha', 0.05),
        orderbook_trade_window_sec=strategy_yaml.get('orderbook', {}).get('trade_window_sec', 300),
        orderbook_log_interval=strategy_yaml.get('orderbook', {}).get('log_interval', 60),
        orderbook_log_min_seconds=strategy_yaml.get('orderbook', {}).get('log_min_seconds', 60),

        # Timing - Load from YAML config (default: 900 seconds = 15 minutes)
        timer_interval_sec=get_env_int('TIMER_INTERVAL_SEC', str(strategy_yaml.get('timer_interval_sec', 900))),
        warmup_bars=get_env_int('WARMUP_BARS', str(strategy_yaml.get('warmup_bars', 200))),
        
        # Telegram Notifications
        enable_telegram=strategy_yaml.get('telegram', {}).get('enabled', False),
        telegram_bot_token=get_env_str('TELEGRAM_BOT_TOKEN', ''),
        telegram_chat_id=get_env_str('TELEGRAM_CHAT_ID', ''),
        telegram_notify_signals=strategy_yaml.get('telegram', {}).get('notify_signals', True),
        telegram_notify_fills=strategy_yaml.get('telegram', {}).get('notify_fills', True),
        telegram_notify_positions=strategy_yaml.get('telegram', {}).get('notify_positions', True),
        telegram_notify_errors=strategy_yaml.get('telegram', {}).get('notify_errors', True),
    )


def get_bybit_config() -> tuple:
    """
    Build Bybit data and execution client configs.

    Returns
    -------
    tuple
        (data_config, exec_config)
    """
    # Get API credentials
    api_key = os.getenv('BYBIT_API_KEY')
    api_secret = os.getenv('BYBIT_API_SECRET')
    use_testnet = get_env_str('BYBIT_TESTNET', 'false').lower() == 'true'
    use_demo = get_env_str('BYBIT_DEMO', 'false').lower() == 'true'

    if not api_key or not api_secret:
        raise ValueError("BYBIT_API_KEY and BYBIT_API_SECRET required in .env")

    common_kwargs = {
        "api_key": api_key,
        "api_secret": api_secret,
        "product_types": [BybitProductType.LINEAR],
        "instrument_provider": InstrumentProviderConfig(load_all=True),
    }

    # Support both older (demo/testnet flags) and newer (environment enum) Nautilus APIs.
    if BybitEnvironment is not None:
        if use_demo:
            environment = BybitEnvironment.DEMO
        elif use_testnet:
            environment = BybitEnvironment.TESTNET
        else:
            environment = BybitEnvironment.MAINNET
        common_kwargs["environment"] = environment
    else:
        common_kwargs["demo"] = use_demo
        common_kwargs["testnet"] = use_testnet

    # Data client config
    data_config = BybitDataClientConfig(**common_kwargs)

    # Execution client config
    exec_config = BybitExecClientConfig(**common_kwargs)

    return data_config, exec_config


def setup_trading_node() -> TradingNodeConfig:
    """
    Configure the NautilusTrader trading node.

    Returns
    -------
    TradingNodeConfig
        Trading node configuration
    """
    # Get configurations
    strategy_config = get_strategy_config()
    data_config, exec_config = get_bybit_config()

    # Wrap strategy config in ImportableStrategyConfig
    importable_config = ImportableStrategyConfig(
        strategy_path="strategy.deepseek_strategy:DeepSeekAIStrategy",
        config_path="strategy.deepseek_strategy:DeepSeekAIStrategyConfig",
        config=strategy_config.dict(),
    )

    # Logging configuration
    log_level = get_env_str('LOG_LEVEL', 'INFO')

    logging_config = LoggingConfig(
        log_level=log_level,
        log_level_file=log_level,
        log_directory="logs",
        log_file_name="deepseek_trader",
        log_file_format="json",
        log_colors=True,
        bypass_logging=False,
        log_file_max_size=104_857_600,  # 100MB -- prevents mid-session rotation from high-frequency OB logs
        log_file_max_backup_count=2,    # Keep 2 backup files (total: 300MB max)
    )

    # Trading node config
    config = TradingNodeConfig(
        trader_id=TraderId("DeepSeekTrader-001"),
        logging=logging_config,
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,  # Enable position reconciliation
            inflight_check_interval_ms=5000,  # Check in-flight orders every 5s
        ),
        # Data clients
        data_clients={
            BYBIT: data_config,
        },
        # Execution clients
        exec_clients={
            BYBIT: exec_config,
        },
        # Strategy configs
        strategies=[importable_config],
    )

    return config


def main():
    """
    Main entry point for live trading.
    """
    print("=" * 70)
    print("DeepSeek AI Trading Strategy - Live Trading Mode")
    print("=" * 70)
    print(f"Exchange: Bybit (Linear Perpetuals)")
    print(f"Instrument: {os.getenv('INSTRUMENT_ID', 'BTCUSDT-LINEAR.BYBIT')}")
    print(f"Strategy: AI-powered with DeepSeek")
    print("=" * 70)

    # Safety check
    test_mode = os.getenv('TEST_MODE', 'false').strip().lower() == 'true'
    auto_confirm = os.getenv('AUTO_CONFIRM', 'false').strip().lower() == 'true'
    
    if test_mode:
        print("⚠️  TEST_MODE=true - This is a simulation, no real orders will be placed")
    else:
        print("🚨 LIVE TRADING MODE - Real orders will be placed!")
        if auto_confirm:
            print("⚠️  AUTO_CONFIRM=true - Skipping user confirmation")
        else:
            response = input("Are you sure you want to continue? (yes/no): ")
            if response.lower() != 'yes':
                print("Exiting...")
                return

    # Build configuration
    print("\n📋 Building configuration...")
    config = setup_trading_node()

    print(f"✅ Trader ID: {config.trader_id}")
    print(f"✅ Strategy configured with DeepSeek AI")
    print(f"✅ Bybit adapter configured")

    # Create and start trading node
    print("\n🚀 Starting trading node...")
    node = TradingNode(config=config)
    
    # Register Bybit factories
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.add_exec_client_factory(BYBIT, BybitLiveExecClientFactory)
    print("✅ Bybit factories registered")

    try:
        # Build the node (connects to exchange, loads instruments)
        node.build()
        print("✅ Trading node built successfully")

        # Run the node (this starts strategies and begins event processing)
        print("✅ Starting trading node...")
        print("\n🟢 Strategy is now running. Press Ctrl+C to stop.\n")
        
        # Run the node - this is a blocking call that processes all events
        node.run()

    except KeyboardInterrupt:
        print("\n\n⚠️  Keyboard interrupt received...")

    except Exception as e:
        print(f"\n❌ Error occurred: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Dispose the node to clean up resources
        print("\n🛑 Cleaning up resources...")
        node.dispose()
        print("✅ Resources cleaned up")

        print("\n" + "=" * 70)
        print("Trading session ended")
        print("=" * 70)


if __name__ == "__main__":
    # Check Python path
    import sys
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Run the trading bot
    try:
        main()
    except KeyboardInterrupt:
        print("\n✅ Program terminated by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
