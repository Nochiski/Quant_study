"""워크벤치 어댑터 e2e 구간별 벤치마크 (이슈 #98 Phase 3-2).

`BacktestEngineExecutorAdapter.execute`는 `engine.run()` 하나만 쓰는 게 아니라 그 뒤에
결과 조회·비용 집계·raw artifact 변환·지표 계산을 이어서 한다. Rust 코어로 `run()`을
아무리 줄여도 이 뒤쪽 구간은 그대로 Python이라, 워크벤치가 체감하는 배수는 엔진 배수보다
낮다. 이 스크립트는 그 경계를 코어별로 나눠 재서 후속 최적화의 타깃을 정한다.

측정 구간:

- `dataset_to_engine_inputs`: `execute()` 시작 → `BacktestEngine` 생성. dataset 레코드를
  엔진 `Bar`·`UniverseResult`·`CorporateActionEvent`로 옮기는 구간이다.
- `strategy_and_feed_build`: `BacktestEngine` 생성 → `run()` 진입. `TargetTapeStrategy`가
  TargetTape 프레임을 엔진 액션으로 옮기고 `DataFeed`가 bar를 세션으로 묶는 구간이다.
- `engine.run`: 엔진 루프만.
- `result_materialize`: `result.snapshots`/`orders`/`fills` 최초 조회. Rust 코어는 여기서
  공개 객체를 만들고 Python 코어는 `run()` 안에서 이미 만들었으므로 거의 0이다.
- `event_store_costs`: `engine.event_store.costs()`.
- `artifacts`: `_artifacts()` — 엔진 결과를 raw artifact 레코드로 변환.
- `analysis_points`: `_artifacts()` 반환 → 첫 `compute_analytics()` 진입. 벤치마크 시계열,
  `AnalysisPoint` 생성, fill 단위 합계(traded notional·수수료·슬리피지)를 모두 포함한다.
- `compute_analytics`: `compute_analytics()` 호출 누적 (full + metric window).
- `manifest`: `artifacts` 진행 콜백 → `execute()` 반환. manifest·fingerprint 조립.

구간 계측은 어댑터를 수정하지 않고 어댑터 모듈의 `BacktestEngine`·`_artifacts`·
`compute_analytics` 이름을 `unittest.mock.patch`로 감싸 얻는다. 벤치 목적의 계측이므로
private 모듈을 직접 import한다 — 프로덕션 코드에서는 facade 경유가 규칙이다.

`--core all`은 한 프로세스에서 두 코어를 모두 돌리므로 시간만 비교 대상이고 RSS는 격리되지
않는다.

사용법:
    uv run python scripts/bench_workbench_adapter.py --instruments 100 --core all --repeat 3
    uv run python scripts/bench_workbench_adapter.py --core rust --json-out out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

from bench_universe import synthetic_universe

from backtest_engine import BacktestEngine
from backtest_engine.adapters.krx_parquet import KrxParquetBarSource, KrxParquetUniverseSource
from backtest_engine.engine.store import EventStore
from backtest_engine.ports.market_data import BarQuery, OhlcPolicy
from backtest_engine.ports.universe import UniverseQuery
from backtest_engine.types.results import BacktestResult
from strategy_workbench.adapters.outbound.backtest_engine import _adapter as adapter_module
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataset,
    BacktestExecutionRequest,
    MarketBarRecord,
    UniverseMembershipRecord,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    ExecutionCore,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioObservation,
    TargetTape,
    compile_rebalance_schedule,
    compile_target_tape,
)
from strategy_workbench.domain.strategy.facade.specification import (
    RebalanceFrequency,
    StrategySpec,
    strategy_spec_hash,
)

_SNAPSHOT_ID = "bench-workbench-snapshot"
_FACTOR_ID = "price.close"
_SECTOR_ID = "bench-sector"

AFTER_RUN_STAGES: tuple[str, ...] = (
    "result_materialize",
    "event_store_costs",
    "artifacts",
    "analysis_points",
    "compute_analytics",
    "manifest",
)
# `other`는 total에서 위 구간들을 뺀 잔여다 (진행 콜백 사이 간격, 지표 parity 확인,
# metric window 슬라이싱 등). 표가 total로 합산되도록 남겨 계측 누락을 드러낸다.
STAGES: tuple[str, ...] = (
    "dataset_to_engine_inputs",
    "strategy_and_feed_build",
    "engine.run",
    *AFTER_RUN_STAGES,
    "other",
)


@dataclass
class StageClock:
    """구간별 누적 시간과 단발 시각을 함께 들고 있는 계측 버퍼."""

    accumulated: dict[str, float] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)

    def add(self, stage: str, seconds: float) -> None:
        self.accumulated[stage] = self.accumulated.get(stage, 0.0) + seconds

    def mark_first(self, name: str) -> None:
        """같은 이름이 여러 번 와도 첫 시각만 남긴다 (compute_analytics 첫 진입 등)."""
        if name not in self.marks:
            self.marks[name] = time.perf_counter()

    def mark(self, name: str) -> None:
        self.marks[name] = time.perf_counter()

    def elapsed(self, start: str, end: str) -> float:
        """두 mark 사이 간격. 어느 한쪽이라도 안 찍혔으면 0으로 본다."""
        if start not in self.marks or end not in self.marks:
            return 0.0
        return max(0.0, self.marks[end] - self.marks[start])


class _TimedEventStore:
    """`costs()` 호출 시간만 재서 위임한다."""

    def __init__(self, delegate: EventStore, clock: StageClock) -> None:
        self._delegate = delegate
        self._clock = clock

    def costs(self) -> tuple[object, ...]:
        started = time.perf_counter()
        costs = self._delegate.costs()
        self._clock.add("event_store_costs", time.perf_counter() - started)
        return costs


class _TimedEngine:
    """`run()`과 결과 조회를 나눠 재고 나머지는 실제 엔진에 위임한다."""

    def __init__(self, delegate: BacktestEngine, clock: StageClock) -> None:
        self._delegate = delegate
        self._clock = clock

    @property
    def event_store(self) -> _TimedEventStore:
        return _TimedEventStore(self._delegate.event_store, self._clock)

    # reason: 어댑터의 engine.run(...) 호출을 그대로 통과시키는 래퍼다.
    def run(self, *args: Any, **kwargs: Any) -> BacktestResult:
        # 인자(`TargetTapeStrategy`·`DataFeed`)는 호출 시점에 이미 만들어져 있으므로 이
        # mark가 그 조립 구간의 끝이다.
        self._clock.mark("run_entered")
        started = time.perf_counter()
        result = self._delegate.run(*args, **kwargs)
        self._clock.add("engine.run", time.perf_counter() - started)
        # 조회를 여기서 먼저 끝내 lazy 코어의 공개 객체 생성 비용을 `artifacts` 구간에서
        # 떼어낸다. 어댑터는 뒤에서 같은 속성을 다시 읽지만 그때는 이미 만들어져 있다.
        materialize_started = time.perf_counter()
        _ = (result.snapshots, result.orders, result.fills)
        self._clock.add("result_materialize", time.perf_counter() - materialize_started)
        return result


def synthetic_dataset(
    root: Path, size: int, start: date, end: date
) -> tuple[BacktestDataset, tuple[str, ...], tuple[date, ...]]:
    """실제 KRX fixture 가격 경로를 복제한 synthetic dataset을 만든다.

    `bench_universe.synthetic_universe`가 만든 엔진 `Bar`를 워크벤치 포트 레코드로 옮긴다.
    두 벤치가 같은 가격 경로를 쓰므로 엔진 벤치 수치와 직접 비교할 수 있다.
    """
    universe = KrxParquetUniverseSource(root).load_universe(
        UniverseQuery(venue="XKRX", start=start, end=end)
    )
    if not universe.ok:
        raise RuntimeError(
            f"universe load failed — root={root} start={start} end={end} detail={universe.detail}"
        )
    first = min(item.first_session for item in universe.memberships)
    last = max(item.last_session for item in universe.memberships)
    full = tuple(
        item.instrument
        for item in universe.memberships
        if item.first_session == first and item.last_session == last
    )
    if not full:
        raise RuntimeError(
            f"no instrument spans the whole calendar — root={root} first={first} last={last}"
        )
    loaded = KrxParquetBarSource(root).load_bars(
        BarQuery(instruments=full[:1], start=start, end=end, ohlc_policy=OhlcPolicy.CLAMP)
    )
    if not loaded.ok:
        raise RuntimeError(
            f"bars load failed — root={root} instruments={len(full[:1])} detail={loaded.detail}"
        )
    instruments, bars = synthetic_universe(loaded.bars, size)
    records = tuple(
        MarketBarRecord(
            session=bar.ts.date(),
            security_id=bar.instrument.symbol,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        for bar in bars
    )
    sessions = tuple(sorted({record.session for record in records}))
    security_ids = tuple(instrument.symbol for instrument in instruments)
    memberships = tuple(
        UniverseMembershipRecord(
            security_id=security_id, first_session=sessions[0], last_session=sessions[-1]
        )
        for security_id in security_ids
    )
    dataset = BacktestDataset(
        data_snapshot_id=_SNAPSHOT_ID,
        bars=records,
        memberships=memberships,
        corporate_actions=(),
        benchmark_security_id=None,
    )
    return dataset, security_ids, sessions


def bench_strategy_spec(
    security_ids: tuple[str, ...], sessions: tuple[date, ...], rebalance_every: int
) -> StrategySpec:
    """유니버스 전체를 동일 비중으로 담는 전략. 워크벤치 템플릿에서 최소한만 바꾼다."""
    template = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "bench-workbench",
        today=lambda: sessions[-1],
    ).template()
    return replace(
        template,
        data=replace(template.data, start=sessions[0], end=sessions[-1]),
        portfolio=replace(
            template.portfolio,
            selection_count=len(security_ids),
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=rebalance_every,
        ),
        risk=replace(template.risk, max_name_weight=1.0, max_sector_weight=1.0),
    )


def compile_bench_tape(
    spec: StrategySpec,
    dataset: BacktestDataset,
    security_ids: tuple[str, ...],
    sessions: tuple[date, ...],
) -> TargetTape:
    """워크벤치 컴파일러(`compile_target_tape`)로 실제 TargetTape를 만든다.

    팩터 값은 템플릿 전략이 읽는 `price.close`이며, dataset의 종가를 그대로 쓴다. 공개일은
    관측일과 같아 FUTURE_DATA 가드에 걸리지 않는다.
    """
    schedule = compile_rebalance_schedule(spec, sessions)
    signal_dates = frozenset(signal_as_of for signal_as_of, _ in schedule.pairs)
    closes: dict[tuple[date, str], float] = {
        (record.session, record.security_id): record.close
        for record in dataset.bars
        if record.session in signal_dates
    }
    observations = tuple(
        PortfolioObservation(
            as_of=signal_as_of,
            security_id=security_id,
            universe_member=True,
            factor_values=(
                PortfolioFactorValue(
                    factor_id=_FACTOR_ID,
                    value=closes[(signal_as_of, security_id)],
                    available_date=signal_as_of,
                ),
            ),
            sector_id=_SECTOR_ID,
        )
        for signal_as_of in sorted(signal_dates)
        for security_id in security_ids
        if (signal_as_of, security_id) in closes
    )
    return compile_target_tape(
        spec,
        data_snapshot_id=dataset.data_snapshot_id,
        sessions=sessions,
        observations=observations,
        schedule=schedule,
    )


def execution_request(
    core: ExecutionCore,
    spec: StrategySpec,
    tape: TargetTape,
    dataset: BacktestDataset,
) -> BacktestExecutionRequest:
    spec_hash = strategy_spec_hash(spec)
    return BacktestExecutionRequest(
        run_id=f"bench-workbench-{core.value}",
        spec=BacktestRunSpec(strategy=spec, core=core, initial_cash=1_000_000_000.0),
        target_tape=tape,
        dataset=dataset,
        strategy_provenance=StrategyProvenance(
            kind=StrategySourceKind.INLINE_DRAFT,
            spec_hash=spec_hash,
            schema_version=spec.identity.schema_version,
        ),
    )


def measure_once(
    adapter: BacktestEngineExecutorAdapter, request: BacktestExecutionRequest
) -> tuple[dict[str, float], float, BacktestRunResult]:
    """`execute()` 한 번을 구간별로 재고 (구간, 전체, 결과)를 돌려준다."""
    clock = StageClock()
    real_engine = adapter_module.BacktestEngine
    real_artifacts = adapter_module._artifacts
    real_compute = adapter_module.compute_analytics

    # reason: 세 래퍼 모두 어댑터의 원래 호출 인자를 그대로 통과시키는 pass-through 계측이다.
    def engine_factory(*args: Any, **kwargs: Any) -> _TimedEngine:
        clock.mark("engine_constructed")
        return _TimedEngine(real_engine(*args, **kwargs), clock)

    def timed_artifacts(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        bundle = real_artifacts(*args, **kwargs)
        clock.add("artifacts", time.perf_counter() - started)
        clock.mark("artifacts_returned")
        return bundle

    def timed_compute(*args: Any, **kwargs: Any) -> Any:
        clock.mark_first("compute_analytics_entered")
        started = time.perf_counter()
        analytics = real_compute(*args, **kwargs)
        clock.add("compute_analytics", time.perf_counter() - started)
        return analytics

    def progress(value: float, stage: str, message: str) -> None:
        clock.mark(f"progress:{stage}")

    with ExitStack() as stack:
        stack.enter_context(patch.object(adapter_module, "BacktestEngine", engine_factory))
        stack.enter_context(patch.object(adapter_module, "_artifacts", timed_artifacts))
        stack.enter_context(patch.object(adapter_module, "compute_analytics", timed_compute))
        started = time.perf_counter()
        clock.mark("execute_started")
        result = adapter.execute(request, progress=progress, cancelled=lambda: False)
        total_seconds = time.perf_counter() - started
        clock.mark("execute_returned")

    stage_seconds = dict.fromkeys(STAGES, 0.0)
    stage_seconds.update(clock.accumulated)
    stage_seconds["dataset_to_engine_inputs"] = clock.elapsed(
        "execute_started", "engine_constructed"
    )
    stage_seconds["strategy_and_feed_build"] = clock.elapsed("engine_constructed", "run_entered")
    stage_seconds["analysis_points"] = clock.elapsed(
        "artifacts_returned", "compute_analytics_entered"
    )
    stage_seconds["manifest"] = clock.elapsed("progress:artifacts", "execute_returned")
    stage_seconds["other"] = max(
        0.0, total_seconds - sum(stage_seconds[stage] for stage in STAGES if stage != "other")
    )
    return stage_seconds, total_seconds, result


def result_signature(result: BacktestRunResult) -> tuple[object, ...]:
    """코어 간 동치 비교용 서명. 지표 값과 시계열 전체를 담는다."""
    return (
        tuple(
            (
                metric.metric_id,
                metric.scope.value,
                metric.scope_label,
                metric.value,
                metric.sample_count,
                metric.unavailable_reason,
            )
            for metric in result.metrics
        ),
        result.series.equity,
        result.series.drawdown,
        result.series.monthly_returns,
        result.series.rolling_sharpe,
    )


def _first_difference(left: tuple[object, ...], right: tuple[object, ...]) -> str:
    labels = ("metrics", "equity", "drawdown", "monthly_returns", "rolling_sharpe")
    for label, left_part, right_part in zip(labels, left, right, strict=True):
        if left_part == right_part:
            continue
        left_items = tuple(left_part) if isinstance(left_part, tuple) else (left_part,)
        right_items = tuple(right_part) if isinstance(right_part, tuple) else (right_part,)
        for index, (left_item, right_item) in enumerate(
            zip(left_items, right_items, strict=False)
        ):
            if left_item != right_item:
                return f"{label}[{index}]: {left_item!r} != {right_item!r}"
        return f"{label}: length {len(left_items)} != {len(right_items)}"
    return "no difference"


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("tests/fixtures/krx_parquet"))
    parser.add_argument("--instruments", type=int, default=100)
    parser.add_argument("--core", choices=("python", "rust", "all"), default="all")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--rebalance-every", type=int, default=5)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2020, 1, 1))
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2024, 12, 31))
    args = parser.parse_args(argv[1:])

    dataset, security_ids, sessions = synthetic_dataset(
        args.root, args.instruments, args.start, args.end
    )
    spec = bench_strategy_spec(security_ids, sessions, args.rebalance_every)
    tape = compile_bench_tape(spec, dataset, security_ids, sessions)
    cores = (
        (ExecutionCore.PYTHON, ExecutionCore.RUST)
        if args.core == "all"
        else (ExecutionCore(args.core),)
    )
    print(
        f"instruments={len(security_ids)} sessions={len(sessions)} bars={len(dataset.bars)} "
        f"frames={len(tape.frames)} cores={','.join(core.value for core in cores)} "
        f"repeat={args.repeat}"
    )

    adapter = BacktestEngineExecutorAdapter()
    stage_samples: dict[ExecutionCore, list[dict[str, float]]] = {core: [] for core in cores}
    total_samples: dict[ExecutionCore, list[float]] = {core: [] for core in cores}
    signatures: dict[ExecutionCore, tuple[object, ...]] = {}
    for _ in range(args.repeat):
        for core in cores:
            stages, total, result = measure_once(
                adapter, execution_request(core, spec, tape, dataset)
            )
            stage_samples[core].append(stages)
            total_samples[core].append(total)
            previous = signatures.setdefault(core, result_signature(result))
            current = result_signature(result)
            if previous != current:
                raise RuntimeError(
                    f"non-deterministic workbench result for core={core.value} — "
                    f"{_first_difference(previous, current)}"
                )

    if len(signatures) > 1:
        baseline_signature = signatures[ExecutionCore.PYTHON]
        for core, signature in signatures.items():
            if signature != baseline_signature:
                raise RuntimeError(
                    "workbench result mismatch between cores — "
                    f"python vs {core.value}: "
                    f"{_first_difference(baseline_signature, signature)}"
                )

    def median_stage(core: ExecutionCore, stage: str) -> float:
        return statistics.median(sample[stage] for sample in stage_samples[core])

    baseline_total = (
        statistics.median(total_samples[ExecutionCore.PYTHON])
        if ExecutionCore.PYTHON in cores
        else None
    )
    core_payload: dict[str, object] = {}
    payload: dict[str, object] = {
        "workload": {
            "instruments": len(security_ids),
            "sessions": len(sessions),
            "bars": len(dataset.bars),
            "tape_frames": len(tape.frames),
            "rebalance_every": args.rebalance_every,
            "repeat": args.repeat,
            "synthetic": True,
        },
        "cores": core_payload,
    }
    for core in cores:
        stage_medians = {stage: median_stage(core, stage) for stage in STAGES}
        total_median = statistics.median(total_samples[core])
        speedup = baseline_total / total_median if baseline_total is not None else None
        run_seconds = stage_medians["engine.run"]
        after_run = sum(stage_medians[stage] for stage in AFTER_RUN_STAGES)
        core_payload[core.value] = {
            "stage_seconds": stage_medians,
            "total_seconds": total_median,
            "total_seconds_samples": total_samples[core],
            "speedup_vs_python": speedup,
            "after_run_seconds": after_run,
            # `engine.run` 한 번의 비용 대비 그 뒤 어댑터 구간이 몇 배인지. 1보다 크면
            # 엔진을 더 줄여도 워크벤치 체감 배수가 따라오지 않는다는 뜻이다.
            "after_run_over_run_ratio": after_run / run_seconds if run_seconds > 0 else None,
        }
        speedup_text = f" speedup={speedup:.3f}x" if speedup is not None else ""
        print(f"core={core.value} total={total_median:.6f}s{speedup_text}")
        for stage in STAGES:
            seconds = stage_medians[stage]
            share = seconds / total_median * 100 if total_median > 0 else 0.0
            print(f"  {stage:<26} {seconds:.6f}s ({share:5.1f}%)")
        if run_seconds > 0:
            print(
                f"  after_run={after_run:.6f}s after_run/engine.run={after_run / run_seconds:.3f}x"
            )

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"json={args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
