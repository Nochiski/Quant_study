"""run-worker spec D8: 메모리 가드 — 예약치 어림, 꺼내기 전 판정, 대기·해제 순서.

전송은 가짜다. `memory()` 가 주는 측정값을 테스트가 정하고, run 의 종결은 테스트가 listener 로
알린다. 그래서 경계(딱 맞음·1바이트 부족)와 대기·해제 순서를 타이밍 없이 단언한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run._memory_guard import (
    estimated_sessions,
    gb_bytes,
    memory_verdict,
    run_reserve_bytes,
)
from strategy_workbench.application.backtest_run.facade.computation import RunComputation
from strategy_workbench.application.backtest_run.facade.ports import (
    RunFailed,
    RunJob,
    RunListener,
    RunMemory,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunSpec,
    RunStatus,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldNode,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec

_ENVIRONMENT = RunEnvironment(
    start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
)
_GB = 1024**3


def _weekdays(start: date, end: date) -> int:
    return sum(
        (start + timedelta(days=offset)).weekday() < 5 for offset in range((end - start).days + 1)
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2025, 1, 2), date(2025, 12, 30)),
        (date(2021, 1, 4), date(2026, 9, 18)),
        (date(2024, 1, 6), date(2024, 1, 7)),  # 주말만
        (date(2024, 1, 8), date(2024, 1, 8)),
    ],
)
def test_estimated_sessions_are_the_weekdays_in_range_plus_the_warm_up(
    start: date, end: date
) -> None:
    assert estimated_sessions(start, end, 272) == _weekdays(start, end) + 272


def test_reserve_follows_the_spec_formula_at_the_two_measured_points() -> None:
    # spec D8: 1년 약 520세션은 약 2.5GB, 5년 약 1,690세션은 약 6.9GB 를 잡는다.
    assert run_reserve_bytes(520) == gb_bytes(1.25 * (0.44 + 0.0030 * 520))
    assert round(run_reserve_bytes(520) / _GB, 2) == 2.5
    assert round(run_reserve_bytes(1690) / _GB, 2) == 6.89


def _memory(available: int, *, overhead: int = 0, **rss: int) -> RunMemory:
    return RunMemory(available_bytes=available, rss_bytes=rss, overhead_bytes=overhead)


def test_a_run_that_just_fits_is_admitted_and_one_byte_short_is_not() -> None:
    running = [("a", 3_000)]
    fits = memory_verdict(_memory(10_000, overhead=500, a=1_000), running, 7_500)
    short = memory_verdict(_memory(9_999, overhead=500, a=1_000), running, 7_500)

    # 10,000 − 500 − (3,000 − 1,000) = 7,500
    assert (fits.admitted, short.admitted) == (True, False)
    assert fits.running_remaining_bytes == 2_000


def test_a_taken_spare_baseline_is_not_counted_twice() -> None:
    """#591 리뷰: 가져간 예비의 유휴 RSS 는 이미 가용에서 빠졌다. 남은 몫은 예약치 − 지금 RSS 다."""
    verdict = memory_verdict(_memory(5_000, a=120), [("a", 2_000)], 3_120)

    assert verdict.running_remaining_bytes == 1_880
    assert verdict.admitted


def test_a_run_without_a_measured_worker_counts_its_whole_reserve_and_a_big_rss_counts_zero() -> (
    None
):
    verdict = memory_verdict(_memory(0, b=9_000), [("a", 2_000), ("b", 3_000)], 0)

    assert verdict.running_remaining_bytes == 2_000


class _Handle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeTransport:
    """띄운 job 을 모으고, 테스트가 정한 메모리 측정값을 준다. 계산은 하지 않는다."""

    def __init__(self) -> None:
        self.started: list[tuple[RunJob, RunListener]] = []
        self.sample: RunMemory | None = None
        self.holds: list[bool] = []

    def start(self, job: RunJob, listener: RunListener) -> _Handle:
        self.started.append((job, listener))
        return _Handle()

    def memory(self) -> RunMemory | None:
        return self.sample

    def set_memory_hold(self, held: bool) -> None:
        self.holds.append(held)

    def shutdown(self) -> None:
        return None

    def started_ids(self) -> list[str]:
        return [job.run_id for job, _ in self.started]

    def finish(self, run_id: str) -> None:
        listener = next(listener for job, listener in self.started if job.run_id == run_id)
        listener.on_finished(RunFailed("backtest.run.internal", f"test finished run_id={run_id}"))


