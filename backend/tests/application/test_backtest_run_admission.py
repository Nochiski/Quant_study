"""이슈 #161: 백테스트 run 접수 — 동시 실행 상한·대기열·같은 입력 dedupe.

run 한 건이 실데이터에서 CPU 수백 초·RSS 수 GB 를 쓰므로 한꺼번에 계산하는 run 수를
묶고, 같은 입력의 재요청(재클릭·새로고침 뒤 재시작)은 도는 run 으로 잇는다. 타이밍에
기대지 않도록 원시 관측 로딩 입구에서 멈추는 포트로 run 을 붙잡아 두고 단언한다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Thread

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock._fixture import build_demo_fixture
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
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
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import PageRequest
from strategy_workbench.domain.analytics.facade.metrics import (
    MetricScope,
    build_default_metric_registry,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore, MetricWindow
from strategy_workbench.domain.strategy.facade.specification import (
    FloatParameter,
    RebalanceFrequency,
    StrategySpec,
)
from tests.backtest_run_wait import RawLoadBarrier, join_run_thread, wait_for_terminal_run

_ENVIRONMENT = RunEnvironment(
    start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
)


_LINEAGE = "lineage-s"
# (run 서비스, 입구 포트, 입구마다 남긴 (질의 끝 날짜, 그 순간 `running` 인 run 수))
_Gated = tuple[BacktestRunService, RawLoadBarrier, list[tuple[date, int]]]
_GatedRuns = Callable[..., _Gated]


@pytest.fixture
def gated_runs(tmp_path: Path) -> Iterator[_GatedRuns]:
    """원시 관측 로딩 입구에서 멈추는 run 서비스를 만든다. 테스트가 끝나면 입구를 연다.

    단언이 해제 전에 실패해도 run 스레드가 입구에 30초씩 남아 다음 테스트를 흔들지 않게 한다.
    """

    barriers: list[RawLoadBarrier] = []

    def make(*run_ids: str, run_slots: int = 1) -> _Gated:
        entries: list[tuple[date, int]] = []

        def enter(query: RawObservationQuery) -> None:
            summaries = runs.list_runs(PageRequest(offset=0, limit=50)).items
            running = sum(summary.run.status is RunStatus.RUNNING for summary in summaries)
            entries.append((query.end, running))

        barrier = RawLoadBarrier(MockEquityDataAdapter.demo(), on_enter=enter)
        strategies = InMemoryStrategyRepository()
        # 인라인 초안이 실을 계열(`_LINEAGE`)은 저장된 전략이어야 한다.
        StrategyDesignService(strategies, new_id=lambda: _LINEAGE).create(_spec())
        runs = BacktestRunService(
            PortfolioDesignService(
                barrier,
                BacktestEnginePortfolioAdapter(),
                factor_metadata=MockEquityDataAdapter.demo(),
                factor_registry_version="test-registry",
            ),
            strategies,
            MockEquityDataAdapter.demo(),
            BacktestEngineExecutorAdapter(build_default_metric_registry()),
            LocalArtifactStore(tmp_path),
            run_repository=SQLiteBacktestRunRepository(),
            new_id=iter(run_ids).__next__,
            run_slots=run_slots,
        )
        barriers.append(barrier)
        return runs, barrier, entries

    yield make
    for barrier in barriers:
        barrier.release.set()


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
    runs, port, entries = gated_runs("queue-1", "queue-2", "queue-3")

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
    assert entries == [
        (date(2024, 1, 12), 1),
        (date(2024, 1, 11), 1),
        (date(2024, 1, 10), 1),
    ]


def test_a_run_whose_thread_fails_to_start_frees_its_slot_for_the_next_queued_run(
    gated_runs: _GatedRuns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#284 리뷰 P3-1·#294 리뷰 P3-A: 스레드 기동에 실패한 run 은 `failed` 로 끝나고 자리 수를
    바꾸지 않는다.

    첫 run 은 접수 스레드에서, 셋째 run 은 끝나는 run 의 스레드에서 기동에 실패한다. 실패가 자리를
    쥐면(누수) 둘째가 기다리고, 쥐지 않은 자리를 내놓으면(과소 계산) 둘째가 도는 동안 셋째가 바로
    뜨고, 넘기기를 멈추면 넷째가 오류 없이 `queued` 에 남는다.
    """

    class _SomeRunsFailToStart(Thread):
        def start(self) -> None:
            if self.name in ("backtest-startfail-first", "backtest-startfail-third"):
                raise RuntimeError("can't start new thread")
            super().start()

    monkeypatch.setattr(
        "strategy_workbench.application.backtest_run._service.Thread", _SomeRunsFailToStart
    )
    runs, port, entries = gated_runs(
        "startfail-first", "startfail-second", "startfail-third", "startfail-fourth"
    )

    runs.start(_request(end=date(2024, 1, 12)))  # 접수 스레드에서 기동 실패
    assert runs.start(_request(end=date(2024, 1, 11))).run.message == "Run accepted"
    assert port.entered.wait(timeout=30), "second run never reached the tape stage"
    for end in (date(2024, 1, 10), date(2024, 1, 9)):
        assert runs.start(_request(end=end)).run.message == "Waiting for a free run slot"
    port.release.set()  # 끝나는 run 의 스레드가 셋째를 띄우다 실패하고 넷째로 넘긴다

    assert wait_for_terminal_run(runs, "startfail-fourth").status is RunStatus.COMPLETED
    assert runs.state("startfail-first").status is RunStatus.FAILED
    assert runs.state("startfail-third").status is RunStatus.FAILED
    assert entries == [(date(2024, 1, 11), 1), (date(2024, 1, 9), 1)]


