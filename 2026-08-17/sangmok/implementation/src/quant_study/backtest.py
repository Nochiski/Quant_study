from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from zipline import run_algorithm
from zipline.api import order_target_percent, record, set_benchmark, symbol


def make_initialize(ticker: str):
    def initialize(context) -> None:
        context.asset = symbol(ticker)
        set_benchmark(context.asset)

    return initialize


def make_handle_data(short_window_days: int, long_window_days: int):
    if short_window_days >= long_window_days:
        raise ValueError(
            "short window must be smaller than long window: "
            f"short_window_days={short_window_days} long_window_days={long_window_days}"
        )

    def handle_data(context, data) -> None:
        history = data.history(
            context.asset,
            "close",
            bar_count=long_window_days,
            frequency="1d",
        )
        if history.isna().any():
            record(price=data.current(context.asset, "price"), invested=0)
            return

        short_ma = history.tail(short_window_days).mean()
        long_ma = history.mean()
        target_weight = 1.0 if short_ma > long_ma else 0.0
        order_target_percent(context.asset, target_weight)
        record(
            price=data.current(context.asset, "price"),
            short_ma=short_ma,
            long_ma=long_ma,
            invested=target_weight,
        )

    return handle_data


def run_moving_average_backtest(
    ticker: str,
    start: str,
    end: str,
    bundle: str,
    short_window_days: int,
    long_window_days: int,
    capital_base_krw: float,
) -> pd.DataFrame:
    return run_algorithm(
        start=pd.Timestamp(start),
        end=pd.Timestamp(end),
        initialize=make_initialize(ticker),
        handle_data=make_handle_data(short_window_days, long_window_days),
        capital_base=capital_base_krw,
        data_frequency="daily",
        bundle=bundle,
    )


def save_backtest_outputs(
    performance: pd.DataFrame,
    ticker: str,
    results_dir: Path,
) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / f"{ticker}_performance.csv"
    chart_path = results_dir / f"{ticker}_equity_curve.png"

    performance.to_csv(csv_path)

    ax = performance["portfolio_value"].plot(figsize=(10, 5), title=f"{ticker} portfolio value")
    ax.set_xlabel("date")
    ax.set_ylabel("portfolio value")
    ax.figure.tight_layout()
    ax.figure.savefig(chart_path, dpi=150)
    plt.close(ax.figure)

    return csv_path, chart_path
