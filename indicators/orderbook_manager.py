"""
Order Book Feature Manager for NautilusTrader Strategy  (Phase 2)

Collects L2 depth snapshots and trade ticks, computes microstructure
features, detects sweeps, classifies depth regime, and provides
feature-dump + IC-analysis utilities.

Feature groups
  2a  spread / depth / microprice
  2b  OFI, queue pressure
  2c  trade-flow, sweeps, VWAP deviation, spread volatility, depth regime
"""

from __future__ import annotations

import csv
import math
import os
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Lightweight data containers
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DepthSnapshot:
    """Flattened representation of one OrderBookDepth10 update."""
    ts_event: int
    bid_prices: List[float] = field(default_factory=list)
    bid_sizes: List[float] = field(default_factory=list)
    ask_prices: List[float] = field(default_factory=list)
    ask_sizes: List[float] = field(default_factory=list)
    bid_counts: List[int] = field(default_factory=list)
    ask_counts: List[int] = field(default_factory=list)


@dataclass(slots=True)
class TradeRecord:
    """Flattened representation of one TradeTick."""
    ts_event: int
    price: float
    size: float
    is_buyer_aggressor: bool


@dataclass(slots=True)
class MicrostructureFeatures:
    """Full feature vector computed from one depth snapshot + trade window."""
    ts: int

    # -- 2a: spread / depth / microprice --
    best_bid: float
    best_ask: float
    mid: float
    spread_abs: float
    spread_bps: float
    microprice: float
    tob_imbalance: float
    total_bid_depth: float
    total_ask_depth: float
    depth_imbalance: float
    weighted_depth_imbalance: float
    avg_bid_orders_per_level: float
    avg_ask_orders_per_level: float

    # -- 2b: OFI / queue pressure --
    ofi: float
    # Queue pressure: bid-side depth within 0.1% of best bid vs ask-side
    queue_pressure: float

    # -- 2c: trade-flow / sweeps / VWAP / spread vol / regime --
    buy_volume: float
    sell_volume: float
    trade_flow_imbalance: float
    trade_count_buy: int
    trade_count_sell: int
    sweep_buy_count: int
    sweep_sell_count: int
    sweep_buy_volume: float
    sweep_sell_volume: float
    recent_vwap: float
    vwap_deviation_bps: float
    spread_volatility: float
    depth_regime: str      # "thin" / "normal" / "thick"


# ---------------------------------------------------------------------------
# Aggressor side helper
# ---------------------------------------------------------------------------

def _is_buyer(aggressor_side) -> bool:
    """
    Robust buyer-aggressor check.

    Nautilus AggressorSide is a Rust-backed enum. Depending on version
    and binding layer, ``str()`` may return ``"BUYER"`` or ``"1"``.
    The enum's integer value for BUYER is 1.
    """
    try:
        val = int(aggressor_side)
        return val == 1
    except (TypeError, ValueError):
        pass
    s = str(aggressor_side).upper()
    return s in ("BUYER", "BUY")


# ---------------------------------------------------------------------------
# Main manager
# ---------------------------------------------------------------------------