def test_cancelling_a_queued_run_ends_it_at_once_without_starting_it(
    gated_runs: _GatedRuns,
) -> None:
    runs, port, entries = gated_runs("cancel-running", "cancel-queued")

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
    assert [end for end, _running in entries] == [date(2024, 1, 12)]
    assert runs.state("cancel-queued").status is RunStatus.CANCELLED


def test_the_same_request_joins_its_run_while_the_run_is_in_flight(
    gated_runs: _GatedRuns,
) -> None:
    runs, port, entries = gated_runs("join-first", "join-after-completion")
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
    assert [end for end, _running in entries] == [_ENVIRONMENT.end, _ENVIRONMENT.end]


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
    runs, port, _entries = gated_runs("variant-base", "variant-other", run_slots=2)
    request = _request()

    runs.start(request)
    assert port.entered.wait(timeout=30), "first run never reached the tape stage"
    other = runs.start(variant(request))

    assert other.run.run_id == "variant-other"
    port.release.set()
    for run_id in ("variant-base", "variant-other"):
        assert wait_for_terminal_run(runs, run_id).status is RunStatus.COMPLETED


def test_parameter_values_split_runs_and_an_explicit_default_joins(
    gated_runs: _GatedRuns,
) -> None:
    """검증 랩 V3-02: 해소된 파라미터 값이 실행 spec 에 박혀 같은 입력 판정을 가른다(spec D4)."""
    runs, port, _entries = gated_runs("parameter-base", "parameter-other", run_slots=2)
    request = replace(
        _request(),
        strategy=replace(_spec(), parameters=(FloatParameter("scale", 1.0, -1.0, 1.0, "float"),)),
    )

    runs.start(request)
    assert port.entered.wait(timeout=30), "first run never reached the tape stage"
    # 기본값을 적은 요청은 생략한 요청과 해소 결과가 같아 도는 run 에 잇는다.
    joined = runs.start(replace(request, parameter_values={"scale": 1}))
    other = runs.start(replace(request, parameter_values={"scale": -1.0}))

    assert joined.run.run_id == "parameter-base"
    assert other.run.run_id == "parameter-other"
    port.release.set()
    for run_id in ("parameter-base", "parameter-other"):
        assert wait_for_terminal_run(runs, run_id).status is RunStatus.COMPLETED


def test_a_run_whose_cancellation_was_requested_is_not_joined(gated_runs: _GatedRuns) -> None:
    runs, port, _entries = gated_runs("restart-cancelled", "restart-new", run_slots=2)
    request = _request()

    runs.start(request)
    assert port.entered.wait(timeout=30), "first run never reached the tape stage"
    assert runs.cancel("restart-cancelled").status is RunStatus.CANCEL_REQUESTED
    restarted = runs.start(request)

    assert restarted.run.run_id == "restart-new"
    port.release.set()
    assert wait_for_terminal_run(runs, "restart-cancelled").status is RunStatus.CANCELLED
    assert wait_for_terminal_run(runs, "restart-new").status is RunStatus.COMPLETED


