"""다종목 벤치마크: 동일 비중 리밸런싱을 Python/legacy/persistent 코어로 비교한다.

실제 KRX fixture 또는 그 가격 경로를 복제한 synthetic universe에서 실행 시간, 결과 signature,
peak RSS와 raw sample을 기록한다.

사용법:
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core python
    uv run python scripts/bench_universe.py <원장 디렉토리> --instruments 100 --core all --profile
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
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot, PriceField
from backtest_engine.types.requirements import (
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext


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
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--json-out", type=Path)
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
    bars = loaded.bars
    if args.synthetic:
        instruments, bars = synthetic_universe(bars, args.instruments)
    feed = DataFeed(bars)
    cores = ("python", "rust_legacy", "rust") if args.core == "all" else (args.core,)
    print(
        f"instruments={len(instruments)} sessions={len(feed)} "
        f"bars={len(bars)} cores={','.join(cores)} repeat={args.repeat} warmup={args.warmup}"
    )

    def run_once(core: str) -> tuple[float, tuple[float, int, int]]:
        engine = BacktestEngine(
            RunConfig(run_id=f"bench-{core}", initial_cash=1_000_000_000, fee_bps=15),
            core=core,
        )
        strategy = EqualWeightRebalance(instruments, args.every, 0.9)
        profiler = cProfile.Profile() if args.profile else None
        if profiler is not None:
            profiler.enable()
        started = time.perf_counter()
        result = engine.run(strategy, feed)
        elapsed = time.perf_counter() - started
        if profiler is not None:
            profiler.disable()
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)
        final_equity = result.snapshots[-1].equity if result.snapshots else float("nan")
        return elapsed, (final_equity, len(result.orders), len(result.fills))

    for core in cores:
        for _ in range(args.warmup):
            run_once(core)

    elapsed_by_core: dict[str, list[float]] = {core: [] for core in cores}
    signature_by_core: dict[str, tuple[float, int, int]] = {}
    for _ in range(args.repeat):
        for core in cores:
            elapsed, signature = run_once(core)
            elapsed_by_core[core].append(elapsed)
            previous = signature_by_core.setdefault(core, signature)
            if previous != signature:
                raise RuntimeError(
                    f"non-deterministic result for {core}: {previous} != {signature}"
                )

    baseline = statistics.median(elapsed_by_core["python"]) if "python" in cores else None
    payload: dict[str, object] = {
        "workload": {
            "instruments": len(instruments),
            "sessions": len(feed),
            "bars": len(bars),
            "rebalance_every": args.every,
            "synthetic": args.synthetic,
            "repeat": args.repeat,
            "warmup": args.warmup,
        },
        "cores": {},
    }
    core_payload = payload["cores"]
    assert isinstance(core_payload, dict)
    for core in cores:
        samples = elapsed_by_core[core]
        median = statistics.median(samples)
        final_equity, orders, fills = signature_by_core[core]
        speedup = baseline / median if baseline is not None else None
        core_payload[core] = {
            "samples_seconds": samples,
            "median_seconds": median,
            "speedup_vs_python": speedup,
            "final_equity": final_equity,
            "orders": orders,
            "fills": fills,
            "process_peak_rss_bytes": peak_rss_bytes(),
        }
        speedup_text = f" speedup={speedup:.3f}x" if speedup is not None else ""
        print(
            f"core={core} median={median:.6f}s samples="
            f"{','.join(f'{sample:.6f}' for sample in samples)}{speedup_text} "
            f"orders={orders} fills={fills} final_equity={final_equity:,.0f} "
            f"process_peak_rss={peak_rss_bytes() / 1024 / 1024:.1f}MiB"
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
