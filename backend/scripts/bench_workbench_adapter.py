"""워크벤치 어댑터 e2e 구간별 벤치마크 (이슈 #98 Phase 3-2).

`BacktestEngineExecutorAdapter.execute`는 `engine.run()` 하나만 쓰는 게 아니라 그 뒤에
결과 조회·비용 집계·raw artifact 변환·지표 계산을 이어서 한다. Rust 코어로 `run()`을
아무리 줄여도 이 뒤쪽 구간은 그대로 Python이라, 워크벤치가 체감하는 배수는 엔진 배수보다
낮다. 이 스크립트는 그 경계를 코어별로 나눠 재서 후속 최적화의 타깃을 정한다.

측정 구간:

- `dataset_to_engine_inputs`: `execute()` 시작 → `BacktestEngine` 생성. dataset 레코드를
  엔진 `UniverseResult`·`CorporateActionEvent`로 옮기는 구간이다. bar 행은 여기서 다루지
  않는다 — feed를 만드는 `_columnar_feed`가 `run()` 호출 인자라 다음 구간에 들어간다.
- `strategy_and_feed_build`: `BacktestEngine` 생성 → `run()` 진입. `TargetTapeStrategy`가
  TargetTape 프레임을 엔진 액션으로 옮기고, dataset bar 행을 세션별 열로 펴 `DataFeed`를
  만드는 구간이다.
- `engine.run`: 엔진 루프만.
- `result_tables`: `engine.event_store.result_tables()` — 어댑터가 읽는 유일한 결과 조회다.
  Rust 코어는 여기서 레코드를 한 번 훑어 primitive 행을 만들고, Python 코어는 `run()` 안에서
  이미 만든 공개 객체에서 행을 떠온다.
- `artifacts`: `_artifacts()` — 결과 테이블을 raw artifact 레코드로 변환.
- `analysis_points`: `_artifacts()` 반환 → 첫 `compute_analytics()` 진입. 벤치마크 시계열,
  `AnalysisPoint` 생성, fill 단위 합계(traded notional·수수료·슬리피지)를 모두 포함한다.
- `compute_analytics`: `compute_analytics()` 호출 누적. 기본 요청은 HTTP `_run_body`와 같은
  모양으로 out-of-sample metric window 하나를 실으므로 full 1회 + window 1회가 누적된다
  (`--no-metric-windows`로 full 1회만 재는 비교도 가능).
- `manifest`: `artifacts` 진행 콜백 → `execute()` 반환. manifest·fingerprint 조립.

구간 계측은 어댑터를 수정하지 않고 어댑터 모듈의 `BacktestEngine`·`_artifacts`·
`compute_analytics` 이름을 `unittest.mock.patch`로 감싸 얻는다. 벤치 목적의 계측이므로
private 모듈을 직접 import한다 — 프로덕션 코드에서는 facade 경유가 규칙이다.

`--core all`은 한 프로세스에서 두 코어를 모두 돌리므로 시간만 비교 대상이고 RSS는 격리되지
않는다. 그래서 JSON의 `peak_rss_bytes`는 단일 코어 실행(`--core python` 또는 `--core rust`)일
때만 값을 담고, `--core all`이면 null이다 (`rss_isolated`가 어느 쪽인지 말한다). peak RSS는
프로세스 단위 누적 최댓값이라 한 프로세스에서 두 코어를 돌리면 뒤 코어 값이 앞 코어를 포함한다.

사용법:
    uv run python scripts/bench_workbench_adapter.py --instruments 100 --core all --repeat 3
    uv run python scripts/bench_workbench_adapter.py --core rust --json-out out.json
"""

from __future__ import annotations

import argparse
import gc
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

from bench_universe import (
    cpu_load_percent,
    load_full_calendar_universe,
    peak_rss_bytes,
    synthetic_universe,
)

