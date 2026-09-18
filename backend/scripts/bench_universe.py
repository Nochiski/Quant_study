"""다종목 벤치마크: 동일 비중 리밸런싱을 Python/legacy/persistent 코어로 비교한다.

실제 KRX fixture 또는 그 가격 경로를 복제한 synthetic universe에서 실행 시간, 결과 signature,
peak RSS와 raw sample을 기록한다.

전략 형태는 둘이다. `callback`은 세션마다 Python `on_event`가 호출되는 일반 전략,
`tape`는 같은 목표를 미리 표로 만든 선언형 전략(`DeclarativeTapeStrategy`)이라 persistent Rust
코어에서 Python 콜백 없이 완주한다 (워크벤치 TargetTape 경로와 같은 형태).

측정 경계는 `engine.run()`과 결과 조회(`snapshots`/`orders`/`fills`) 둘로 나눠 잰다. rust 코어는
`BacktestResult`가 lazy라 결과를 읽는 시점에 공개 객체를 만들고 python 코어는 `run()` 안에서 이미
다 만든다. `run()`만 재면 rust 쪽 비용이 배수에서 빠지므로 `speedup_vs_python`은 두 구간의 합
(total)을 기준으로 한다.

`--core all`은 프로세스 공유라 RSS가 격리되지 않는다 — 세 코어가 같은 프로세스 peak를 받는다.
Peak RSS 정본은 `--core <one>` 단독 실행이다.

사용법:
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core python
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core all --profile
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core all \
        --strategy tape
"""

from __future__ import annotations

import argparse
import cProfile
import ctypes
import json
import math
import pstats
import statistics
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.adapters.krx_parquet import KrxParquetBarSource, KrxParquetUniverseSource
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.tape import evaluate_tape
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
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot, PriceField
from backtest_engine.types.requirements import (
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from backtest_engine.types.tape import TapeFrame


@dataclass(frozen=True)
class Timing:
    """한 회차의 구간별 wall time과 그 시점까지의 프로세스 peak RSS."""

    run_seconds: float
    materialize_seconds: float
    peak_rss_after_materialize_bytes: int

    @property
    def total_seconds(self) -> float:
        return self.run_seconds + self.materialize_seconds


def peak_rss_bytes() -> int:
    """Return this process's peak resident set size on Windows, Linux, and macOS."""
    if sys.platform == "win32":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        )
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        current_process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(current_process, ctypes.byref(counters), counters.cb)
        if not ok:
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.PeakWorkingSetSize)

    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1_024)


class EqualWeightRebalance:
    """매 `every` 세션마다 그 세션에 bar가 있는 종목을 동일 비중으로 리밸런싱한다."""

    def __init__(
        self, instruments: tuple[InstrumentId, ...], every: int, allocation: float
    ) -> None:
        self._instruments = frozenset(instruments)
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


class EqualWeightTape:
    """`EqualWeightRebalance`와 같은 목표를 세션 날짜 → 프레임 표로 미리 만든 선언형 전략.

    콜백 k(1부터)는 세션 k-1이므로 `k % every == 1`은 `session_index % every == 0`이다. 이 동치는
    callback 전략의 warmup(lookback=1)이 첫 세션 dispatch를 미루지 않을 때만 성립한다 — lookback을
    올리면 두 전략의 리밸런싱 세션이 어긋나므로 그때는 이 표도 같은 warmup만큼 밀어야 한다.
    """

    idle_reason = "tape_idle"

    def __init__(
        self, instruments: tuple[InstrumentId, ...], every: int, allocation: float, feed: DataFeed
    ) -> None:
        universe = frozenset(instruments)
        frames: dict[date, TapeFrame] = {}
        for session_index, snapshot in enumerate(feed.snapshots()):
            if session_index % every != 0:
                continue
            active = tuple(bar.instrument for bar in snapshot.bars if bar.instrument in universe)
            if not active:
                continue
            weight = allocation / len(active)
            frames[snapshot.ts.date()] = TapeFrame(
                action=SetPortfolioTarget(
                    targets=tuple(WeightTarget(i, weight) for i in active),
                    scope=TargetScope.REPLACE,
                    execution=ExecutionPolicy.market_next_open(),
                ),
                reason=f"tape:{session_index}",
            )
        self._frames = frames

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET}),
            features=frozenset(),
        )

    def tape_frames(self) -> Mapping[date, TapeFrame]:
        return self._frames

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        return evaluate_tape(self._frames, self.idle_reason, ctx, event)