class OrderBookManager:
    """
    Ingests OrderBookDepth10 / managed-book snapshots and TradeTick
    events, maintains rolling windows, computes micro-structure features
    on each update, and provides utilities for feature dump and IC
    analysis.
    """

    # Sweep detection: a single trade > SWEEP_SIZE_MULT * rolling median
    SWEEP_SIZE_MULT = 5.0
    SWEEP_LOOKBACK = 200

    # Queue pressure: near-touch depth window
    QUEUE_PRESSURE_BPS = 10  # 0.10%
    QUEUE_PRESSURE_MAX_LEVELS = 3  # hard cap to avoid collapsing to full-depth imbalance

    # Spread volatility rolling window (in depth updates, not nanos)
    SPREAD_VOL_WINDOW = 60

    # Depth regime thresholds (vs rolling median of total depth)
    DEPTH_THIN_MULT = 0.5
    DEPTH_THICK_MULT = 1.5
    DEPTH_REGIME_WINDOW = 120

    def __init__(
        self,
        depth_buffer_size: int = 300,
        trade_buffer_size: int = 5000,
        feature_buffer_size: int = 500,
        depth_levels: int = 10,
        ema_alpha: float = 0.05,
        trade_window_ns: int = 300_000_000_000,
    ):
        self.depth_levels = depth_levels

        self._depth_buf: deque[DepthSnapshot] = deque(maxlen=depth_buffer_size)
        self._trade_buf: deque[TradeRecord] = deque(maxlen=trade_buffer_size)
        self._feature_buf: deque[MicrostructureFeatures] = deque(maxlen=feature_buffer_size)

        # EMA state
        self._ema_alpha = ema_alpha
        self._ema_ofi: float = 0.0
        self._ema_spread_bps: float = 0.0
        self._ema_tob_imbalance: float = 0.0
        self._ema_depth_imbalance: float = 0.0

        self._trade_window_ns = trade_window_ns

        # Rolling buffers for derived stats
        self._spread_history: deque[float] = deque(maxlen=self.SPREAD_VOL_WINDOW)
        self._total_depth_history: deque[float] = deque(maxlen=self.DEPTH_REGIME_WINDOW)

        # Counters
        self.depth_updates_received: int = 0
        self.trade_ticks_received: int = 0

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def update_depth(self, depth) -> None:
        """Process an OrderBookDepth10."""
        snap = self._flatten_depth10(depth)
        self._ingest_snapshot(snap)

    def update_from_managed_book(self, book, ts_event: int = 0) -> None:
        """Extract top-N levels from a Nautilus managed OrderBook (cache)."""
        snap = self._flatten_managed_book(book, ts_event)
        self._ingest_snapshot(snap)

    def _ingest_snapshot(self, snap: DepthSnapshot) -> None:
        self._depth_buf.append(snap)
        self.depth_updates_received += 1

        features = self._compute_features(snap)
        self._feature_buf.append(features)
        self._update_emas(features)

        self._spread_history.append(features.spread_bps)
        self._total_depth_history.append(
            features.total_bid_depth + features.total_ask_depth
        )

    def update_trade(self, tick) -> None:
        """Process a TradeTick."""
        rec = TradeRecord(
            ts_event=tick.ts_event,
            price=float(tick.price),
            size=float(tick.size),
            is_buyer_aggressor=_is_buyer(tick.aggressor_side),
        )
        self._trade_buf.append(rec)
        self.trade_ticks_received += 1

    # ------------------------------------------------------------------
    # Flattening
    # ------------------------------------------------------------------

    def _flatten_depth10(self, depth) -> DepthSnapshot:
        bid_px, bid_sz, ask_px, ask_sz = [], [], [], []
        bid_cnt: List[int] = []
        ask_cnt: List[int] = []

        for i in range(min(self.depth_levels, len(depth.bids))):
            bid_px.append(float(depth.bids[i][0]))
            bid_sz.append(float(depth.bids[i][1]))
        for i in range(min(self.depth_levels, len(depth.asks))):
            ask_px.append(float(depth.asks[i][0]))
            ask_sz.append(float(depth.asks[i][1]))
        for i in range(min(self.depth_levels, len(depth.bid_counts))):
            bid_cnt.append(int(depth.bid_counts[i]))
        for i in range(min(self.depth_levels, len(depth.ask_counts))):
            ask_cnt.append(int(depth.ask_counts[i]))

        return DepthSnapshot(
            ts_event=depth.ts_event,
            bid_prices=bid_px, bid_sizes=bid_sz,
            ask_prices=ask_px, ask_sizes=ask_sz,
            bid_counts=bid_cnt, ask_counts=ask_cnt,
        )

    def _flatten_managed_book(self, book, ts_event: int = 0) -> DepthSnapshot:
        bid_px, bid_sz, ask_px, ask_sz = [], [], [], []
        bid_cnt: List[int] = []
        ask_cnt: List[int] = []

        try:
            for i, lvl in enumerate(book.bids()):
                if i >= self.depth_levels:
                    break
                bid_px.append(float(lvl.price))
                bid_sz.append(float(lvl.size()))
                orders = lvl.orders() if hasattr(lvl, "orders") else []
                bid_cnt.append(len(orders) if orders else 1)
        except Exception:
            pass

        try:
            for i, lvl in enumerate(book.asks()):
                if i >= self.depth_levels:
                    break
                ask_px.append(float(lvl.price))
                ask_sz.append(float(lvl.size()))
                orders = lvl.orders() if hasattr(lvl, "orders") else []
                ask_cnt.append(len(orders) if orders else 1)
        except Exception:
            pass

        if ts_event == 0:
            ts_event = getattr(book, "ts_last", 0) or 0

        return DepthSnapshot(
            ts_event=ts_event,
            bid_prices=bid_px, bid_sizes=bid_sz,
            ask_prices=ask_px, ask_sizes=ask_sz,
            bid_counts=bid_cnt, ask_counts=ask_cnt,
        )

    # ------------------------------------------------------------------
    # Feature computation
    # ------------------------------------------------------------------

    def _compute_features(self, snap: DepthSnapshot) -> MicrostructureFeatures:

        best_bid = snap.bid_prices[0] if snap.bid_prices else 0.0
        best_ask = snap.ask_prices[0] if snap.ask_prices else 0.0
        best_bid_sz = snap.bid_sizes[0] if snap.bid_sizes else 0.0
        best_ask_sz = snap.ask_sizes[0] if snap.ask_sizes else 0.0

        mid = (best_bid + best_ask) / 2.0 if (best_bid and best_ask) else 0.0
        spread_abs = best_ask - best_bid if (best_bid and best_ask) else 0.0
        spread_bps = (spread_abs / mid * 10_000) if mid > 0 else 0.0

        total_tob = best_bid_sz + best_ask_sz
        microprice = (
            (best_bid * best_ask_sz + best_ask * best_bid_sz) / total_tob
            if total_tob > 0 else mid
        )
        tob_imbalance = (
            (best_bid_sz - best_ask_sz) / total_tob
            if total_tob > 0 else 0.0
        )

        total_bid_depth = sum(snap.bid_sizes)
        total_ask_depth = sum(snap.ask_sizes)
        total_depth = total_bid_depth + total_ask_depth
        depth_imbalance = (
            (total_bid_depth - total_ask_depth) / total_depth
            if total_depth > 0 else 0.0
        )

        # Exponential-weighted depth imbalance (decay λ=0.3 per level)
        w_bid = sum(sz * math.exp(-0.3 * k) for k, sz in enumerate(snap.bid_sizes))
        w_ask = sum(sz * math.exp(-0.3 * k) for k, sz in enumerate(snap.ask_sizes))
        w_total = w_bid + w_ask
        weighted_depth_imbalance = (w_bid - w_ask) / w_total if w_total > 0 else 0.0

        avg_bid_orders = (
            sum(snap.bid_counts) / len(snap.bid_counts) if snap.bid_counts else 0.0
        )
        avg_ask_orders = (
            sum(snap.ask_counts) / len(snap.ask_counts) if snap.ask_counts else 0.0
        )

        # OFI
        ofi = self._compute_ofi(snap)

        # Queue pressure: depth within QUEUE_PRESSURE_BPS of best price
        queue_pressure = self._compute_queue_pressure(snap, best_bid, best_ask)

        # Trade flow / sweep / vwap (single pass over trade window)
        (
            buy_vol,
            sell_vol,
            buy_cnt,
            sell_cnt,
            sb_cnt,
            ss_cnt,
            sb_vol,
            ss_vol,
            recent_vwap,
        ) = self._compute_trade_window_features(snap.ts_event)
        tf_total = buy_vol + sell_vol
        trade_flow_imbalance = (
            (buy_vol - sell_vol) / tf_total if tf_total > 0 else 0.0
        )

        # VWAP deviation
        vwap_deviation_bps = (
            (mid - recent_vwap) / mid * 10_000 if mid > 0 and recent_vwap > 0
            else 0.0
        )

        # Spread volatility (std of recent spread_bps)
        spread_volatility = (
            statistics.pstdev(self._spread_history)
            if len(self._spread_history) >= 5 else 0.0
        )

        # Depth regime
        depth_regime = self._classify_depth_regime(total_depth)

        return MicrostructureFeatures(
            ts=snap.ts_event,
            best_bid=best_bid, best_ask=best_ask, mid=mid,
            spread_abs=spread_abs, spread_bps=spread_bps,
            microprice=microprice, tob_imbalance=tob_imbalance,
            total_bid_depth=total_bid_depth, total_ask_depth=total_ask_depth,
            depth_imbalance=depth_imbalance,
            weighted_depth_imbalance=weighted_depth_imbalance,
            avg_bid_orders_per_level=avg_bid_orders,
            avg_ask_orders_per_level=avg_ask_orders,
            ofi=ofi, queue_pressure=queue_pressure,
            buy_volume=buy_vol, sell_volume=sell_vol,
            trade_flow_imbalance=trade_flow_imbalance,
            trade_count_buy=buy_cnt, trade_count_sell=sell_cnt,
            sweep_buy_count=sb_cnt, sweep_sell_count=ss_cnt,
            sweep_buy_volume=sb_vol, sweep_sell_volume=ss_vol,
            recent_vwap=recent_vwap,
            vwap_deviation_bps=vwap_deviation_bps,
            spread_volatility=spread_volatility,
            depth_regime=depth_regime,
        )

    # -- 2b helpers --

    def _compute_ofi(self, current: DepthSnapshot) -> float:
        """
        Order Flow Imbalance (Cont, Kukanov, Stoikov 2014).
        OFI_t = ΔBidQty_t − ΔAskQty_t  where the delta accounts for
        price-level changes.
        """
        if len(self._depth_buf) < 2:
            return 0.0
        prev = self._depth_buf[-2]
        if not (prev.bid_prices and prev.ask_prices
                and current.bid_prices and current.ask_prices):
            return 0.0

        pb, sb = prev.bid_prices[0], prev.bid_sizes[0]
        cb, csb = current.bid_prices[0], current.bid_sizes[0]
        pa, sa = prev.ask_prices[0], prev.ask_sizes[0]
        ca, csa = current.ask_prices[0], current.ask_sizes[0]

        if cb > pb:
            d_bid = csb
        elif cb == pb:
            d_bid = csb - sb
        else:
            d_bid = -sb

        if ca < pa:
            d_ask = csa
        elif ca == pa:
            d_ask = csa - sa
        else:
            d_ask = -sa

        return d_bid - d_ask

    def _compute_queue_pressure(
        self, snap: DepthSnapshot, best_bid: float, best_ask: float
    ) -> float:
        """
        Depth within QUEUE_PRESSURE_BPS of the best price on each side,
        normalised to [-1, +1].  Positive = more bid pressure.
        """
        threshold = self.QUEUE_PRESSURE_BPS / 10_000
        bid_near = 0.0
        for i, (px, sz) in enumerate(zip(snap.bid_prices, snap.bid_sizes)):
            if i >= self.QUEUE_PRESSURE_MAX_LEVELS:
                break
            if best_bid > 0 and (best_bid - px) / best_bid <= threshold:
                bid_near += sz
        ask_near = 0.0
        for i, (px, sz) in enumerate(zip(snap.ask_prices, snap.ask_sizes)):
            if i >= self.QUEUE_PRESSURE_MAX_LEVELS:
                break
            if best_ask > 0 and (px - best_ask) / best_ask <= threshold:
                ask_near += sz
        total = bid_near + ask_near
        return (bid_near - ask_near) / total if total > 0 else 0.0

    # -- 2c helpers --

    def _compute_trade_window_features(
        self, now_ns: int
    ) -> Tuple[float, float, int, int, int, int, float, float, float]:
        """
        Compute trade-window features in one traversal.

        Returns:
            buy_vol, sell_vol, buy_count, sell_count,
            sweep_buy_count, sweep_sell_count, sweep_buy_vol, sweep_sell_vol,
            recent_vwap
        """
        cutoff = now_ns - self._trade_window_ns
        recent: List[TradeRecord] = []
        for t in reversed(self._trade_buf):
            if t.ts_event < cutoff:
                break
            recent.append(t)

        if not recent:
            return 0.0, 0.0, 0, 0, 0, 0, 0.0, 0.0, 0.0

        buy_vol = sell_vol = 0.0
        buy_count = sell_count = 0
        notional = volume = 0.0

        for t in recent:
            notional += t.price * t.size
            volume += t.size
            if t.is_buyer_aggressor:
                buy_vol += t.size
                buy_count += 1
            else:
                sell_vol += t.size
                sell_count += 1

        # Use the most recent SWEEP_LOOKBACK trades for robust thresholding.
        # `recent` is newest-first because we iterated reversed(self._trade_buf).
        sweep_sample = recent[: self.SWEEP_LOOKBACK]
        if len(sweep_sample) < 10:
            recent_vwap = notional / volume if volume > 0 else 0.0
            return buy_vol, sell_vol, buy_count, sell_count, 0, 0, 0.0, 0.0, recent_vwap

        median_sz = statistics.median(t.size for t in sweep_sample)
        threshold = median_sz * self.SWEEP_SIZE_MULT

        sweep_buy_count = sweep_sell_count = 0
        sweep_buy_vol = sweep_sell_vol = 0.0
        for t in recent:
            if t.size < threshold:
                continue
            if t.is_buyer_aggressor:
                sweep_buy_count += 1
                sweep_buy_vol += t.size
            else:
                sweep_sell_count += 1
                sweep_sell_vol += t.size

        recent_vwap = notional / volume if volume > 0 else 0.0
        return (
            buy_vol,
            sell_vol,
            buy_count,
            sell_count,
            sweep_buy_count,
            sweep_sell_count,
            sweep_buy_vol,
            sweep_sell_vol,
            recent_vwap,
        )

    def _classify_depth_regime(self, total_depth: float) -> str:
        """
        Classify current total depth vs rolling median.
          thin   : < DEPTH_THIN_MULT × median
          thick  : > DEPTH_THICK_MULT × median
          normal : otherwise
        """
        if len(self._total_depth_history) < 20:
            return "normal"
        med = statistics.median(self._total_depth_history)
        if med <= 0:
            return "normal"
        ratio = total_depth / med
        if ratio < self.DEPTH_THIN_MULT:
            return "thin"
        elif ratio > self.DEPTH_THICK_MULT:
            return "thick"
        return "normal"

    def _update_emas(self, f: MicrostructureFeatures) -> None:
        a = self._ema_alpha
        self._ema_ofi = a * f.ofi + (1 - a) * self._ema_ofi
        self._ema_spread_bps = a * f.spread_bps + (1 - a) * self._ema_spread_bps
        self._ema_tob_imbalance = a * f.tob_imbalance + (1 - a) * self._ema_tob_imbalance
        self._ema_depth_imbalance = a * f.depth_imbalance + (1 - a) * self._ema_depth_imbalance

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return len(self._depth_buf) >= 2

    @property
    def latest_features(self) -> Optional[MicrostructureFeatures]:
        return self._feature_buf[-1] if self._feature_buf else None

    @property
    def latest_depth(self) -> Optional[DepthSnapshot]:
        return self._depth_buf[-1] if self._depth_buf else None

    def get_summary(self) -> Dict[str, Any]:
        """Compact dict for logging / LLM prompt / dashboard."""
        f = self.latest_features
        if f is None:
            return {"ready": False}

        return {
            "ready": True,
            "best_bid": f.best_bid,
            "best_ask": f.best_ask,
            "mid": f.mid,
            "spread_abs": round(f.spread_abs, 6),
            "spread_bps": round(f.spread_bps, 2),
            "microprice": round(f.microprice, 2),
            "tob_imbalance": round(f.tob_imbalance, 4),
            "depth_imbalance": round(f.depth_imbalance, 4),
            "weighted_depth_imbalance": round(f.weighted_depth_imbalance, 4),
            "ofi": round(f.ofi, 6),
            "ema_ofi": round(self._ema_ofi, 6),
            "queue_pressure": round(f.queue_pressure, 4),
            "buy_volume": round(f.buy_volume, 6),
            "sell_volume": round(f.sell_volume, 6),
            "trade_flow_imbalance": round(f.trade_flow_imbalance, 4),
            "trade_count_buy": f.trade_count_buy,
            "trade_count_sell": f.trade_count_sell,
            "sweep_buy_count": f.sweep_buy_count,
            "sweep_sell_count": f.sweep_sell_count,
            "sweep_buy_volume": round(f.sweep_buy_volume, 6),
            "sweep_sell_volume": round(f.sweep_sell_volume, 6),
            "recent_vwap": round(f.recent_vwap, 2),
            "vwap_deviation_bps": round(f.vwap_deviation_bps, 2),
            "spread_volatility": round(f.spread_volatility, 4),
            "depth_regime": f.depth_regime,
            "ema_spread_bps": round(self._ema_spread_bps, 2),
            "ema_tob_imbalance": round(self._ema_tob_imbalance, 4),
            "ema_depth_imbalance": round(self._ema_depth_imbalance, 4),
            "avg_bid_orders_per_level": round(f.avg_bid_orders_per_level, 1),
            "avg_ask_orders_per_level": round(f.avg_ask_orders_per_level, 1),
            "depth_updates": self.depth_updates_received,
            "trade_ticks": self.trade_ticks_received,
        }

    def get_depth_profile(self, levels: int = 5) -> Dict[str, Any]:
        snap = self.latest_depth
        if snap is None:
            return {"ready": False}
        n = min(levels, len(snap.bid_prices), len(snap.ask_prices))
        return {
            "ready": True,
            "bids": [{"price": snap.bid_prices[i], "size": snap.bid_sizes[i]} for i in range(n)],
            "asks": [{"price": snap.ask_prices[i], "size": snap.ask_sizes[i]} for i in range(n)],
        }

    # ------------------------------------------------------------------
    # Feature vector helpers (for IC analysis / CSV dump)
    # ------------------------------------------------------------------

    _FEATURE_COLUMNS = [
        "ts", "mid", "spread_bps", "microprice", "tob_imbalance",
        "depth_imbalance", "weighted_depth_imbalance",
        "ofi", "queue_pressure",
        "trade_flow_imbalance",
        "sweep_buy_count", "sweep_sell_count",
        "sweep_buy_volume", "sweep_sell_volume",
        "vwap_deviation_bps", "spread_volatility",
        "depth_regime",
    ]

    def _feature_row(self, f: MicrostructureFeatures) -> Dict[str, Any]:
        return {
            "ts": f.ts,
            "mid": f.mid,
            "spread_bps": f.spread_bps,
            "microprice": f.microprice,
            "tob_imbalance": f.tob_imbalance,
            "depth_imbalance": f.depth_imbalance,
            "weighted_depth_imbalance": f.weighted_depth_imbalance,
            "ofi": f.ofi,
            "queue_pressure": f.queue_pressure,
            "trade_flow_imbalance": f.trade_flow_imbalance,
            "sweep_buy_count": f.sweep_buy_count,
            "sweep_sell_count": f.sweep_sell_count,
            "sweep_buy_volume": f.sweep_buy_volume,
            "sweep_sell_volume": f.sweep_sell_volume,
            "vwap_deviation_bps": f.vwap_deviation_bps,
            "spread_volatility": f.spread_volatility,
            "depth_regime": f.depth_regime,
        }

    def get_feature_history(self, count: int = 100) -> List[Dict[str, Any]]:
        start = max(0, len(self._feature_buf) - count)
        return [self._feature_row(self._feature_buf[i])
                for i in range(start, len(self._feature_buf))]

    # ------------------------------------------------------------------
    # CSV / Parquet dump with forward returns
    # ------------------------------------------------------------------

    def dump_features_csv(
        self,
        path: str = "data/microstructure_features.csv",
        fwd_bars: Tuple[int, ...] = (1, 5),
    ) -> str:
        """
        Write the feature buffer to CSV with forward-return columns.

        Forward returns are computed from the mid price series within
        the feature buffer itself (so both features and returns share
        the same time axis -- depth-update cadence, not bar cadence).

        Parameters
        ----------
        path : str
            Output file path.
        fwd_bars : tuple of int
            How many *depth snapshots* ahead to compute returns.
            Default (1, 5) gives "fwd_ret_1" and "fwd_ret_5".

        Returns
        -------
        str  Path to the written file.
        """
        buf = list(self._feature_buf)
        n = len(buf)
        if n < max(fwd_bars) + 1:
            return ""

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        extra_cols = [f"fwd_ret_{k}" for k in fwd_bars]
        header = self._FEATURE_COLUMNS + extra_cols

        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            writer.writeheader()
            for i in range(n):
                row = self._feature_row(buf[i])
                for k in fwd_bars:
                    if i + k < n and buf[i].mid > 0:
                        row[f"fwd_ret_{k}"] = (
                            (buf[i + k].mid - buf[i].mid) / buf[i].mid
                        )
                    else:
                        row[f"fwd_ret_{k}"] = ""
                writer.writerow(row)
        return path

    # ------------------------------------------------------------------
    # IC analysis
    # ------------------------------------------------------------------

    @staticmethod
    def compute_ic_from_csv(
        path: str,
        feature_cols: Optional[List[str]] = None,
        return_cols: Optional[List[str]] = None,
        ic_threshold: float = 0.02,
    ) -> Dict[str, Any]:
        """
        Rank-IC (Spearman) of each feature vs each forward return column.

        Returns
        -------
        dict with keys:
          "ic_table"  : list of {feature, return_col, ic, abs_ic, kept}
          "kept"      : list of feature names with |IC| >= threshold
          "dropped"   : list of feature names with |IC| < threshold
          "n_rows"    : number of valid rows used
        """
        import csv as _csv

        with open(path, "r") as fh:
            reader = _csv.DictReader(fh)
            rows = list(reader)

        if not rows:
            return {"ic_table": [], "kept": [], "dropped": [], "n_rows": 0}

        if feature_cols is None:
            feature_cols = [
                "spread_bps", "microprice", "tob_imbalance",
                "depth_imbalance", "weighted_depth_imbalance",
                "ofi", "queue_pressure", "trade_flow_imbalance",
                "sweep_buy_count", "sweep_sell_count",
                "vwap_deviation_bps", "spread_volatility",
            ]
        if return_cols is None:
            return_cols = [c for c in rows[0] if c.startswith("fwd_ret_")]

        def _to_float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def _rank(vals):
            indexed = sorted(range(len(vals)), key=lambda i: vals[i])
            ranks = [0.0] * len(vals)
            i = 0
            while i < len(indexed):
                j = i
                while j + 1 < len(indexed) and vals[indexed[j + 1]] == vals[indexed[i]]:
                    j += 1
                avg_rank = (i + j) / 2.0
                for k in range(i, j + 1):
                    ranks[indexed[k]] = avg_rank
                i = j + 1
            return ranks

        def _spearman(xs, ys):
            """Spearman rank correlation."""
            paired = [
                (x, y) for x, y in zip(xs, ys)
                if x is not None and y is not None
            ]
            if len(paired) < 20:
                return 0.0
            xv, yv = zip(*paired)
            rx = _rank(list(xv))
            ry = _rank(list(yv))
            n = len(rx)
            mx = sum(rx) / n
            my = sum(ry) / n
            cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / n
            sx = (sum((a - mx) ** 2 for a in rx) / n) ** 0.5
            sy = (sum((b - my) ** 2 for b in ry) / n) ** 0.5
            return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0

        ic_table = []
        best_abs_ic: Dict[str, float] = {}
        ret_series = {ret_col: [_to_float(r.get(ret_col)) for r in rows] for ret_col in return_cols}

        for feat in feature_cols:
            feat_vals = [_to_float(r.get(feat)) for r in rows]
            for ret_col in return_cols:
                ret_vals = ret_series[ret_col]
                ic = _spearman(feat_vals, ret_vals)
                abs_ic = abs(ic)
                ic_table.append({
                    "feature": feat,
                    "return_col": ret_col,
                    "ic": round(ic, 6),
                    "abs_ic": round(abs_ic, 6),
                    "kept": abs_ic >= ic_threshold,
                })
                best_abs_ic[feat] = max(best_abs_ic.get(feat, 0.0), abs_ic)

        kept = sorted([f for f, v in best_abs_ic.items() if v >= ic_threshold])
        dropped = sorted([f for f, v in best_abs_ic.items() if v < ic_threshold])

        return {
            "ic_table": ic_table,
            "kept": kept,
            "dropped": dropped,
            "n_rows": len(rows),
        }
