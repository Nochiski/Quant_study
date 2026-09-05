from __future__ import annotations

import argparse
from pathlib import Path
from shutil import copyfile

from quant_study.backtest import run_moving_average_backtest, save_backtest_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Zipline moving average backtest.")
    parser.add_argument("--ticker", default="005930")
    parser.add_argument("--start", default="2021-01-04")
    parser.add_argument("--end", default="2024-12-30")
    parser.add_argument("--bundle", default="krx-csvdir")
    parser.add_argument("--short-window", type=int, default=20)
    parser.add_argument("--long-window", type=int, default=60)
    parser.add_argument("--capital-base", type=float, default=10_000_000.0)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument(
        "--web-output",
        type=Path,
        default=Path("public/backtests/latest_performance.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    performance = run_moving_average_backtest(
        ticker=args.ticker,
        start=args.start,
        end=args.end,
        bundle=args.bundle,
        short_window_days=args.short_window,
        long_window_days=args.long_window,
        capital_base_krw=args.capital_base,
    )
    csv_path, chart_path = save_backtest_outputs(
        performance=performance,
        ticker=args.ticker,
        results_dir=args.results_dir,
    )
    final_value = performance["portfolio_value"].iloc[-1]
    args.web_output.parent.mkdir(parents=True, exist_ok=True)
    copyfile(csv_path, args.web_output)
    print(f"saved performance CSV: {csv_path}")
    print(f"saved equity curve: {chart_path}")
    print(f"saved web CSV: {args.web_output}")
    print(f"final portfolio value: {final_value:,.0f}")


if __name__ == "__main__":
    main()