from backtest_engine import BacktestEngine
from backtest_engine.engine.store import EventStore
from backtest_engine.types.result_tables import ResultTables
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
from strategy_workbench.domain.analytics.facade.metrics import MetricScope
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    ExecutionCore,
    MetricWindow,
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
    "result_tables",
    "artifacts",
    "analysis_points",
    "compute_analytics",
    "manifest",
)
# `other`는 total에서 위 구간들을 뺀 잔여다 (지표 parity 확인, metric window의
# `_slice_analytics`, 진행 콜백 사이 간격). 표가 total로 합산되도록 남겨 계측 누락을 드러낸다.
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
    """`result_tables()` 호출 시간만 재서 위임한다."""

    def __init__(self, delegate: EventStore, clock: StageClock) -> None:
        self._delegate = delegate
        self._clock = clock

    def result_tables(self) -> ResultTables:
        started = time.perf_counter()
        tables = self._delegate.result_tables()
        self._clock.add("result_tables", time.perf_counter() - started)
        return tables


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
        # 공개 객체(`result.snapshots`/`orders`/`fills`)는 일부러 건드리지 않는다 — 어댑터가
        # 더 이상 읽지 않는 경로를 벤치가 대신 태우면 없앤 비용이 표에 그대로 남는다.
        return result


def synthetic_dataset(
    root: Path, size: int, start: date, end: date
) -> tuple[BacktestDataset, tuple[str, ...], tuple[date, ...]]:
    """실제 KRX fixture 가격 경로를 복제한 synthetic dataset을 만든다.

    fixture 로딩과 전 구간 상장 필터는 `bench_universe.load_full_calendar_universe`가 정본이고
    `synthetic_universe`가 만든 엔진 `Bar`를 워크벤치 포트 레코드로 옮긴다. 두 벤치가 같은 가격
    경로를 쓰므로 엔진 벤치 수치와 직접 비교할 수 있다.
    """
    fixture = load_full_calendar_universe(root, start=start, end=end, limit=1)
    instruments, bars = synthetic_universe(fixture.bars, size)
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


def bench_environment(sessions: tuple[date, ...]) -> RunEnvironment:
    """벤치가 쓰는 실행 설정. 1.2 부터 기간·유니버스는 전략 문서가 아니라 실행이 소유한다."""
    return RunEnvironment(start=sessions[0], end=sessions[-1], universe_id="bench.synthetic")


def bench_strategy_spec(
    security_ids: tuple[str, ...], sessions: tuple[date, ...], rebalance_every: int
) -> StrategySpec:
    """유니버스 전체를 동일 비중으로 담는 전략. 워크벤치 템플릿에서 최소한만 바꾼다."""
    template = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "bench-workbench",
    ).template()
    return replace(
        template,
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
    environment: RunEnvironment,
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
        environment=environment,
        data_snapshot_id=dataset.data_snapshot_id,
        sessions=sessions,
        observations=observations,
        schedule=schedule,
    )


