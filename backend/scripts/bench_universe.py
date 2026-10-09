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

`--density`는 synthetic 유니버스의 세션 × 종목 격자를 비워 희소 피드를 만든다. Rust feed의
행 조회표는 밀도 20% 미만에서 `RowIndex::Sparse`(세션별 HashMap)를, 그 이상에서 `Dense`를
고르므로 이 옵션 없이는 Sparse 경로가 한 번도 돌지 않는다. 어느 쪽이 뽑혔는지는 Rust가
노출하지 않으므로 같은 규칙을 Python에서 재현해 JSON `workload.row_index_expected`에 남긴다.

사용법:
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core python
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core all --profile
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core all \
        --strategy tape
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 300 --synthetic \
        --density 0.15 --core all --strategy tape
"""

from __future__ import annotations

import argparse
import cProfile
import ctypes
import importlib
import json
import math
import pstats
import statistics
import subprocess
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


def listing_windows(sessions: int, size: int, density: float) -> tuple[tuple[int, int], ...]:
    """종목별 상장 구간 `[start, end)`를 세션 인덱스로 돌려준다.

    밀도 `density`를 맞추는 방법은 여럿이지만 여기서는 **순차 상장**을 쓴다. 모든 종목이
    같은 길이 `round(sessions * density)`의 연속 구간을 갖고, 시작 세션만 0 ~ `sessions -
    window` 사이에 고르게 흩어진다. 종목마다 bar 수가 같으므로 실제 밀도가 `density`와
    정확히 같고, 구간이 연속이라 상장·상폐가 한 번씩만 일어나는 실제 누적 유니버스와 같은
    모양이다 (세포 단위 의사난수로 비우면 한 종목이 살아 있는 내내 bar가 깜빡거려 실제
    원장에 없는 패턴이 된다).

    `size == 1`이면 시작이 0 하나뿐이라 뒤쪽 세션에 bar가 없어 feed 세션 수 자체가 줄고
    실효 밀도는 1.0이 된다. 희소 경로 측정은 종목 수가 충분할 때만 뜻이 있다.
    """
    window = max(1, min(sessions, round(sessions * density)))
    spread = sessions - window
    last = max(size - 1, 1)
    windows: list[tuple[int, int]] = []
    for index in range(size):
        start = spread * index // last
        windows.append((start, start + window))
    return tuple(windows)


def synthetic_universe(
    bars: tuple[Bar, ...], size: int, density: float = 1.0
) -> tuple[tuple[InstrumentId, ...], tuple[Bar, ...]]:
    """Clone one complete price history into a deterministic order-heavy universe.

    `density`가 1.0 미만이면 `listing_windows`의 순차 상장 구간 밖 bar를 비워 세션 × 종목
    격자를 희소하게 만든다. Rust `PersistentFeed`는 밀도 20% 미만에서 `RowIndex::Sparse`를
    고르므로(`feed.rs` `RowIndex` 문서) 그 경로를 재려면 0.2 미만을 준다.
    """
    by_instrument: dict[InstrumentId, list[Bar]] = {}
    for bar in bars:
        by_instrument.setdefault(bar.instrument, []).append(bar)
    template = max(by_instrument.values(), key=len)
    template.sort(key=lambda bar: bar.ts)
    instruments = tuple(
        InstrumentId("XKRX", f"SYN{i:06d}", AssetClass.EQUITY, "KRW") for i in range(size)
    )
    windows = listing_windows(len(template), size, density)
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
        if windows[instrument_index][0] <= session_index < windows[instrument_index][1]
        for factor in (
            math.exp(0.10 * math.sin(session_index * 0.12 + instrument_index * 0.37)),
        )
    )
    return instruments, expanded


# 행 조회표 표현 선택에 쓰는 바이트 상수의 정본은 Rust `feed.rs`이고
# `backtest_core.ROW_INDEX_BYTES`로 노출된다. 확장이 빌드되지 않은 환경(python 코어만 재는
# 실행)에서도 벤치가 돌아야 하므로 같은 리터럴로 폴백한다 — 폴백 값이 Rust와 어긋나면
# `tests/test_core_parity.py::test_row_index_bytes_match_bench_fallback`이 잡는다.
ROW_INDEX_BYTES_FALLBACK: Mapping[str, int] = {"dense_per_slot": 4, "sparse_per_row": 20}


def row_index_bytes() -> Mapping[str, int]:
    """Rust가 노출한 행 조회표 바이트 상수. 확장이 없으면 폴백 리터럴."""
    try:
        core = importlib.import_module("backtest_core")
    except ImportError:
        return ROW_INDEX_BYTES_FALLBACK
    exported = getattr(core, "ROW_INDEX_BYTES", None)
    if not isinstance(exported, dict):
        return ROW_INDEX_BYTES_FALLBACK
    return exported


def row_index_expected(sessions: int, instruments: int, rows: int) -> str:
    """Rust `PersistentFeed`가 고를 행 조회표 표현을 같은 규칙으로 재현한다.

    Rust는 어느 표현을 골랐는지 Python에 노출하지 않으므로 선택 규칙(`slots × 4B ≤ rows × 20B`)을
    여기서 다시 계산한다. 규칙은 복제하되 상수는 Rust에서 읽는다.
    """
    sizes = row_index_bytes()
    if sessions * instruments * sizes["dense_per_slot"] <= rows * sizes["sparse_per_row"]:
        return "dense"
    return "sparse"


def cpu_load_percent() -> int | None:
    """실행 직전 CPU 부하(%). Windows `Win32_Processor.LoadPercentage`를 읽는다.

    측정 결과를 해석할 때 필요한 조건이므로 JSON에 함께 남긴다 — 부하가 40%를 넘은 실행의
    절대값은 다른 실행과 비교할 수 없다. PowerShell이 없거나 값을 못 읽으면 `None`이고,
    그때는 부하를 알 수 없다는 뜻이지 0이라는 뜻이 아니다.
    """
    if sys.platform != "win32":
        return None
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor).LoadPercentage",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = completed.stdout.strip().splitlines()
    if not first:
        return None
    try:
        return int(first[0].strip())
    except ValueError:
        return None


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
    parser.add_argument(
        "--density",
        type=float,
        default=1.0,
        help=(
            "synthetic 유니버스의 세션 × 종목 격자 밀도 (0 초과 1 이하). 1.0 미만이면 종목마다 "
            "연속 상장 구간만 bar를 남겨 희소 피드를 만든다. 0.2 미만이면 Rust feed가 "
            "RowIndex::Sparse를 고른다."
        ),
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2020, 1, 1))
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2024, 12, 31))
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args(argv[1:])
    if not 0.0 < args.density <= 1.0:
        parser.error(f"--density는 0 초과 1 이하여야 한다 — 받은 값={args.density}")
    if args.density != 1.0 and not args.synthetic:
        parser.error("--density는 --synthetic 유니버스에서만 뜻이 있다")

    try:
        fixture = load_full_calendar_universe(
            args.root, start=args.start, end=args.end, limit=args.instruments
        )
    except FixtureLoadError as error:
        print(error)
        return 1
    instruments, bars = fixture.instruments, fixture.bars
    if args.synthetic:
        instruments, bars = synthetic_universe(bars, args.instruments, args.density)
    feed = DataFeed(bars)
    cores = ("python", "rust_legacy", "rust") if args.core == "all" else (args.core,)
    slots = len(feed) * len(instruments)
    bar_density = len(bars) / slots if slots else 1.0
    index_kind = row_index_expected(len(feed), len(instruments), len(bars))
    load = cpu_load_percent()
    print(
        f"instruments={len(instruments)} sessions={len(feed)} "
        f"bars={len(bars)} density={bar_density:.4f} row_index={index_kind} "
        f"cores={','.join(cores)} strategy={args.strategy} "
        f"repeat={args.repeat} warmup={args.warmup} "
        f"cpu_load={'unknown' if load is None else f'{load}%'}"
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
    # --core all은 한 프로세스라 코어별 RSS가 격리되지 않는다.
    rss_isolated = len(cores) == 1
    core_payload: dict[str, object] = {}
    payload: dict[str, object] = {
        "workload": {
            "instruments": len(instruments),
            "sessions": len(feed),
            "bars": len(bars),
            "rebalance_every": args.every,
            "strategy": args.strategy,
            "synthetic": args.synthetic,
            "requested_density": args.density,
            "bar_density": bar_density,
            "row_index_expected": index_kind,
            "cpu_load_percent": load,
            "repeat": args.repeat,
            "warmup": args.warmup,
            "rss_isolated": rss_isolated,
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
            # 코어가 프로세스를 공유하면 이 값은 그 코어의 것이 아니므로 null로 남긴다.
            "peak_rss_after_materialize_bytes": (
                timings[-1].peak_rss_after_materialize_bytes if rss_isolated else None
            ),
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
            + (
                f"peak_rss={timings[-1].peak_rss_after_materialize_bytes / 1024 / 1024:.1f}MiB"
                if rss_isolated
                else f"process_peak_rss={peak_rss_bytes() / 1024 / 1024:.1f}MiB(shared)"
            )
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
