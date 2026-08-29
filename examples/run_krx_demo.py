"""KRX 원장 parquet(레포 슬라이스 또는 실제 빌드 디렉토리)로 골든크로스 전략을 실행하는 데모.

포트/어댑터 경계를 보여 주는 예제: 데이터 소스는 `KrxParquetBarSource` 한 줄로 바뀌고,
엔진·전략은 `BarQuery`/`LoadResult`/`DataFeed`만 본다.

사용법:
    uv sync --extra parquet
    uv run python examples/run_krx_demo.py                 # tests/fixtures/krx_parquet 슬라이스
    uv run python examples/run_krx_demo.py <원장 디렉토리>  # quant-data 빌드 전체
    uv run python examples/run_krx_demo.py [<디렉토리>] --core rust   # Rust 코어 사용
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

from golden_cross import GoldenCrossConfig, GoldenCrossStrategy

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.adapters.krx_parquet import (
    KrxParquetBarSource,
    KrxParquetCorporateActionSource,
)
from backtest_engine.data.feed import DataFeed
from backtest_engine.ports.corporate_actions import CorporateActionQuery, CorporateActionSource
from backtest_engine.ports.market_data import BarQuery, BarSource, OhlcPolicy
from backtest_engine.types.instruments import AssetClass, InstrumentId

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "krx_parquet"


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    core = "python"
    if "--core" in args:
        index = args.index("--core")
        if index + 1 >= len(args):
            print("usage: --core <python|rust> — missing value after --core", file=sys.stderr)
            return 2
        core = args[index + 1]
        del args[index : index + 2]
    root = Path(args[0]) if args else FIXTURE_DIR
    instrument = InstrumentId(
        venue="XKRX", symbol="005930", asset_class=AssetClass.EQUITY, currency="KRW"
    )
    # 원장은 원주가다. 2018-05 액면분할(50:1) 이전 구간을 넣으면 가격 불연속이
    # 그대로 수익률에 잡히므로 분할 이후 구간만 쓴다.
    query = BarQuery(
        instruments=(instrument,),
        start=date(2018, 6, 1),
        end=date(2024, 12, 31),
        ohlc_policy=OhlcPolicy.CLAMP,
    )
    source: BarSource = KrxParquetBarSource(root)
    loaded = source.load_bars(query)
    if not loaded.ok:
        print(f"failed to load bars: status={loaded.status.value} detail={loaded.detail}")
        return 1
    if loaded.repaired_rows:
        print(f"[data prep] clamped OHLC on {loaded.repaired_rows} rows")
    if loaded.dropped_rows:
        print(f"[data prep] dropped {loaded.dropped_rows} halt rows (volume 0 / price 0)")

    # 자본변동(액면분할 등)은 같은 원장에서 검출해 엔진에 넘긴다. 이 기간에는 사건이 없다.
    action_source: CorporateActionSource = KrxParquetCorporateActionSource(root)
    actions = action_source.load_actions(
        CorporateActionQuery(instruments=(instrument,), start=query.start, end=query.end)
    )
    if not actions.ok:
        print(
            f"failed to load corporate actions: status={actions.status.value} "
            f"detail={actions.detail}"
        )
        return 1
    if actions.actions:
        print(f"[data prep] {len(actions.actions)} corporate action(s) in period")

    feed = DataFeed(loaded.bars)
    config = RunConfig(run_id="demo-krx-005930-golden-cross", initial_cash=10_000_000, fee_bps=15)
    started = time.perf_counter()
    result = BacktestEngine(config, core=core).run(
        GoldenCrossStrategy(GoldenCrossConfig(instrument=instrument)),
        feed,
        corporate_actions=actions.actions,
    )
    elapsed_s = time.perf_counter() - started

    first, last = result.snapshots[0], result.snapshots[-1]
    metrics = result.metrics
    print(f"source           {root}")
    print(f"core             {core} ({elapsed_s:.3f}s engine wall-clock)")
    print(f"run_id           {result.run_id}")
    print(f"sessions         {len(result.snapshots)}  ({first.ts.date()} -> {last.ts.date()})")
    print(f"initial equity   {first.equity:,.0f} KRW")
    print(f"final equity     {last.equity:,.0f} KRW")
    print(f"orders / fills   {len(result.orders)} / {len(result.fills)}")
    print(f"total return     {metrics.total_return:+.2%}")
    print(f"cagr             {metrics.cagr:+.2%}")
    print(f"max drawdown     {metrics.max_drawdown:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