def test_the_same_request_on_another_field_contract_gets_another_run_fingerprint(
    tmp_path: Path,
) -> None:
    """#235·#284: 같은 입력 잇기(spec·provenance)는 프로세스 안에서 데이터 판이 고정이라는 전제를
    쓴다. 필드 계약 판이 다른 프로세스라면 같은 요청이라도 run 지문이 갈려, 지문으로 결과를 나눌
    쪽(검증 랩 V3-04)이 옛 뜻으로 계산한 결과를 재사용하지 않는다.
    """

    def fingerprint(adapter: MockEquityDataAdapter, run_id: str) -> str:
        runs = BacktestRunService(
            PortfolioDesignService(
                adapter,
                BacktestEnginePortfolioAdapter(),
                factor_metadata=adapter,
                factor_registry_version="test-registry",
            ),
            InMemoryStrategyRepository(),
            adapter,
            BacktestEngineExecutorAdapter(build_default_metric_registry()),
            LocalArtifactStore(tmp_path / run_id),
            run_repository=SQLiteBacktestRunRepository(),
            new_id=lambda: run_id,
        )
        runs.start(_request())
        assert wait_for_terminal_run(runs, run_id).status is RunStatus.COMPLETED
        return runs.result(run_id).manifest.run_fingerprint

    relabeled = tuple(
        replace(profile, unit="USD") if profile.field_id == "flow.foreign_net_buy" else profile
        for profile in MockEquityDataAdapter.demo().list_fields()
    )
    same = fingerprint(MockEquityDataAdapter.demo(), "contract-same")
    assert fingerprint(MockEquityDataAdapter.demo(), "contract-again") == same
    other = MockEquityDataAdapter(replace(build_demo_fixture(), profiles=relabeled))
    assert fingerprint(other, "contract-other") != same


def test_the_limit_must_allow_at_least_one_run(gated_runs: _GatedRuns) -> None:
    with pytest.raises(ValueError, match="run_slots=0"):
        gated_runs(run_slots=0)


def _wait_for_entries(entries: list[tuple[date, int]], count: int) -> None:
    # 입구 barrier 가 30초 뒤 스스로 풀리므로 그보다 짧게 기다린다.
    deadline = time.monotonic() + 10
    while len(entries) < count:
        if time.monotonic() > deadline:
            raise AssertionError(f"runs never reached the tape stage — entered={entries}")
        time.sleep(0.01)


def test_experiments_share_all_but_one_slot_and_take_turns(gated_runs: _GatedRuns) -> None:
    """검증 랩 V3-04: 슬롯 2개면 실험은 1개만 쓰고, 실험끼리는 번갈아 배정한다(spec D6)."""
    runs, port, entries = gated_runs("a1", "a2", "a3", "b1", "user", run_slots=2)
    for end, owner in (
        (date(2024, 1, 12), "exp-a"),
        (date(2024, 1, 11), "exp-a"),
        (date(2024, 1, 10), "exp-a"),
        (date(2024, 1, 9), "exp-b"),
    ):
        runs.start(_request(end=end), owner=owner)
    # 실험 몫(1개)이 찼어도 단일 실행은 비워 둔 슬롯에서 바로 뜬다.
    user = runs.start(_request(end=date(2024, 1, 8)))
    _wait_for_entries(entries, 2)
    assert runs.state("a2").status is RunStatus.QUEUED

    port.release.set()
    # 끝나는 방식은 보지 않는다(하루짜리 구간은 tape 가 비어 실패로 끝난다). 배정 순서만 본다.
    for run_id in ("a1", "a2", "a3", "b1", "user"):
        wait_for_terminal_run(runs, run_id)

    assert user.run.message == "Run accepted"
    # 접수 순(FIFO)이라면 a2 다음이 a3 이다. b1 이 a3 보다 먼저 돈다.
    assert [end.day for end, _running in entries] == [12, 8, 11, 9, 10]


def test_a_shared_run_is_cancelled_only_when_every_owner_withdraws(
    gated_runs: _GatedRuns,
) -> None:
    """V3-03 인계 (1): 실험을 취소해도 사용자가 이은 run 은 돌고, 사용자 취소는 실험 run 을 멈추지
    않는다."""
    runs, port, _entries = gated_runs("shared", "experiment-only")
    shared = runs.start(_request()).run.run_id
    assert port.entered.wait(timeout=30), "shared run never reached the tape stage"
    joined = runs.start(_request(), owner="exp").run.run_id
    queued = runs.start(_request(end=date(2024, 1, 11)), owner="exp").run.run_id

    after_user = runs.cancel(shared)
    after_user_on_experiment_run = runs.cancel(queued)
    after_experiment = runs.cancel(shared, owner="exp")
    experiment_only = runs.cancel(queued, owner="exp")
    port.release.set()

    assert joined == shared
    assert after_user.status is RunStatus.RUNNING
    assert after_user_on_experiment_run.status is RunStatus.QUEUED
    assert after_experiment.status is RunStatus.CANCEL_REQUESTED
    assert experiment_only.status is RunStatus.CANCELLED
    assert wait_for_terminal_run(runs, shared).status is RunStatus.CANCELLED


