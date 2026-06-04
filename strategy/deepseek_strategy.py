"""
DeepSeek AI Strategy for NautilusTrader

AI-powered cryptocurrency trading strategy using DeepSeek for decision making,
technical indicators for market analysis, and sentiment data for validation.
"""

import os
import re
import asyncio
import threading
import time
from decimal import Decimal
from typing import Dict, Any, Optional, List, Tuple

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce, PositionSide, PriceType, TriggerType, OrderType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.position import Position
from nautilus_trader.model.orders import MarketOrder
from datetime import timedelta, datetime

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.technical_manager import TechnicalIndicatorManager
from indicators.orderbook_manager import OrderBookManager
from utils.deepseek_client import DeepSeekAnalyzer
from utils.sentiment_client import SentimentDataFetcher
from utils.bybit_account_context import BybitAccountContextFetcher
from utils.trade_journal import TradeJournalCSV
# OCOManager no longer needed - using NautilusTrader's built-in bracket orders


class DeepSeekAIStrategyConfig(StrategyConfig, frozen=True):
    """Configuration for DeepSeek AI Strategy."""

    # Instrument
    instrument_id: str
    bar_type: str

    # Capital
    equity: float = 10000.0
    leverage: float = 20.0

    # Position sizing
    base_usdt_amount: float = 2500.0
    fixed_trade_usdt: float = 2500.0  # If > 0, use fixed margin capital per protected entry
    high_confidence_multiplier: float = 1.5
    medium_confidence_multiplier: float = 1.0
    low_confidence_multiplier: float = 0.5
    max_position_ratio: float = 20.0
    trend_strength_multiplier: float = 1.2
    min_trade_amount: float = 0.001

    # Technical indicators
    sma_periods: Tuple[int, ...] = (5, 20, 50)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    bb_period: int = 20
    bb_std: float = 2.0
    support_resistance_lookback: int = 48

    # AI configuration
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-reasoner"
    deepseek_temperature: float = 0.1
    deepseek_max_retries: int = 2
    llm_kline_context_bars: int = 10

    # Sentiment
    sentiment_enabled: bool = True
    sentiment_lookback_hours: int = 4
    sentiment_timeframe: str = "15m"  # Sentiment data timeframe (should match or be compatible with bar_type)

    # Risk management
    min_confidence_to_trade: str = "MEDIUM"
    rsi_extreme_threshold_upper: float = 75.0
    rsi_extreme_threshold_lower: float = 25.0
    rsi_extreme_multiplier: float = 0.7
    
    # Stop Loss & Take Profit
    enable_auto_sl_tp: bool = True
    sl_use_support_resistance: bool = True
    sl_buffer_pct: float = 0.001
    tp_high_confidence_pct: float = 0.03
    tp_medium_confidence_pct: float = 0.02
    tp_low_confidence_pct: float = 0.01
    min_entry_rr: float = 0.5
    default_target_r: float = 1.0
    max_target_r: float = 3.0
    
    # OCO (One-Cancels-the-Other)
    enable_oco: bool = True
    oco_redis_host: str = "localhost"
    oco_redis_port: int = 6379
    oco_redis_db: int = 0
    oco_redis_password: Optional[str] = None
    oco_group_ttl_hours: int = 24
    
    # Trailing Stop Loss
    enable_trailing_stop: bool = True
    trailing_activation_pct: float = 0.01
    trailing_distance_pct: float = 0.005
    trailing_update_threshold_pct: float = 0.002
    
    # Partial Take Profit
    enable_partial_tp: bool = False
    partial_tp_levels: Tuple[Dict[str, float], ...] = (
        {"profit_pct": 0.004, "position_pct": 0.5},
        {"profit_pct": 0.008, "position_pct": 0.5},
    )

    # LLM decision cadence
    enable_market_state_gate: bool = True
    
    # Telegram Notifications
    enable_telegram: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_notify_signals: bool = True
    telegram_notify_fills: bool = True
    telegram_notify_positions: bool = True
    telegram_notify_errors: bool = True

    # Execution
    position_adjustment_threshold: float = 0.001

    # Order Book / Microstructure
    enable_orderbook: bool = True
    orderbook_depth_levels: int = 10
    orderbook_depth_buffer_size: int = 300
    orderbook_trade_buffer_size: int = 5000
    orderbook_feature_buffer_size: int = 500
    orderbook_ema_alpha: float = 0.05
    orderbook_trade_window_sec: int = 300  # 5 minutes
    orderbook_log_interval: int = 60  # log summary every N depth updates
    orderbook_log_min_seconds: int = 60  # additional wall-clock throttle for OB summary logs

    # Timing
    timer_interval_sec: int = 900
    warmup_bars: int = 200


