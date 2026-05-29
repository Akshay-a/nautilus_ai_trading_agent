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
    leverage: float = 10.0

    # Position sizing
    base_usdt_amount: float = 100.0
    fixed_trade_usdt: float = 0.0  # If > 0, use fixed notional target per entry/reversal
    high_confidence_multiplier: float = 1.5
    medium_confidence_multiplier: float = 1.0
    low_confidence_multiplier: float = 0.5
    max_position_ratio: float = 0.10
    trend_strength_multiplier: float = 1.2
    min_trade_amount: float = 0.001

    # Technical indicators
    sma_periods: Tuple[int, ...] = (5, 20, 50)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    bb_period: int = 20
    bb_std: float = 2.0

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
    allow_reversals: bool = True
    require_high_confidence_for_reversal: bool = False
    rsi_extreme_threshold_upper: float = 75.0
    rsi_extreme_threshold_lower: float = 25.0
    rsi_extreme_multiplier: float = 0.7
    
    # Stop Loss & Take Profit
    enable_auto_sl_tp: bool = True
    sl_use_support_resistance: bool = True
    sl_buffer_pct: float = 0.001
    tp_high_confidence_pct: float = 0.008
    tp_medium_confidence_pct: float = 0.005
    tp_low_confidence_pct: float = 0.003
    
    # OCO (One-Cancels-the-Other)
    enable_oco: bool = True
    oco_redis_host: str = "localhost"
    oco_redis_port: int = 6379
    oco_redis_db: int = 0
    oco_redis_password: Optional[str] = None
    oco_group_ttl_hours: int = 24
    
    # Trailing Stop Loss
    enable_trailing_stop: bool = True
    trailing_activation_pct: float = 0.003
    trailing_distance_pct: float = 0.002
    trailing_update_threshold_pct: float = 0.001
    
    # Partial Take Profit
    enable_partial_tp: bool = True
    partial_tp_levels: Tuple[Dict[str, float], ...] = (
        {"profit_pct": 0.004, "position_pct": 0.5},
        {"profit_pct": 0.008, "position_pct": 0.5},
    )
    
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
        self.allow_reversals = config.allow_reversals
        self.require_high_conf_reversal = config.require_high_confidence_for_reversal
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
                self.log.info(f"Decision trade journal enabled: {self.trade_journal_path}")
            except Exception as e:
                self.trade_journal_enabled = False
                self.log.warning(f"Failed to initialize trade journal: {e}")

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

        if self.enable_oco and self.oco_manager:
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

        # --- Give-back protection circuit breaker ---
        # If a profitable position has given back >60% of peak profit,
        # auto-exit without consulting the LLM (saves latency + prevents further loss).
        if current_position:
            health = current_position.get("position_health") or {}
            giveback = health.get("giveback_pct", 0)
            peak_pnl = health.get("peak_unrealized_pnl", 0)
            if peak_pnl > 5.0 and giveback > 60:
                self.log.warning(
                    f"🛡️ GIVE-BACK PROTECTION: peak uPnL was ${peak_pnl:.2f}, "
                    f"now given back {giveback:.0f}%. Auto-closing position."
                )
                close_side = (
                    OrderSide.SELL if current_position["side"] == "long" else OrderSide.BUY
                )
                qty = current_position["quantity"]
                self._submit_order(side=close_side, quantity=qty, reduce_only=True)

                gb_exec = {
                    "status": "submitted",
                    "action": "giveback_protection_exit",
                    "target_side": "flat",
                    "target_quantity": qty,
                    "note": f"auto_exit_giveback_{giveback:.0f}pct_peak_{peak_pnl:.2f}",
                }
                fb = self.deepseek._emit_fallback(price_data)
                fb["signal"] = "SELL" if current_position["side"] == "long" else "BUY"
                fb["confidence"] = "HIGH"
                fb["thesis"] = f"Give-back protection: peak uPnL ${peak_pnl:.2f}, gave back {giveback:.0f}%"
                fb["reasoning_content"] = "circuit_breaker:giveback_protection"
                self.last_signal = fb

                position_after = self._get_current_position_data()
                self._append_trade_journal_row(
                    signal_data=fb,
                    price_data=price_data,
                    technical_data=technical_data,
                    microstructure_data=microstructure_data,
                    risk_context=risk_context,
                    current_position=current_position,
                    position_after=position_after,
                    execution_summary=gb_exec,
                    bar_close_iso=price_data.get("bar_close_ts_utc"),
                    execution_ts_iso=datetime.utcnow().isoformat(),
                    decision_ts_iso=datetime.utcnow().isoformat(),
                    latency_ms=0,
                    decision_trigger="giveback_protection",
                )
                return

        execution_summary = {"status": "error", "action": "none", "note": "pre_llm"}

        signal_data = None

        try:
            import time as _time

            self.log.info("Calling DeepSeek AI (bar-aligned synthesis)...")
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

        recommendation = "hold"
        if giveback_pct > 60:
            recommendation = "exit_now"
        elif giveback_pct > 40:
            recommendation = "consider_exit"
        elif profit_pct > 0.3:
            recommendation = "consider_taking_profit"
        elif profit_pct < -0.5:
            recommendation = "consider_cutting_loss"

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
            self.log.info("⏸️ Trading is paused - skipping signal execution")
            return {"status": "skipped", "action": "none", "note": "trading_paused"}
        
        # Store signal and technical data for SL/TP calculation
        self.latest_signal_data = signal_data
        self.latest_technical_data = technical_data
        self.latest_price_data = price_data
        
        signal = signal_data['signal']
        confidence = signal_data['confidence']

        # Check minimum confidence
        confidence_levels = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
        min_conf_level = confidence_levels.get(self.min_confidence, 1)
        signal_conf_level = confidence_levels.get(confidence, 1)

        if signal_conf_level < min_conf_level:
            self.log.warning(
                f"⚠️ Signal confidence {confidence} below minimum {self.min_confidence}, skipping trade"
            )
            return {
                "status": "skipped",
                "action": "none",
                "note": f"confidence_below_min:{confidence}<{self.min_confidence}",
            }

        partial_execution = self._apply_partial_close_from_signal(
            signal_data=signal_data,
            current_position=current_position,
        )
        if partial_execution is not None:
            return partial_execution

        # Handle HOLD signal
        if signal == 'HOLD':
            self.log.info("📊 Signal: HOLD - No action taken")
            return {"status": "hold", "action": "none", "note": "hold_signal"}

        # Calculate target position size
        target_quantity = self._calculate_position_size(
            signal_data, price_data, technical_data, current_position, risk_context
        )

        if target_quantity == 0:
            self.log.warning("⚠️ Calculated position size is 0, skipping trade")
            return {"status": "skipped", "action": "none", "note": "zero_target_quantity"}

        # Determine order side
        target_side = 'long' if signal == 'BUY' else 'short'

        # Execute position management logic
        if current_position:
            self._manage_existing_position(
                current_position, target_side, target_quantity, confidence
            )
            return {
                "status": "submitted",
                "action": "manage_existing",
                "target_side": target_side,
                "target_quantity": target_quantity,
                "note": "order_logic_dispatched",
            }
        else:
            self._open_new_position(target_side, target_quantity)
            return {
                "status": "submitted",
                "action": "open_new",
                "target_side": target_side,
                "target_quantity": target_quantity,
                "note": "order_logic_dispatched",
            }

    def _apply_partial_close_from_signal(
        self,
        signal_data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Execute partial position reduction when LLM provides partial_close_pct.

        Works for both:
        - HOLD + partial_close_pct (scale out while holding bias)
        - Opposite bias + partial_close_pct (reduce existing exposure, avoid full reversal)
        """
        if not current_position:
            return None

        raw = signal_data.get("partial_close_pct")
        if raw is None:
            return None

        try:
            pct = float(raw)
        except (TypeError, ValueError):
            self.log.warning("⚠️ Ignoring invalid partial_close_pct from LLM")
            return None

        if pct <= 0:
            return None
        pct = min(1.0, pct)

        current_qty = float(current_position.get("quantity") or 0.0)
        if current_qty <= 0:
            return None

        reduce_qty = self._normalize_order_quantity(current_qty * pct)
        if reduce_qty is None:
            return {
                "status": "skipped",
                "action": "partial_close",
                "note": "partial_close_qty_below_increment",
            }

        reduce_qty = min(reduce_qty, current_qty)
        exit_side = (
            OrderSide.SELL if current_position.get("side") == "long" else OrderSide.BUY
        )
        self._submit_order(side=exit_side, quantity=reduce_qty, reduce_only=True)
        self.log.info(
            f"✂️ Partial close from LLM: pct={pct:.2f}, qty={reduce_qty:.6g}/{current_qty:.6g} {self.base_asset}"
        )
        return {
            "status": "submitted",
            "action": "partial_close",
            "target_side": current_position.get("side"),
            "target_quantity": max(0.0, current_qty - reduce_qty),
            "note": f"partial_close_pct:{pct:.4f}",
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
        Calculate intelligent position size.

        Returns BTC quantity based on confidence, trend, and RSI.
        """
        # Treat fixed_trade_usdt as the base target, but still vary exposure by
        # confidence and risk conditions. This prevents the LLM confidence field
        # from being ignored during live/demo operation.
        base_usdt = self.fixed_trade_usdt if self.fixed_trade_usdt > 0 else self.base_usdt

        # Confidence multiplier
        conf_mult = self.position_config.get(
            f"{signal_data['confidence'].lower()}_confidence_multiplier",
            1.0
        )

        # Trend multiplier
        trend = technical_data.get('overall_trend', 'mixed')
        trend_mult = (
            self.position_config['trend_strength_multiplier']
            if trend in ['strong_up', 'strong_down']
            else 1.0
        )

        # RSI multiplier (reduce size in extreme RSI)
        rsi = technical_data.get('rsi', 50)
        rsi_mult = (
            self.rsi_extreme_mult
            if rsi > self.rsi_extreme_upper or rsi < self.rsi_extreme_lower
            else 1.0
        )

        suggested_usdt = base_usdt * conf_mult * trend_mult * rsi_mult
        sizing_reason = (
            f"Base:{base_usdt} × Conf:{conf_mult} × Trend:{trend_mult} × RSI:{rsi_mult}"
        )

        # Apply max position ratio limit
        account_equity = self._account_equity_for_sizing(risk_context)
        available_balance = self._available_balance_for_sizing(risk_context)
        max_usdt = account_equity * self.position_config['max_position_ratio']
        if available_balance is not None and available_balance > 0:
            max_usdt = min(max_usdt, available_balance * self.leverage * 0.95)
        final_usdt = min(suggested_usdt, max_usdt)

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

    def _manage_existing_position(
        self,
        current_position: Dict[str, Any],
        target_side: str,
        target_quantity: float,
        confidence: str,
    ):
        """Manage existing position (add, reduce, or reverse)."""
        current_side = current_position['side']
        current_qty = current_position['quantity']

        # Same direction - adjust position
        if target_side == current_side:
            size_diff = target_quantity - current_qty
            threshold = self._position_adjustment_threshold()

            if abs(size_diff) < threshold:
                self.log.info(
                    f"✅ Position size appropriate ({current_qty:.6g} {self.base_asset}), no adjustment needed"
                )
                return

            order_quantity = self._normalize_order_quantity(abs(size_diff))
            if order_quantity is None:
                return

            if size_diff > 0:
                # Add to position
                self._submit_order(
                    side=OrderSide.BUY if target_side == 'long' else OrderSide.SELL,
                    quantity=order_quantity,
                    reduce_only=False,
                )
                self.log.info(
                    f"📈 Adding to {target_side} position: {order_quantity:.6g} {self.base_asset} "
                    f"({current_qty:.6g} → {target_quantity:.6g})"
                )
            else:
                # Reduce position
                self._submit_order(
                    side=OrderSide.SELL if target_side == 'long' else OrderSide.BUY,
                    quantity=order_quantity,
                    reduce_only=True,
                )
                self.log.info(
                    f"📉 Reducing {target_side} position: {order_quantity:.6g} {self.base_asset} "
                    f"({current_qty:.6g} → {target_quantity:.6g})"
                )

        # Opposite direction - reverse position
        elif self.allow_reversals:
            # Check if high confidence required for reversal
            if self.require_high_conf_reversal and confidence != 'HIGH':
                self.log.warning(
                    f"🔒 Reversal requires HIGH confidence, got {confidence}. "
                    f"Keeping {current_side} position."
                )
                return

            self.log.info(f"🔄 Reversing position: {current_side} → {target_side}")

            # Close current position
            self._submit_order(
                side=OrderSide.SELL if current_side == 'long' else OrderSide.BUY,
                quantity=current_qty,
                reduce_only=True,
            )

            # Open opposite position
            self._submit_order(
                side=OrderSide.BUY if target_side == 'long' else OrderSide.SELL,
                quantity=target_quantity,
                reduce_only=False,
            )

        else:
            self.log.warning(
                f"⚠️ Signal suggests {target_side} but have {current_side} position. "
                f"Reversals disabled."
            )

    def _open_new_position(self, side: str, quantity: float):
        """
        Open new position using bracket order (entry + SL + TP).

        This method submits a bracket order which automatically includes:
        - Entry order (MARKET)
        - Stop Loss order (STOP_MARKET)
        - Take Profit order(s) (LIMIT)

        The SL and TP orders are linked with OCO, so when one fills, the others cancel.
        """
        order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL

        # Submit bracket order with SL/TP
        self._submit_bracket_order(
            side=order_side,
            quantity=quantity,
        )

        self.log.info(
            f"🚀 Opening {side} position: {quantity:.6g} {self.base_asset} (with bracket SL/TP)"
        )

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
        - Entry order (MARKET)
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
            return
        quantity = normalized_quantity

        if quantity < self.position_config['min_trade_amount']:
            self.log.warning(
                f"⚠️ Order quantity {quantity:.6g} below minimum "
                f"{self.position_config['min_trade_amount']:.6g}, skipping"
            )
            return

        if self._is_dry_run():
            self.log.info(
                f"🧪 DRY RUN: Simulated bracket order {side.name} {quantity:.6g} {self.base_asset} "
                f"(entry + SL + TP not submitted)"
            )
            return

        if not self.enable_auto_sl_tp:
            self.log.warning("⚠️ Auto SL/TP is disabled - submitting simple market order instead")
            self._submit_order(side=side, quantity=quantity, reduce_only=False)
            return

        if not self.latest_signal_data or not self.latest_technical_data:
            self.log.warning("⚠️ No signal/technical data available for SL/TP - submitting simple market order")
            self._submit_order(side=side, quantity=quantity, reduce_only=False)
            return

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
            self.log.error("❌ Unable to determine entry price for bracket order, submitting market order instead")
            self._submit_order(side=side, quantity=quantity, reduce_only=False)
            return

        # Get confidence and technical data
        confidence = self.latest_signal_data.get('confidence', 'MEDIUM')
        support = self.latest_technical_data.get('support', 0.0)
        resistance = self.latest_technical_data.get('resistance', 0.0)

        # Calculate Stop Loss price
        if side == OrderSide.BUY:
            # BUY: Stop loss below support
            if self.sl_use_support_resistance and support > 0:
                stop_loss_price = support * (1 - self.sl_buffer_pct)
                self.log.info(f"📍 Using support level for SL: ${support:,.2f} → ${stop_loss_price:,.2f}")
            else:
                stop_loss_price = entry_price * 0.98  # Default 2% below entry
                self.log.info(f"📍 Using default 2% SL: ${stop_loss_price:,.2f}")
        else:
            # SELL: Stop loss above resistance
            if self.sl_use_support_resistance and resistance > 0:
                stop_loss_price = resistance * (1 + self.sl_buffer_pct)
                self.log.info(f"📍 Using resistance level for SL: ${resistance:,.2f} → ${stop_loss_price:,.2f}")
            else:
                stop_loss_price = entry_price * 1.02  # Default 2% above entry
                self.log.info(f"📍 Using default 2% SL: ${stop_loss_price:,.2f}")

        # Calculate Take Profit price (use first level for bracket order)
        # Note: Bracket orders support single TP. For multiple TPs, we'll submit additional orders after entry fills
        tp_pct = self.tp_pct_config.get(confidence, 0.02)
        if side == OrderSide.BUY:
            tp_price = entry_price * (1 + tp_pct)
        else:
            tp_price = entry_price * (1 - tp_pct)

        # Log SL/TP summary
        self.log.info(
            f"🎯 Creating bracket order for {side.name}:\n"
            f"   Entry: ~${entry_price:,.2f} (MARKET)\n"
            f"   Stop Loss: ${stop_loss_price:,.2f} ({((stop_loss_price/entry_price - 1) * 100):.2f}%)\n"
            f"   Take Profit: ${tp_price:,.2f} ({((tp_price/entry_price - 1) * 100):.2f}%)\n"
            f"   Quantity: {quantity:.6g} {self.base_asset}\n"
            f"   Confidence: {confidence}"
        )

        try:
            # Create bracket order using OrderFactory
            # This automatically creates entry + SL + TP with OTO/OCO linkage
            # IMPORTANT: Use emulation_trigger to enable order emulation for Binance compatibility
            # Binance doesn't support native OCO+OTO orders, so NautilusTrader will emulate them
            bracket_order_list = self.order_factory.bracket(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=self.instrument.make_qty(quantity),
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

        except Exception as e:
            self.log.error(f"❌ Failed to submit bracket order: {e}")
            self.log.warning("⚠️ Falling back to simple market order without SL/TP")
            self._submit_order(side=side, quantity=quantity, reduce_only=False)

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
        
        # Clear trailing stop state
        instrument_key = str(self.instrument_id)
        if instrument_key in self.trailing_stop_state:
            del self.trailing_stop_state[instrument_key]
            self.log.debug(f"🗑️ Cleared trailing stop state for {instrument_key}")
        
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
