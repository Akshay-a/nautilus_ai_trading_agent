"""
Technical Indicator Manager for NautilusTrader Strategy

Manages all technical indicators using NautilusTrader's built-in indicators.
"""

from typing import Any, Dict, List, Optional, Tuple
import statistics

from nautilus_trader.indicators import (
    AverageTrueRange,
    BollingerBands,
    DirectionalMovement,
    ExponentialMovingAverage,
    MovingAverageConvergenceDivergence,
    MovingAverageType,
    RelativeStrengthIndex,
    SimpleMovingAverage,
)
from nautilus_trader.model.data import Bar


class TechnicalIndicatorManager:
    """
    Manages technical indicators for strategy analysis.

    Uses NautilusTrader's built-in indicators for efficiency and consistency.
    """

    def __init__(
        self,
        sma_periods: List[int] = [5, 20, 50],
        ema_periods: List[int] = [12, 26],
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0,
        volume_ma_period: int = 20,
        support_resistance_lookback: int = 20,
        atr_period: int = 14,
        directional_movement_period: int = 14,
    ):
        """
        Initialize technical indicator manager.

        Parameters
        ----------
        sma_periods : List[int]
            Periods for Simple Moving Averages
        ema_periods : List[int]
            Periods for Exponential Moving Averages
        rsi_period : int
            Period for RSI
        macd_fast : int
            Fast period for MACD
        macd_slow : int
            Slow period for MACD
        macd_signal : int
            Signal period for MACD
        bb_period : int
            Period for Bollinger Bands
        bb_std : float
            Standard deviation multiplier for Bollinger Bands
        volume_ma_period : int
            Period for volume moving average
        support_resistance_lookback : int
            Lookback period for support/resistance calculation
        atr_period : int
            Period for Average True Range (Nautilus AverageTrueRange, Wilder-style MA).
        directional_movement_period : int
            Period for DirectionalMovement (+/- components); DX derived for trend strength.
        """
        # SMA indicators
        self.smas = {period: SimpleMovingAverage(period) for period in sma_periods}

        # EMA indicators (for MACD calculation reference)
        self.emas = {period: ExponentialMovingAverage(period) for period in ema_periods}

        # RSI
        self.rsi = RelativeStrengthIndex(rsi_period)

        # MACD
        self.macd = MovingAverageConvergenceDivergence(
            fast_period=macd_fast,
            slow_period=macd_slow,
        )
        self.macd_signal = ExponentialMovingAverage(macd_signal)

        # Bollinger Bands (Nautilus-native)
        self.bollinger = BollingerBands(bb_period, bb_std, MovingAverageType.SIMPLE)
        self.bb_period = bb_period
        self.bb_std = bb_std

        self.atr = AverageTrueRange(atr_period, MovingAverageType.WILDER)
        self.directional_movement = DirectionalMovement(
            directional_movement_period, MovingAverageType.WILDER
        )

        # Volume MA
        self.volume_sma = SimpleMovingAverage(volume_ma_period)
        self.volume_ma_period = volume_ma_period

        # Store recent bars for calculations
        self.recent_bars: List[Bar] = []
        self.max_bars = (
            max(
                list(sma_periods)
                + [
                    bb_period,
                    volume_ma_period,
                    support_resistance_lookback,
                    atr_period,
                    directional_movement_period,
                ]
            )
            + 10
        )

        # Configuration
        self.support_resistance_lookback = support_resistance_lookback
        self.atr_period = atr_period
        self.directional_movement_period = directional_movement_period
        # ADX = Wilder-smoothed DX; DX each bar uses Nautilus Wilder +/-DI from DirectionalMovement
        self._adx_wilder: Optional[float] = None
        self._dx_seed_buffer: List[float] = []
        self.sma_periods = sma_periods
        self.ema_periods = ema_periods
        self.rsi_period = rsi_period
        self.macd_slow_period = macd_slow
        self.macd_fast_period = macd_fast
        self.macd_signal_period = macd_signal

    def _compute_dmi_dx(self, pos: float, neg: float) -> float:
        """Directional Index (single-bar) from smoothed +DI/-DI."""
        total = pos + neg
        if total <= 1e-12:
            return 0.0
        return 100.0 * abs(pos - neg) / total

    def _update_adx_from_directional_movement(self) -> None:
        """
        Maintain ADX as Wilder's smoothed DX (same period as DirectionalMovement).

        DX uses Nautilus Wilder-smoothed pos/neg. First ADX is the SMA of the first
        `period` DX samples; thereafter ADX follows Wilder's recurrence.
        """
        if not self.directional_movement.initialized:
            return
        pos = float(self.directional_movement.pos)
        neg = float(self.directional_movement.neg)
        dx = self._compute_dmi_dx(pos, neg)
        period = self.directional_movement_period
        if self._adx_wilder is None:
            self._dx_seed_buffer.append(dx)
            if len(self._dx_seed_buffer) >= period:
                self._adx_wilder = sum(self._dx_seed_buffer) / period
                self._dx_seed_buffer.clear()
        else:
            self._adx_wilder = (
                self._adx_wilder * (period - 1.0) + dx
            ) / period

    def update(self, bar: Bar):
        """
        Update all indicators with new bar data.

        Parameters
        ----------
        bar : Bar
            New bar data
        """
        # Store bar for manual calculations
        self.recent_bars.append(bar)
        if len(self.recent_bars) > self.max_bars:
            self.recent_bars.pop(0)

        h = float(bar.high)
        l = float(bar.low)
        c = float(bar.close)

        # Update SMA indicators
        for sma in self.smas.values():
            sma.update_raw(c)

        # Update EMA indicators
        for ema in self.emas.values():
            ema.update_raw(c)

        # Update RSI
        self.rsi.update_raw(c)

        # Update MACD
        self.macd.update_raw(c)
        self.macd_signal.update_raw(self.macd.value)

        self.bollinger.update_raw(h, l, c)
        self.atr.update_raw(h, l, c)
        self.directional_movement.update_raw(h, l)
        self._update_adx_from_directional_movement()

        # Update Volume SMA
        self.volume_sma.update_raw(float(bar.volume))

    def get_technical_data(self, current_price: float) -> Dict[str, Any]:
        """
        Get all technical indicator values.

        Parameters
        ----------
        current_price : float
            Current market price

        Returns
        -------
        Dict
            Dictionary containing all technical indicator values
        """
        # Basic SMA values
        sma_values = {f'sma_{period}': self.smas[period].value for period in self.sma_periods}

        # EMA values
        ema_values = {f'ema_{period}': self.emas[period].value for period in self.ema_periods}

        # RSI (convert from 0-1 scale to 0-100 scale)
        rsi_value = self.rsi.value * 100

        # MACD
        macd_value = self.macd.value
        macd_signal_value = self.macd_signal.value  # Signal line from MACD indicator

        # Bollinger Bands (from Nautilus BollingerBands)
        bb_upper = float(self.bollinger.upper)
        bb_middle = float(self.bollinger.middle)
        bb_lower = float(self.bollinger.lower)
        bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5

        atr_val = float(self.atr.value) if self.atr.initialized else 0.0
        atr_pct = (atr_val / current_price) * 100.0 if current_price else 0.0

        dmi_pos = float(self.directional_movement.pos) if self.directional_movement.initialized else 0.0
        dmi_neg = float(self.directional_movement.neg) if self.directional_movement.initialized else 0.0
        dmi_dx = (
            self._compute_dmi_dx(dmi_pos, dmi_neg)
            if self.directional_movement.initialized
            else 0.0
        )
        adx_val = (
            float(self._adx_wilder) if self._adx_wilder is not None else 0.0
        )

        # Volume analysis
        volume_ma = self.volume_sma.value
        current_volume = float(self.recent_bars[-1].volume) if self.recent_bars else 0
        volume_ratio = current_volume / volume_ma if volume_ma > 0 else 1.0
        # Relative volume alias (backward-compatible name)
        rvol = volume_ratio

        volumes_tail = [
            float(b.volume) for b in self.recent_bars[-max(self.volume_ma_period + 5, 15) :]
        ]
        volume_zscore, volume_trend_slope = self._volume_zscore_and_slope(volumes_tail, current_volume)

        directional_volume_confirmation, volume_regime = self._classify_directional_volume(
            current_volume, volume_ma, volume_zscore
        )

        # Support and Resistance
        support, resistance = self._calculate_support_resistance()

        # Trend analysis
        trend_data = self._analyze_trend(
            current_price, sma_values, macd_value, macd_signal_value
        )

        # Combine all data
        technical_data = {
            # SMAs
            **sma_values,
            # EMAs
            **ema_values,
            # RSI
            "rsi": rsi_value,
            # MACD
            "macd": macd_value,
            "macd_signal": macd_signal_value,
            "macd_histogram": macd_value - macd_signal_value,
            "atr": atr_val,
            "atr_pct": round(atr_pct, 6),
            "dmi_pos": dmi_pos,
            "dmi_neg": dmi_neg,
            "dmi_dx": round(dmi_dx, 6),
            "adx": round(adx_val, 6),
            # Bollinger Bands
            "bb_upper": bb_upper,
            "bb_middle": bb_middle,
            "bb_lower": bb_lower,
            "bb_position": bb_position,
            # Volume (legacy ratio + regime features)
            "volume_ratio": volume_ratio,
            "rvol": rvol,
            "volume_zscore": volume_zscore,
            "volume_trend_slope": volume_trend_slope,
            "directional_volume_confirmation": directional_volume_confirmation,
            "volume_regime": volume_regime,
            # Support/Resistance
            "support": support,
            "resistance": resistance,
            # Trend analysis
            **trend_data,
        }

        return technical_data

    def _volume_zscore_and_slope(
        self,
        volumes: List[float],
        current_volume: float,
    ) -> Tuple[float, float]:
        """Z-score vs recent-volume distribution and OLS slope of last few bars."""
        if len(volumes) < 3:
            return 0.0, 0.0
        hist = volumes[:-1] if len(volumes) > 1 else volumes
        if len(hist) < 2:
            return 0.0, 0.0
        try:
            mu = statistics.mean(hist)
            sigma = statistics.pstdev(hist)
        except statistics.StatisticsError:
            return 0.0, 0.0
        z = (current_volume - mu) / sigma if sigma > 1e-12 else 0.0

        tail = volumes[-min(12, len(volumes)) :]
        if len(tail) < 2:
            return z, 0.0
        xs = list(range(len(tail)))
        x_mean = statistics.mean(xs)
        y_mean = statistics.mean(tail)
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, tail))
        den = sum((x - x_mean) ** 2 for x in xs)
        slope = num / den if den > 1e-18 else 0.0
        return round(z, 4), round(slope, 6)

    def _classify_directional_volume(
        self,
        current_volume: float,
        volume_ma: float,
        volume_zscore: float,
    ) -> Tuple[str, str]:
        """Directional confirmation label + coarse volume regime."""
        if not self.recent_bars:
            return "insufficient_history", "normal"

        bar = self.recent_bars[-1]
        o = float(bar.open)
        cl = float(bar.close)
        body_dir = 0
        if cl > o:
            body_dir = 1
        elif cl < o:
            body_dir = -1

        vma = volume_ma if volume_ma > 0 else 1.0
        vol_excess = (current_volume - vma) / vma

        confirmation = "neutral"
        if body_dir > 0 and vol_excess > 0.25:
            confirmation = "bullish_volume_confirmed"
        elif body_dir < 0 and vol_excess > 0.25:
            confirmation = "bearish_volume_confirmed"
        elif body_dir > 0 and vol_excess < -0.2:
            confirmation = "up_move_weak_volume"
        elif body_dir < 0 and vol_excess < -0.2:
            confirmation = "down_move_weak_volume"

        zs = float(volume_zscore)
        if zs < -0.75:
            regime = "low"
        elif zs < 1.25:
            regime = "normal"
        elif zs < 2.5:
            regime = "high"
        else:
            regime = "climactic"

        if body_dir == 0 and regime == "climactic":
            regime = "high"

        return confirmation, regime

    def _calculate_support_resistance(self) -> tuple:
        """Calculate support and resistance levels."""
        if len(self.recent_bars) < self.support_resistance_lookback:
            return 0.0, 0.0

        recent = self.recent_bars[-self.support_resistance_lookback:]
        support = min(float(bar.low) for bar in recent)
        resistance = max(float(bar.high) for bar in recent)

        return support, resistance

    def _analyze_trend(
        self,
        current_price: float,
        sma_values: Dict[str, float],
        macd_value: float,
        macd_signal_value: float,
    ) -> Dict[str, Any]:
        """
        Analyze market trend using multiple indicators.

        Returns
        -------
        Dict
            Trend analysis data
        """
        sma_20 = sma_values.get('sma_20', current_price)
        sma_50 = sma_values.get('sma_50', current_price)

        short_term_trend = "up" if current_price > sma_20 else "down"
        medium_term_trend = "up" if current_price > sma_50 else "down"
        macd_trend = "bullish" if macd_value > macd_signal_value else "bearish"

        if short_term_trend == "up" and medium_term_trend == "up":
            overall_trend = "strong_up"
        elif short_term_trend == "down" and medium_term_trend == "down":
            overall_trend = "strong_down"
        else:
            overall_trend = "mixed"

        return {
            'short_term_trend': short_term_trend,
            'medium_term_trend': medium_term_trend,
            'macd_trend': macd_trend,
            'overall_trend': overall_trend,
        }

    def is_initialized(self) -> bool:
        """Check if indicators have enough data to be valid."""
        # Check if we have minimum bars for key indicators
        # Use dynamic calculation based on actual indicator periods
        min_required_bars = max(
            self.rsi_period,
            self.macd_slow_period,
            self.bb_period,
            self.atr_period,
            self.directional_movement_period,
            min(self.sma_periods) if self.sma_periods else 0,
        )
        
        if len(self.recent_bars) < min_required_bars:
            return False

        # Check if key indicators are initialized
        if not self.rsi.initialized:
            return False

        if not self.macd.initialized:
            return False

        if not self.bollinger.initialized:
            return False

        if not self.atr.initialized:
            return False

        if not self.directional_movement.initialized:
            return False

        # Check if we have at least one SMA initialized (for trend analysis)
        if not any(sma.initialized for sma in self.smas.values()):
            return False

        return True

    def get_kline_data(self, count: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent K-line data for analysis.

        Parameters
        ----------
        count : int
            Number of recent bars to return

        Returns
        -------
        List[Dict]
            List of K-line data dictionaries
        """
        if not self.recent_bars:
            return []

        kline_data = []
        for bar in self.recent_bars[-count:]:
            kline_data.append({
                'timestamp': bar.ts_init,
                'open': float(bar.open),
                'high': float(bar.high),
                'low': float(bar.low),
                'close': float(bar.close),
                'volume': float(bar.volume),
            })

        return kline_data