class FixtureLoadError(RuntimeError):
    """KRX fixture 로딩 실패. 이 스크립트와 `bench_workbench_adapter.py`가 함께 쓴다."""


@dataclass(frozen=True)
class FixtureUniverse:
    """마스터 캘린더 전 구간에 상장된 종목과 그 bar."""

    instruments: tuple[InstrumentId, ...]
    bars: tuple[Bar, ...]


def load_full_calendar_universe(
    root: Path, *, start: date, end: date, limit: int
) -> FixtureUniverse:
    """fixture에서 전 구간 상장 종목 `limit`개와 그 bar를 읽는다.

    정지·상폐로 bar가 빠지는 종목은 벤치마크 노이즈이므로 마스터 캘린더의 첫·마지막 세션을
    모두 가진 종목만 남긴다. synthetic 유니버스를 만들 때도 이 함수가 고른 종목이 템플릿이다.

    Raises:
        FixtureLoadError: universe 또는 bar 로딩이 실패했을 때. detail과 조회 조건을 담는다.
    """
    universe = KrxParquetUniverseSource(root).load_universe(
        UniverseQuery(venue="XKRX", start=start, end=end)
    )
    if not universe.ok:
        raise FixtureLoadError(
            f"universe load failed: root={root} start={start} end={end} detail={universe.detail}"
        )
    first = min(item.first_session for item in universe.memberships)
    last = max(item.last_session for item in universe.memberships)
    full = [
        item.instrument
        for item in universe.memberships
        if item.first_session == first and item.last_session == last
    ]
    if not full:
        raise FixtureLoadError(
            f"no instrument spans the whole calendar: root={root} first={first} last={last} "
            f"memberships={len(universe.memberships)}"
        )
    instruments = tuple(full[:limit])
    loaded = KrxParquetBarSource(root).load_bars(
        BarQuery(instruments=instruments, start=start, end=end, ohlc_policy=OhlcPolicy.CLAMP)
    )
    if not loaded.ok:
        raise FixtureLoadError(
            f"bars load failed: root={root} instruments={len(instruments)} "
            f"start={start} end={end} detail={loaded.detail}"
        )
    return FixtureUniverse(instruments=instruments, bars=loaded.bars)