def test_requests_that_differ_only_in_metric_window_labels_join_one_run(
    gated_runs: _GatedRuns,
) -> None:
    """#335 DOMAIN-V1-02: 지표 창 표시 이름은 같은 입력 잇기를 가르지 않는다."""
    runs, port, _entries = gated_runs("labelled")
    window = MetricWindow(MetricScope.OUT_OF_SAMPLE, date(2024, 1, 10), date(2024, 1, 12), "OOS")

    first = runs.start(replace(_request(), metric_windows=(window,))).run.run_id
    again = runs.start(replace(_request(), metric_windows=(replace(window, label="다른 이름"),)))
    port.release.set()

    assert again.run.run_id == first
    assert wait_for_terminal_run(runs, first).status is RunStatus.COMPLETED


def test_a_user_who_joins_a_waiting_experiment_run_moves_it_to_the_single_run_slot(
    gated_runs: _GatedRuns,
) -> None:
    runs, port, entries = gated_runs("e1", "e2", run_slots=2)
    runs.start(_request(end=date(2024, 1, 12)), owner="exp")
    waiting = runs.start(_request(end=date(2024, 1, 11)), owner="exp").run.run_id
    _wait_for_entries(entries, 1)

    joined = runs.start(_request(end=date(2024, 1, 11))).run.run_id
    # 실험 몫이 찼어도 사용자가 이은 run 은 비워 둔 단일 실행 슬롯에서 뜬다.
    _wait_for_entries(entries, 2)
    port.release.set()

    assert joined == waiting
    assert [end.day for end, _running in entries] == [12, 11]
    wait_for_terminal_run(runs, "e2")


def test_only_runs_of_the_same_lineage_and_trial_key_join(gated_runs: _GatedRuns) -> None:
    """#346 리뷰 P2-1: 잇기는 원장 행을 적지 않으므로 계열·시도 키가 다르면 잇지 않는다."""
    runs, port, _entries = gated_runs("base", "in-lineage", "other-key")
    request = _request()

    base = runs.start(request).run.run_id
    assert port.entered.wait(timeout=30), "base run never reached the tape stage"
    in_lineage = runs.start(replace(request, lineage_strategy_id=_LINEAGE)).run.run_id
    other_key = runs.start(request, owner="exp", trial_key_override="f" * 64).run.run_id
    same = runs.start(request, owner="exp").run.run_id
    port.release.set()

    assert (in_lineage, other_key, same) == ("in-lineage", "other-key", base)
    assert [entry.run_id for entry in runs.trial_ledger(_LINEAGE).trials[0].runs] == ["in-lineage"]
    for run_id in (base, in_lineage, other_key):
        wait_for_terminal_run(runs, run_id)


def test_a_user_who_withdraws_from_a_joined_experiment_run_returns_it_to_the_experiment(
    gated_runs: _GatedRuns,
) -> None:
    """#346 리뷰 P3-1: 사용자가 이었다 취소한 대기 run 이 단일 실행 전용 슬롯을 쥐지 않는다."""
    runs, port, entries = gated_runs("e1", "u1", "e2", "u2", run_slots=2)
    runs.start(_request(end=date(2024, 1, 12)), owner="exp")
    runs.start(_request(end=date(2024, 1, 9)))
    _wait_for_entries(entries, 2)
    runs.start(_request(end=date(2024, 1, 11)), owner="exp")
    runs.start(_request(end=date(2024, 1, 11)))  # 사용자가 e2 를 잇는다
    still_waiting = runs.cancel("e2")  # 사용자만 빠진다
    user = runs.start(_request(end=date(2024, 1, 10)))
    port.release.set()
    for run_id in ("e1", "u1", "e2", "u2"):
        wait_for_terminal_run(runs, run_id)

    assert still_waiting.status is RunStatus.QUEUED
    assert user.run.message == "Waiting for a free run slot"
    # e2 가 단일 실행 레인에 남았다면 먼저 빈 슬롯을 잡아 u2 보다 앞선다.
    assert [end.day for end, _running in entries] == [12, 9, 10, 11]


def test_an_experiment_run_waiting_for_the_experiment_share_says_it_waits(
    gated_runs: _GatedRuns,
) -> None:
    """#346 리뷰 P3-2: 전체 슬롯이 남아도 실험 몫이 찼으면 기다린다는 문장이다."""
    runs, port, entries = gated_runs("e1", "e2", run_slots=2)
    runs.start(_request(end=date(2024, 1, 12)), owner="exp")
    _wait_for_entries(entries, 1)
    waiting = runs.start(_request(end=date(2024, 1, 11)), owner="exp")
    port.release.set()

    assert (waiting.run.status, waiting.run.message) == (
        RunStatus.QUEUED,
        "Waiting for a free run slot",
    )
    wait_for_terminal_run(runs, "e2")
