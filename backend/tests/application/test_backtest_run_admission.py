"""이슈 #161: 백테스트 run 접수 — 동시 실행 상한·대기열·같은 입력 dedupe.

run 한 건이 실데이터에서 CPU 수백 초·RSS 수 GB 를 쓰므로 한꺼번에 계산하는 run 수를
묶고, 같은 입력의 재요청(재클릭·새로고침 뒤 재시작)은 도는 run 으로 잇는다. 타이밍에
기대지 않도록 원시 관측 로딩 입구에서 멈추는 포트로 run 을 붙잡아 두고 단언한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Event

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunSpec,
    InlineDraft,
    RunStatus,
)
from strategy_workbench.application.portfolio_design.facade.design import PortfolioDesignService
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import PageRequest
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore
from strategy_workbench.domain.strategy.facade.specification import (
    RebalanceFrequency,
    StrategySpec,
)
from tests.backtest_run_wait import join_run_thread, wait_for_terminal_run

_ENVIRONMENT = RunEnvironment(
    start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
)


class _GatedRawPort:
    """원시 관측 로딩 입구에서 `release` 까지 멈추는 관측 포트(tape 단계 안).

    입구마다 질의 끝 날짜와 그 순간 `running` 인 run 수를 남긴다. 로딩은 mock 어댑터에 맡긴다.
    """

    def __init__(self) -> None:
        self._delegate = MockEquityDataAdapter.demo()
        self.entered = Event()
        self.release = Event()
        self.entries: list[tuple[date, int]] = []
        self.runs: BacktestRunService | None = None

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        return self.load_raw_observations_cancellable(query, checkpoint=lambda: None)

    def load_raw_observations_cancellable(
        self, query: RawObservationQuery, *, checkpoint: Callable[[], None]
    ) -> RawObservationSet:
        if self.runs is None:
            raise RuntimeError("gated raw port is not bound to a run service")
        summaries = self.runs.list_runs(PageRequest(offset=0, limit=50)).items
        running = sum(summary.run.status is RunStatus.RUNNING for summary in summaries)
        self.entries.append((query.end, running))
        self.entered.set()
        if not self.release.wait(timeout=30):
            raise TimeoutError("gated raw port was not released")
        return self._delegate.load_raw_observations_cancellable(query, checkpoint=checkpoint)


_GatedRuns = Callable[..., tuple[BacktestRunService, _GatedRawPort]]


@pytest.fixture
def gated_runs(tmp_path: Path) -> Iterator[_GatedRuns]:
    """입구에서 멈추는 run 서비스를 만든다. 테스트가 끝나면 입구를 연다.

    단언이 해제 전에 실패해도 run 스레드가 입구에 30초씩 남아 다음 테스트를 흔들지 않게 한다.
    """

    ports: list[_GatedRawPort] = []

    def make(
        *run_ids: str, max_concurrent_runs: int = 1
    ) -> tuple[BacktestRunService, _GatedRawPort]:
        port = _GatedRawPort()
        runs = BacktestRunService(
            PortfolioDesignService(
                port,
                BacktestEnginePortfolioAdapter(),
                factor_metadata=MockEquityDataAdapter.demo(),
                factor_registry_version="test-registry",
            ),
            InMemoryStrategyRepository(),
            MockEquityDataAdapter.demo(),
            BacktestEngineExecutorAdapter(build_default_metric_registry()),
            LocalArtifactStore(tmp_path),
            new_id=iter(run_ids).__next__,
            max_concurrent_runs=max_concurrent_runs,
        )
        port.runs = runs
        ports.append(port)
        return runs, port

    yield make
    for port in ports:
        port.release.set()


def _spec(*, weight: float = 1.0) -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    return replace(
        template,
        factors=tuple(replace(factor, weight=weight) for factor in template.factors),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _request(**environment: object) -> BacktestRunSpec:
    return BacktestRunSpec(
        strategy=_spec(),
        core=ExecutionCore.PYTHON,
        environment=replace(_ENVIRONMENT, **environment),
    )


def test_runs_over_the_limit_wait_queued_and_start_in_acceptance_order(
    gated_runs: _GatedRuns,
) -> None:
    runs, port = gated_runs("queue-1", "queue-2", "queue-3")

    runs.start(_request(end=date(2024, 1, 12)))
    assert port.entered.wait(timeout=30), "first run never reached the tape stage"
    second = runs.start(_request(end=date(2024, 1, 11)))
    third = runs.start(_request(end=date(2024, 1, 10)))

    # 자리는 첫 run 이 쥐고 있다. 뒤의 둘은 스레드 없이 기다리고 접수 문장이 그 사실을 말한다.
    for accepted in (second, third):
        assert accepted.run.status is RunStatus.QUEUED
        assert accepted.run.stage == "queued"
        assert accepted.run.message == "Waiting for a free run slot"
        assert runs.state(accepted.run.run_id).status is RunStatus.QUEUED
    port.release.set()

    for run_id in ("queue-1", "queue-2", "queue-3"):
        assert wait_for_terminal_run(runs, run_id).status is RunStatus.COMPLETED
    # 접수 순서대로 돌았고, 어느 run 이 원시 관측을 읽기 시작할 때도 도는 run 은 자기 하나였다.
    assert port.entries == [
        (date(2024, 1, 12), 1),
        (date(2024, 1, 11), 1),
        (date(2024, 1, 10), 1),
    ]


def test_cancelling_a_queued_run_ends_it_at_once_without_starting_it(
    gated_runs: _GatedRuns,
) -> None:
    runs, port = gated_runs("cancel-running", "cancel-queued")

    runs.start(_request(end=date(2024, 1, 12)))
    assert port.entered.wait(timeout=30), "first run never reached the tape stage"
    runs.start(_request(end=date(2024, 1, 11)))

    cancelled = runs.cancel("cancel-queued")

    # 자리가 날 때까지 `cancel_requested` 로 두지 않고 바로 끝낸다.
    assert cancelled.status is RunStatus.CANCELLED
    assert [event.status for event in runs.events("cancel-queued")] == [
        RunStatus.QUEUED,
        RunStatus.CANCELLED,
    ]
    port.release.set()
    assert wait_for_terminal_run(runs, "cancel-running").status is RunStatus.COMPLETED
    join_run_thread("cancel-running")
    # 첫 run 이 자리를 내놓은 뒤에도 취소한 run 은 뜨지 않는다.
    assert [end for end, _running in port.entries] == [date(2024, 1, 12)]
    assert runs.state("cancel-queued").status is RunStatus.CANCELLED


def test_the_same_request_joins_its_run_while_the_run_is_in_flight(
    gated_runs: _GatedRuns,
) -> None:
    runs, port = gated_runs("join-first", "join-after-completion")
    request = _request()

    runs.start(request)
    assert port.entered.wait(timeout=30), "first run never reached the tape stage"
    again = runs.start(request)

    assert again.run.run_id == "join-first"
    assert again.run.status is RunStatus.RUNNING
    assert runs.list_runs(PageRequest(offset=0, limit=50)).total == 1
    port.release.set()
    assert wait_for_terminal_run(runs, "join-first").status is RunStatus.COMPLETED

    # 끝난 run 은 잇지 않는다 — 같은 요청이 새 run 을 만든다.
    after = runs.start(request)
    assert after.run.run_id == "join-after-completion"
    assert wait_for_terminal_run(runs, "join-after-completion").status is RunStatus.COMPLETED
    assert [end for end, _running in port.entries] == [_ENVIRONMENT.end, _ENVIRONMENT.end]


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(
            lambda r: replace(r, environment=replace(_ENVIRONMENT, fee_bps=30.0)), id="fee"
        ),
        pytest.param(lambda r: replace(r, initial_cash=r.initial_cash * 2), id="initial-cash"),
        pytest.param(lambda r: replace(r, core=ExecutionCore.RUST), id="core"),
        pytest.param(
            lambda r: replace(
                r, strategy=None, strategy_source=InlineDraft(_spec(), "inline_draft", "a" * 64)
            ),
            id="strategy-source",
        ),
        # `==` 는 가중치 1 과 1.0 을 같은 spec 으로 보지만 `spec_hash` 는 가른다. provenance 가
        # 갈라야 매니페스트가 다른 전략 hash 를 기록한 run 을 돌려주지 않는다.
        pytest.param(lambda r: replace(r, strategy=_spec(weight=1)), id="same-spec-other-hash"),
    ],
)
def test_a_request_that_differs_in_any_input_starts_its_own_run(
    gated_runs: _GatedRuns, variant: Callable[[BacktestRunSpec], BacktestRunSpec]
) -> None:
    runs, port = gated_runs("variant-base", "variant-other", max_concurrent_runs=2)
    request = _request()

    runs.start(request)
    assert port.entered.wait(timeout=30), "first run never reached the tape stage"
    other = runs.start(variant(request))

    assert other.run.run_id == "variant-other"
    port.release.set()
    for run_id in ("variant-base", "variant-other"):
        assert wait_for_terminal_run(runs, run_id).status is RunStatus.COMPLETED


def test_a_run_whose_cancellation_was_requested_is_not_joined(gated_runs: _GatedRuns) -> None:
    runs, port = gated_runs("restart-cancelled", "restart-new", max_concurrent_runs=2)
    request = _request()

    runs.start(request)
    assert port.entered.wait(timeout=30), "first run never reached the tape stage"
    assert runs.cancel("restart-cancelled").status is RunStatus.CANCEL_REQUESTED
    restarted = runs.start(request)

    assert restarted.run.run_id == "restart-new"
    port.release.set()
    assert wait_for_terminal_run(runs, "restart-cancelled").status is RunStatus.CANCELLED
    assert wait_for_terminal_run(runs, "restart-new").status is RunStatus.COMPLETED


def test_the_limit_must_allow_at_least_one_run(gated_runs: _GatedRuns) -> None:
    with pytest.raises(ValueError, match="max_concurrent_runs=0"):
        gated_runs(max_concurrent_runs=0)
