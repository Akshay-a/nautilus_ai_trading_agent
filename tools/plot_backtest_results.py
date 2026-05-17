#!/usr/bin/env python3
"""Generate simple visualizations from a backtest result directory."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result_dir = Path(args.result_dir)

    metrics = pd.read_csv(result_dir / "metrics_table.csv")
    equity = pd.read_csv(result_dir / "equity_curve.csv")
    positions = pd.read_csv(result_dir / "positions_list.csv")

    metrics_base = metrics[metrics["slippage_bps_per_side"] == args.slippage_bps].copy()

    plots_dir = result_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    # 1) Equity curves
    if not equity.empty:
        eq = equity[equity["slippage_bps_per_side"] == args.slippage_bps].copy()
        eq["timestamp"] = pd.to_datetime(eq["timestamp"], utc=True)

        plt.figure(figsize=(12, 6))
        for (variant, split), g in eq.groupby(["variant", "split"]):
            plt.plot(g["timestamp"], g["equity_adjusted"], label=f"{variant} ({split})")
        plt.title(f"Equity Curves ({args.slippage_bps:.1f} bps per side)")
        plt.xlabel("Time (UTC)")
        plt.ylabel("Equity")
        plt.legend()
        plt.tight_layout()
        plt.savefig(plots_dir / "equity_curves.png", dpi=140)
        plt.close()

    # 2) Win/Loss counts
    if not positions.empty and "realized_pnl" in positions.columns:
        pos = positions.copy()
        pos["realized_pnl_num"] = pd.to_numeric(
            pos["realized_pnl"].astype(str).str.split().str[0].str.replace("_", "", regex=False),
            errors="coerce",
        ).fillna(0.0)
        pos["win"] = pos["realized_pnl_num"] > 0

        agg = pos.groupby(["variant", "split", "win"]).size().unstack(fill_value=0)
        agg = agg.rename(columns={False: "losses", True: "wins"}).reset_index()

        fig, ax = plt.subplots(figsize=(10, 5))
        x = range(len(agg))
        ax.bar([i - 0.2 for i in x], agg.get("wins", 0), width=0.4, label="Wins")
        ax.bar([i + 0.2 for i in x], agg.get("losses", 0), width=0.4, label="Losses")
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{r.variant}\n{r.split}" for r in agg.itertuples()], rotation=0)
        ax.set_title("Win/Loss Counts by Variant and Split")
        ax.set_ylabel("Closed Trades")
        ax.legend()
        fig.tight_layout()
        fig.savefig(plots_dir / "win_loss_counts.png", dpi=140)
        plt.close(fig)

        # 3) Realized PnL histogram
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.hist(pos["realized_pnl_num"], bins=50)
        ax.set_title("Distribution of Closed-Trade Realized PnL")
        ax.set_xlabel("Realized PnL")
        ax.set_ylabel("Frequency")
        fig.tight_layout()
        fig.savefig(plots_dir / "trade_pnl_distribution.png", dpi=140)
        plt.close(fig)

    # 4) KPI table (baseline slippage)
    if not metrics_base.empty:
        kpi_cols = [
            "variant",
            "split",
            "net_return",
            "max_drawdown",
            "sharpe_annualized",
            "profit_factor",
            "win_rate",
            "total_trades",
        ]
        view = metrics_base[kpi_cols].copy()
        for col in ["net_return", "max_drawdown", "sharpe_annualized", "profit_factor", "win_rate"]:
            view[col] = view[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")

        fig, ax = plt.subplots(figsize=(14, 3 + 0.3 * len(view)))
        ax.axis("off")
        tbl = ax.table(cellText=view.values, colLabels=view.columns, loc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)
        tbl.scale(1, 1.2)
        ax.set_title(f"KPI Summary ({args.slippage_bps:.1f} bps per side)", pad=18)
        fig.tight_layout()
        fig.savefig(plots_dir / "kpi_summary_table.png", dpi=140)
        plt.close(fig)

    print(str(plots_dir.resolve()))


if __name__ == "__main__":
    main()
