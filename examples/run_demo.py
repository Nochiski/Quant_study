"""005930(삼성전자) 일봉 CSV로 골든크로스 전략을 실행하는 데모.

사용법:
    uv run python examples/run_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from golden_cross import GoldenCrossConfig, GoldenCrossStrategy

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.csv_loader import OhlcPolicy, load_bars_csv
from backtest_engine.data.feed import DataFeed
from backtest_engine.types.instruments import AssetClass, InstrumentId

CSV_PATH = (
    Path(__file__).resolve().parent.parent
    / "2026-08-17/sangmok/result/html/app/market_data/005930.csv"
)


def main() -> int:
    instrument = InstrumentId(
        venue="XKRX", symbol="005930", asset_class=AssetClass.EQUITY, currency="KRW"
    )
    # PyKRX 수정주가에는 close > high인 행이 있어 명시적 CLAMP 정책으로 정제한다.
    loaded = load_bars_csv(CSV_PATH, instrument, ohlc_policy=OhlcPolicy.CLAMP)
    if not loaded.ok:
        print(f"failed to load bars: status={loaded.status.value} detail={loaded.detail}")
        return 1
    if loaded.repaired_rows:
        print(f"[data prep] clamped OHLC on {loaded.repaired_rows} rows")
    if loaded.dropped_rows:
        print(f"[data prep] dropped {loaded.dropped_rows} halt rows (non-positive price)")

    feed = DataFeed(loaded.bars)
    config = RunConfig(run_id="demo-005930-golden-cross", initial_cash=10_000_000, fee_bps=15)
    strategy = GoldenCrossStrategy(GoldenCrossConfig(instrument=instrument))

    result = BacktestEngine(config).run(strategy, feed)

    first, last = result.snapshots[0], result.snapshots[-1]
    metrics = result.metrics
    print(f"run_id           {result.run_id}")
    print(f"sessions         {len(result.snapshots)}  ({first.ts.date()} → {last.ts.date()})")
    print(f"initial equity   {first.equity:,.0f} KRW")
    print(f"final equity     {last.equity:,.0f} KRW")
    print(f"orders / fills   {len(result.orders)} / {len(result.fills)}")
    print(f"total return     {metrics.total_return:+.2%}")
    print(f"cagr             {metrics.cagr:+.2%}")
    print(f"volatility       {metrics.volatility:.2%}")
    sharpe_text = f"{metrics.sharpe:.2f}" if metrics.sharpe is not None else "n/a"
    print(f"sharpe           {sharpe_text}")
    print(f"max drawdown     {metrics.max_drawdown:.2%}")
    print(f"turnover         {metrics.turnover:.2f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
