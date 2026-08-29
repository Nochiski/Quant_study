"""다종목 벤치마크: 유니버스 N종목 동일 비중 리밸런싱을 두 코어로 돌려 시간과 프로파일을 남긴다.

6d(세션 루프 Rust 이전)의 선행 조건 — 병목이 실제로 어디인지 측정한다.

사용법:
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core python
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core rust --profile
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import sys
import time
from datetime import date
from pathlib import Path

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.adapters.krx_parquet import KrxParquetBarSource, KrxParquetUniverseSource
from backtest_engine.data.feed import DataFeed
from backtest_engine.ports.market_data import BarQuery, OhlcPolicy
from backtest_engine.ports.universe import UniverseQuery
from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    SetPortfolioTarget,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot, PriceField
from backtest_engine.types.requirements import (
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext


class EqualWeightRebalance:
    """매 `every` 세션마다 그 세션에 bar가 있는 종목을 동일 비중으로 리밸런싱한다."""

    def __init__(
        self, instruments: tuple[InstrumentId, ...], every: int, allocation: float
    ) -> None:
        self._instruments = instruments
        self._every = every
        self._allocation = allocation
        self._calls = 0
        self.prices = HistoryRequest(instruments=instruments, field=PriceField.CLOSE, lookback=1)

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(self.prices,),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET}),
            features=frozenset(),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        self._calls += 1
        if not isinstance(event, MarketSnapshot) or self._calls % self._every != 1:
            return StrategyDecision.no_action(ctx.now)
        active = tuple(bar.instrument for bar in event.bars if bar.instrument in self._instruments)
        if not active:
            return StrategyDecision.no_action(ctx.now)
        weight = self._allocation / len(active)
        return StrategyDecision.of(
            ctx.now,
            SetPortfolioTarget(
                targets=tuple(WeightTarget(i, weight) for i in active),
                scope=TargetScope.REPLACE,
                execution=ExecutionPolicy.market_next_open(),
            ),
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--instruments", type=int, default=100)
    parser.add_argument("--core", default="python")
    parser.add_argument("--every", type=int, default=5)
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2020, 1, 1))
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2024, 12, 31))
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args(argv[1:])

    universe = KrxParquetUniverseSource(args.root).load_universe(
        UniverseQuery(venue="XKRX", start=args.start, end=args.end)
    )
    if not universe.ok:
        print(f"universe load failed: {universe.detail}")
        return 1
    # 마스터 캘린더 전 구간에 상장된 종목만 (정지·상폐로 bar가 빠지는 종목은 벤치마크 노이즈).
    first = min(m.first_session for m in universe.memberships)
    last = max(m.last_session for m in universe.memberships)
    full = [
        m.instrument
        for m in universe.memberships
        if m.first_session == first and m.last_session == last
    ]
    instruments = tuple(full[: args.instruments])
    loaded = KrxParquetBarSource(args.root).load_bars(
        BarQuery(
            instruments=instruments, start=args.start, end=args.end, ohlc_policy=OhlcPolicy.CLAMP
        )
    )
    if not loaded.ok:
        print(f"bars load failed: {loaded.detail}")
        return 1
    feed = DataFeed(loaded.bars)
    print(
        f"instruments={len(instruments)} sessions={len(feed)} "
        f"bars={len(loaded.bars)} core={args.core}"
    )

    engine = BacktestEngine(
        RunConfig(run_id=f"bench-{args.core}", initial_cash=1_000_000_000, fee_bps=15),
        core=args.core,
    )
    strategy = EqualWeightRebalance(instruments, args.every, 0.9)
    started = time.perf_counter()
    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()
        result = engine.run(strategy, feed)
        profiler.disable()
        elapsed = time.perf_counter() - started
        stats = pstats.Stats(profiler).sort_stats("cumulative")
        stats.print_stats(25)
    else:
        result = engine.run(strategy, feed)
        elapsed = time.perf_counter() - started
    print(
        f"elapsed={elapsed:.2f}s fills={len(result.fills)} orders={len(result.orders)} "
        f"final_equity={result.snapshots[-1].equity:,.0f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