def _spec() -> StrategySpec:
    """20세션 모멘텀 하나 — 원시 관측이 시작일 앞의 워밍업을 읽어 예약치에 든다."""
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    graph = FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 20, "time_series"),
        ),
        output_node_id="mom",
    )
    return replace(template, factors=(replace(template.factors[0], graph=graph),))


def _portfolio() -> PortfolioDesignService:
    return PortfolioDesignService(
        MockEquityDataAdapter.demo(),
        BacktestEnginePortfolioAdapter(),
        factor_metadata=MockEquityDataAdapter.demo(),
        factor_registry_version="test-registry",
    )


def _service(
    tmp_path: Path,
    transport: _FakeTransport,
    *,
    run_slots: int = 2,
    memory_reserve_gb: float | None = None,
) -> BacktestRunService:
    def computation(_: RunComputation) -> _FakeTransport:
        return transport

    ids = iter(f"run-{index}" for index in range(1, 10))
    return BacktestRunService(
        _portfolio(),
        InMemoryStrategyRepository(),
        MockEquityDataAdapter.demo(),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        run_repository=SQLiteBacktestRunRepository(),
        new_id=lambda: next(ids),
        run_execution=computation,
        run_slots=run_slots,
        memory_reserve_gb=memory_reserve_gb,
    )


def _request(*, end: date = _ENVIRONMENT.end) -> BacktestRunSpec:
    # 끝 날짜를 달리해 같은 입력 잇기를 피한다.
    return BacktestRunSpec(
        strategy=_spec(), core=ExecutionCore.PYTHON, environment=replace(_ENVIRONMENT, end=end)
    )


def _reserve(request: BacktestRunSpec) -> int:
    """접수가 이 요청에 잡을 예약치 — 실행 설정 구간과 preflight 의 워밍업으로 어림한다."""
    environment = request.environment
    assert environment is not None and request.strategy is not None
    history = _portfolio().preflight(
        PortfolioPreviewRequest(request.strategy, environment=environment)
    )
    return run_reserve_bytes(
        estimated_sessions(environment.start, environment.end, history.history_sessions)
    )


def test_two_runs_accepted_back_to_back_hold_the_second_while_the_first_is_still_small(
    tmp_path: Path,
) -> None:
    """WORKFLOW W2-02: 둘째 run 은 첫 run 의 RSS 가 아직 작아도 그 남은 예약분 때문에 기다린다."""
    transport = _FakeTransport()
    runs = _service(tmp_path, transport)
    first, second = _request(), _request(end=date(2024, 1, 11))
    # 남은 몫 = 첫 예약치 − 지금 RSS(100바이트). 둘째 예약치에 1바이트 모자라다.
    transport.sample = _memory(
        _reserve(first) - 100 + _reserve(second) - 1 + 50, overhead=50, **{"run-1": 100}
    )

    runs.start(first)
    held = runs.start(second)

    assert transport.started_ids() == ["run-1"]
    assert held.run.status is RunStatus.QUEUED
    state = runs.state("run-2")
    assert (state.status, state.message) == (RunStatus.QUEUED, "Waiting for free memory")
    assert transport.holds[-1] is True


def test_a_run_that_exactly_fits_starts_beside_the_running_one(tmp_path: Path) -> None:
    transport = _FakeTransport()
    runs = _service(tmp_path, transport)
    first, second = _request(), _request(end=date(2024, 1, 11))
    transport.sample = _memory(
        _reserve(first) - 100 + _reserve(second) + 50, overhead=50, **{"run-1": 100}
    )

    runs.start(first)
    runs.start(second)

    assert transport.started_ids() == ["run-1", "run-2"]
    assert transport.holds[-1] is False


def test_a_held_run_starts_when_the_running_run_finishes_and_frees_its_reserve(
    tmp_path: Path,
) -> None:
    transport = _FakeTransport()
    runs = _service(tmp_path, transport)
    transport.sample = _memory(0)
    runs.start(_request())
    runs.start(_request(end=date(2024, 1, 11)))
    assert transport.started_ids() == ["run-1"]

    transport.finish("run-1")

    # 도는 run 이 없으면 가드를 쓰지 않는다 — 메모리가 0 으로 보여도 run 하나는 돈다.
    assert transport.started_ids() == ["run-1", "run-2"]
    assert transport.holds[-1] is False
    assert runs.state("run-1").status is RunStatus.FAILED


