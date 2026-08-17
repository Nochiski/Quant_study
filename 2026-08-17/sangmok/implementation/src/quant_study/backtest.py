from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from zipline import run_algorithm
from zipline.api import (
    order_target_percent,
    record,
    set_benchmark,
    set_commission,
    set_slippage,
    symbol,
)
from zipline.finance import commission, slippage


class StrategyKind(StrEnum):
    MOVING_AVERAGE = "ma-trend"
    BUY_AND_HOLD = "buy-hold"
    PRICE_ABOVE_MA = "price-above-ma"


class RebalanceFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


def rebalance_period(timestamp: pd.Timestamp, frequency: RebalanceFrequency) -> str:
    if frequency is RebalanceFrequency.DAILY:
        return timestamp.date().isoformat()
    if frequency is RebalanceFrequency.WEEKLY:
        iso_year, iso_week, _ = timestamp.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return timestamp.strftime("%Y-%m")


def make_initialize(ticker: str, transaction_cost_bps: float = 0.0):
    def initialize(context) -> None:
        context.asset = symbol(ticker)
        context.last_rebalance_period = None
        context.target_weight = 0.0
        set_benchmark(context.asset)
        set_commission(
            us_equities=commission.PerDollar(cost=transaction_cost_bps / 10_000)
        )
        set_slippage(us_equities=slippage.FixedSlippage(spread=0.0))

    return initialize


def make_handle_data(
    short_window_days: int,
    long_window_days: int,
    *,
    strategy: StrategyKind = StrategyKind.MOVING_AVERAGE,
    rebalance: RebalanceFrequency = RebalanceFrequency.DAILY,
    allocation: float = 1.0,
):
    if short_window_days >= long_window_days:
        raise ValueError(
            "short window must be smaller than long window: "
            f"short_window_days={short_window_days} long_window_days={long_window_days}"
        )
    if not 0 <= allocation <= 1:
        raise ValueError(f"allocation must be between 0 and 1: allocation={allocation}")

    def handle_data(context, data) -> None:
        history = data.history(
            context.asset,
            "close",
            # XKRX 캘린더와 원본 CSV의 휴장일 차이를 흡수할 여유분을 요청한다.
            bar_count=long_window_days * 2,
            frequency="1d",
        ).dropna()
        if len(history) < long_window_days:
            record(
                price=data.current(context.asset, "price"),
                short_ma=float("nan"),
                long_ma=float("nan"),
                invested=context.target_weight,
            )
            return

        history = history.tail(long_window_days)
        short_ma = history.tail(short_window_days).mean()
        long_ma = history.mean()
        current_period = rebalance_period(pd.Timestamp(context.get_datetime()), rebalance)
        should_rebalance = current_period != context.last_rebalance_period

        if should_rebalance and data.can_trade(context.asset):
            if strategy is StrategyKind.BUY_AND_HOLD:
                target_weight = allocation
            elif strategy is StrategyKind.PRICE_ABOVE_MA:
                current_price = data.current(context.asset, "price")
                target_weight = allocation if current_price > long_ma else 0.0
            else:
                target_weight = allocation if short_ma > long_ma else 0.0

            order_target_percent(context.asset, target_weight)
            context.target_weight = target_weight
            context.last_rebalance_period = current_period

        record(
            price=data.current(context.asset, "price"),
            short_ma=short_ma,
            long_ma=long_ma,
            invested=context.target_weight,
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
    strategy: StrategyKind = StrategyKind.MOVING_AVERAGE,
    rebalance: RebalanceFrequency = RebalanceFrequency.DAILY,
    allocation: float = 1.0,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    environ: dict[str, str] | None = None,
) -> pd.DataFrame:
    zipline_environ = environ if environ is not None else None
    return run_algorithm(
        start=pd.Timestamp(start),
        end=pd.Timestamp(end),
        initialize=make_initialize(ticker, transaction_cost_bps=fee_bps + slippage_bps),
        handle_data=make_handle_data(
            short_window_days,
            long_window_days,
            strategy=strategy,
            rebalance=rebalance,
            allocation=allocation,
        ),
        capital_base=capital_base_krw,
        data_frequency="daily",
        bundle=bundle,
        default_extension=zipline_environ is None,
        **({"environ": zipline_environ} if zipline_environ is not None else {}),
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
