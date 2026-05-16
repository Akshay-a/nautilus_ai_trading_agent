"""
Order Book Feature Manager for NautilusTrader Strategy

Collects L2 depth snapshots and trade ticks, computes microstructure
features (spread, imbalance, OFI, microprice, trade flow) suitable
for downstream statistical models and LLM context.

Parallel to TechnicalIndicatorManager -- this handles market
microstructure while that handles bar-level technicals.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class DepthSnapshot:
    """Flattened representation of one OrderBookDepth10 update."""

    ts_event: int  # nanosecond unix timestamp
    bid_prices: List[float] = field(default_factory=list)  # best → worst
    bid_sizes: List[float] = field(default_factory=list)
    ask_prices: List[float] = field(default_factory=list)  # best → worst
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
    """Single-point feature vector computed from one depth snapshot."""

    ts: int

    # L1
    best_bid: float
    best_ask: float
    mid: float
    spread_abs: float
    spread_bps: float
    microprice: float  # size-weighted mid

    # Top-of-book imbalance  (bid_sz - ask_sz) / (bid_sz + ask_sz)
    tob_imbalance: float

    # Depth across N levels
    total_bid_depth: float
    total_ask_depth: float
    depth_imbalance: float  # (bid_depth - ask_depth) / (bid_depth + ask_depth)
    weighted_depth_imbalance: float  # exponential decay by level

    # Order Flow Imbalance (delta from previous snapshot)
    ofi: float

    # Trade flow (rolling window)
    buy_volume: float
    sell_volume: float
    trade_flow_imbalance: float  # (buy_vol - sell_vol) / (buy_vol + sell_vol)
    trade_count_buy: int
    trade_count_sell: int

    # Order count features (from bid_counts / ask_counts)
    avg_bid_orders_per_level: float
    avg_ask_orders_per_level: float


class OrderBookManager:
    """
    Ingests OrderBookDepth10 snapshots and TradeTick events, maintains
    rolling windows, and computes microstructure features on each update.

    Designed to sit alongside TechnicalIndicatorManager in the strategy.
    """

    def __init__(
        self,
        depth_buffer_size: int = 300,
        trade_buffer_size: int = 5000,
        feature_buffer_size: int = 500,
        depth_levels: int = 10,
        ema_alpha: float = 0.05,
        trade_window_ns: int = 300_000_000_000,  # 5 minutes in nanoseconds
    ):
        self.depth_levels = depth_levels

        # Raw ring buffers
        self._depth_buf: deque[DepthSnapshot] = deque(maxlen=depth_buffer_size)
        self._trade_buf: deque[TradeRecord] = deque(maxlen=trade_buffer_size)

        # Computed feature history
        self._feature_buf: deque[MicrostructureFeatures] = deque(maxlen=feature_buffer_size)

        # EMA state for smoothed features
        self._ema_alpha = ema_alpha
        self._ema_ofi: float = 0.0
        self._ema_spread_bps: float = 0.0
        self._ema_tob_imbalance: float = 0.0
        self._ema_depth_imbalance: float = 0.0

        # Trade flow window (nanoseconds)
        self._trade_window_ns = trade_window_ns

        # Counters for logging
        self.depth_updates_received: int = 0
        self.trade_ticks_received: int = 0

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def update_depth(self, depth) -> None:
        """
        Process an OrderBookDepth10 from NautilusTrader.

        Parameters
        ----------
        depth : OrderBookDepth10
            The depth snapshot from NautilusTrader callback.
        """
        snap = self._flatten_depth10(depth)
        self._ingest_snapshot(snap)

    def update_from_managed_book(self, book, ts_event: int = 0) -> None:
        """
        Extract top-N levels from a Nautilus managed OrderBook (cache).

        Parameters
        ----------
        book : OrderBook
            The Nautilus managed order book from cache.
        ts_event : int
            Nanosecond timestamp for this snapshot.
        """
        snap = self._flatten_managed_book(book, ts_event)
        self._ingest_snapshot(snap)

    def _ingest_snapshot(self, snap: DepthSnapshot) -> None:
        self._depth_buf.append(snap)
        self.depth_updates_received += 1

        features = self._compute_features(snap)
        self._feature_buf.append(features)
        self._update_emas(features)

    def update_trade(self, tick) -> None:
        """
        Process a TradeTick from NautilusTrader.

        Parameters
        ----------
        tick : TradeTick
            The trade tick from NautilusTrader callback.
        """
        rec = TradeRecord(
            ts_event=tick.ts_event,
            price=float(tick.price),
            size=float(tick.size),
            is_buyer_aggressor=str(tick.aggressor_side).upper() in ("BUYER", "BUY"),
        )
        self._trade_buf.append(rec)
        self.trade_ticks_received += 1

    # ------------------------------------------------------------------
    # Feature computation
    # ------------------------------------------------------------------

    def _flatten_depth10(self, depth) -> DepthSnapshot:
        """Convert OrderBookDepth10 to our plain dataclass."""
        bid_prices = []
        bid_sizes = []
        ask_prices = []
        ask_sizes = []
        bid_counts_list: List[int] = []
        ask_counts_list: List[int] = []

        for i in range(min(self.depth_levels, len(depth.bids))):
            lvl = depth.bids[i]
            bid_prices.append(float(lvl[0]))
            bid_sizes.append(float(lvl[1]))

        for i in range(min(self.depth_levels, len(depth.asks))):
            lvl = depth.asks[i]
            ask_prices.append(float(lvl[0]))
            ask_sizes.append(float(lvl[1]))

        for i in range(min(self.depth_levels, len(depth.bid_counts))):
            bid_counts_list.append(int(depth.bid_counts[i]))

        for i in range(min(self.depth_levels, len(depth.ask_counts))):
            ask_counts_list.append(int(depth.ask_counts[i]))

        return DepthSnapshot(
            ts_event=depth.ts_event,
            bid_prices=bid_prices,
            bid_sizes=bid_sizes,
            ask_prices=ask_prices,
            ask_sizes=ask_sizes,
            bid_counts=bid_counts_list,
            ask_counts=ask_counts_list,
        )

    def _flatten_managed_book(self, book, ts_event: int = 0) -> DepthSnapshot:
        """
        Extract top-N levels from a Nautilus managed OrderBook.

        The managed book exposes .bids() and .asks() returning lists of
        BookLevel objects with .price and .size() accessors.
        """
        bid_prices = []
        bid_sizes = []
        ask_prices = []
        ask_sizes = []
        bid_counts_list: List[int] = []
        ask_counts_list: List[int] = []

        try:
            bid_levels = book.bids()
            for i in range(min(self.depth_levels, len(bid_levels))):
                lvl = bid_levels[i]
                bid_prices.append(float(lvl.price))
                bid_sizes.append(float(lvl.size()))
                orders = lvl.orders() if hasattr(lvl, "orders") else []
                bid_counts_list.append(len(orders) if orders else 1)
        except Exception:
            pass

        try:
            ask_levels = book.asks()
            for i in range(min(self.depth_levels, len(ask_levels))):
                lvl = ask_levels[i]
                ask_prices.append(float(lvl.price))
                ask_sizes.append(float(lvl.size()))
                orders = lvl.orders() if hasattr(lvl, "orders") else []
                ask_counts_list.append(len(orders) if orders else 1)
        except Exception:
            pass

        if ts_event == 0:
            ts_event = getattr(book, "ts_last", 0) or 0

        return DepthSnapshot(
            ts_event=ts_event,
            bid_prices=bid_prices,
            bid_sizes=bid_sizes,
            ask_prices=ask_prices,
            ask_sizes=ask_sizes,
            bid_counts=bid_counts_list,
            ask_counts=ask_counts_list,
        )

    def _compute_features(self, snap: DepthSnapshot) -> MicrostructureFeatures:
        """Derive feature vector from a single depth snapshot + trade window."""

        best_bid = snap.bid_prices[0] if snap.bid_prices else 0.0
        best_ask = snap.ask_prices[0] if snap.ask_prices else 0.0
        best_bid_sz = snap.bid_sizes[0] if snap.bid_sizes else 0.0
        best_ask_sz = snap.ask_sizes[0] if snap.ask_sizes else 0.0

        mid = (best_bid + best_ask) / 2.0 if (best_bid and best_ask) else 0.0
        spread_abs = best_ask - best_bid if (best_bid and best_ask) else 0.0
        spread_bps = (spread_abs / mid * 10_000) if mid > 0 else 0.0

        # Microprice: size-weighted mid -- prices pulled toward the thicker side
        total_tob = best_bid_sz + best_ask_sz
        microprice = (
            (best_bid * best_ask_sz + best_ask * best_bid_sz) / total_tob
            if total_tob > 0
            else mid
        )

        # Top-of-book imbalance
        tob_imbalance = (
            (best_bid_sz - best_ask_sz) / (best_bid_sz + best_ask_sz)
            if total_tob > 0
            else 0.0
        )

        # Multi-level depth
        total_bid_depth = sum(snap.bid_sizes)
        total_ask_depth = sum(snap.ask_sizes)
        total_depth = total_bid_depth + total_ask_depth
        depth_imbalance = (
            (total_bid_depth - total_ask_depth) / total_depth
            if total_depth > 0
            else 0.0
        )

        # Weighted depth imbalance (exponential decay: level 0 weight=1, level k weight=exp(-0.3*k))
        w_bid = 0.0
        w_ask = 0.0
        for k, sz in enumerate(snap.bid_sizes):
            w = math.exp(-0.3 * k)
            w_bid += sz * w
        for k, sz in enumerate(snap.ask_sizes):
            w = math.exp(-0.3 * k)
            w_ask += sz * w
        w_total = w_bid + w_ask
        weighted_depth_imbalance = (
            (w_bid - w_ask) / w_total if w_total > 0 else 0.0
        )

        # OFI from consecutive snapshots
        ofi = self._compute_ofi(snap)

        # Trade flow in rolling window
        buy_vol, sell_vol, buy_cnt, sell_cnt = self._trade_flow_in_window(snap.ts_event)
        tf_total = buy_vol + sell_vol
        trade_flow_imbalance = (
            (buy_vol - sell_vol) / tf_total if tf_total > 0 else 0.0
        )

        # Order count features
        avg_bid_orders = (
            sum(snap.bid_counts) / len(snap.bid_counts) if snap.bid_counts else 0.0
        )
        avg_ask_orders = (
            sum(snap.ask_counts) / len(snap.ask_counts) if snap.ask_counts else 0.0
        )

        return MicrostructureFeatures(
            ts=snap.ts_event,
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread_abs=spread_abs,
            spread_bps=spread_bps,
            microprice=microprice,
            tob_imbalance=tob_imbalance,
            total_bid_depth=total_bid_depth,
            total_ask_depth=total_ask_depth,
            depth_imbalance=depth_imbalance,
            weighted_depth_imbalance=weighted_depth_imbalance,
            ofi=ofi,
            buy_volume=buy_vol,
            sell_volume=sell_vol,
            trade_flow_imbalance=trade_flow_imbalance,
            trade_count_buy=buy_cnt,
            trade_count_sell=sell_cnt,
            avg_bid_orders_per_level=avg_bid_orders,
            avg_ask_orders_per_level=avg_ask_orders,
        )

    def _compute_ofi(self, current: DepthSnapshot) -> float:
        """
        Order Flow Imbalance between current and previous snapshot.

        OFI tracks how resting limit-order depth shifts between two
        consecutive book snapshots.  When the best bid price stays the
        same (or improves), an increase in bid size represents new limit
        buys arriving; when it drops, the old level was consumed.  The
        same logic applies symmetrically to the ask side.

        Returns 0.0 when no previous snapshot exists.
        """
        if len(self._depth_buf) < 2:
            return 0.0

        prev = self._depth_buf[-2]
        if not prev.bid_prices or not prev.ask_prices:
            return 0.0
        if not current.bid_prices or not current.ask_prices:
            return 0.0

        prev_bid_px, prev_bid_sz = prev.bid_prices[0], prev.bid_sizes[0]
        curr_bid_px, curr_bid_sz = current.bid_prices[0], current.bid_sizes[0]
        prev_ask_px, prev_ask_sz = prev.ask_prices[0], prev.ask_sizes[0]
        curr_ask_px, curr_ask_sz = current.ask_prices[0], current.ask_sizes[0]

        # Bid side contribution
        if curr_bid_px > prev_bid_px:
            delta_bid = curr_bid_sz
        elif curr_bid_px == prev_bid_px:
            delta_bid = curr_bid_sz - prev_bid_sz
        else:
            delta_bid = -prev_bid_sz

        # Ask side contribution
        if curr_ask_px < prev_ask_px:
            delta_ask = curr_ask_sz
        elif curr_ask_px == prev_ask_px:
            delta_ask = curr_ask_sz - prev_ask_sz
        else:
            delta_ask = -prev_ask_sz

        return delta_bid - delta_ask

    def _trade_flow_in_window(self, now_ns: int):
        """Aggregate buy/sell volume and counts in the rolling trade window."""
        cutoff = now_ns - self._trade_window_ns
        buy_vol = 0.0
        sell_vol = 0.0
        buy_cnt = 0
        sell_cnt = 0
        for t in reversed(self._trade_buf):
            if t.ts_event < cutoff:
                break
            if t.is_buyer_aggressor:
                buy_vol += t.size
                buy_cnt += 1
            else:
                sell_vol += t.size
                sell_cnt += 1
        return buy_vol, sell_vol, buy_cnt, sell_cnt

    def _update_emas(self, f: MicrostructureFeatures) -> None:
        """Update exponential moving averages of key features."""
        a = self._ema_alpha
        self._ema_ofi = a * f.ofi + (1 - a) * self._ema_ofi
        self._ema_spread_bps = a * f.spread_bps + (1 - a) * self._ema_spread_bps
        self._ema_tob_imbalance = a * f.tob_imbalance + (1 - a) * self._ema_tob_imbalance
        self._ema_depth_imbalance = a * f.depth_imbalance + (1 - a) * self._ema_depth_imbalance

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """True once we have at least 2 depth snapshots (needed for OFI)."""
        return len(self._depth_buf) >= 2

    @property
    def latest_features(self) -> Optional[MicrostructureFeatures]:
        """Return the most recent feature vector, or None if not ready."""
        return self._feature_buf[-1] if self._feature_buf else None

    @property
    def latest_depth(self) -> Optional[DepthSnapshot]:
        """Return the most recent raw depth snapshot."""
        return self._depth_buf[-1] if self._depth_buf else None

    def get_summary(self) -> Dict[str, Any]:
        """
        Compact dict of current microstructure state for logging or
        downstream consumers (LLM prompt, dashboard, etc.).
        """
        f = self.latest_features
        if f is None:
            return {"ready": False}

        return {
            "ready": True,
            # L1
            "best_bid": f.best_bid,
            "best_ask": f.best_ask,
            "mid": f.mid,
            "spread_abs": round(f.spread_abs, 6),
            "spread_bps": round(f.spread_bps, 2),
            "microprice": round(f.microprice, 2),
            # Imbalance
            "tob_imbalance": round(f.tob_imbalance, 4),
            "depth_imbalance": round(f.depth_imbalance, 4),
            "weighted_depth_imbalance": round(f.weighted_depth_imbalance, 4),
            # OFI
            "ofi": round(f.ofi, 6),
            "ema_ofi": round(self._ema_ofi, 6),
            # Trade flow
            "buy_volume": round(f.buy_volume, 6),
            "sell_volume": round(f.sell_volume, 6),
            "trade_flow_imbalance": round(f.trade_flow_imbalance, 4),
            "trade_count_buy": f.trade_count_buy,
            "trade_count_sell": f.trade_count_sell,
            # EMA smoothed
            "ema_spread_bps": round(self._ema_spread_bps, 2),
            "ema_tob_imbalance": round(self._ema_tob_imbalance, 4),
            "ema_depth_imbalance": round(self._ema_depth_imbalance, 4),
            # Order counts
            "avg_bid_orders_per_level": round(f.avg_bid_orders_per_level, 1),
            "avg_ask_orders_per_level": round(f.avg_ask_orders_per_level, 1),
            # Counters
            "depth_updates": self.depth_updates_received,
            "trade_ticks": self.trade_ticks_received,
        }

    def get_depth_profile(self, levels: int = 5) -> Dict[str, Any]:
        """
        Return the current depth profile for the top N levels.
        Useful for detailed logging and dashboard display.
        """
        snap = self.latest_depth
        if snap is None:
            return {"ready": False}

        n = min(levels, len(snap.bid_prices), len(snap.ask_prices))
        return {
            "ready": True,
            "bids": [
                {"price": snap.bid_prices[i], "size": snap.bid_sizes[i]}
                for i in range(n)
            ],
            "asks": [
                {"price": snap.ask_prices[i], "size": snap.ask_sizes[i]}
                for i in range(n)
            ],
        }

    def get_feature_history(self, count: int = 100) -> List[Dict[str, float]]:
        """
        Return recent feature vectors as dicts for IC analysis or CSV export.
        """
        result = []
        start = max(0, len(self._feature_buf) - count)
        for i in range(start, len(self._feature_buf)):
            f = self._feature_buf[i]
            result.append({
                "ts": f.ts,
                "mid": f.mid,
                "spread_bps": f.spread_bps,
                "microprice": f.microprice,
                "tob_imbalance": f.tob_imbalance,
                "depth_imbalance": f.depth_imbalance,
                "weighted_depth_imbalance": f.weighted_depth_imbalance,
                "ofi": f.ofi,
                "trade_flow_imbalance": f.trade_flow_imbalance,
            })
        return result