def test_cancelling_the_held_run_lets_the_next_one_be_judged_and_releases_the_spare_hold(
    tmp_path: Path,
) -> None:
    transport = _FakeTransport()
    runs = _service(tmp_path, transport, run_slots=3)
    small, big = _request(end=date(2024, 1, 9)), _request(end=date(2024, 3, 29))
    assert _reserve(big) > _reserve(small)
    # 도는 첫 run 의 예약분을 빼면 작은 run 하나 몫만 남는다.
    transport.sample = _memory(_reserve(_request()) + _reserve(small))
    runs.start(_request())
    runs.start(big)
    runs.start(small)
    # 큰 run 이 앞에서 기다리면 뒤의 작은 run 이 앞지르지 않는다.
    assert transport.started_ids() == ["run-1"]
    assert runs.state("run-3").status is RunStatus.QUEUED
    assert transport.holds[-1] is True

    cancelled = runs.cancel("run-2")

    assert cancelled.status is RunStatus.CANCELLED
    assert transport.started_ids() == ["run-1", "run-3"]
    assert transport.holds[-1] is False


def test_cancelling_a_held_run_releases_the_hold_when_nothing_else_waits(tmp_path: Path) -> None:
    transport = _FakeTransport()
    runs = _service(tmp_path, transport)
    transport.sample = _memory(0)
    runs.start(_request())
    runs.start(_request(end=date(2024, 1, 11)))
    assert transport.holds[-1] is True

    runs.cancel("run-2")

    assert runs.state("run-2").status is RunStatus.CANCELLED
    assert transport.holds[-1] is False
    assert transport.started_ids() == ["run-1"]


def test_a_transport_that_cannot_measure_runs_keeps_the_guard_off(tmp_path: Path) -> None:
    transport = _FakeTransport()
    runs = _service(tmp_path, transport)
    transport.sample = None

    runs.start(_request())
    runs.start(_request(end=date(2024, 1, 11)))

    assert transport.started_ids() == ["run-1", "run-2"]


def test_the_reserve_counts_the_factor_warm_up(tmp_path: Path) -> None:
    """spec D8: 세션 수는 측정 구간에 팩터 워밍업을 더한 값이다. 워밍업을 빼면 둘째 run 이 든다."""
    request = _request()
    environment, strategy = request.environment, request.strategy
    assert environment is not None and strategy is not None
    preflight = _portfolio().preflight(PortfolioPreviewRequest(strategy, environment=environment))
    without_warm_up = run_reserve_bytes(estimated_sessions(environment.start, environment.end, 0))
    assert preflight.history_sessions > 0
    transport = _FakeTransport()
    runs = _service(tmp_path, transport)
    transport.sample = _memory(_reserve(request) + without_warm_up)

    runs.start(request)
    # 같은 구간의 다른 입력(초기자본) — 예약치가 첫 run 과 같다.
    runs.start(replace(request, initial_cash=1.0e9))

    assert transport.started_ids() == ["run-1"]


@pytest.mark.parametrize(("short_bytes", "started"), [(0, 2), (1, 1)])
def test_a_fixed_reserve_replaces_the_estimate(
    tmp_path: Path, short_bytes: int, started: int
) -> None:
    # 추정식이면 mock run 도 0.5GB 를 넘게 잡아 둘째 run 이 늘 기다린다.
    assert _reserve(_request()) > gb_bytes(0.5)
    transport = _FakeTransport()
    runs = _service(tmp_path, transport, memory_reserve_gb=0.1)
    transport.sample = _memory(2 * gb_bytes(0.1) - short_bytes)

    runs.start(_request())
    runs.start(_request(end=date(2024, 1, 11)))

    assert len(transport.started_ids()) == started


@pytest.mark.parametrize("reserve_gb", [0.0, -1.0, float("inf"), float("nan")])
def test_a_fixed_reserve_must_be_a_positive_number(tmp_path: Path, reserve_gb: float) -> None:
    with pytest.raises(ValueError, match="memory_reserve_gb"):
        _service(tmp_path, _FakeTransport(), memory_reserve_gb=reserve_gb)
