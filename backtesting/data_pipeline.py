"""Historical data ingestion + validation using Nautilus ParquetDataCatalog."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import BarDataWrangler

from backtesting.instruments import build_bybit_linear_perpetual


BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"


@dataclass(frozen=True)
class CoverageSummary:
    bars: int
    start_utc: str | None
    end_utc: str | None
    expected_bars: int
    missing_intervals: int
    missing_samples_utc: list[str]


def _infer_base_currency(symbol: str, quote_currency: str) -> str:
    upper_symbol = symbol.upper()
    upper_quote = quote_currency.upper()
    if upper_symbol.endswith(upper_quote) and len(upper_symbol) > len(upper_quote):
        return upper_symbol[: -len(upper_quote)]
    return upper_symbol


def _iso_to_ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _ms_to_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_bybit_15m_bars(
    symbol: str,
    start_iso_utc: str,
    end_iso_utc: str,
    interval_minutes: int = 15,
    request_limit: int = 1000,
    throttle_seconds: float = 0.05,
) -> pd.DataFrame:
    """Fetch Bybit linear kline data in deterministic chronological order."""
    start_ms = _iso_to_ms(start_iso_utc)
    end_ms = _iso_to_ms(end_iso_utc)
    if end_ms <= start_ms:
        raise ValueError("end time must be after start time")

    interval_map = {1: "1", 3: "3", 5: "5", 15: "15", 30: "30", 60: "60", 240: "240", 1440: "D"}
    if interval_minutes not in interval_map:
        raise ValueError(f"Unsupported Bybit interval minutes: {interval_minutes}")

    bybit_interval = interval_map[interval_minutes]
    step_ms = interval_minutes * 60 * 1000

    rows_by_ts: dict[int, list[str]] = {}
    cursor_end_ms = end_ms

    # Bybit V5 kline endpoint returns newest rows for the requested window.
    # Use backward pagination from end -> start for deterministic full-range coverage.
    while cursor_end_ms >= start_ms:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": bybit_interval,
            "start": start_ms,
            "end": cursor_end_ms,
            "limit": min(max(request_limit, 1), 1000),
        }
        response = requests.get(BYBIT_KLINE_URL, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()

        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error: {payload}")

        chunk = (payload.get("result") or {}).get("list") or []
        if not chunk:
            break

        chunk = sorted(chunk, key=lambda item: int(item[0]))
        min_ts_in_chunk = cursor_end_ms

        for row in chunk:
            ts_ms = int(row[0])
            if ts_ms < start_ms or ts_ms > end_ms:
                continue
            rows_by_ts[ts_ms] = row
            min_ts_in_chunk = min(min_ts_in_chunk, ts_ms)

        if min_ts_in_chunk >= cursor_end_ms:
            cursor_end_ms -= step_ms
        else:
            cursor_end_ms = min_ts_in_chunk - step_ms

        time.sleep(throttle_seconds)

    if not rows_by_ts:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    records = []
    for ts_ms in sorted(rows_by_ts):
        row = rows_by_ts[ts_ms]
        records.append(
            {
                "timestamp": pd.to_datetime(ts_ms, unit="ms", utc=True),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            },
        )

    df = pd.DataFrame.from_records(records).set_index("timestamp")
    return df.sort_index()


def persist_bars_to_catalog(
    df: pd.DataFrame,
    catalog_path: str,
    instrument_id: str,
    raw_symbol: str,
    bar_type_str: str,
    maker_fee: float,
    taker_fee: float,
    quote_currency: str = "USDT",
    base_currency: str | None = None,
    settlement_currency: str | None = None,
    price_increment: str = "0.1",
    size_increment: str = "0.001",
    price_precision: int = 1,
    size_precision: int = 3,
) -> dict[str, Any]:
    """Persist bars + instrument metadata to Nautilus ParquetDataCatalog."""
    if df.empty:
        raise ValueError("No bars to persist")

    catalog = ParquetDataCatalog(catalog_path)
    inferred_base = base_currency or _infer_base_currency(raw_symbol, quote_currency)
    settlement = settlement_currency or quote_currency

    instrument = build_bybit_linear_perpetual(
        instrument_id=instrument_id,
        raw_symbol=raw_symbol,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        base_currency=inferred_base,
        quote_currency=quote_currency,
        settlement_currency=settlement,
        price_increment=price_increment,
        size_increment=size_increment,
        price_precision=price_precision,
        size_precision=size_precision,
    )

    bar_type = BarType.from_str(bar_type_str)
    wrangler = BarDataWrangler(bar_type, instrument)
    bars = wrangler.process(df)

    catalog.write_data([instrument])
    catalog.write_data(bars)

    return {
        "bars_written": len(bars),
        "catalog_path": str(Path(catalog_path).resolve()),
        "first_bar_utc": df.index.min().isoformat().replace("+00:00", "Z"),
        "last_bar_utc": df.index.max().isoformat().replace("+00:00", "Z"),
    }


def validate_catalog_coverage(
    catalog_path: str,
    bar_type_str: str,
    interval_minutes: int,
) -> CoverageSummary:
    """Validate row count, date coverage, and missing 15m intervals."""
    catalog = ParquetDataCatalog(catalog_path)
    bar_type = BarType.from_str(bar_type_str)
    bars = catalog.bars(bar_types=[bar_type])

    if not bars:
        return CoverageSummary(
            bars=0,
            start_utc=None,
            end_utc=None,
            expected_bars=0,
            missing_intervals=0,
            missing_samples_utc=[],
        )

    ts = pd.Series([int(bar.ts_event) for bar in bars], dtype="int64")
    dt = pd.to_datetime(ts, unit="ns", utc=True).sort_values().drop_duplicates().reset_index(drop=True)

    freq = f"{interval_minutes}min"
    full_range = pd.date_range(start=dt.iloc[0], end=dt.iloc[-1], freq=freq, tz="UTC")
    missing = full_range.difference(pd.DatetimeIndex(dt))

    missing_samples = [item.isoformat().replace("+00:00", "Z") for item in missing[:10]]

    return CoverageSummary(
        bars=int(len(dt)),
        start_utc=dt.iloc[0].isoformat().replace("+00:00", "Z"),
        end_utc=dt.iloc[-1].isoformat().replace("+00:00", "Z"),
        expected_bars=int(len(full_range)),
        missing_intervals=int(len(missing)),
        missing_samples_utc=missing_samples,
    )
