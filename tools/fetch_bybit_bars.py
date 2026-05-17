#!/usr/bin/env python3
"""Fetch Bybit historical bars into Nautilus ParquetDataCatalog and validate coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.data_pipeline import (
    fetch_bybit_15m_bars,
    persist_bars_to_catalog,
    validate_catalog_coverage,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Fetch bars from Bybit and persist to ParquetDataCatalog")
    fetch.add_argument("--symbol", default="BTCUSDT")
    fetch.add_argument("--instrument-id", default="BTCUSDT-LINEAR.BYBIT")
    fetch.add_argument("--bar-type", default="BTCUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
    fetch.add_argument("--start", required=True, help="ISO UTC start, e.g. 2025-01-01T00:00:00Z")
    fetch.add_argument("--end", required=True, help="ISO UTC end, e.g. 2025-07-01T00:00:00Z")
    fetch.add_argument("--interval-minutes", type=int, default=15)
    fetch.add_argument("--catalog-path", default="data/catalog")
    fetch.add_argument("--maker-fee", type=float, default=0.0002)
    fetch.add_argument("--taker-fee", type=float, default=0.00055)
    fetch.add_argument("--quote-currency", default="USDT")
    fetch.add_argument("--base-currency", default=None)
    fetch.add_argument("--settlement-currency", default=None)
    fetch.add_argument("--price-increment", default="0.1")
    fetch.add_argument("--size-increment", default="0.001")
    fetch.add_argument("--price-precision", type=int, default=1)
    fetch.add_argument("--size-precision", type=int, default=3)

    validate = sub.add_parser("validate", help="Validate catalog coverage and interval gaps")
    validate.add_argument("--catalog-path", default="data/catalog")
    validate.add_argument("--bar-type", default="BTCUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
    validate.add_argument("--interval-minutes", type=int, default=15)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "fetch":
        frame = fetch_bybit_15m_bars(
            symbol=args.symbol,
            start_iso_utc=args.start,
            end_iso_utc=args.end,
            interval_minutes=args.interval_minutes,
        )
        result = persist_bars_to_catalog(
            df=frame,
            catalog_path=args.catalog_path,
            instrument_id=args.instrument_id,
            raw_symbol=args.symbol,
            bar_type_str=args.bar_type,
            maker_fee=args.maker_fee,
            taker_fee=args.taker_fee,
            quote_currency=args.quote_currency,
            base_currency=args.base_currency,
            settlement_currency=args.settlement_currency,
            price_increment=args.price_increment,
            size_increment=args.size_increment,
            price_precision=args.price_precision,
            size_precision=args.size_precision,
        )

        coverage = validate_catalog_coverage(
            catalog_path=args.catalog_path,
            bar_type_str=args.bar_type,
            interval_minutes=args.interval_minutes,
        )

        payload = {
            "fetch": result,
            "coverage": {
                "bars": coverage.bars,
                "start_utc": coverage.start_utc,
                "end_utc": coverage.end_utc,
                "expected_bars": coverage.expected_bars,
                "missing_intervals": coverage.missing_intervals,
                "missing_samples_utc": coverage.missing_samples_utc,
            },
        }
        print(json.dumps(payload, indent=2))
        return

    if args.command == "validate":
        coverage = validate_catalog_coverage(
            catalog_path=args.catalog_path,
            bar_type_str=args.bar_type,
            interval_minutes=args.interval_minutes,
        )
        payload = {
            "bars": coverage.bars,
            "start_utc": coverage.start_utc,
            "end_utc": coverage.end_utc,
            "expected_bars": coverage.expected_bars,
            "missing_intervals": coverage.missing_intervals,
            "missing_samples_utc": coverage.missing_samples_utc,
        }
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
