"""Instrument helpers for Nautilus backtests."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Price, Quantity


def build_bybit_linear_perpetual(
    instrument_id: str,
    raw_symbol: str,
    maker_fee: float,
    taker_fee: float,
    base_currency: str,
    quote_currency: str,
    settlement_currency: str,
    price_increment: str = "0.1",
    size_increment: str = "0.001",
    price_precision: int = 1,
    size_precision: int = 3,
) -> CryptoPerpetual:
    """Build a linear perpetual instrument for backtesting."""
    now_ns = dt_to_unix_nanos(pd.Timestamp.utcnow().to_pydatetime())

    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(instrument_id),
        raw_symbol=Symbol(raw_symbol),
        base_currency=Currency.from_str(base_currency),
        quote_currency=Currency.from_str(quote_currency),
        settlement_currency=Currency.from_str(settlement_currency),
        is_inverse=False,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=Price.from_str(price_increment),
        size_increment=Quantity.from_str(size_increment),
        ts_event=now_ns,
        ts_init=now_ns,
        maker_fee=Decimal(str(maker_fee)),
        taker_fee=Decimal(str(taker_fee)),
    )
