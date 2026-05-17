#!/usr/bin/env python3
"""Run Nautilus-native backtests for buy-and-hold, rule-proxy, and replay variants."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_trader.backtest.config import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestRunConfig,
    BacktestVenueConfig,
)
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import ImportableStrategyConfig, LoggingConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from backtesting.data_pipeline import validate_catalog_coverage
from backtesting.metrics import (
    compute_performance_metrics,
    hash_metrics_payload,
    viability_verdict,
)
from backtesting.replay import extract_replay_decisions, write_replay_csv


@dataclass(frozen=True)
class PeriodWindow:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/backtest_config.yaml")
    return parser.parse_args()


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def to_utc_timestamp(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def split_is_oos(start: pd.Timestamp, end: pd.Timestamp, train_ratio: float) -> list[PeriodWindow]:
    span_ns = (end.value - start.value)
    split_point = pd.Timestamp(start.value + int(span_ns * train_ratio), tz="UTC")
    return [
        PeriodWindow(name="in_sample", start=start, end=split_point),
        PeriodWindow(name="out_of_sample", start=split_point, end=end),
    ]


def query_data_window(catalog_path: str, bar_type: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    catalog = ParquetDataCatalog(catalog_path)
    bars = catalog.bars(
        bar_types=[bar_type],
        start=int(start.value),
        end=int(end.value),
    )
    if not bars:
        return pd.DataFrame(columns=["timestamp"])

    ts = pd.to_datetime([int(item.ts_event) for item in bars], unit="ns", utc=True)
    return pd.DataFrame({"timestamp": ts}).drop_duplicates().sort_values("timestamp")


def build_run_config(
    catalog_path: str,
    bar_type: str,
    instrument_id: str,
    venue_name: str,
    starting_balance: str,
    variant: str,
    period: PeriodWindow,
    fixed_trade_usdt: float,
    min_trade_amount: float,
    allow_short: bool,
    replay_csv: str,
) -> BacktestRunConfig:
    strategy_config = {
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "variant": variant,
        "fixed_trade_usdt": fixed_trade_usdt,
        "min_trade_amount": min_trade_amount,
        "allow_short": allow_short,
        "replay_signals_csv": replay_csv,
    }

    engine_config = BacktestEngineConfig(
        logging=LoggingConfig(
            log_level="ERROR",
            log_level_file="ERROR",
            log_file_format=None,
            bypass_logging=False,
        ),
        strategies=[
            ImportableStrategyConfig(
                strategy_path="strategy.backtest_variants:BacktestVariantStrategy",
                config_path="strategy.backtest_variants:BacktestVariantStrategyConfig",
                config=strategy_config,
            ),
        ],
    )

    return BacktestRunConfig(
        venues=[
            BacktestVenueConfig(
                name=venue_name,
                oms_type="NETTING",
                account_type="MARGIN",
                starting_balances=[starting_balance],
                base_currency="USDT",
                default_leverage=1.0,
            ),
        ],
        data=[
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=Bar.fully_qualified_name(),
                bar_types=[bar_type],
                start_time=period.start.isoformat(),
                end_time=period.end.isoformat(),
            ),
        ],
        engine=engine_config,
        raise_exception=True,
        dispose_on_completion=False,
        start=period.start.isoformat(),
        end=period.end.isoformat(),
    )


def infer_verdict(metrics_rows: list[dict[str, Any]]) -> str:
    oos_baseline = [
        row
        for row in metrics_rows
        if row.get("split") == "out_of_sample" and float(row.get("slippage_bps_per_side", -1)) == 1.0
    ]
    if not oos_baseline:
        return "INCONCLUSIVE"

    # Rule: if any tested OOS baseline is KILL -> KILL; else if any PASS -> PASS.
    verdicts = [viability_verdict(row) for row in oos_baseline]
    if "KILL" in verdicts:
        return "KILL"
    if "PASS" in verdicts:
        return "PASS"
    return "INCONCLUSIVE"


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)

    run_cfg = cfg["run"]
    data_cfg = cfg["data"]
    split_cfg = cfg["split"]
    portfolio_cfg = cfg["portfolio"]
    costs_cfg = cfg["costs"]
    replay_cfg = cfg.get("replay", {})
    quality_cfg = cfg.get("quality", {})

    run_id = str(run_cfg["run_id"])
    timestamp_label = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(run_cfg["results_dir"]) / f"{timestamp_label}_{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog_path = str(Path(run_cfg["catalog_path"]))
    instrument_id = str(data_cfg["instrument_id"])
    bar_type = str(data_cfg["bar_type"])
    interval_minutes = int(data_cfg.get("interval_minutes", 15))

    start = to_utc_timestamp(str(data_cfg["start"]))
    end = to_utc_timestamp(str(data_cfg["end"]))
    periods = split_is_oos(start, end, float(split_cfg.get("train_ratio", 0.7)))

    coverage = validate_catalog_coverage(
        catalog_path=catalog_path,
        bar_type_str=bar_type,
        interval_minutes=interval_minutes,
    )

    warnings: list[str] = []
    if coverage.bars == 0:
        raise RuntimeError("Catalog has no bars for requested bar type; run tools/fetch_bybit_bars.py first")

    coverage_days = 0.0
    if coverage.start_utc and coverage.end_utc:
        coverage_days = (to_utc_timestamp(coverage.end_utc) - to_utc_timestamp(coverage.start_utc)).total_seconds() / 86400.0

    min_months_target = float(quality_cfg.get("min_months_target", 6))
    if coverage_days < min_months_target * 30:
        warnings.append(
            f"Provisional results: coverage is {coverage_days:.1f} days, below {min_months_target:.1f} month target",
        )

    replay_all_decisions = extract_replay_decisions(
        log_patterns=list(replay_cfg.get("log_patterns", [])),
        start_time_ns=None,
        end_time_ns=None,
    )
    replay_decisions = extract_replay_decisions(
        log_patterns=list(replay_cfg.get("log_patterns", [])),
        start_time_ns=int(start.value),
        end_time_ns=int(end.value),
    )
    replay_csv = write_replay_csv(replay_decisions, output_dir / "recorded_llm_replay_signals.csv")

    variants = list(cfg.get("variants", []))
    slippage_levels = [float(costs_cfg.get("slippage_bps_baseline", 1))] + [
        float(item) for item in costs_cfg.get("slippage_bps_sensitivity", [])
    ]

    all_metrics_rows: list[dict[str, Any]] = []
    trade_exports: list[pd.DataFrame] = []
    position_exports: list[pd.DataFrame] = []
    equity_exports: list[pd.DataFrame] = []
    skipped: list[dict[str, str]] = []
    run_plan: list[dict[str, Any]] = []

    venue_name = instrument_id.split(".")[-1]

    for variant in variants:
        if variant == "recorded_llm_replay" and len(replay_decisions) == 0:
            if replay_all_decisions:
                skipped.append(
                    {
                        "variant": variant,
                        "reason": "Replay decisions exist in logs but none overlap configured backtest window",
                    },
                )
            else:
                skipped.append({"variant": variant, "reason": "No replay decisions extracted from log patterns"})
            continue

        for period in periods:
            period_bars = query_data_window(catalog_path, bar_type, period.start, period.end)
            if period_bars.empty:
                skipped.append({"variant": variant, "reason": f"No bars in {period.name} window"})
                continue

            if variant == "recorded_llm_replay":
                decisions_in_period = [d for d in replay_decisions if period.start.value <= d.ts_ns <= period.end.value]
                if not decisions_in_period:
                    skipped.append({"variant": variant, "reason": f"No replay decisions in {period.name} window"})
                    continue

            run_plan.append(
                {
                    "variant": variant,
                    "period": period,
                    "run_config": build_run_config(
                        catalog_path=catalog_path,
                        bar_type=bar_type,
                        instrument_id=instrument_id,
                        venue_name=venue_name,
                        starting_balance=str(portfolio_cfg["starting_balance"]),
                        variant=variant,
                        period=period,
                        fixed_trade_usdt=float(portfolio_cfg["fixed_trade_usdt"]),
                        min_trade_amount=float(portfolio_cfg["min_trade_amount"]),
                        allow_short=bool(portfolio_cfg.get("allow_short", True)),
                        replay_csv=str(replay_csv),
                    ),
                },
            )

    if run_plan:
        node = BacktestNode(configs=[item["run_config"] for item in run_plan])
        try:
            node.run()
            for item in run_plan:
                variant = item["variant"]
                period = item["period"]
                run_config = item["run_config"]

                engine = node.get_engine(run_config.id)
                if engine is None:
                    skipped.append({"variant": variant, "reason": f"Missing engine for {period.name}"})
                    continue

                trader = engine.trader
                fills = trader.generate_fills_report()
                positions = trader.generate_positions_report()
                account = trader.generate_account_report(venue=Venue(venue_name))

                for slippage_bps in slippage_levels:
                    bundle = compute_performance_metrics(
                        account_df=account,
                        fills_df=fills,
                        positions_df=positions,
                        initial_equity=float(str(portfolio_cfg["starting_balance"]).split()[0]),
                        period_start=period.start,
                        period_end=period.end,
                        slippage_bps_per_side=slippage_bps,
                    )

                    row = {
                        "variant": variant,
                        "split": period.name,
                        "period_start_utc": period.start.isoformat().replace("+00:00", "Z"),
                        "period_end_utc": period.end.isoformat().replace("+00:00", "Z"),
                        "slippage_bps_per_side": slippage_bps,
                        **bundle.metrics,
                    }
                    all_metrics_rows.append(row)
                    warnings.extend(bundle.warnings)

                    curve = bundle.adjusted_equity_curve.copy()
                    if not curve.empty:
                        curve["variant"] = variant
                        curve["split"] = period.name
                        curve["slippage_bps_per_side"] = slippage_bps
                        equity_exports.append(curve)

                if not fills.empty:
                    fills_out = fills.copy().reset_index(drop=False)
                    fills_out["variant"] = variant
                    fills_out["split"] = period.name
                    trade_exports.append(fills_out)

                if not positions.empty:
                    positions_out = positions.copy().reset_index(drop=False)
                    positions_out["variant"] = variant
                    positions_out["split"] = period.name
                    position_exports.append(positions_out)
        finally:
            node.dispose()

    if not all_metrics_rows and not skipped:
        raise RuntimeError("No metrics generated and no explicit skip reason recorded")

    metrics_df = pd.DataFrame(all_metrics_rows)
    metrics_hash = hash_metrics_payload({"rows": metrics_df.to_dict(orient="records")}) if not metrics_df.empty else ""

    # Determinism check: hash must match when canonicalized twice.
    determinism_check = metrics_hash == hash_metrics_payload({"rows": metrics_df.to_dict(orient="records")})
    if not determinism_check:
        warnings.append("Determinism hash mismatch on canonical payload")

    verdict = infer_verdict(all_metrics_rows)

    config_snapshot_path = output_dir / "config_snapshot.yaml"
    with config_snapshot_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(cfg, handle, sort_keys=False)

    metrics_json_path = output_dir / "metrics_table.json"
    metrics_csv_path = output_dir / "metrics_table.csv"
    metrics_df.to_json(metrics_json_path, orient="records", indent=2)
    metrics_df.to_csv(metrics_csv_path, index=False)

    trades_path = output_dir / "trade_list.csv"
    if trade_exports:
        pd.concat(trade_exports, ignore_index=True).to_csv(trades_path, index=False)
    else:
        pd.DataFrame(columns=["variant", "split"]).to_csv(trades_path, index=False)

    positions_path = output_dir / "positions_list.csv"
    if position_exports:
        pd.concat(position_exports, ignore_index=True).to_csv(positions_path, index=False)
    else:
        pd.DataFrame(columns=["variant", "split"]).to_csv(positions_path, index=False)

    equity_path = output_dir / "equity_curve.csv"
    if equity_exports:
        pd.concat(equity_exports, ignore_index=True).to_csv(equity_path, index=False)
    else:
        pd.DataFrame(columns=["variant", "split", "timestamp", "equity_adjusted"]).to_csv(equity_path, index=False)

    run_log = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "catalog_path": str(Path(catalog_path).resolve()),
        "bar_type": bar_type,
        "coverage": {
            "bars": coverage.bars,
            "start_utc": coverage.start_utc,
            "end_utc": coverage.end_utc,
            "expected_bars": coverage.expected_bars,
            "missing_intervals": coverage.missing_intervals,
            "missing_samples_utc": coverage.missing_samples_utc,
            "coverage_days": coverage_days,
        },
        "split": {
            "train_ratio": split_cfg.get("train_ratio", 0.7),
            "windows": [
                {
                    "name": p.name,
                    "start_utc": p.start.isoformat().replace("+00:00", "Z"),
                    "end_utc": p.end.isoformat().replace("+00:00", "Z"),
                }
                for p in periods
            ],
        },
        "assumptions": {
            "maker_fee": costs_cfg.get("maker_fee"),
            "taker_fee": costs_cfg.get("taker_fee"),
            "slippage_bps_baseline": costs_cfg.get("slippage_bps_baseline"),
            "slippage_bps_sensitivity": costs_cfg.get("slippage_bps_sensitivity", []),
            "funding_rate_available": costs_cfg.get("funding_rate_available", False),
            "funding_rate_assumption": costs_cfg.get("funding_rate_assumption", "Not specified"),
        },
        "determinism": {
            "metrics_hash": metrics_hash,
            "hash_reproducible": determinism_check,
        },
        "viability_verdict": verdict,
        "warnings": sorted(set(warnings)),
        "skipped_variants": skipped,
    }

    with (output_dir / "run_log.json").open("w", encoding="utf-8") as handle:
        json.dump(run_log, handle, indent=2)

    stdout_summary = {
        "output_dir": str(output_dir.resolve()),
        "metrics_rows": int(len(all_metrics_rows)),
        "skipped": skipped,
        "viability_verdict": verdict,
        "metrics_hash": metrics_hash,
    }
    print(json.dumps(stdout_summary, indent=2))


if __name__ == "__main__":
    main()