def synthetic_universe(
    bars: tuple[Bar, ...], size: int
) -> tuple[tuple[InstrumentId, ...], tuple[Bar, ...]]:
    """Clone one complete price history into a deterministic order-heavy universe."""
    by_instrument: dict[InstrumentId, list[Bar]] = {}
    for bar in bars:
        by_instrument.setdefault(bar.instrument, []).append(bar)
    template = max(by_instrument.values(), key=len)
    template.sort(key=lambda bar: bar.ts)
    instruments = tuple(
        InstrumentId("XKRX", f"SYN{i:06d}", AssetClass.EQUITY, "KRW") for i in range(size)
    )
    expanded = tuple(
        Bar(
            ts=bar.ts,
            instrument=instrument,
            open=bar.open * factor,
            high=bar.high * factor,
            low=bar.low * factor,
            close=bar.close * factor,
            volume=bar.volume,
        )
        for session_index, bar in enumerate(template)
        for instrument_index, instrument in enumerate(instruments)
        for factor in (
            math.exp(0.10 * math.sin(session_index * 0.12 + instrument_index * 0.37)),
        )
    )
    return instruments, expanded


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--instruments", type=int, default=100)
    parser.add_argument(
        "--core",
        choices=("python", "rust", "rust_legacy", "rust_persistent", "all"),
        default="python",
    )
    parser.add_argument("--every", type=int, default=5)
    parser.add_argument("--strategy", choices=("callback", "tape"), default="callback")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2020, 1, 1))
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2024, 12, 31))
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args(argv[1:])

    try:
        fixture = load_full_calendar_universe(
            args.root, start=args.start, end=args.end, limit=args.instruments
        )
    except FixtureLoadError as error:
        print(error)
        return 1
    instruments, bars = fixture.instruments, fixture.bars
    if args.synthetic:
        instruments, bars = synthetic_universe(bars, args.instruments)
    feed = DataFeed(bars)
    cores = ("python", "rust_legacy", "rust") if args.core == "all" else (args.core,)
    print(
        f"instruments={len(instruments)} sessions={len(feed)} "
        f"bars={len(bars)} cores={','.join(cores)} strategy={args.strategy} "
        f"repeat={args.repeat} warmup={args.warmup}"
    )

    def run_once(core: str) -> tuple[Timing, tuple[float, int, int]]:
        engine = BacktestEngine(
            RunConfig(run_id=f"bench-{core}", initial_cash=1_000_000_000, fee_bps=15),
            core=core,
        )
        strategy = (
            EqualWeightTape(instruments, args.every, 0.9, feed)
            if args.strategy == "tape"
            else EqualWeightRebalance(instruments, args.every, 0.9)
        )
        profiler = cProfile.Profile() if args.profile else None
        if profiler is not None:
            profiler.enable()
        started = time.perf_counter()
        result = engine.run(strategy, feed)
        run_seconds = time.perf_counter() - started
        # 결과 조회를 타이머 안에 넣는다 — rust 코어는 여기서 공개 객체를 만들고 python 코어는
        # run() 안에서 이미 만들었다. 두 코어를 같은 경계로 재야 배수가 뜻을 가진다.
        materialize_started = time.perf_counter()
        snapshots, orders, fills = result.snapshots, result.orders, result.fills
        materialize_seconds = time.perf_counter() - materialize_started
        if profiler is not None:
            profiler.disable()
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)
        final_equity = snapshots[-1].equity if snapshots else float("nan")
        return (
            Timing(run_seconds, materialize_seconds, peak_rss_bytes()),
            (final_equity, len(orders), len(fills)),
        )

    for core in cores:
        for _ in range(args.warmup):
            run_once(core)

    timings_by_core: dict[str, list[Timing]] = {core: [] for core in cores}
    signature_by_core: dict[str, tuple[float, int, int]] = {}
    for _ in range(args.repeat):
        for core in cores:
            timing, signature = run_once(core)
            timings_by_core[core].append(timing)
            previous = signature_by_core.setdefault(core, signature)
            if previous != signature:
                raise RuntimeError(
                    f"non-deterministic result for {core}: {previous} != {signature}"
                )

    def medians(core: str) -> tuple[float, float, float]:
        timings = timings_by_core[core]
        return (
            statistics.median(timing.run_seconds for timing in timings),
            statistics.median(timing.materialize_seconds for timing in timings),
            statistics.median(timing.total_seconds for timing in timings),
        )

    baseline = medians("python") if "python" in cores else None
    core_payload: dict[str, object] = {}
    payload: dict[str, object] = {
        "workload": {
            "instruments": len(instruments),
            "sessions": len(feed),
            "bars": len(bars),
            "rebalance_every": args.every,
            "strategy": args.strategy,
            "synthetic": args.synthetic,
            "repeat": args.repeat,
            "warmup": args.warmup,
            # --core all은 한 프로세스라 코어별 RSS가 격리되지 않는다.
            "rss_isolated": len(cores) == 1,
        },
        "cores": core_payload,
    }
    for core in cores:
        timings = timings_by_core[core]
        run_median, materialize_median, total_median = medians(core)
        final_equity, orders, fills = signature_by_core[core]
        if baseline is None:
            speedup = None
            run_speedup = None
        else:
            baseline_run, _, baseline_total = baseline
            speedup = baseline_total / total_median
            run_speedup = baseline_run / run_median
        core_payload[core] = {
            "run_seconds_samples": [timing.run_seconds for timing in timings],
            "materialize_seconds_samples": [timing.materialize_seconds for timing in timings],
            "total_seconds_samples": [timing.total_seconds for timing in timings],
            "run_median_seconds": run_median,
            "materialize_median_seconds": materialize_median,
            "median_seconds": total_median,
            "speedup_vs_python": speedup,
            "run_speedup_vs_python": run_speedup,
            "final_equity": final_equity,
            "orders": orders,
            "fills": fills,
            "peak_rss_after_materialize_bytes": timings[-1].peak_rss_after_materialize_bytes,
            "process_peak_rss_bytes": peak_rss_bytes(),
        }
        speedup_text = (
            f" speedup={speedup:.3f}x(run {run_speedup:.3f}x)"
            if speedup is not None and run_speedup is not None
            else ""
        )
        print(
            f"core={core} run={run_median:.6f}s materialize={materialize_median:.6f}s "
            f"total={total_median:.6f}s{speedup_text} "
            f"orders={orders} fills={fills} final_equity={final_equity:,.0f} "
            f"peak_rss={timings[-1].peak_rss_after_materialize_bytes / 1024 / 1024:.1f}MiB"
        )

    if len(signature_by_core) > 1 and len(set(signature_by_core.values())) != 1:
        raise RuntimeError(f"core result mismatch: {signature_by_core}")
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"json={args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