def out_of_sample_window(sessions: tuple[date, ...]) -> MetricWindow:
    """HTTP `_run_body`와 같은 모양의 out-of-sample window 하나.

    거기서는 전체 기간의 뒤쪽 약 1/3을 OOS로 잡는다. 세션 수가 다르므로 같은 비율로 맞춘다.
    이 window가 있어야 어댑터의 window 루프와 `_slice_analytics`가 실제로 돈다.
    """
    start_index = len(sessions) - max(1, len(sessions) // 3)
    return MetricWindow(
        scope=MetricScope.OUT_OF_SAMPLE,
        start=sessions[start_index],
        end=sessions[-1],
        label="OOS",
    )


def execution_request(
    core: ExecutionCore,
    spec: StrategySpec,
    environment: RunEnvironment,
    tape: TargetTape,
    dataset: BacktestDataset,
    metric_windows: tuple[MetricWindow, ...],
) -> BacktestExecutionRequest:
    spec_hash = strategy_spec_hash(spec)
    return BacktestExecutionRequest(
        run_id=f"bench-workbench-{core.value}",
        spec=BacktestRunSpec(
            strategy=spec,
            core=core,
            environment=environment,
            initial_cash=1_000_000_000.0,
            metric_windows=metric_windows,
        ),
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
        engine = real_engine(*args, **kwargs)
        clock.mark("engine_constructed")
        return _TimedEngine(engine, clock)

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
        for index, (left_item, right_item) in enumerate(zip(left_items, right_items, strict=False)):
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
    parser.add_argument(
        "--no-metric-windows",
        action="store_true",
        help="metric window 없이 full 지표만 계산한다 (window 루프 비용을 뺀 비교용)",
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2020, 1, 1))
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2024, 12, 31))
    args = parser.parse_args(argv[1:])

    dataset, security_ids, sessions = synthetic_dataset(
        args.root, args.instruments, args.start, args.end
    )
    spec = bench_strategy_spec(security_ids, sessions, args.rebalance_every)
    environment = bench_environment(sessions)
    tape = compile_bench_tape(spec, environment, dataset, security_ids, sessions)
    metric_windows = () if args.no_metric_windows else (out_of_sample_window(sessions),)
    cores = (
        (ExecutionCore.PYTHON, ExecutionCore.RUST)
        if args.core == "all"
        else (ExecutionCore(args.core),)
    )
    load = cpu_load_percent()
    print(
        f"instruments={len(security_ids)} sessions={len(sessions)} bars={len(dataset.bars)} "
        f"frames={len(tape.frames)} cores={','.join(core.value for core in cores)} "
        f"repeat={args.repeat} metric_windows={len(metric_windows)} "
        f"cpu_load={'unknown' if load is None else f'{load}%'}"
    )

    adapter = BacktestEngineExecutorAdapter()
    stage_samples: dict[ExecutionCore, list[dict[str, float]]] = {core: [] for core in cores}
    total_samples: dict[ExecutionCore, list[float]] = {core: [] for core in cores}
    signatures: dict[ExecutionCore, tuple[object, ...]] = {}
    # 코어 실행이 끝난 시점의 프로세스 peak RSS. 단일 코어 실행일 때만 그 코어의 값이다.
    peak_rss_by_core: dict[ExecutionCore, int] = {}
    for _ in range(args.repeat):
        for core in cores:
            # 앞 회차가 남긴 쓰레기를 타이머 밖에서 치운다. 그러지 않으면 전면 GC가 임의의
            # 구간에 붙어 그 구간만 부풀어 보인다.
            gc.collect()
            stages, total, result = measure_once(
                adapter,
                execution_request(core, spec, environment, tape, dataset, metric_windows),
            )
            peak_rss_by_core[core] = peak_rss_bytes()
            stage_samples[core].append(stages)
            total_samples[core].append(total)
            signature = result_signature(result)
            previous = signatures.setdefault(core, signature)
            if previous != signature:
                raise RuntimeError(
                    f"non-deterministic workbench result for core={core.value} — "
                    f"{_first_difference(previous, signature)}"
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
    rss_isolated = len(cores) == 1
    core_payload: dict[str, object] = {}
    payload: dict[str, object] = {
        "workload": {
            "instruments": len(security_ids),
            "sessions": len(sessions),
            "bars": len(dataset.bars),
            "tape_frames": len(tape.frames),
            "rebalance_every": args.rebalance_every,
            "repeat": args.repeat,
            "metric_windows": len(metric_windows),
            "synthetic": True,
            "cpu_load_percent": load,
        },
        # 한 프로세스에서 두 코어를 돌리면 peak RSS가 코어별로 갈라지지 않는다.
        "rss_isolated": rss_isolated,
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
            "stage_seconds_samples": {
                stage: [sample[stage] for sample in stage_samples[core]] for stage in STAGES
            },
            "total_seconds": total_median,
            "total_seconds_samples": total_samples[core],
            "speedup_vs_python": speedup,
            "after_run_seconds": after_run,
            # `engine.run` 한 번의 비용 대비 그 뒤 어댑터 구간이 몇 배인지. 1보다 크면
            # 엔진을 더 줄여도 워크벤치 체감 배수가 따라오지 않는다는 뜻이다.
            "after_run_over_run_ratio": after_run / run_seconds if run_seconds > 0 else None,
            # 격리되지 않은 실행에서 값을 담으면 다른 코어의 할당까지 그 코어 수치로 읽힌다.
            "peak_rss_bytes": peak_rss_by_core[core] if rss_isolated else None,
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
        rss_text = (
            f"{peak_rss_by_core[core] / 1024 / 1024:.1f}MiB"
            if rss_isolated
            else f"{peak_rss_by_core[core] / 1024 / 1024:.1f}MiB(shared, not recorded)"
        )
        print(f"  peak_rss={rss_text}")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"json={args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