class DeepSeekAIStrategy(Strategy):
    """
    DeepSeek AI-powered trading strategy.

    Combines AI decision making, technical analysis, and sentiment data
    for intelligent cryptocurrency trading on perpetual futures.
    """

    def __init__(self, config: DeepSeekAIStrategyConfig):
        """
        Initialize DeepSeek AI strategy.

        Parameters
        ----------
        config : DeepSeekAIStrategyConfig
            Strategy configuration
        """
        super().__init__(config)

        # Configuration
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Position sizing config
        self.equity = config.equity
        self.leverage = config.leverage
        self.base_usdt = config.base_usdt_amount
        self.fixed_trade_usdt = config.fixed_trade_usdt
        self.position_config = {
            'high_confidence_multiplier': config.high_confidence_multiplier,
            'medium_confidence_multiplier': config.medium_confidence_multiplier,
            'low_confidence_multiplier': config.low_confidence_multiplier,
            'max_position_ratio': config.max_position_ratio,
            'trend_strength_multiplier': config.trend_strength_multiplier,
            'min_trade_amount': config.min_trade_amount,
            'adjustment_threshold': config.position_adjustment_threshold,
        }

        # Risk management
        self.min_confidence = config.min_confidence_to_trade
        self.rsi_extreme_upper = config.rsi_extreme_threshold_upper
        self.rsi_extreme_lower = config.rsi_extreme_threshold_lower
        self.rsi_extreme_mult = config.rsi_extreme_multiplier
        
        # Stop Loss & Take Profit
        self.enable_auto_sl_tp = config.enable_auto_sl_tp
        self.sl_use_support_resistance = config.sl_use_support_resistance
        self.sl_buffer_pct = config.sl_buffer_pct
        self.tp_pct_config = {
            'HIGH': config.tp_high_confidence_pct,
            'MEDIUM': config.tp_medium_confidence_pct,
            'LOW': config.tp_low_confidence_pct,
        }
        self.min_entry_rr = config.min_entry_rr
        self.default_target_r = config.default_target_r
        self.max_target_r = config.max_target_r
        
        # Store latest signal, technical, and price data for SL/TP calculation
        self.latest_signal_data: Optional[Dict[str, Any]] = None
        self.latest_technical_data: Optional[Dict[str, Any]] = None
        self.latest_price_data: Optional[Dict[str, Any]] = None

        # OCO (One-Cancels-the-Other) - Now handled by NautilusTrader's bracket orders
        # No need for manual OCO manager anymore
        self.enable_oco = config.enable_oco  # Keep for config compatibility
        self.oco_manager = None  # Deprecated: bracket orders handle OCO automatically
        
        # Trailing Stop Loss
        self.enable_trailing_stop = config.enable_trailing_stop
        self.trailing_activation_pct = config.trailing_activation_pct
        self.trailing_distance_pct = config.trailing_distance_pct
        self.trailing_update_threshold_pct = config.trailing_update_threshold_pct
        
        # Track trailing stop state for each position
        self.trailing_stop_state: Dict[str, Dict[str, Any]] = {}

        # Position health tracking for scalp-aware LLM prompting
        self._position_health: Dict[str, Any] = {
            "peak_unrealized_pnl": 0.0,
            "peak_profit_pct": 0.0,
            "entry_bar_count": 0,
            "entry_price": 0.0,
            "entry_side": None,
            "entry_key": None,
        }

        # Technical indicators manager
        sma_periods = config.sma_periods if config.sma_periods else [5, 20, 50]
        self.indicator_manager = TechnicalIndicatorManager(
            sma_periods=sma_periods,
            ema_periods=[config.macd_fast, config.macd_slow],
            rsi_period=config.rsi_period,
            macd_fast=config.macd_fast,
            macd_slow=config.macd_slow,
            bb_period=config.bb_period,
            bb_std=config.bb_std,
            support_resistance_lookback=config.support_resistance_lookback,
        )

        # Order Book / Microstructure manager
        self.enable_orderbook = config.enable_orderbook
        self.orderbook_manager: Optional[OrderBookManager] = None
        if self.enable_orderbook:
            self.orderbook_manager = OrderBookManager(
                depth_buffer_size=config.orderbook_depth_buffer_size,
                trade_buffer_size=config.orderbook_trade_buffer_size,
                feature_buffer_size=config.orderbook_feature_buffer_size,
                depth_levels=config.orderbook_depth_levels,
                ema_alpha=config.orderbook_ema_alpha,
                trade_window_ns=config.orderbook_trade_window_sec * 1_000_000_000,
            )
            self._ob_log_interval = max(1, int(config.orderbook_log_interval))
            self._ob_log_min_seconds = max(0, int(config.orderbook_log_min_seconds))
            self._last_ob_log_ts_ns: Optional[int] = None

        # DeepSeek AI analyzer
        api_key = config.deepseek_api_key or os.getenv('DEEPSEEK_API_KEY')
        if not api_key:
            raise ValueError("DeepSeek API key not provided")

        self.deepseek = DeepSeekAnalyzer(
            api_key=api_key,
            model=config.deepseek_model,
            temperature=config.deepseek_temperature,
            max_retries=config.deepseek_max_retries,
            instrument_id=str(self.instrument_id),
            bar_type=str(self.bar_type),
            nautilus_logger=self.log,
        )
        
        # Telegram Bot
        self.telegram_bot = None
        self.enable_telegram = config.enable_telegram
        if self.enable_telegram:
            try:
                from utils.telegram_bot import TelegramBot
                
                bot_token = config.telegram_bot_token or os.getenv('TELEGRAM_BOT_TOKEN', '')
                chat_id = config.telegram_chat_id or os.getenv('TELEGRAM_CHAT_ID', '')
                
                if bot_token and chat_id:
                    self.telegram_bot = TelegramBot(
                        token=bot_token,
                        chat_id=chat_id,
                        logger=self.log,
                        enabled=True
                    )
                    # Store notification preferences
                    self.telegram_notify_signals = config.telegram_notify_signals
                    self.telegram_notify_fills = config.telegram_notify_fills
                    self.telegram_notify_positions = config.telegram_notify_positions
                    self.telegram_notify_errors = config.telegram_notify_errors
                    
                    self.log.info("✅ Telegram Bot initialized successfully")
                    
                    # Initialize command handler for remote control
                    try:
                        from utils.telegram_command_handler import TelegramCommandHandler
                        import threading
                        
                        # Create callback function for commands
                        def command_callback(command: str, args: Dict[str, Any]) -> Dict[str, Any]:
                            """Callback function for Telegram commands."""
                            return self.handle_telegram_command(command, args)
                        
                        # Initialize command handler
                        allowed_chat_ids = [chat_id]  # Only allow the configured chat ID
                        self.telegram_command_handler = TelegramCommandHandler(
                            token=bot_token,
                            allowed_chat_ids=allowed_chat_ids,
                            strategy_callback=command_callback,
                            logger=self.log
                        )
                        
                        # Start command handler in background thread
                        def run_command_handler():
                            """Run command handler in background thread."""
                            try:
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                # Start polling (this will run indefinitely via idle())
                                loop.run_until_complete(self.telegram_command_handler.start_polling())
                            except Exception as e:
                                self.log.error(f"❌ Command handler thread error: {e}")
                        
                        # Start background thread for command listening
                        command_thread = threading.Thread(
                            target=run_command_handler,
                            daemon=True,
                            name="TelegramCommandHandler"
                        )
                        command_thread.start()
                        self.log.info("✅ Telegram Command Handler started in background thread")
                        
                    except ImportError:
                        self.log.warning("⚠️ Telegram command handler not available")
                        self.telegram_command_handler = None
                    except Exception as e:
                        self.log.error(f"❌ Failed to initialize command handler: {e}")
                        self.telegram_command_handler = None
                    
                else:
                    self.log.warning("⚠️ Telegram enabled but token/chat_id not configured")
                    self.enable_telegram = False
            except ImportError:
                self.log.warning("⚠️ Telegram bot not available (python-telegram-bot not installed)")
                self.enable_telegram = False
            except Exception as e:
                self.log.error(f"❌ Failed to initialize Telegram Bot: {e}")
                self.enable_telegram = False
        
        # Strategy control state for remote commands
        self.is_trading_paused = False
        self.strategy_start_time = None

        # Sentiment data fetcher
        self.sentiment_enabled = config.sentiment_enabled
        if self.sentiment_enabled:
            # Use sentiment_timeframe from config, or derive from bar_type if not specified
            sentiment_tf = config.sentiment_timeframe
            if not sentiment_tf or sentiment_tf == "":
                # Extract timeframe from bar_type (e.g., "1-MINUTE" -> "1m")
                bar_str = str(self.bar_type)
                if "1-MINUTE" in bar_str:
                    sentiment_tf = "1m"
                elif "5-MINUTE" in bar_str:
                    sentiment_tf = "5m"
                elif "15-MINUTE" in bar_str:
                    sentiment_tf = "15m"
                elif "1-HOUR" in bar_str:
                    sentiment_tf = "1h"
                else:
                    sentiment_tf = "15m"  # Default fallback
            
            self.sentiment_fetcher = SentimentDataFetcher(
                lookback_hours=config.sentiment_lookback_hours,
                timeframe=sentiment_tf,
            )
            self.log.info(f"Sentiment fetcher initialized with timeframe: {sentiment_tf}")
        else:
            self.sentiment_fetcher = None

        # State tracking
        self.instrument: Optional[Instrument] = None
        self.last_signal: Optional[Dict[str, Any]] = None
        self._last_llm_decision_bar_count: Optional[int] = None
        self.bars_received = 0
        self.dry_run = os.getenv("DRY_RUN", "false").strip().lower() == "true"
        self.llm_kline_context_bars = max(10, int(config.llm_kline_context_bars))
        self.base_asset = self._derive_base_asset()
        self.exchange_context_fetcher = BybitAccountContextFetcher.from_env(
            instrument_id=str(self.instrument_id),
            logger=self.log,
        )
        self.exchange_risk_context: Optional[Dict[str, Any]] = None
        self.exchange_context_interval_sec = int(os.getenv("EXCHANGE_CONTEXT_INTERVAL_SEC", "60"))
        self._last_exchange_context_refresh_monotonic = 0.0
        self.trade_journal_enabled = os.getenv("TRADE_JOURNAL_ENABLED", "true").strip().lower() == "true"
        self.trade_journal_path = os.getenv("TRADE_JOURNAL_CSV_PATH", "logs/trade_journal.csv")
        self.trade_journal: Optional[TradeJournalCSV] = None
        if self.trade_journal_enabled:
            try:
                self.trade_journal = TradeJournalCSV(self.trade_journal_path)
                self.trade_journal_path = self.trade_journal.path
                self.log.info(f"Decision trade journal enabled: {self.trade_journal_path}")
            except Exception as e:
                self.trade_journal_enabled = False
                self.log.warning(f"Failed to initialize trade journal: {e}")

        self.enable_market_state_gate = config.enable_market_state_gate
        self._last_llm_market_state: Optional[Dict[str, Any]] = None
        self._force_next_llm_reason: Optional[str] = "startup"

        self.log.info(f"DeepSeek AI Strategy initialized for {self.instrument_id}")
        if self.dry_run:
            self.log.warning("⚠️ DRY_RUN=true: Orders will be simulated and NOT submitted to exchange")
        if self.exchange_context_fetcher:
            self.log.info("Bybit read-only risk context enabled")

    def _derive_base_asset(self) -> str:
        """Derive the base asset symbol from the configured instrument id."""
        symbol = str(self.instrument_id).split("-")[0].split(".")[0]
        for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
            if symbol.endswith(quote) and len(symbol) > len(quote):
                return symbol[:-len(quote)]
        return symbol

    def _log_info_safe(self, message: str) -> None:
        """Log info when Nautilus has registered the strategy logger."""
        try:
            self.log.info(message)
        except Exception:
            return

    def _log_warning_safe(self, message: str) -> None:
        """Log warning when Nautilus has registered the strategy logger."""
        try:
            self.log.warning(message)
        except Exception:
            return

    def _refresh_exchange_risk_context(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """Refresh read-only exchange context with simple time throttling."""
        if self.exchange_context_fetcher is None:
            return self.exchange_risk_context

        now = time.monotonic()
        if (
            not force
            and self.exchange_risk_context is not None
            and (now - self._last_exchange_context_refresh_monotonic) < self.exchange_context_interval_sec
        ):
            return self.exchange_risk_context

        try:
            context = self.exchange_context_fetcher.fetch()
        except Exception as e:
            self.log.warning(f"⚠️ Failed to refresh Bybit risk context: {e}")
            return self.exchange_risk_context

        self.exchange_risk_context = context
        self._last_exchange_context_refresh_monotonic = now
        position = context.get("position") or {}
        wallet = context.get("wallet") or {}
        self.log.info(
            "💼 Bybit Risk Context: "
            f"available={wallet.get('total_available_balance')} "
            f"equity={wallet.get('total_equity')} "
            f"position={position.get('side', 'flat')} "
            f"{position.get('quantity', 0)} {self.base_asset} "
            f"open_orders={len(context.get('open_orders') or [])} "
            f"last5_pnl={(context.get('recent_trade_summary') or {}).get('last_5_realized_pnl')}"
        )
        return context

    def on_start(self):
        """Actions to be performed on strategy start."""
        self.log.info("Starting DeepSeek AI Strategy...")

        # Load instrument
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument {self.instrument_id}")
            self.stop()
            return

        self.log.info(f"Loaded instrument: {self.instrument.id}")
        self._refresh_exchange_risk_context(force=True)

        # Pre-fetch historical bars before subscribing to live data.
        # Warmup size is configurable so operators can trade off startup time vs context depth.
        warmup_limit = max(50, int(self.config.warmup_bars))
        self._prefetch_historical_bars(limit=warmup_limit)

        # Subscribe to bars (live data)
        self.subscribe_bars(self.bar_type)
        self.log.info(f"Subscribed to {self.bar_type}")

        # Subscribe to order book depth and trade ticks
        if self.enable_orderbook:
            try:
                self.subscribe_order_book_deltas(self.instrument_id)
                self.log.info(f"Subscribed to order book deltas for {self.instrument_id}")
            except Exception as e:
                self.log.warning(f"Failed to subscribe to order book deltas: {e}")

            try:
                self.subscribe_trade_ticks(self.instrument_id)
                self.log.info(f"Subscribed to trade ticks for {self.instrument_id}")
            except Exception as e:
                self.log.warning(f"Failed to subscribe to trade ticks: {e}")

        # Set up timer for periodic analysis
        self.clock.set_timer(
            name="analysis_timer",
            interval=timedelta(seconds=self.config.timer_interval_sec),
            callback=self.on_timer,
        )

        self.log.info("Strategy started successfully")

        # Record start time for uptime tracking
        from datetime import datetime
        self.strategy_start_time = datetime.utcnow()

        # Send Telegram startup notification
        if self.telegram_bot and self.enable_telegram:
            try:
                startup_msg = self.telegram_bot.format_startup_message(
                    instrument_id=str(self.instrument_id),
                    config={
                        'enable_auto_sl_tp': self.enable_auto_sl_tp,
                        'enable_oco': self.enable_oco,
                        'enable_trailing_stop': self.enable_trailing_stop,
                        'enable_partial_tp': hasattr(self, 'enable_partial_tp') and getattr(self, 'enable_partial_tp', False),
                    }
                )
                self.telegram_bot.send_message_sync(startup_msg)

                # Send command help message
                help_msg = self.telegram_bot.format_help_response()
                self.telegram_bot.send_message_sync(help_msg)

            except Exception as e:
                self.log.warning(f"Failed to send Telegram startup notification: {e}")

    def on_stop(self):
        """Actions to be performed on strategy stop."""
        self.log.info("Stopping DeepSeek AI Strategy...")

        # Cancel any pending orders
        self.cancel_all_orders(self.instrument_id)

        # Unsubscribe from data
        self.unsubscribe_bars(self.bar_type)
        if self.enable_orderbook:
            try:
                self.unsubscribe_order_book_deltas(self.instrument_id)
            except Exception:
                pass
            try:
                self.unsubscribe_trade_ticks(self.instrument_id)
            except Exception:
                pass

        self.log.info("Strategy stopped")

    def _prefetch_historical_bars(self, limit: int = 200):
        """
        Pre-fetch historical bars from exchange REST API on startup.

        This eliminates the waiting period for indicators to initialize by loading
        historical data directly from the exchange on strategy startup.

        Parameters
        ----------
        limit : int
            Number of historical bars to fetch (default: 200)
        """
        try:
            import requests
            from nautilus_trader.core.datetime import millis_to_nanos

            # Extract symbol from instrument_id
            # Example: BTCUSDT-PERP.BINANCE -> BTCUSDT
            symbol_str = str(self.instrument_id)
            symbol = symbol_str.split('-')[0]

            # Convert bar type to venue interval
            bar_type_str = str(self.bar_type)
            if '1-MINUTE' in bar_type_str:
                interval = '1m'
            elif '5-MINUTE' in bar_type_str:
                interval = '5m'
            elif '15-MINUTE' in bar_type_str:
                interval = '15m'
            elif '1-HOUR' in bar_type_str:
                interval = '1h'
            elif '4-HOUR' in bar_type_str:
                interval = '4h'
            elif '1-DAY' in bar_type_str:
                interval = '1d'
            else:
                interval = '5m'  # Default fallback

            self.log.info(
                f"📡 Pre-fetching {limit} historical bars "
                f"(symbol={symbol}, interval={interval})..."
            )

            venue = str(self.instrument_id).split(".")[-1]
            klines = []

            if venue == "BYBIT":
                # Bybit V5 kline API (linear for USDT perpetuals)
                bybit_interval_map = {
                    "1m": "1",
                    "5m": "5",
                    "15m": "15",
                    "1h": "60",
                    "4h": "240",
                    "1d": "D",
                }
                bybit_interval = bybit_interval_map.get(interval, "5")
                url = "https://api.bybit.com/v5/market/kline"
                params = {
                    "category": "linear",
                    "symbol": symbol,
                    "interval": bybit_interval,
                    "limit": min(limit, 1000),  # Bybit max
                }
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                payload = response.json()
                rows = (payload.get("result") or {}).get("list") or []
                # API returns newest first; strategy warmup needs chronological order
                rows = sorted(rows, key=lambda r: int(r[0]))
                for row in rows:
                    klines.append([
                        int(row[0]),  # open_time_ms
                        row[1],       # open
                        row[2],       # high
                        row[3],       # low
                        row[4],       # close
                        row[5],       # volume
                    ])
            else:
                # Binance fallback for legacy instruments
                url = "https://fapi.binance.com/fapi/v1/klines"
                params = {
                    'symbol': symbol,
                    'interval': interval,
                    'limit': min(limit, 1500),  # Binance max
                }
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                klines = response.json()

            if not klines:
                self.log.warning("⚠️ No bars received from exchange API")
                return

            self.log.info(f"📊 Received {len(klines)} warmup bars from {venue}")

            # Convert to NautilusTrader bars and feed to indicators
            bars_fed = 0
            for kline in klines:
                try:
                    # Create Bar object
                    bar = Bar(
                        bar_type=self.bar_type,
                        open=self.instrument.make_price(float(kline[1])),
                        high=self.instrument.make_price(float(kline[2])),
                        low=self.instrument.make_price(float(kline[3])),
                        close=self.instrument.make_price(float(kline[4])),
                        volume=self.instrument.make_qty(float(kline[5])),
                        ts_event=millis_to_nanos(kline[0]),
                        ts_init=millis_to_nanos(kline[0]),
                    )

                    # Feed to indicator manager
                    self.indicator_manager.update(bar)
                    bars_fed += 1

                except Exception as e:
                    self.log.warning(f"Failed to convert kline to bar: {e}")
                    continue

            self.log.info(
                f"✅ Pre-fetched {bars_fed} bars successfully! "
                f"Indicators ready: {self.indicator_manager.is_initialized()}"
            )

        except Exception as e:
            self.log.error(f"❌ Failed to pre-fetch bars from exchange: {e}")
            self.log.warning("Continuing with live bars only...")

    def _is_dry_run(self) -> bool:
        """Return whether strategy should simulate execution only."""
        return self.dry_run

    def _derive_bar_period_seconds(self) -> int:
        """Bar duration implied by configured bar_type (used for OB window anchoring)."""
        text = str(self.bar_type).upper()
        m = re.search(r"-([0-9]+)-(MINUTE|HOUR|DAY)-", text)
        if not m:
            return 60
        qty = int(m.group(1))
        unit = m.group(2)
        if unit == "MINUTE":
            return max(60, qty * 60)
        if unit == "HOUR":
            return max(60, qty * 3600)
        if unit == "DAY":
            return max(60, qty * 86400)
        return 60

    @staticmethod
    def _nanos_to_utc_iso(nanos: int) -> str:
        """RFC3339-style UTC ISO timestamp from unix epoch nanoseconds."""
        try:
            sec = float(nanos) / 1e9
            return datetime.utcfromtimestamp(sec).isoformat() + "Z"
        except (TypeError, ValueError, OSError):
            return ""

    def on_bar(self, bar: Bar):
        """
        Primary decision loop: run after each completed bar for the configured bar_type.
        """
        if bar.bar_type != self.bar_type:
            return

        self.bars_received += 1

        # Update technical indicators
        self.indicator_manager.update(bar)

        if self.bars_received % 10 == 0:
            self.log.info(
                f"Bar #{self.bars_received}: "
                f"O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close} V:{bar.volume}"
            )

        self._run_bar_close_decision_cycle(completed_bar=bar)

    def on_order_book_deltas(self, deltas) -> None:
        """
        Handle order book delta updates.

        The Nautilus DataEngine maintains the book internally; we read
        the managed book from cache to get a consistent multi-level
        snapshot for feature engineering.
        """
        if self.orderbook_manager is None:
            return

        book = self.cache.order_book(self.instrument_id)
        if book is None:
            return

        ts = deltas.ts_event if hasattr(deltas, "ts_event") else 0
        self.orderbook_manager.update_from_managed_book(book, ts_event=ts)

        n = self.orderbook_manager.depth_updates_received
        should_log = n == 1 or (self._ob_log_interval and n % self._ob_log_interval == 0)
        if should_log and self._ob_log_min_seconds > 0 and ts:
            if self._last_ob_log_ts_ns is not None:
                min_gap_ns = self._ob_log_min_seconds * 1_000_000_000
                if (ts - self._last_ob_log_ts_ns) < min_gap_ns:
                    should_log = False

        if should_log:
            s = self.orderbook_manager.get_summary()
            self.log.info(
                f"📊 OB #{n}: "
                f"bid={s.get('best_bid', 0):.2f} ask={s.get('best_ask', 0):.2f} "
                f"spr={s.get('spread_bps', 0):.1f}bps "
                f"tob={s.get('tob_imbalance', 0):+.3f} "
                f"ofi={s.get('ema_ofi', 0):+.4f} "
                f"qp={s.get('queue_pressure', 0):+.3f} "
                f"tf={s.get('trade_flow_imbalance', 0):+.3f} "
                f"sw_b={s.get('sweep_buy_count', 0)} sw_s={s.get('sweep_sell_count', 0)} "
                f"regime={s.get('depth_regime', '?')} "
                f"ticks={s.get('trade_ticks', 0)}"
            )
            if ts:
                self._last_ob_log_ts_ns = ts

    def on_order_book_depth(self, depth) -> None:
        """
        Handle OrderBookDepth10 snapshot updates (alternative subscription path).
        """
        if self.orderbook_manager is None:
            return
        self.orderbook_manager.update_depth(depth)

    def on_trade_tick(self, tick) -> None:
        """
        Handle individual trade tick updates.

        Each tick carries price, size and aggressor side.  We feed these
        to the OrderBookManager to compute trade-flow features.
        """
        if self.orderbook_manager is None:
            return
        self.orderbook_manager.update_trade(tick)

    def on_timer(self, event):
        """Timer-only housekeeping: risk refresh, bracket maintenance, optional OB CSV dumps."""
        self.log.info("⏲️ Ops timer: maintenance (no standalone LLM call).")

        try:
            self._refresh_exchange_risk_context()
        except Exception:
            pass

        last_px: Optional[float] = None
        bars = getattr(self.indicator_manager, "recent_bars", []) or []
        if bars:
            last_px = float(bars[-1].close)

        if last_px is not None:
            if self.enable_trailing_stop:
                self._update_trailing_stops(last_px)

        self._cleanup_oco_orphans()

        if self.orderbook_manager and self.orderbook_manager.is_ready():
            try:
                dump_path = self.orderbook_manager.dump_features_csv(
                    path="data/microstructure_features.csv",
                    fwd_bars=(1, 5),
                )
                if dump_path:
                    self.log.debug(f"📁 Ops timer OB feature dump appended: {dump_path}")
            except Exception as e:
                self.log.warning(f"Feature dump failed: {e}")

    def _run_bar_close_decision_cycle(self, completed_bar: Bar) -> None:
        """
        Candle-close LLM synthesis + journaling + discretionary execution hooks.
        Must only run AFTER indicators update for `completed_bar`.
        """
        if not self.indicator_manager.is_initialized():
            self.log.warning("Indicators not yet initialized, skipping candle-close synthesis")
            return

        current_bar = completed_bar
        current_price = float(current_bar.close)

        try:
            technical_data = self.indicator_manager.get_technical_data(current_price)
            self.log.debug(f"Technical data retrieved: {len(technical_data)} indicators")
        except Exception as e:
            self.log.error(f"Failed to get technical data: {e}")
            return

        kline_data = self.indicator_manager.get_kline_data(count=self.llm_kline_context_bars)
        self.log.debug(f"Retrieved {len(kline_data)} K-lines for analysis")

        sentiment_data = None
        if self.sentiment_enabled and self.sentiment_fetcher:
            try:
                sentiment_data = self.sentiment_fetcher.fetch()
                if sentiment_data:
                    self.log.info(self.sentiment_fetcher.format_for_display(sentiment_data))
            except Exception as e:
                self.log.warning(f"Failed to fetch sentiment data: {e}")

        microstructure_data = None
        if self.orderbook_manager and self.orderbook_manager.is_ready():
            summary = dict(self.orderbook_manager.get_summary())
            tf_secs = self._derive_bar_period_seconds()
            summary["tf_windows"] = self.orderbook_manager.get_tf_window_summaries(
                tf_secs, now_ns=int(current_bar.ts_event)
            )
            microstructure_data = summary

        price_data = {
            'price': current_price,
            'timestamp': self.clock.utc_now().isoformat(),
            'bar_ts_event': int(current_bar.ts_event),
            'bar_ts_init': int(current_bar.ts_init),
            'high': float(current_bar.high),
            'low': float(current_bar.low),
            'volume': float(current_bar.volume),
            'price_change': self._calculate_price_change(),
            'kline_data': kline_data,
            'instrument_id': str(self.instrument_id),
            'bar_type': str(self.bar_type),
        }
        price_data["bar_close_ts_utc"] = self._nanos_to_utc_iso(int(current_bar.ts_event))
        if self._last_llm_decision_bar_count is None:
            price_data["bars_since_last_llm_decision"] = None
        else:
            price_data["bars_since_last_llm_decision"] = max(
                0,
                self.bars_received - self._last_llm_decision_bar_count,
            )
        trade_margin_usdt = self.fixed_trade_usdt if self.fixed_trade_usdt > 0 else self.base_usdt
        price_data["fixed_trade_margin_usdt"] = trade_margin_usdt
        price_data["fixed_trade_notional_usdt"] = trade_margin_usdt * self.leverage
        price_data["configured_leverage"] = self.leverage

        if microstructure_data:
            price_data["microstructure"] = microstructure_data

        risk_context = self._refresh_exchange_risk_context()

        current_position = self._get_current_position_data()
        current_position = self._merge_exchange_position_context(current_position, risk_context)
        current_position = self._enrich_position_health(current_position)

        # Compact bar-close summary (single line instead of 3-4 verbose lines)
        ms = microstructure_data or {}
        self.log.info(
            f"📌 Bar-close @ {price_data['bar_close_ts_utc'] or '?'} "
            f"px=${current_price:,.2f} "
            f"trend={technical_data.get('overall_trend', '?')} "
            f"rsi={technical_data.get('rsi', 0):.1f} "
            f"rvol={technical_data.get('rvol', 0):.2f} "
            f"ob_tfi={ms.get('trade_flow_imbalance', 0):+.2f} "
            f"regime={ms.get('depth_regime', '?')}"
        )

        if current_position:
            health = current_position.get("position_health") or {}
            self.log.info(
                f"Current Position: {current_position['side']} "
                f"{current_position['quantity']} {self.base_asset} @ ${current_position['avg_px']:.2f} "
                f"uPnL={current_position.get('unrealized_pnl', 0):.2f} "
                f"peak={health.get('peak_unrealized_pnl', 0):.2f} "
                f"giveback={health.get('giveback_pct', 0):.0f}% "
                f"bars_held={health.get('bars_held', 0)} "
                f"health={health.get('recommendation', '?')} "
                f"(source={current_position.get('source', 'nautilus')})"
            )

        market_state = self._build_market_state(
            price_data=price_data,
            technical_data=technical_data,
            microstructure_data=microstructure_data,
            current_position=current_position,
            risk_context=risk_context,
        )
        price_data["market_state"] = market_state

        should_call_llm, trigger_reason = self._should_call_llm(
            market_state=market_state,
            price_data=price_data,
        )
        price_data["llm_trigger_reason"] = trigger_reason

        if not should_call_llm:
            self.log.info(
                "🧭 Market-state gate: previous LLM decision remains valid "
                f"({trigger_reason})"
            )
            gated_signal = self._build_gated_signal(trigger_reason, market_state)
            position_after_gate = self._get_current_position_data()
            self._append_trade_journal_row(
                signal_data=gated_signal,
                price_data=price_data,
                technical_data=technical_data,
                microstructure_data=microstructure_data,
                risk_context=risk_context,
                current_position=current_position,
                position_after=position_after_gate,
                execution_summary={
                    "status": "gated",
                    "action": "none",
                    "note": trigger_reason,
                },
                bar_close_iso=price_data.get("bar_close_ts_utc"),
                execution_ts_iso=datetime.utcnow().isoformat(),
                decision_ts_iso=datetime.utcnow().isoformat(),
                latency_ms=0,
                decision_trigger="market_state_gate",
            )
            return

        execution_summary = {"status": "error", "action": "none", "note": "pre_llm"}

        signal_data = None

        try:
            import time as _time

            self.log.info(f"Calling DeepSeek AI (market-change trigger: {trigger_reason})...")
            _t0 = _time.monotonic()
            signal_data = self.deepseek.analyze(
                price_data=price_data,
                technical_data=technical_data,
                sentiment_data=sentiment_data,
                current_position=current_position,
                risk_context=risk_context,
            )
            _elapsed = _time.monotonic() - _t0
            self.log.info(
                f"🤖 Signal: {signal_data['signal']} | "
                f"Confidence: {signal_data['confidence']} | "
                f"API time: {_elapsed:.1f}s | "
                f"Reason: {signal_data.get('reason', '')}"
            )
            signal_data["llm_api_seconds"] = round(_elapsed, 6)
            latency_ms_val = int(round(_elapsed * 1000))
            signal_data["_latency_ms"] = latency_ms_val
            decision_ts_completed = datetime.utcnow().isoformat()

            if self.telegram_bot and self.enable_telegram and self.telegram_notify_signals:
                if signal_data['signal'] in ['BUY', 'SELL']:
                    try:
                        signal_notification = self.telegram_bot.format_trade_signal({
                            'signal': signal_data['signal'],
                            'confidence': signal_data['confidence'],
                            'price': price_data['price'],
                            'timestamp': price_data['timestamp'],
                            'rsi': technical_data.get('rsi', 0),
                            'macd': technical_data.get('macd', 0),
                            'support': technical_data.get('support', 0),
                            'resistance': technical_data.get('resistance', 0),
                            'reasoning': signal_data.get('reason', ''),
                        })
                        self.telegram_bot.send_message_sync(signal_notification)
                    except Exception as e:
                        self.log.warning(f"Failed to send Telegram signal notification: {e}")

            self.last_signal = signal_data
            self._record_llm_market_state(market_state)
            self._last_llm_decision_bar_count = self.bars_received
            self._force_next_llm_reason = None

            execution_summary = self._execute_trade(
                signal_data, price_data, technical_data, current_position, risk_context
            )
            execution_ts_wall = datetime.utcnow()
            signal_data["_execution_completed"] = execution_ts_wall.isoformat()

            position_after = self._get_current_position_data()
            tw = (microstructure_data or {}).get('tf_windows') or {}

            ob_fast = ""
            ob_main = ""
            ob_ctx = ""
            if isinstance(tw, dict) and tw.get("ready"):
                ob_fast = tw.get("fast")
                ob_main = tw.get("main")
                ob_ctx = tw.get("context")

            self._append_trade_journal_row(
                signal_data=signal_data,
                price_data=price_data,
                technical_data=technical_data,
                microstructure_data=microstructure_data,
                risk_context=risk_context,
                current_position=current_position,
                position_after=position_after,
                execution_summary=execution_summary,
                bar_close_iso=price_data.get("bar_close_ts_utc"),
                execution_ts_iso=execution_ts_wall.isoformat(),
                decision_ts_iso=decision_ts_completed,
                latency_ms=latency_ms_val,
                decision_trigger="on_bar",
                ob_window_fast_json=ob_fast,
                ob_window_main_json=ob_main,
                ob_window_context_json=ob_ctx,
            )

        except Exception as e:
            self.log.error(f"DeepSeek AI analysis failed: {e}", exc_info=True)
            if self.telegram_bot and self.enable_telegram and self.telegram_notify_errors:
                try:
                    error_msg = self.telegram_bot.format_error_alert({
                        'level': 'ERROR',
                        'message': f"AI Analysis Failed: {str(e)[:100]}",
                        'context': 'on_bar_cycle',
                    })
                    self.telegram_bot.send_message_sync(error_msg)
                except Exception:
                    pass

            fb = self.deepseek._emit_fallback(price_data)
            fb["reasoning_content"] = (fb.get("reasoning_content") or "") + (
                "\nexception:" + repr(e)[:500]
            )
            fb["_latency_ms"] = None
            self.last_signal = fb
            self._force_next_llm_reason = "last_llm_error"
            pos_after_fail = self._get_current_position_data()
            tw = (microstructure_data or {}).get('tf_windows') or {}
            ob_fast_f = ""
            ob_main_f = ""
            ob_ctx_f = ""
            if isinstance(tw, dict) and tw.get("ready"):
                ob_fast_f, ob_main_f, ob_ctx_f = tw.get("fast"), tw.get("main"), tw.get("context")

            fail_exec = {"status": "error", "action": "none", "note": f"analyze_exception:{type(e).__name__}"}
            self._append_trade_journal_row(
                signal_data=fb,
                price_data=price_data,
                technical_data=technical_data,
                microstructure_data=microstructure_data,
                risk_context=risk_context,
                current_position=current_position,
                position_after=pos_after_fail,
                execution_summary=fail_exec,
                bar_close_iso=price_data.get("bar_close_ts_utc"),
                execution_ts_iso=datetime.utcnow().isoformat(),
                decision_ts_iso=datetime.utcnow().isoformat(),
                latency_ms=None,
                decision_trigger="on_bar",
                ob_window_fast_json=ob_fast_f,
                ob_window_main_json=ob_main_f,
                ob_window_context_json=ob_ctx_f,
            )

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _bucket_signed(self, value: Any, weak: float = 0.15, strong: float = 0.45) -> str:
        val = self._safe_float(value)
        if val >= strong:
            return "strong_buy"
        if val >= weak:
            return "buy"
        if val <= -strong:
            return "strong_sell"
        if val <= -weak:
            return "sell"
        return "neutral"

    def _bucket_rvol(self, value: Any) -> str:
        val = self._safe_float(value, 1.0)
        if val >= 2.0:
            return "climactic"
        if val >= 1.2:
            return "elevated"
        if val <= 0.7:
            return "quiet"
        return "normal"

    def _bucket_bb_position(self, value: Any) -> str:
        val = self._safe_float(value, 0.5)
        if val >= 0.9:
            return "above_upper"
        if val >= 0.7:
            return "upper_band"
        if val <= 0.1:
            return "below_lower"
        if val <= 0.3:
            return "lower_band"
        return "middle"

    def _app_regime_label(self, technical_data: Dict[str, Any]) -> str:
        trend = str(technical_data.get("overall_trend") or "mixed")
        adx = self._safe_float(technical_data.get("adx"))
        dmi_dx = self._safe_float(technical_data.get("dmi_dx"))
        trend_strength = max(adx, dmi_dx)
        if trend == "strong_up" and trend_strength >= 20:
            return "trend_up"
        if trend == "strong_down" and trend_strength >= 20:
            return "trend_down"
        if trend == "mixed" or trend_strength < 18:
            return "range_or_chop"
        return "transition"

    def _structure_state(
        self,
        price: float,
        technical_data: Dict[str, Any],
    ) -> str:
        support = self._safe_float(technical_data.get("support"))
        resistance = self._safe_float(technical_data.get("resistance"))
        atr = self._safe_float(technical_data.get("atr"))
        buffer = max(atr * 0.20, price * 0.0005)
        if support > 0 and price < support - buffer:
            return "below_support"
        if resistance > 0 and price > resistance + buffer:
            return "above_resistance"
        if support > 0 and abs(price - support) <= buffer:
            return "at_support"
        if resistance > 0 and abs(price - resistance) <= buffer:
            return "at_resistance"
        return "inside_range"

    def _extract_ob_label(self, microstructure_data: Optional[Dict[str, Any]], window: str, label: str) -> str:
        tw = (microstructure_data or {}).get("tf_windows") or {}
        node = tw.get(window) if isinstance(tw, dict) else None
        labels = node.get("labels") if isinstance(node, dict) else None
        return str((labels or {}).get(label) or "unknown")

    def _build_market_state(
        self,
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        microstructure_data: Optional[Dict[str, Any]],
        current_position: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        price = self._safe_float(price_data.get("price"))
        atr = self._safe_float(technical_data.get("atr"))
        position_side = str((current_position or {}).get("side") or "flat").lower()
        position_qty = round(self._safe_float((current_position or {}).get("quantity")), 8)
        open_orders_count = len((risk_context or {}).get("open_orders") or [])
        main_pressure = self._extract_ob_label(microstructure_data, "main", "directional_pressure")
        main_shift = self._extract_ob_label(microstructure_data, "main", "regime_shift")
        tw = (microstructure_data or {}).get("tf_windows") or {}
        main_node = tw.get("main") if isinstance(tw, dict) else None
        main_trade_flow_imbalance = self._safe_float(
            (main_node or {}).get("trade_flow_imbalance"),
            self._safe_float((microstructure_data or {}).get("trade_flow_imbalance")),
        )
        main_normalized_ofi_score = self._safe_float(
            (main_node or {}).get("normalized_ofi_score"),
            self._safe_float((microstructure_data or {}).get("normalized_ofi_score")),
        )
        invalidation_price = self._extract_position_invalidation_price(
            position=current_position,
            current_price=price,
        )

        return {
            "price": price,
            "atr": atr,
            "position_key": f"{position_side}:{position_qty}",
            "open_orders_count": open_orders_count,
            "has_pending_intent": open_orders_count > 0,
            "app_regime": self._app_regime_label(technical_data),
            "structure_state": self._structure_state(price, technical_data),
            "main_pressure": main_pressure,
            "main_regime_shift": main_shift,
            "main_trade_flow_imbalance": main_trade_flow_imbalance,
            "main_normalized_ofi_score": main_normalized_ofi_score,
            "position_invalidation_price": invalidation_price,
            "support_12": self._safe_float(technical_data.get("support_12")),
            "resistance_12": self._safe_float(technical_data.get("resistance_12")),
            "support_48": self._safe_float(technical_data.get("support_48")),
            "resistance_48": self._safe_float(technical_data.get("resistance_48")),
            "support": self._safe_float(technical_data.get("support")),
            "resistance": self._safe_float(technical_data.get("resistance")),
        }

    def _extract_position_invalidation_price(
        self,
        position: Optional[Dict[str, Any]],
        current_price: float,
    ) -> Optional[float]:
        if not position:
            return None
        side = str(position.get("side") or "").lower()
        if side not in {"long", "short"}:
            return None
        signal = self.last_signal or {}
        candidates: List[float] = []
        submitted_stop_loss = self._safe_float(signal.get("submitted_stop_loss"))
        if submitted_stop_loss > 0:
            candidates.append(submitted_stop_loss)
        invalidation_price = self._safe_float(signal.get("invalidation_price"))
        if invalidation_price > 0:
            candidates.append(invalidation_price)
        stop_loss = self._safe_float(signal.get("stop_loss"))
        if stop_loss > 0:
            candidates.append(stop_loss)
        invalidation = str(signal.get("invalidation") or "")
        for raw in re.findall(r"[-+]?[0-9]*\.?[0-9]+", invalidation):
            level = self._safe_float(raw)
            if level > 0:
                candidates.append(level)
        if not candidates:
            return None
        if side == "short":
            above = [lvl for lvl in candidates if lvl >= current_price]
            selected = min(above) if above else max(candidates)
        else:
            below = [lvl for lvl in candidates if lvl <= current_price]
            selected = max(below) if below else min(candidates)
        return selected if selected > 0 else None

    def _annotate_signal_with_bracket_plan(
        self,
        entry_price: float,
        bracket_plan: Dict[str, Any],
    ) -> None:
        updates = {
            "submitted_entry_price": round(entry_price, 10),
            "submitted_stop_loss": round(float(bracket_plan["stop_loss_price"]), 10),
            "submitted_take_profit": round(float(bracket_plan["take_profit_price"]), 10),
            "bracket_levels_source": bracket_plan.get("levels_source"),
            "invalidation_price": round(float(bracket_plan["stop_loss_price"]), 10),
        }
        deepseek_history = getattr(getattr(self, "deepseek", None), "signal_history", None)
        for signal in (
            getattr(self, "latest_signal_data", None),
            getattr(self, "last_signal", None),
            deepseek_history[-1] if deepseek_history else None,
        ):
            if isinstance(signal, dict):
                signal.update(updates)

    def _atr_move_threshold(self, price: float, atr: float, atr_fraction: float) -> float:
        bps_floor = price * 0.0005 if price > 0 else 0.0
        atr_component = atr_fraction * atr if atr > 0 else 0.0
        return max(atr_component, bps_floor)

    def _flat_thesis_ttl_bars(self, app_regime: str) -> int:
        regime = str(app_regime or "range_or_chop")
        if regime in {"trend_up", "trend_down"}:
            return 3
        if regime == "transition":
            return 4
        return 6

    def _parse_watch_trigger_spec(self, signal: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        signal = signal or {}
        trigger_price = self._safe_float(signal.get("watch_trigger_price"))
        direction = str(signal.get("watch_trigger_direction") or "").strip().lower()
        expiry_raw = signal.get("watch_trigger_expiry_bars")
        try:
            expiry_bars = int(expiry_raw) if expiry_raw is not None else 6
        except (TypeError, ValueError):
            expiry_bars = 6
        expiry_bars = max(1, expiry_bars)

        if trigger_price > 0 and direction:
            return {
                "price": trigger_price,
                "direction": self._normalize_watch_trigger_direction(direction),
                "expiry_bars": expiry_bars,
                "source": "structured",
            }

        text = str(signal.get("watch_trigger") or "").strip()
        if not text:
            return None

        lowered = text.lower()
        if any(token in lowered for token in ("below", "under", "break down", "breakdown", "sell", "short")):
            direction = "short"
        elif any(token in lowered for token in ("above", "over", "break up", "breakout", "buy", "long")):
            direction = "long"
        else:
            direction = "unknown"

        numbers = [self._safe_float(raw) for raw in re.findall(r"[-+]?[0-9]*\.?[0-9]+", text)]
        numbers = [value for value in numbers if value > 0]
        if not numbers or direction == "unknown":
            return None

        return {
            "price": numbers[-1],
            "direction": direction,
            "expiry_bars": expiry_bars,
            "source": "text",
        }

    @staticmethod
    def _normalize_watch_trigger_direction(direction: str) -> str:
        normalized = str(direction or "").strip().lower()
        if normalized in {"short", "sell", "down", "below"}:
            return "short"
        if normalized in {"long", "buy", "up", "above"}:
            return "long"
        return normalized

    def _watch_trigger_state(
        self,
        signal: Optional[Dict[str, Any]],
        price: float,
        atr: float,
        bars_since_last_llm: Optional[int],
        last_price: float,
    ) -> Tuple[bool, bool]:
        spec = self._parse_watch_trigger_spec(signal)
        if spec is None:
            return False, False

        cross_buffer = max(0.15 * atr, price * 0.0005) if atr > 0 and price > 0 else 0.0
        trigger_price = float(spec["price"])
        direction = str(spec["direction"])
        fired = False
        if direction == "short" and cross_buffer > 0:
            fired = self._crossed_below_threshold(last_price, price, trigger_price - cross_buffer)
        elif direction == "long" and cross_buffer > 0:
            fired = self._crossed_above_threshold(last_price, price, trigger_price + cross_buffer)

        expired = False
        if bars_since_last_llm is not None and bars_since_last_llm >= int(spec["expiry_bars"]):
            expired = True
        return fired, expired

    def _flat_no_action_rearm_reason(
        self,
        market_state: Dict[str, Any],
        previous: Dict[str, Any],
        price_data: Dict[str, Any],
    ) -> Optional[str]:
        position_key = str(market_state.get("position_key") or "flat:0")
        if not position_key.startswith("flat:"):
            return None

        last_signal = self.last_signal or {}
        position_action = str(last_signal.get("position_action") or "").upper()
        if position_action != "NO_ACTION":
            return None

        price = self._safe_float(market_state.get("price"))
        last_price = self._safe_float(previous.get("price"), price)
        atr = self._safe_float(market_state.get("atr"))
        app_regime = str(market_state.get("app_regime") or "range_or_chop")
        bars_since = price_data.get("bars_since_last_llm_decision")
        try:
            bars_since_int = int(bars_since) if bars_since is not None else None
        except (TypeError, ValueError):
            bars_since_int = None

        ttl_bars = self._flat_thesis_ttl_bars(app_regime)
        if bars_since_int is not None and bars_since_int >= ttl_bars:
            return "flat_thesis_ttl_expired"

        fired, expired = self._watch_trigger_state(
            signal=last_signal,
            price=price,
            atr=atr,
            bars_since_last_llm=bars_since_int,
            last_price=last_price,
        )
        if fired:
            return "watch_trigger_fired"
        if expired:
            return "watch_trigger_expired"

        prev_structure = str(previous.get("structure_state") or "inside_range")
        now_structure = str(market_state.get("structure_state") or "inside_range")
        if prev_structure != now_structure:
            return "structure_state_change"

        continuation_threshold = self._atr_move_threshold(price, atr, 0.35)
        support_walk_threshold = self._atr_move_threshold(price, atr, 0.20)
        if app_regime == "trend_down" and continuation_threshold > 0:
            if (last_price - price) >= continuation_threshold:
                return "trend_continuation_progress"
            prev_support = self._safe_float(previous.get("support_12")) or self._safe_float(previous.get("support"))
            now_support = self._safe_float(market_state.get("support_12")) or self._safe_float(market_state.get("support"))
            if prev_support > 0 and now_support > 0 and (prev_support - now_support) >= support_walk_threshold:
                return "support_walk_extension"
        elif app_regime == "trend_up" and continuation_threshold > 0:
            if (price - last_price) >= continuation_threshold:
                return "trend_continuation_progress"
            prev_resistance = self._safe_float(previous.get("resistance_12")) or self._safe_float(previous.get("resistance"))
            now_resistance = self._safe_float(market_state.get("resistance_12")) or self._safe_float(market_state.get("resistance"))
            if prev_resistance > 0 and now_resistance > 0 and (now_resistance - prev_resistance) >= support_walk_threshold:
                return "resistance_walk_extension"

        return None

    def _stop_risk_bounds(self, entry_price: float, atr: float) -> Tuple[float, float]:
        if entry_price <= 0:
            return 0.0, 0.0
        floor = max(1.2 * atr, entry_price * 0.004) if atr > 0 else entry_price * 0.004
        cap = min(3.0 * atr, entry_price * 0.02) if atr > 0 else entry_price * 0.02
        return floor, cap

    def _required_min_rr_for_risk_pct(self, risk_pct: float) -> float:
        if risk_pct <= 0.5:
            return 0.5
        if risk_pct <= 1.0:
            return 0.75
        return 1.0

    def _estimate_round_trip_friction_pct(self) -> float:
        micro = (self.latest_price_data or {}).get("microstructure") or {}
        spread_bps = self._safe_float(micro.get("spread_bps"))
        return (13.0 + max(0.0, spread_bps)) / 10000.0

    def _net_r_multiple(
        self,
        entry_price: float,
        risk_per_unit: float,
        reward_per_unit: float,
    ) -> float:
        if entry_price <= 0 or risk_per_unit <= 0:
            return 0.0
        friction = entry_price * self._estimate_round_trip_friction_pct()
        net_reward = reward_per_unit - friction
        net_risk = risk_per_unit + friction
        if net_risk <= 0:
            return 0.0
        return net_reward / net_risk

    def _validate_bracket_geometry(
        self,
        side: OrderSide,
        entry_price: float,
        stop_loss_price: float,
        tp_price: float,
        atr: float,
    ) -> Optional[Dict[str, Any]]:
        if entry_price <= 0:
            return None
        if side == OrderSide.BUY:
            risk_per_unit = entry_price - stop_loss_price
            reward_per_unit = tp_price - entry_price
        else:
            risk_per_unit = stop_loss_price - entry_price
            reward_per_unit = entry_price - tp_price
        if risk_per_unit <= 0 or reward_per_unit <= 0:
            return None

        floor, cap = self._stop_risk_bounds(entry_price, atr)
        if floor > 0 and risk_per_unit < floor:
            return None
        if cap > 0 and risk_per_unit > cap:
            return None

        gross_rr = reward_per_unit / risk_per_unit
        risk_pct = (risk_per_unit / entry_price) * 100.0
        required_rr = max(self.min_entry_rr, self._required_min_rr_for_risk_pct(risk_pct))
        if gross_rr < required_rr:
            return None

        net_rr = self._net_r_multiple(entry_price, risk_per_unit, reward_per_unit)
        if net_rr < required_rr:
            return None

        reward_pct = (reward_per_unit / entry_price) * 100.0
        return {
            "risk_per_unit": risk_per_unit,
            "reward_per_unit": reward_per_unit,
            "risk_pct": risk_pct,
            "reward_pct": reward_pct,
            "rr": gross_rr,
            "net_rr": net_rr,
            "required_rr": required_rr,
        }

    @staticmethod
    def _crossed_outside_band(previous_value: float, current_value: float, neutral_abs: float) -> bool:
        return abs(previous_value) <= neutral_abs < abs(current_value)

    @staticmethod
    def _crossed_above_threshold(previous_value: float, current_value: float, threshold: float) -> bool:
        return previous_value <= threshold < current_value

    @staticmethod
    def _crossed_below_threshold(previous_value: float, current_value: float, threshold: float) -> bool:
        return previous_value >= threshold > current_value

    def _should_call_llm(
        self,
        market_state: Dict[str, Any],
        price_data: Dict[str, Any],
    ) -> Tuple[bool, str]:
        if not self.enable_market_state_gate:
            return True, "market_state_gate_disabled"
        force_reason = str(self._force_next_llm_reason or "")
        if force_reason in {"startup", "last_llm_error"}:
            return True, "startup_or_recovery"
        if force_reason:
            return True, force_reason
        if not self.last_signal or self.last_signal.get("is_fallback"):
            return True, "startup_or_recovery"
        previous = self._last_llm_market_state
        if not previous:
            return True, "startup_or_recovery"

        if (
            market_state.get("open_orders_count") != previous.get("open_orders_count")
            or (
                bool(previous.get("has_pending_intent"))
                and not bool(market_state.get("has_pending_intent"))
            )
            or market_state.get("position_key") != previous.get("position_key")
        ):
            return True, "pending_intent_state_changed"

        price = self._safe_float(market_state.get("price"))
        last_price = self._safe_float(previous.get("price"))
        atr = self._safe_float(market_state.get("atr"))
        cross_buffer = max(0.15 * atr, price * 0.0005) if atr > 0 and price > 0 else 0.0

        if cross_buffer > 0:
            for support_key, resistance_key in (("support_12", "resistance_12"), ("support_48", "resistance_48")):
                support = self._safe_float(previous.get(support_key))
                resistance = self._safe_float(previous.get(resistance_key))
                if resistance > 0 and self._crossed_above_threshold(
                    last_price,
                    price,
                    resistance + cross_buffer,
                ):
                    return True, "structure_cross"
                if support > 0 and self._crossed_below_threshold(
                    last_price,
                    price,
                    support - cross_buffer,
                ):
                    return True, "structure_cross"

        position_key = str(market_state.get("position_key") or "flat:0")
        in_position = not position_key.startswith("flat:")
        invalidation_price = self._safe_float(market_state.get("position_invalidation_price"))
        if in_position and invalidation_price > 0 and atr > 0:
            side = "short" if position_key.startswith("short:") else "long"
            if side == "short" and price >= (invalidation_price - 0.15 * atr):
                return True, "position_invalidation_threat"
            if side == "long" and price <= (invalidation_price + 0.15 * atr):
                return True, "position_invalidation_threat"

        tfi_prev = self._safe_float(previous.get("main_trade_flow_imbalance"))
        tfi_now = self._safe_float(market_state.get("main_trade_flow_imbalance"))
        if self._crossed_outside_band(tfi_prev, tfi_now, 0.15):
            return True, "micro_numeric_cross"
        ofi_prev = self._safe_float(previous.get("main_normalized_ofi_score"))
        ofi_now = self._safe_float(market_state.get("main_normalized_ofi_score"))
        if self._crossed_outside_band(ofi_prev, ofi_now, 0.20):
            return True, "micro_numeric_cross"

        prev_shift = str(previous.get("main_regime_shift") or "unknown")
        now_shift = str(market_state.get("main_regime_shift") or "unknown")
        if (prev_shift == "transitioning") != (now_shift == "transitioning"):
            return True, "hard_regime_flip"

        flat_rearm_reason = self._flat_no_action_rearm_reason(
            market_state=market_state,
            previous=previous,
            price_data=price_data,
        )
        if flat_rearm_reason:
            return True, flat_rearm_reason

        return False, "no_material_market_change"

    def _record_llm_market_state(self, market_state: Dict[str, Any]) -> None:
        self._last_llm_market_state = dict(market_state)

    def _build_gated_signal(self, trigger_reason: str, market_state: Dict[str, Any]) -> Dict[str, Any]:
        previous = self.last_signal or {}
        thesis = previous.get("thesis") or previous.get("reason") or ""
        return {
            "signal": "HOLD",
            "position_action": (
                "NO_ACTION"
                if str(market_state.get("position_key") or "flat:0").startswith("flat:")
                else "HOLD_POSITION"
            ),
            "confidence": previous.get("confidence", "LOW"),
            "regime": market_state.get("app_regime", previous.get("regime", "")),
            "trend_strength": previous.get("trend_strength", ""),
            "risk_assessment": previous.get("risk_assessment", ""),
            "reason": "Market-state gate: prior LLM decision still valid.",
            "thesis": thesis or "No material regime, structure, volume, or order-book change.",
            "invalidation": previous.get("invalidation", ""),
            "execution_note": trigger_reason,
            "volume_note": previous.get("volume_note", ""),
            "reasoning_content": f"market_state_gate:{trigger_reason}",
            "llm_model": getattr(self.deepseek, "model", ""),
            "llm_api_seconds": "",
            "is_gated": True,
            "market_state": market_state,
        }

    def _calculate_price_change(self) -> float:
        """Calculate price change percentage."""
        bars = self.indicator_manager.recent_bars
        if len(bars) < 2:
            return 0.0

        current = float(bars[-1].close)
        previous = float(bars[-2].close)

        return ((current - previous) / previous) * 100

    def _append_trade_journal_row(
        self,
        signal_data: Dict[str, Any],
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        microstructure_data: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]],
        current_position: Optional[Dict[str, Any]],
        position_after: Optional[Dict[str, Any]],
        execution_summary: Dict[str, Any],
        *,
        bar_close_iso: Optional[str] = None,
        execution_ts_iso: Optional[str] = None,
        decision_ts_iso: Optional[str] = None,
        latency_ms: Optional[float] = None,
        decision_trigger: str = "on_bar",
        ob_window_fast_json: Any = None,
        ob_window_main_json: Any = None,
        ob_window_context_json: Any = None,
    ) -> None:
        """Append one decision row to the CSV trade journal."""
        if not self.trade_journal_enabled or self.trade_journal is None:
            return

        wallet = (risk_context or {}).get("wallet") or {}
        trade_summary = (risk_context or {}).get("recent_trade_summary") or {}
        micro = microstructure_data or {}
        position_before = current_position or {}
        execution = execution_summary or {}
        bracket_plan = execution.get("bracket_plan") or {}

        tw = micro.get("tf_windows") or {}
        if ob_window_fast_json is None and isinstance(tw, dict) and tw.get("ready"):
            ob_window_fast_json = tw.get("fast")
        if ob_window_main_json is None and isinstance(tw, dict) and tw.get("ready"):
            ob_window_main_json = tw.get("main")
        if ob_window_context_json is None and isinstance(tw, dict) and tw.get("ready"):
            ob_window_context_json = tw.get("context")

        bc = bar_close_iso or price_data.get("bar_close_ts_utc") or ""
        dec_ts = decision_ts_iso or datetime.utcnow().isoformat()
        exe_ts = execution_ts_iso or ""
        lat = latency_ms
        if lat is None and signal_data.get("_latency_ms") is not None:
            lat = signal_data.get("_latency_ms")

        reasoning_cell = signal_data.get("reasoning_content")
        if reasoning_cell is None:
            reasoning_cell = ""
        sig_json = {
            k: v for k, v in signal_data.items()
            if not str(k).startswith("_")
        }

        try:
            latency_cell = "" if lat is None else int(round(float(lat)))
        except (TypeError, ValueError):
            latency_cell = ""

        row = {
            "decision_ts_utc": dec_ts,
            "decision_ts": dec_ts,
            "strategy_ts": price_data.get("timestamp"),
            "instrument_id": str(self.instrument_id),
            "bar_type": str(self.bar_type),
            "bar_ts_event": price_data.get("bar_ts_event"),
            "bar_ts_init": price_data.get("bar_ts_init"),
            "signal": signal_data.get("signal"),
            "position_action": signal_data.get("position_action"),
            "confidence": signal_data.get("confidence"),
            "trend_strength": signal_data.get("trend_strength"),
            "risk_assessment": signal_data.get("risk_assessment"),
            "is_fallback": signal_data.get("is_fallback", False),
            "reason": signal_data.get("reason"),
            "reasoning_content": reasoning_cell,
            "llm_model": signal_data.get("llm_model"),
            "llm_api_seconds": signal_data.get("llm_api_seconds"),
            "current_price": price_data.get("price"),
            "period_high": price_data.get("high"),
            "period_low": price_data.get("low"),
            "bar_volume": price_data.get("volume"),
            "price_change_pct": price_data.get("price_change"),
            "overall_trend": technical_data.get("overall_trend"),
            "short_term_trend": technical_data.get("short_term_trend"),
            "medium_term_trend": technical_data.get("medium_term_trend"),
            "rsi": technical_data.get("rsi"),
            "macd": technical_data.get("macd"),
            "macd_signal": technical_data.get("macd_signal"),
            "macd_histogram": technical_data.get("macd_histogram"),
            "volume_ratio": technical_data.get("volume_ratio"),
            "support": technical_data.get("support"),
            "resistance": technical_data.get("resistance"),
            "ob_spread_bps": micro.get("spread_bps"),
            "ob_spread_volatility": micro.get("spread_volatility"),
            "ob_tob_imbalance": micro.get("tob_imbalance"),
            "ob_depth_imbalance": micro.get("depth_imbalance"),
            "ob_ema_ofi": micro.get("ema_ofi"),
            "ob_queue_pressure": micro.get("queue_pressure"),
            "ob_trade_flow_imbalance": micro.get("trade_flow_imbalance"),
            "ob_vwap_deviation_bps": micro.get("vwap_deviation_bps"),
            "ob_sweep_buy_count": micro.get("sweep_buy_count"),
            "ob_sweep_sell_count": micro.get("sweep_sell_count"),
            "ob_depth_regime": micro.get("depth_regime"),
            "position_before_side": position_before.get("side"),
            "position_before_qty": position_before.get("quantity"),
            "position_before_avg_px": position_before.get("avg_px"),
            "position_before_upnl": position_before.get("unrealized_pnl"),
            "position_before_source": position_before.get("source"),
            "risk_total_equity": wallet.get("total_equity"),
            "risk_total_available_balance": wallet.get("total_available_balance"),
            "risk_open_orders_count": len((risk_context or {}).get("open_orders") or []),
            "risk_recent_realized_pnl_5": trade_summary.get("last_5_realized_pnl"),
            "execution_status": execution.get("status"),
            "execution_action": execution.get("action"),
            "execution_target_side": execution.get("target_side"),
            "execution_target_quantity": execution.get("target_quantity"),
            "execution_note": execution.get("note"),
            "bracket_levels_source": bracket_plan.get("levels_source"),
            "bracket_stop_loss": bracket_plan.get("stop_loss_price"),
            "bracket_take_profit": bracket_plan.get("take_profit_price"),
            "technical_snapshot_json": technical_data,
            "microstructure_snapshot_json": micro,
            "risk_context_json": risk_context,
            "position_before_json": current_position,
            "position_after_json": position_after,
            "bar_close_ts_utc": bc,
            "bar_close_ts": bc,
            "execution_ts_utc": exe_ts,
            "execution_ts": exe_ts,
            "latency_ms": latency_cell,
            "decision_cycle_trigger": decision_trigger,
            "llm_market_regime": signal_data.get("regime"),
            "thesis": signal_data.get("thesis"),
            "invalidation": signal_data.get("invalidation"),
            "llm_execution_note": signal_data.get("execution_note"),
            "volume_note": signal_data.get("volume_note"),
            "rvol": technical_data.get("rvol"),
            "volume_zscore": technical_data.get("volume_zscore"),
            "volume_trend_slope": technical_data.get("volume_trend_slope"),
            "directional_volume_confirmation": technical_data.get(
                "directional_volume_confirmation"
            ),
            "technical_volume_regime": technical_data.get("volume_regime"),
            "ob_window_fast_json": ob_window_fast_json,
            "ob_window_main_json": ob_window_main_json,
            "ob_window_context_json": ob_window_context_json,
            "signal_json": sig_json,
        }

        try:
            self.trade_journal.append(row)
        except Exception as e:
            self._log_warning_safe(f"Trade journal append failed: {e}")

    def _get_current_position_data(self) -> Optional[Dict[str, Any]]:
        """Get aggregate current position information for the active instrument."""
        # Get open positions for this instrument
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        
        if not positions:
            return None
        
        # Use last bar close price as it's more reliable than cache.price().
        # cache.price() requires tick data which may not be available.
        bars = self.indicator_manager.recent_bars
        if bars:
            current_price = bars[-1].close
        else:
            try:
                current_price = self.cache.price(self.instrument_id, PriceType.LAST)
            except (TypeError, AttributeError):
                current_price = None

        signed_qty = 0.0
        gross_qty = 0.0
        weighted_avg_notional = 0.0
        unrealized_pnl = 0.0
        position_ids: List[str] = []

        for position in positions:
            if not position or not position.is_open:
                continue

            qty = float(position.quantity)
            if qty <= 0:
                continue

            side_mult = 1.0 if position.side == PositionSide.LONG else -1.0
            signed_qty += side_mult * qty
            gross_qty += qty
            weighted_avg_notional += qty * float(position.avg_px_open)
            position_ids.append(str(position.id))

            if current_price:
                unrealized_pnl += float(position.unrealized_pnl(current_price))

        if abs(signed_qty) <= 0:
            return None

        quantity = abs(signed_qty)
        avg_px = (weighted_avg_notional / gross_qty) if gross_qty > 0 else 0.0
        current_price_float = float(current_price) if current_price else avg_px

        return {
            'side': 'long' if signed_qty > 0 else 'short',
            'quantity': quantity,
            'signed_quantity': signed_qty,
            'avg_px': avg_px,
            'unrealized_pnl': unrealized_pnl,
            'notional_usdt': quantity * current_price_float,
            'position_count': len(position_ids),
            'position_ids': position_ids,
            'source': 'nautilus_aggregate',
        }

    @staticmethod
    def _position_identity_key(position_data: Dict[str, Any]) -> Any:
        """
        Stable identity for the currently open position/trade.

        Used to decide when peak-uPnL tracking must be reset. Nautilus assigns a
        fresh position id on every new trade, so a re-entry on the same side still
        produces a distinct key. When ids are unavailable (e.g. exchange-context
        fallback), fall back to (side, rounded entry price) which still changes on
        a genuine re-entry.
        """
        position_ids = position_data.get("position_ids") or []
        if position_ids:
            return tuple(sorted(str(pid) for pid in position_ids))
        side = position_data.get("side")
        avg_px = round(float(position_data.get("avg_px", 0.0)), 6)
        return (side, avg_px)

    def _enrich_position_health(self, position_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Add position health metrics for scalp-aware LLM prompting.

        Tracks peak unrealized P&L, giveback percentage, and bars held so the LLM
        can make informed profit-taking decisions.
        """
        if position_data is None:
            self._position_health = {
                "peak_unrealized_pnl": 0.0,
                "peak_profit_pct": 0.0,
                "entry_bar_count": 0,
                "entry_price": 0.0,
                "entry_side": None,
                "entry_key": None,
            }
            return None

        upnl = float(position_data.get("unrealized_pnl", 0.0))
        notional = float(position_data.get("notional_usdt", 0.0))
        current_side = position_data.get("side")
        current_key = self._position_identity_key(position_data)

        # Reset peak tracking whenever this is a *different* open position than the
        # one we were tracking. A fresh re-entry (even on the same side) gets a new
        # Nautilus position id, so the prior trade's peak uPnL must not carry over.
        if self._position_health.get("entry_key") != current_key:
            self._position_health = {
                "peak_unrealized_pnl": upnl,
                "peak_profit_pct": 0.0,
                "entry_bar_count": self.bars_received,
                "entry_price": float(position_data.get("avg_px", 0.0)),
                "entry_side": current_side,
                "entry_key": current_key,
            }

        if upnl > self._position_health["peak_unrealized_pnl"]:
            self._position_health["peak_unrealized_pnl"] = upnl

        peak = self._position_health["peak_unrealized_pnl"]
        profit_pct = (upnl / notional * 100) if notional > 0 else 0.0
        peak_profit_pct = (peak / notional * 100) if notional > 0 else 0.0
        if peak_profit_pct > self._position_health["peak_profit_pct"]:
            self._position_health["peak_profit_pct"] = peak_profit_pct

        giveback_pct = 0.0
        if peak > 0 and upnl < peak:
            giveback_pct = ((peak - upnl) / peak) * 100

        bars_held = max(0, self.bars_received - self._position_health["entry_bar_count"])

        recommendation = "stable"
        if profit_pct < 0:
            recommendation = "underwater"
        elif giveback_pct >= 60:
            recommendation = "deep_giveback"
        elif giveback_pct >= 30:
            recommendation = "minor_giveback"
        elif profit_pct >= 0.4:
            recommendation = "extended_profit"

        position_data["position_health"] = {
            "profit_pct": round(profit_pct, 4),
            "peak_profit_pct": round(self._position_health["peak_profit_pct"], 4),
            "giveback_pct": round(giveback_pct, 1),
            "bars_held": bars_held,
            "peak_unrealized_pnl": round(peak, 2),
            "recommendation": recommendation,
        }
        return position_data

    def _position_adjustment_threshold(self) -> float:
        """Return the minimum meaningful position delta for this instrument."""
        configured = float(self.position_config.get('adjustment_threshold', 0.0))
        increment = None
        if self.instrument is not None:
            for attr_name in ("size_increment", "min_quantity", "min_size"):
                value = getattr(self.instrument, attr_name, None)
                if value is not None:
                    try:
                        increment = float(value)
                        break
                    except (TypeError, ValueError):
                        continue
        return max(configured, increment or 0.0, float(self.position_config['min_trade_amount']))

    def _position_from_exchange_context(self, risk_context: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        """Convert Bybit position context into the strategy position shape."""
        exchange_position = (risk_context or {}).get("position") or {}
        quantity = float(exchange_position.get("quantity") or 0.0)
        if quantity <= 0:
            return None
        side = str(exchange_position.get("side") or "").lower()
        signed_quantity = -quantity if side == "short" else quantity
        mark_price = float(exchange_position.get("mark_price") or 0.0)
        return {
            "side": side,
            "quantity": quantity,
            "signed_quantity": signed_quantity,
            "avg_px": float(exchange_position.get("avg_price") or 0.0),
            "unrealized_pnl": float(exchange_position.get("unrealized_pnl") or 0.0),
            "notional_usdt": float(exchange_position.get("position_value") or (quantity * mark_price)),
            "position_count": 1,
            "position_ids": [],
            "source": source,
        }

    def _merge_exchange_position_context(
        self,
        current_position: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Prefer exchange position truth when Nautilus is missing or materially stale.

        Nautilus remains the execution substrate, but the decision loop should not
        open duplicate exposure just because the local cache missed an external
        or pre-existing position.
        """
        if not risk_context:
            return current_position

        exchange_position = risk_context.get("position")
        if not exchange_position:
            if current_position and risk_context.get("ok"):
                open_orders = risk_context.get("open_orders") or []
                if not open_orders:
                    self.log.warning(
                        "⚠️ Nautilus cache reports an open position but Bybit is flat "
                        f"(nautilus={current_position.get('side')} {current_position.get('quantity')} {self.base_asset}, "
                        "bybit=flat); using Bybit flat state for this cycle"
                    )
                    return None
            return current_position

        exchange_as_position = self._position_from_exchange_context(
            risk_context,
            source="bybit_exchange_fallback",
        )
        if exchange_as_position is None:
            return current_position

        if current_position is None:
            self.log.warning(
                "⚠️ Nautilus cache has no open position but Bybit reports "
                f"{exchange_as_position['side']} {exchange_as_position['quantity']} {self.base_asset}; "
                "using exchange position context for this cycle"
            )
            return exchange_as_position

        threshold = self._position_adjustment_threshold()
        side_mismatch = current_position.get("side") != exchange_as_position.get("side")
        qty_diff = abs(float(current_position.get("quantity") or 0.0) - exchange_as_position["quantity"])
        if side_mismatch or qty_diff >= threshold:
            self.log.warning(
                "⚠️ Nautilus/Bybit position mismatch: "
                f"nautilus={current_position.get('side')} {current_position.get('quantity')} "
                f"{self.base_asset}, bybit={exchange_as_position['side']} "
                f"{exchange_as_position['quantity']} {self.base_asset}; "
                "using Bybit quantity for decision/execution sizing"
            )
            exchange_as_position["source"] = "bybit_exchange_override"
            exchange_as_position["nautilus_position"] = current_position
            return exchange_as_position

        merged = dict(current_position)
        merged["exchange_position"] = exchange_position
        return merged

    def _execute_trade(
        self,
        signal_data: Dict[str, Any],
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute trading logic based on signal.

        Parameters
        ----------
        signal_data : Dict
            AI-generated signal
        price_data : Dict
            Current price data
        technical_data : Dict
            Technical indicators
        current_position : Dict or None
            Current position info
        """
        # Check if trading is paused
        if self.is_trading_paused:
            self._log_info_safe("⏸️ Trading is paused - skipping signal execution")
            return {"status": "skipped", "action": "none", "note": "trading_paused"}
        
        # Store signal and technical data for SL/TP calculation
        self.latest_signal_data = signal_data
        self.latest_technical_data = technical_data
        self.latest_price_data = price_data
        
        action = str(signal_data.get("position_action") or "NO_ACTION").upper()
        confidence = str(signal_data.get('confidence') or 'LOW').upper()

        valid_actions = {
            "ENTER_LONG",
            "ENTER_SHORT",
            "HOLD_POSITION",
            "EXIT_NOW",
            "NO_ACTION",
        }
        if action not in valid_actions:
            self._log_warning_safe(f"⚠️ Invalid position_action={action}; taking no action")
            return {"status": "skipped", "action": "none", "note": f"invalid_position_action:{action}"}

        if current_position:
            current_side = str(current_position.get("side") or "").lower()
            current_qty = float(current_position.get("quantity") or 0.0)

            if action == "EXIT_NOW":
                self._log_warning_safe(
                    "⚠️ Ignoring EXIT_NOW while bracket-owned position is open; "
                    "holding position until bracket SL/TP or later flat-state decision"
                )
                return {
                    "status": "hold",
                    "action": "hold_position",
                    "target_side": current_side,
                    "target_quantity": current_qty,
                    "note": "exit_now_ignored_while_exposed",
                }

            if action in {"HOLD_POSITION", "NO_ACTION"}:
                self._log_info_safe(f"📊 Action: {action} - retaining current position")
                return {
                    "status": "hold",
                    "action": "hold_position",
                    "target_side": current_side,
                    "target_quantity": current_qty,
                    "note": action.lower(),
                }

            self._log_warning_safe(
                f"⚠️ Ignoring {action} while {current_side} position is open; "
                "close first and wait for a later flat-state entry decision"
            )
            return {
                "status": "skipped",
                "action": "hold_position",
                "target_side": current_side,
                "target_quantity": current_qty,
                "note": f"entry_action_while_exposed:{action}",
            }

        if action in {"NO_ACTION", "HOLD_POSITION", "EXIT_NOW"}:
            self._log_info_safe(f"📊 Action: {action} while flat - no action taken")
            return {"status": "hold", "action": "none", "note": f"flat_{action.lower()}"}

        # Confidence gating applies to new exposure only.
        confidence_levels = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
        min_conf_level = confidence_levels.get(self.min_confidence, 1)
        signal_conf_level = confidence_levels.get(confidence, 1)

        if signal_conf_level < min_conf_level:
            self._log_warning_safe(
                f"⚠️ Signal confidence {confidence} below minimum {self.min_confidence}, skipping trade"
            )
            return {
                "status": "skipped",
                "action": "none",
                "note": f"confidence_below_min:{confidence}<{self.min_confidence}",
            }

        # Calculate target position size
        target_quantity = self._calculate_position_size(
            signal_data, price_data, technical_data, current_position, risk_context
        )

        if target_quantity == 0:
            self._log_warning_safe("⚠️ Calculated position size is 0, skipping trade")
            return {"status": "skipped", "action": "none", "note": "zero_target_quantity"}

        target_side = "long" if action == "ENTER_LONG" else "short"
        result = self._open_new_position(target_side, target_quantity)
        if result:
            return result
        return {
            "status": "skipped",
            "action": "open_new",
            "target_side": target_side,
            "target_quantity": target_quantity,
            "note": "bracket_order_not_submitted",
        }

    def _calculate_position_size(
        self,
        signal_data: Dict[str, Any],
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        Calculate fixed-margin position size.

        Configured USDT is margin capital. Effective order notional is
        margin * configured leverage. Confidence/trend no longer scale entries.
        """
        requested_margin_usdt = self.fixed_trade_usdt if self.fixed_trade_usdt > 0 else self.base_usdt
        requested_usdt = requested_margin_usdt * self.leverage

        # Apply max position ratio limit
        account_equity = self._account_equity_for_sizing(risk_context)
        available_balance = self._available_balance_for_sizing(risk_context)
        max_usdt = account_equity * self.position_config['max_position_ratio']
        if available_balance is not None and available_balance > 0:
            max_usdt = min(max_usdt, available_balance * self.leverage * 0.95)
        final_usdt = min(requested_usdt, max_usdt)
        sizing_reason = (
            f"Fixed margin request:${requested_margin_usdt:.2f} "
            f"x {self.leverage:.2f}x = notional:${requested_usdt:.2f}"
        )
        if final_usdt < requested_usdt:
            sizing_reason += f", capped:${final_usdt:.2f}"

        # Enforce a conservative minimum notional requirement.
        MIN_NOTIONAL_USDT = 100.0
        if final_usdt < MIN_NOTIONAL_USDT:
            final_usdt = MIN_NOTIONAL_USDT

        # Convert to instrument quantity
        current_price = price_data['price']
        raw_quantity = final_usdt / current_price

        # Apply minimum trade amount
        if raw_quantity < self.position_config['min_trade_amount']:
            raw_quantity = self.position_config['min_trade_amount']

        quantity = self._normalize_order_quantity(raw_quantity, log_skipped=False)
        if quantity is None:
            self._log_warning_safe("⚠️ Position size rounded below instrument minimum, skipping trade")
            return 0.0

        # Re-check notional after instrument rounding.
        actual_notional = quantity * current_price
        if actual_notional < MIN_NOTIONAL_USDT:
            quantity = self._normalize_order_quantity(MIN_NOTIONAL_USDT / current_price, log_skipped=False)
            if quantity is None:
                self._log_warning_safe("⚠️ Minimum notional quantity is below instrument minimum, skipping trade")
                return 0.0
            actual_notional = quantity * current_price

        self._log_info_safe(
            f"📊 Position Sizing: "
            f"{sizing_reason} "
            f"= ${final_usdt:.2f} = {quantity:.6g} {self.base_asset} "
            f"(notional: ${actual_notional:.2f}, equity_basis=${account_equity:.2f})"
        )

        return quantity

    def _account_equity_for_sizing(self, risk_context: Optional[Dict[str, Any]]) -> float:
        """Return the best available account equity basis for sizing caps."""
        wallet = (risk_context or {}).get("wallet") or {}
        for key in ("total_equity", "usdt_equity", "total_wallet_balance", "usdt_wallet_balance"):
            value = wallet.get(key)
            if value is not None and float(value) > 0:
                return float(value)
        return float(self.equity)

    def _available_balance_for_sizing(self, risk_context: Optional[Dict[str, Any]]) -> Optional[float]:
        """Return available balance when exchange context provides it."""
        wallet = (risk_context or {}).get("wallet") or {}
        value = wallet.get("total_available_balance")
        if value is not None and float(value) > 0:
            return float(value)
        value = wallet.get("usdt_available_to_withdraw")
        if value is not None and float(value) > 0:
            return float(value)
        return None

    def _normalize_order_quantity(self, quantity: float, log_skipped: bool = True) -> Optional[float]:
        """Normalize a raw quantity through the instrument increment rules."""
        if quantity <= 0 or self.instrument is None:
            return None
        try:
            normalized = self.instrument.make_qty(quantity)
        except Exception as e:
            if log_skipped:
                self._log_info_safe(
                    f"✅ Quantity delta {quantity:.8f} {self.base_asset} is below "
                    f"instrument increment; skipping adjustment ({e})"
                )
            return None

        normalized_float = float(normalized)
        if normalized_float <= 0:
            if log_skipped:
                self._log_info_safe(
                    f"✅ Quantity delta {quantity:.8f} {self.base_asset} rounded to zero; skipping"
                )
            return None
        return normalized_float

    def _open_new_position(self, side: str, quantity: float):
        """
        Open new position using a structural bracket order (entry + SL + TP).

        Delegates to ``_submit_bracket_order``, which uses:
        - Entry: LIMIT post-only at estimated bar close (may not fill immediately)
        - Stop loss: STOP_MARKET at structural invalidation (support/resistance)
        - Take profit: LIMIT at nearest viable structural target (min R:R gate)

        """
        order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL

        # Submit bracket order with SL/TP
        bracket_result = self._submit_bracket_order(
            side=order_side,
            quantity=quantity,
        )

        if bracket_result and bracket_result.get("status") == "submitted":
            self.log.info(
                f"🚀 Opening {side} position: {quantity:.6g} {self.base_asset} (with bracket SL/TP)"
            )
        return bracket_result

    def _submit_order(
        self,
        side: OrderSide,
        quantity: float,
        reduce_only: bool = False,
    ):
        """Submit market order to exchange."""
        normalized_quantity = self._normalize_order_quantity(quantity)
        if normalized_quantity is None:
            return
        quantity = normalized_quantity

        if self._is_dry_run():
            self.log.info(
                f"🧪 DRY RUN: Simulated {side.name} market order {quantity:.6g} {self.base_asset} "
                f"(reduce_only={reduce_only})"
            )
            return

        if quantity < self.position_config['min_trade_amount']:
            self.log.warning(
                f"⚠️ Order quantity {quantity:.6g} below minimum "
                f"{self.position_config['min_trade_amount']:.6g}, skipping"
            )
            return

        # Create market order
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(quantity),
            time_in_force=TimeInForce.GTC,
            reduce_only=reduce_only,
        )

        # Submit order
        self.submit_order(order)

        self.log.info(
            f"📤 Submitted {side.name} market order: {quantity:.6g} {self.base_asset} "
            f"(reduce_only={reduce_only})"
        )
    
    def _submit_bracket_order(
        self,
        side: OrderSide,
        quantity: float,
    ):
        """
        Submit a bracket order with entry, stop loss, and take profit using NautilusTrader's built-in bracket orders.

        This uses the OrderFactory.bracket() method which automatically creates:
        - Entry order (LIMIT, post-only where supported)
        - Stop Loss order (STOP_MARKET) linked with OTO (One-Triggers-Other)
        - Take Profit order (LIMIT) linked with OTO and OCO with SL

        The OCO linkage is handled automatically by NautilusTrader.

        Parameters
        ----------
        side : OrderSide
            Side of the entry order (BUY or SELL)
        quantity : float
            Quantity to trade
        """
        normalized_quantity = self._normalize_order_quantity(quantity)
        if normalized_quantity is None:
            return {
                "status": "skipped",
                "action": "open_new",
                "note": "quantity_below_increment",
            }
        quantity = normalized_quantity

        if quantity < self.position_config['min_trade_amount']:
            self.log.warning(
                f"⚠️ Order quantity {quantity:.6g} below minimum "
                f"{self.position_config['min_trade_amount']:.6g}, skipping"
            )
            return {
                "status": "skipped",
                "action": "open_new",
                "target_quantity": quantity,
                "note": "quantity_below_min_trade_amount",
            }

        if not self.enable_auto_sl_tp:
            self.log.error("❌ Auto SL/TP is disabled - blocking unprotected entry")
            return {
                "status": "skipped",
                "action": "open_new",
                "target_side": "long" if side == OrderSide.BUY else "short",
                "target_quantity": quantity,
                "note": "auto_sl_tp_disabled_entry_blocked",
            }

        if not self.latest_signal_data:
            self.log.error("❌ No signal data available for SL/TP - blocking unprotected entry")
            return {
                "status": "skipped",
                "action": "open_new",
                "target_side": "long" if side == OrderSide.BUY else "short",
                "target_quantity": quantity,
                "note": "missing_signal_entry_blocked",
            }

        # Determine latest price for entry estimation
        entry_price: Optional[float] = None

        if self.latest_price_data and self.latest_price_data.get('price'):
            entry_price = float(self.latest_price_data['price'])

        if entry_price is None and hasattr(self.indicator_manager, "recent_bars"):
            recent_bars = self.indicator_manager.recent_bars
            if recent_bars:
                entry_price = float(recent_bars[-1].close)

        if entry_price is None:
            cache_bars = self.cache.bars(self.bar_type)
            if cache_bars:
                entry_price = float(cache_bars[-1].close)

        if entry_price is None or entry_price <= 0:
            self.log.error("❌ Unable to determine entry price for bracket order - blocking unprotected entry")
            return {
                "status": "skipped",
                "action": "open_new",
                "target_side": "long" if side == OrderSide.BUY else "short",
                "target_quantity": quantity,
                "note": "entry_price_missing_entry_blocked",
            }

        confidence = self.latest_signal_data.get('confidence', 'MEDIUM')

        bracket_plan = self._build_entry_bracket_plan(side, entry_price, quantity)
        self._annotate_signal_with_bracket_plan(entry_price, bracket_plan)

        stop_loss_price = bracket_plan["stop_loss_price"]
        tp_price = bracket_plan["take_profit_price"]

        # Log SL/TP summary
        self.log.info(
            f"🎯 Creating bracket order for {side.name}:\n"
            f"   Entry: ${entry_price:,.2f} (LIMIT, post-only where supported)\n"
            f"   Stop Loss: ${stop_loss_price:,.2f} ({((stop_loss_price/entry_price - 1) * 100):.2f}%)\n"
            f"   Take Profit: ${tp_price:,.2f} ({((tp_price/entry_price - 1) * 100):.2f}%)\n"
            f"   R:R: {bracket_plan['rr']:.2f}R levels_source={bracket_plan['levels_source']}\n"
            f"   1R risk: ${bracket_plan['risk_usdt']:.2f} on ${bracket_plan['notional_usdt']:.2f} notional\n"
            f"   Quantity: {quantity:.6g} {self.base_asset}\n"
            f"   Confidence: {confidence}"
        )

        if self._is_dry_run():
            self.log.info(
                f"🧪 DRY RUN: Simulated protected bracket {side.name} {quantity:.6g} {self.base_asset} "
                f"(SL={stop_loss_price:.6g}, TP={tp_price:.6g}, source={bracket_plan['levels_source']})"
            )
            return {
                "status": "submitted",
                "action": "open_new",
                "target_side": "long" if side == OrderSide.BUY else "short",
                "target_quantity": quantity,
                "note": f"dry_run_protected_bracket:{bracket_plan['levels_source']}",
                "bracket_plan": bracket_plan,
            }

        try:
            # Create bracket order using OrderFactory
            # This automatically creates entry + SL + TP with OTO/OCO linkage
            # IMPORTANT: Use emulation_trigger to enable order emulation for Binance compatibility
            # Binance doesn't support native OCO+OTO orders, so NautilusTrader will emulate them
            bracket_order_list = self.order_factory.bracket(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=self.instrument.make_qty(quantity),
                entry_order_type=OrderType.LIMIT,
                entry_price=self.instrument.make_price(entry_price),
                entry_post_only=True,
                sl_trigger_price=self.instrument.make_price(stop_loss_price),
                tp_price=self.instrument.make_price(tp_price),
                time_in_force=TimeInForce.GTC,
                emulation_trigger=TriggerType.DEFAULT,  # Enable order emulation
            )

            # Submit the bracket order list
            self.submit_order_list(bracket_order_list)

            self.log.info(
                f"✅ Submitted bracket order: {side.name} {quantity:.6g} {self.base_asset} with SL/TP\n"
                f"   OrderList ID: {bracket_order_list.id}"
            )

            # Save bracket order info for trailing stop
            if self.enable_trailing_stop:
                instrument_key = str(self.instrument_id)

                # Extract SL order from bracket (it's typically the second order in the list)
                sl_order = None
                for order in bracket_order_list.orders:
                    if order.order_type == OrderType.STOP_MARKET:
                        sl_order = order
                        break

                if sl_order:
                    self.trailing_stop_state[instrument_key] = {
                        "entry_price": entry_price,
                        "highest_price": entry_price if side == OrderSide.BUY else None,
                        "lowest_price": entry_price if side == OrderSide.SELL else None,
                        "current_sl_price": stop_loss_price,
                        "sl_order_id": str(sl_order.client_order_id),
                        "activated": False,
                        "side": "LONG" if side == OrderSide.BUY else "SHORT",
                        "quantity": quantity,
                    }
                    self.log.debug(
                        f"📌 Saved SL order ID for trailing stop: {str(sl_order.client_order_id)[:8]}..."
                    )

            return {
                "status": "submitted",
                "action": "open_new",
                "target_side": "long" if side == OrderSide.BUY else "short",
                "target_quantity": quantity,
                "note": (
                    f"limit_bracket_rr:{bracket_plan['rr']:.2f} "
                    f"risk_usdt:{bracket_plan['risk_usdt']:.2f} "
                    f"levels_source:{bracket_plan['levels_source']}"
                ),
                "bracket_plan": bracket_plan,
            }

        except Exception as e:
            self.log.error(f"❌ Failed to submit bracket order: {e}")
            self.log.error("❌ Blocking entry because protected bracket submission failed")
            return {
                "status": "skipped",
                "action": "open_new",
                "target_side": "long" if side == OrderSide.BUY else "short",
                "target_quantity": quantity,
                "note": f"protected_bracket_submission_failed:{type(e).__name__}",
            }

    def _build_entry_bracket_plan(
        self,
        side: OrderSide,
        entry_price: float,
        quantity: float,
    ) -> Dict[str, Any]:
        """Choose protected entry levels: LLM, structural, then symmetric 1%."""
        llm_plan = self._build_llm_bracket_plan(side, entry_price, quantity)
        if llm_plan is not None:
            return llm_plan

        structural_plan = self._build_structural_bracket_plan(side, entry_price, quantity)
        if structural_plan["valid"]:
            structural_plan["levels_source"] = "structural"
            return structural_plan

        return self._build_fallback_bracket_plan(side, entry_price, quantity)

    def _build_llm_bracket_plan(
        self,
        side: OrderSide,
        entry_price: float,
        quantity: float,
    ) -> Optional[Dict[str, Any]]:
        """Return validated LLM TP/SL levels, or None when fallback is required."""
        signal = self.latest_signal_data or {}
        try:
            stop_loss_price = float(signal.get("stop_loss"))
            tp_price = float(signal.get("take_profit"))
        except (TypeError, ValueError):
            return None

        atr = self._safe_float((self.latest_technical_data or {}).get("atr"))
        geometry = self._validate_bracket_geometry(
            side=side,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            tp_price=tp_price,
            atr=atr,
        )
        if geometry is None:
            return None

        risk_per_unit = float(geometry["risk_per_unit"])
        reward_per_unit = float(geometry["reward_per_unit"])
        rr = float(geometry["rr"])

        return {
            "valid": True,
            "note": "ok",
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": tp_price,
            "levels_source": "llm",
            "target_source": "llm",
            "target_r": rr,
            "rr": rr,
            "net_rr": float(geometry["net_rr"]),
            "required_rr": float(geometry["required_rr"]),
            "risk_pct": float(geometry["risk_pct"]),
            "reward_pct": float(geometry["reward_pct"]),
            "risk_usdt": risk_per_unit * quantity,
            "notional_usdt": entry_price * quantity,
            "candidates": [],
        }

    def _build_fallback_bracket_plan(
        self,
        side: OrderSide,
        entry_price: float,
        quantity: float,
    ) -> Dict[str, Any]:
        """Return the final symmetric 1% protected bracket fallback."""
        if side == OrderSide.BUY:
            stop_loss_price = entry_price * 0.99
            tp_price = entry_price * 1.01
        else:
            stop_loss_price = entry_price * 1.01
            tp_price = entry_price * 0.99
        risk_per_unit = abs(entry_price - stop_loss_price)
        reward_per_unit = abs(tp_price - entry_price)
        return {
            "valid": True,
            "note": "ok",
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": tp_price,
            "levels_source": "fallback_1pct",
            "target_source": "fallback_1pct",
            "target_r": 1.0,
            "rr": 1.0,
            "risk_pct": 1.0,
            "reward_pct": 1.0,
            "risk_usdt": risk_per_unit * quantity,
            "notional_usdt": entry_price * quantity,
            "candidates": [],
        }

    def _target_r_from_signal(self) -> float:
        raw = (self.latest_signal_data or {}).get("target_r")
        try:
            target_r = float(raw)
        except (TypeError, ValueError):
            target_r = float(self.default_target_r)
        return min(max(target_r, self.min_entry_rr), self.max_target_r)

    def _structural_target_candidates(self, side: OrderSide, entry_price: float) -> List[Tuple[str, float]]:
        tech = self.latest_technical_data or {}
        if side == OrderSide.BUY:
            keys = ("resistance_12", "resistance", "resistance_48", "resistance_288")
            return [
                (key, float(tech.get(key) or 0.0))
                for key in keys
                if float(tech.get(key) or 0.0) > entry_price
            ]
        keys = ("support_12", "support", "support_48", "support_288")
        return [
            (key, float(tech.get(key) or 0.0))
            for key in keys
            if 0.0 < float(tech.get(key) or 0.0) < entry_price
        ]

    def _build_structural_bracket_plan(
        self,
        side: OrderSide,
        entry_price: float,
        quantity: float,
    ) -> Dict[str, Any]:
        tech = self.latest_technical_data or {}
        support = float(tech.get("support") or 0.0)
        resistance = float(tech.get("resistance") or 0.0)
        if side == OrderSide.BUY:
            stop_loss_price = (
                support * (1 - self.sl_buffer_pct)
                if self.sl_use_support_resistance and support > 0
                else entry_price * 0.98
            )
            risk_per_unit = entry_price - stop_loss_price
            direction = 1.0
        else:
            stop_loss_price = (
                resistance * (1 + self.sl_buffer_pct)
                if self.sl_use_support_resistance and resistance > 0
                else entry_price * 1.02
            )
            risk_per_unit = stop_loss_price - entry_price
            direction = -1.0

        atr = self._safe_float(tech.get("atr"))
        floor, cap = self._stop_risk_bounds(entry_price, atr)
        if floor > 0 and risk_per_unit < floor:
            return {
                "valid": False,
                "note": "stop_below_atr_floor",
                "entry_price": entry_price,
                "stop_loss_price": stop_loss_price,
            }
        if cap > 0 and risk_per_unit > cap:
            return {
                "valid": False,
                "note": "stop_above_atr_cap",
                "entry_price": entry_price,
                "stop_loss_price": stop_loss_price,
            }

        if risk_per_unit <= 0:
            return {
                "valid": False,
                "note": "invalid_stop_geometry",
                "entry_price": entry_price,
                "stop_loss_price": stop_loss_price,
            }

        target_r = self._target_r_from_signal()
        risk_pct = (risk_per_unit / entry_price) * 100.0 if entry_price > 0 else 0.0
        required_rr = max(self.min_entry_rr, self._required_min_rr_for_risk_pct(risk_pct))
        desired_reward = target_r * risk_per_unit
        candidates = []
        for source, target in self._structural_target_candidates(side, entry_price):
            reward = (target - entry_price) * direction
            if reward <= 0:
                continue
            candidates.append((source, target, reward / risk_per_unit, reward))

        viable = []
        for source, target, gross_rr, reward in candidates:
            geometry = self._validate_bracket_geometry(
                side=side,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                tp_price=target,
                atr=atr,
            )
            if geometry is not None:
                viable.append((source, target, float(geometry["rr"]), reward))

        if not viable:
            return {
                "valid": False,
                "note": f"no_structural_target_with_min_rr:{required_rr:.2f}",
                "entry_price": entry_price,
                "stop_loss_price": stop_loss_price,
                "risk_pct": risk_pct,
                "target_r": target_r,
                "candidates": candidates,
            }

        preferred = [c for c in viable if c[3] >= desired_reward]
        selected = min(preferred or viable, key=lambda item: item[3])
        source, tp_price, rr, reward_per_unit = selected
        net_rr = self._net_r_multiple(entry_price, risk_per_unit, reward_per_unit)
        notional = quantity * entry_price
        risk_usdt = risk_per_unit * quantity
        return {
            "valid": True,
            "note": "ok",
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": tp_price,
            "target_source": source,
            "target_r": target_r,
            "rr": rr,
            "net_rr": net_rr,
            "required_rr": required_rr,
            "risk_pct": risk_pct,
            "reward_pct": (reward_per_unit / entry_price) * 100.0,
            "risk_usdt": risk_usdt,
            "notional_usdt": notional,
            "candidates": candidates,
        }

    def on_order_filled(self, event):
        """
        Handle order filled events.

        Note: OCO logic is now handled automatically by NautilusTrader's bracket orders.
        We no longer need to manually cancel peer orders.
        """
        filled_order_id = str(event.client_order_id)

        self.log.info(
            f"✅ Order filled: {event.order_side.name} "
            f"{event.last_qty} @ {event.last_px} "
            f"(ID: {filled_order_id[:8]}...)"
        )
        is_reduce_only = bool(getattr(event, "reduce_only", False) or getattr(event, "is_reduce_only", False))
        if is_reduce_only:
            self._force_next_llm_reason = "tp_or_sl_filled"
        else:
            self.log.info(
                "📌 Entry fill observed under bracket ownership; "
                "not forcing immediate next-bar LLM re-analysis"
            )

        # Send Telegram order fill notification
        if self.telegram_bot and self.enable_telegram and self.telegram_notify_fills:
            try:
                fill_msg = self.telegram_bot.format_order_fill({
                    'side': event.order_side.name,
                    'quantity': float(event.last_qty),
                    'price': float(event.last_px),
                    'order_type': 'MARKET',  # Could extract from order if needed
                })
                self.telegram_bot.send_message_sync(fill_msg)
            except Exception as e:
                self.log.warning(f"Failed to send Telegram fill notification: {e}")
    

    def on_order_rejected(self, event):
        """Handle order rejected events."""
        self.log.error(f"❌ Order rejected: {event.reason}")
        self._force_next_llm_reason = "order_rejected"

    def on_order_canceled(self, event):
        """Handle order canceled events."""
        self.log.warning(f"⚠️ Order canceled: {getattr(event, 'reason', 'unknown')}")
        self._force_next_llm_reason = "order_canceled"

    def on_position_opened(self, event):
        """
        Handle position opened events.

        Note: With bracket orders, SL/TP orders are automatically submitted as part of the bracket.
        We no longer need to manually submit them here.
        """
        # PositionOpened event contains position data directly
        self.log.info(
            f"🟢 Position opened: {event.side.name} "
            f"{event.quantity} @ {event.avg_px_open}"
        )

        # Update trailing stop state with actual entry price if it exists
        # (bracket order already initialized it with estimated price)
        if self.enable_trailing_stop:
            instrument_key = str(self.instrument_id)
            entry_price = float(event.avg_px_open)

            if instrument_key in self.trailing_stop_state:
                # Update with actual entry price
                self.trailing_stop_state[instrument_key]["entry_price"] = entry_price
                if event.side == PositionSide.LONG:
                    self.trailing_stop_state[instrument_key]["highest_price"] = entry_price
                else:
                    self.trailing_stop_state[instrument_key]["lowest_price"] = entry_price

                self.log.debug(
                    f"📊 Updated trailing stop state with actual entry price: ${entry_price:,.2f}"
                )
            else:
                # Fallback: initialize if not already set (shouldn't happen with bracket orders)
                self.trailing_stop_state[instrument_key] = {
                    "entry_price": entry_price,
                    "highest_price": entry_price if event.side == PositionSide.LONG else None,
                    "lowest_price": entry_price if event.side == PositionSide.SHORT else None,
                    "current_sl_price": None,
                    "sl_order_id": None,
                    "activated": False,
                    "side": event.side.name,
                    "quantity": float(event.quantity),
                }
                self.log.info(
                    f"📊 Trailing stop initialized for {event.side.name} position @ ${entry_price:,.2f}"
                )

        # Send Telegram position opened notification
        if self.telegram_bot and self.enable_telegram and self.telegram_notify_positions:
            try:
                position_msg = self.telegram_bot.format_position_update({
                    'action': 'OPENED',
                    'side': event.side.name,
                    'quantity': float(event.quantity),
                    'entry_price': float(event.avg_px_open),
                    'current_price': float(event.avg_px_open),
                    'pnl': 0.0,
                    'pnl_pct': 0.0,
                })
                self.telegram_bot.send_message_sync(position_msg)
            except Exception as e:
                self.log.warning(f"Failed to send Telegram position opened notification: {e}")

    def on_position_closed(self, event):
        """Handle position closed events."""
        # PositionOpened event contains position data directly
        self.log.info(
            f"🔴 Position closed: {event.side.name} "
            f"P&L: {float(event.realized_pnl):.2f} USDT"
        )
        self._force_next_llm_reason = "position_closed"
        
        # Clear trailing stop state
        instrument_key = str(self.instrument_id)
        if instrument_key in self.trailing_stop_state:
            del self.trailing_stop_state[instrument_key]
            self.log.debug(f"🗑️ Cleared trailing stop state for {instrument_key}")

        self._cleanup_oco_orphans()
        
        # Send Telegram position closed notification
        if self.telegram_bot and self.enable_telegram and self.telegram_notify_positions:
            try:
                # Calculate P&L percentage (approximate)
                pnl = float(event.realized_pnl)
                # Get rough position size estimate for percentage
                # Note: This is approximate, actual calculation would require more data
                pnl_pct = (pnl / 100.0) * 100 if pnl != 0 else 0.0  # Rough estimate
                
                position_msg = self.telegram_bot.format_position_update({
                    'action': 'CLOSED',
                    'side': event.side.name,
                    'quantity': float(event.quantity) if hasattr(event, 'quantity') else 0.0,
                    'entry_price': float(event.avg_px_open) if hasattr(event, 'avg_px_open') else 0.0,
                    'current_price': float(event.avg_px_close) if hasattr(event, 'avg_px_close') else 0.0,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                })
                self.telegram_bot.send_message_sync(position_msg)
            except Exception as e:
                self.log.warning(f"Failed to send Telegram position closed notification: {e}")
    
    def _cleanup_oco_orphans(self):
        """
        Clean up orphan orders.

        This is a safety mechanism that runs periodically to:
        1. Cancel orphan reduce-only orders when no position exists

        Note: OCO group management is no longer needed as NautilusTrader handles it automatically.
        """
        try:
            # Get current positions
            positions = self.cache.positions_open(instrument_id=self.instrument_id)
            has_position = len(positions) > 0

            if not has_position:
                # No position but check for orphan orders
                open_orders = self.cache.orders_open(instrument_id=self.instrument_id)

                if open_orders:
                    orphan_count = 0
                    for order in open_orders:
                        if order.is_reduce_only:
                            # This is a reduce-only order without a position - orphan!
                            try:
                                self.cancel_order(order)
                                orphan_count += 1
                                self.log.info(
                                    f"🗑️ Cancelled orphan reduce-only order: "
                                    f"{str(order.client_order_id)[:8]}..."
                                )
                            except Exception as e:
                                self.log.error(
                                    f"Failed to cancel orphan order: {e}"
                                )

                    if orphan_count > 0:
                        self.log.warning(
                            f"⚠️ Cleaned up {orphan_count} orphan orders"
                        )

        except Exception as e:
            self.log.error(f"❌ Orphan order cleanup failed: {e}")
    
    def _update_trailing_stops(self, current_price: float):
        """
        Update trailing stop loss orders based on current price.
        
        Logic:
        1. Check if position is profitable enough to activate trailing stop
        2. Track highest price (LONG) or lowest price (SHORT)
        3. Update stop loss when price moves favorably beyond threshold
        4. Stop loss only moves in favorable direction, never backwards
        
        Parameters
        ----------
        current_price : float
            Current market price
        """
        try:
            instrument_key = str(self.instrument_id)
            
            # Check if we have trailing stop state for this instrument
            if instrument_key not in self.trailing_stop_state:
                return
            
            state = self.trailing_stop_state[instrument_key]
            entry_price = state["entry_price"]
            side = state["side"]
            activated = state["activated"]
            
            # Calculate profit percentage
            if side == "LONG":
                profit_pct = (current_price - entry_price) / entry_price
                
                # Update highest price
                if state["highest_price"] is None or current_price > state["highest_price"]:
                    state["highest_price"] = current_price
                
                highest_price = state["highest_price"]
                
                # Check if we should activate trailing stop
                if not activated and profit_pct >= self.trailing_activation_pct:
                    state["activated"] = True
                    self.log.info(
                        f"🎯 Trailing stop ACTIVATED for LONG @ ${current_price:,.2f} "
                        f"(Profit: {profit_pct*100:.2f}%)"
                    )
                    activated = True
                
                # If activated, check if we should update stop loss
                if activated:
                    # Calculate new stop loss based on highest price
                    new_sl_price = highest_price * (1 - self.trailing_distance_pct)
                    current_sl_price = state["current_sl_price"]
                    
                    # Only update if new SL is significantly higher than current
                    if current_sl_price is None:
                        should_update = True
                    else:
                        price_move_pct = (new_sl_price - current_sl_price) / current_sl_price
                        should_update = price_move_pct >= self.trailing_update_threshold_pct
                    
                    if should_update and new_sl_price > current_sl_price:
                        self._execute_trailing_stop_update(
                            instrument_key=instrument_key,
                            new_sl_price=new_sl_price,
                            current_price=current_price,
                            side="LONG"
                        )
            
            elif side == "SHORT":
                profit_pct = (entry_price - current_price) / entry_price
                
                # Update lowest price
                if state["lowest_price"] is None or current_price < state["lowest_price"]:
                    state["lowest_price"] = current_price
                
                lowest_price = state["lowest_price"]
                
                # Check if we should activate trailing stop
                if not activated and profit_pct >= self.trailing_activation_pct:
                    state["activated"] = True
                    self.log.info(
                        f"🎯 Trailing stop ACTIVATED for SHORT @ ${current_price:,.2f} "
                        f"(Profit: {profit_pct*100:.2f}%)"
                    )
                    activated = True
                
                # If activated, check if we should update stop loss
                if activated:
                    # Calculate new stop loss based on lowest price
                    new_sl_price = lowest_price * (1 + self.trailing_distance_pct)
                    current_sl_price = state["current_sl_price"]
                    
                    # Only update if new SL is significantly lower than current
                    if current_sl_price is None:
                        should_update = True
                    else:
                        price_move_pct = (current_sl_price - new_sl_price) / current_sl_price
                        should_update = price_move_pct >= self.trailing_update_threshold_pct
                    
                    if should_update and new_sl_price < current_sl_price:
                        self._execute_trailing_stop_update(
                            instrument_key=instrument_key,
                            new_sl_price=new_sl_price,
                            current_price=current_price,
                            side="SHORT"
                        )
                        
        except Exception as e:
            self.log.error(f"❌ Trailing stop update failed: {e}")
    
    def _execute_trailing_stop_update(
        self,
        instrument_key: str,
        new_sl_price: float,
        current_price: float,
        side: str
    ):
        """
        Execute the actual update of trailing stop loss order.
        
        Parameters
        ----------
        instrument_key : str
            Instrument identifier
        new_sl_price : float
            New stop loss price
        current_price : float
            Current market price
        side : str
            Position side (LONG/SHORT)
        """
        try:
            state = self.trailing_stop_state[instrument_key]
            old_sl_price = state["current_sl_price"]
            old_sl_order_id = state["sl_order_id"]
            quantity = state["quantity"]
            
            # Log the update
            if old_sl_price:
                move_pct = ((new_sl_price - old_sl_price) / old_sl_price) * 100
                self.log.info(
                    f"⬆️ Trailing Stop Update ({side}):\n"
                    f"   Current Price: ${current_price:,.2f}\n"
                    f"   Old SL: ${old_sl_price:,.2f}\n"
                    f"   New SL: ${new_sl_price:,.2f} ({move_pct:+.2f}%)\n"
                    f"   Distance: {abs((new_sl_price - current_price) / current_price * 100):.2f}%"
                )
            else:
                self.log.info(
                    f"📍 Initial Trailing Stop ({side}):\n"
                    f"   Current Price: ${current_price:,.2f}\n"
                    f"   SL Price: ${new_sl_price:,.2f}\n"
                    f"   Distance: {abs((new_sl_price - current_price) / current_price * 100):.2f}%"
                )
            
            # Cancel old stop loss order if it exists
            if old_sl_order_id:
                try:
                    from nautilus_trader.model.identifiers import ClientOrderId
                    old_order_id_obj = ClientOrderId(old_sl_order_id)
                    old_order = self.cache.order(old_order_id_obj)
                    
                    if old_order and old_order.is_open:
                        self.cancel_order(old_order)
                        self.log.debug(f"🔴 Cancelled old SL order: {old_sl_order_id[:8]}...")
                except Exception as e:
                    self.log.warning(f"⚠️ Failed to cancel old SL order: {e}")
            
            # Submit new stop loss order
            exit_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
            
            new_sl_order = self.order_factory.stop_market(
                instrument_id=self.instrument_id,
                order_side=exit_side,
                quantity=self.instrument.make_qty(quantity),
                trigger_price=self.instrument.make_price(new_sl_price),
                trigger_type=TriggerType.LAST_PRICE,
                emulation_trigger=TriggerType.LAST_PRICE,  # Emulate locally, not native STOP_MARKET
                reduce_only=True,
            )
            self.submit_order(new_sl_order)
            
            # Update state
            state["current_sl_price"] = new_sl_price
            state["sl_order_id"] = str(new_sl_order.client_order_id)

            self.log.info(f"✅ New trailing SL order submitted @ ${new_sl_price:,.2f}")

            # Note: OCO relationship is handled automatically by NautilusTrader
            # When the new SL is submitted, it will be linked to the existing TP orders

        except Exception as e:
            self.log.error(f"❌ Failed to execute trailing stop update: {e}")
    
    # ===== Remote Control Methods (for Telegram commands) =====
    
    def handle_telegram_command(self, command: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle Telegram commands.
        
        Parameters
        ----------
        command : str
            Command name (status, position, pause, resume)
        args : dict
            Command arguments
        
        Returns
        -------
        dict
            Response with 'success', 'message', and optional 'error'
        """
        try:
            if command == 'status':
                return self._cmd_status()
            elif command == 'position':
                return self._cmd_position()
            elif command == 'pause':
                return self._cmd_pause()
            elif command == 'resume':
                return self._cmd_resume()
            else:
                return {
                    'success': False,
                    'error': f"Unknown command: {command}"
                }
        except Exception as e:
            self.log.error(f"Error handling command '{command}': {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _cmd_status(self) -> Dict[str, Any]:
        """Handle /status command."""
        try:
            from datetime import datetime
            
            # Get current price
            current_price = 0
            bars = self.indicator_manager.recent_bars if hasattr(self, 'indicator_manager') else []
            if bars:
                current_price = float(bars[-1].close)
            
            # Get unrealized PnL
            unrealized_pnl = 0
            positions = self.cache.positions_open(instrument_id=self.instrument_id)
            if positions:
                position = positions[0]
                if current_price > 0:
                    unrealized_pnl = float(position.unrealized_pnl(current_price))
            
            # Calculate uptime
            uptime_str = "N/A"
            if self.strategy_start_time:
                uptime_delta = datetime.utcnow() - self.strategy_start_time
                hours = uptime_delta.total_seconds() // 3600
                minutes = (uptime_delta.total_seconds() % 3600) // 60
                uptime_str = f"{int(hours)}h {int(minutes)}m"
            
            # Get last signal
            last_signal = "N/A"
            last_signal_time = "N/A"
            if hasattr(self, 'last_signal') and self.last_signal:
                last_signal = f"{self.last_signal.get('signal', 'N/A')} ({self.last_signal.get('confidence', 'N/A')})"
                # You could store timestamp if needed
            
            status_info = {
                'is_running': True,  # If this method is called, strategy is running
                'is_paused': self.is_trading_paused,
                'instrument_id': str(self.instrument_id),
                'current_price': current_price,
                'equity': self.equity,
                'unrealized_pnl': unrealized_pnl,
                'last_signal': last_signal,
                'last_signal_time': last_signal_time,
                'uptime': uptime_str,
            }
            
            message = self.telegram_bot.format_status_response(status_info) if self.telegram_bot else "Status unavailable"
            
            return {
                'success': True,
                'message': message
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _cmd_position(self) -> Dict[str, Any]:
        """Handle /position command."""
        try:
            # Get current position
            current_position = self._get_current_position_data()
            
            position_info: Dict[str, Any] = {
                'has_position': current_position is not None,
            }
            
            if current_position:
                bars = self.indicator_manager.recent_bars if hasattr(self, 'indicator_manager') else []
                current_price = float(bars[-1].close) if bars else current_position['avg_px']
                
                entry_price = current_position['avg_px']
                pnl = current_position['unrealized_pnl']
                pnl_pct = (pnl / (entry_price * current_position['quantity'])) * 100 if entry_price > 0 else 0
                
                position_info.update({
                    'side': current_position['side'].upper(),
                    'quantity': current_position['quantity'],
                    'entry_price': entry_price,
                    'current_price': current_price,
                    'unrealized_pnl': pnl,
                    'pnl_pct': pnl_pct,
                    # SL/TP prices would need to be tracked separately if needed
                })
            
            message = self.telegram_bot.format_position_response(position_info) if self.telegram_bot else "Position unavailable"
            
            return {
                'success': True,
                'message': message
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _cmd_pause(self) -> Dict[str, Any]:
        """Handle /pause command."""
        try:
            if self.is_trading_paused:
                message = self.telegram_bot.format_pause_response(False, "Trading is already paused") if self.telegram_bot else "Already paused"
            else:
                self.is_trading_paused = True
                self.log.info("⏸️ Trading paused by Telegram command")
                message = self.telegram_bot.format_pause_response(True) if self.telegram_bot else "Trading paused"
            
            return {
                'success': True,
                'message': message
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _cmd_resume(self) -> Dict[str, Any]:
        """Handle /resume command."""
        try:
            if not self.is_trading_paused:
                message = self.telegram_bot.format_resume_response(False, "Trading is not paused") if self.telegram_bot else "Not paused"
            else:
                self.is_trading_paused = False
                self.log.info("▶️ Trading resumed by Telegram command")
                message = self.telegram_bot.format_resume_response(True) if self.telegram_bot else "Trading resumed"
            
            return {
                'success': True,
                'message': message
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
