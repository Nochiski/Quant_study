"""run 계산(`RunComputation.compute`)이 종결을 값으로 돌려주는지(run-worker spec D10).

worker 프로세스가 예외 객체 대신 코드와 문장을 보낼 수 있도록 예상한 실패도 값이다. 진행 보고가
취소를 알리면 그 자리에서 멈춘다. 끝까지 도는 경로는 서비스·HTTP 테스트가 본다.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

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
from strategy_workbench.application.backtest_run.facade.computation import RunComputation
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestArtifactStorePort,
    BacktestDataPort,
    BacktestDataQuery,
    BacktestDataset,
    BacktestDataUnavailableError,
    BacktestExecutionRequest,
    BacktestExecutorPort,
    CancellationCheck,
    ProgressCallback,
    RunCancelled,
    RunCancelledError,
    RunCommitted,
    RunFailed,
    RunFootprint,
    RunJob,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPipelineOptions,
    PortfolioPipelineResult,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    ResolvedBacktestRunSpec,
    RunStage,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.strategy.facade.specification import strategy_spec_hash


def _job() -> RunJob:
    strategy = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "x").template()
    environment = RunEnvironment(
        start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
    )
    spec = BacktestRunSpec(
        strategy=strategy, environment=environment, benchmark_security_id="krx:999999"
    )
    return RunJob(
        run_id="run-computation",
        spec=ResolvedBacktestRunSpec.from_request(
            spec, strategy=strategy, environment=environment, parameter_values={}
        ),
        provenance=StrategyProvenance(
            kind=StrategySourceKind.INLINE_DRAFT,
            spec_hash=strategy_spec_hash(strategy),
            schema_version=strategy.identity.schema_version,
        ),
        strategy_source=None,
        lineage_strategy_id=None,
    )


class _UnknownBenchmark:
    """tape 앞의 벤치마크 확인 조회에서 거절하는 데이터 포트. 서버 경로를 담은 원문을 싣는다."""

    def __init__(self) -> None:
        self.queries: list[BacktestDataQuery] = []

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        self.queries.append(query)
        raise BacktestDataUnavailableError(
            f"unknown benchmark — security_id={query.benchmark_security_id} "
            r"root=C:\Users\someone\quant-ledger"
        )


def _computation(data: _UnknownBenchmark) -> RunComputation:
    # 이 경로들은 tape 앞에서 끝나므로 나머지 포트에 닿지 않는다.
    return RunComputation(
        portfolio_design=cast(PortfolioDesignService, object()),
        data_source=cast(BacktestDataPort, data),
        executor=cast(BacktestExecutorPort, object()),
        artifact_store=cast(BacktestArtifactStorePort, object()),
    )


def test_an_expected_failure_comes_back_as_a_code_and_a_masked_message() -> None:
    data = _UnknownBenchmark()
    reported: list[tuple[RunStage, float, str]] = []

    outcome = _computation(data).compute(
        _job(),
        progress=lambda stage, fraction, message: reported.append((stage, fraction, message)),
        cancelled=lambda: False,
    )

    assert isinstance(outcome, RunFailed)
    assert outcome.error_code == "backtest.run.benchmark_unknown"
    assert outcome.error == (
        "BacktestDataUnavailableError: unknown benchmark — security_id=krx:999999 root=<path>"
    )
    # 원래 예외는 같은 프로세스 전송의 로그용으로만 따라온다.
    assert isinstance(outcome.exception, BacktestDataUnavailableError)
    assert reported == [("tape", 0.02, "Compiling target tape")]
    assert [query.benchmark_security_id for query in data.queries] == ["krx:999999"]


def test_a_progress_report_that_sees_the_cancel_stops_the_computation() -> None:
    data = _UnknownBenchmark()

    def cancelled_at_once(stage: RunStage, fraction: float, message: str) -> None:
        raise RunCancelledError(f"run cancelled — stage={stage}")

    outcome = _computation(data).compute(
        _job(), progress=cancelled_at_once, cancelled=lambda: False
    )

    assert outcome == RunCancelled()
    assert data.queries == []


class _Payload:
    """tape·데이터처럼 큰 중간 결과를 흉내 내는 객체. weakref 로 살아 있는지 본다."""


class _FailsHoldingPayload:
    """큰 중간 결과를 지역 변수로 쥔 채 실패하는 데이터 포트."""

    def __init__(self) -> None:
        self.payload_ref: weakref.ref[_Payload] | None = None

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        payload = _Payload()
        self.payload_ref = weakref.ref(payload)
        raise BacktestDataUnavailableError(
            f"unknown benchmark — security_id={query.benchmark_security_id}"
        )


def test_a_failed_computation_does_not_keep_its_intermediate_results_alive() -> None:
    """실패 값의 원래 예외가 계산 프레임의 지역 변수를 붙잡지 않는다(#572 리뷰 P2).

    붙잡으면 tape·데이터가 GC 미루기 구간 밖까지 살아, 구간이 끝난 뒤 전체 수집이 그것을 훑어 같은
    프로세스의 HTTP 요청을 세운다(#196).
    """
    data = _FailsHoldingPayload()

    outcome = _computation(cast(_UnknownBenchmark, data)).compute(
        _job(), progress=lambda stage, fraction, message: None, cancelled=lambda: False
    )

    assert isinstance(outcome, RunFailed)
    # 로그용 원래 예외와 그 traceback 위치는 남는다.
    assert isinstance(outcome.exception, BacktestDataUnavailableError)
    assert outcome.exception.__traceback__ is not None
    assert data.payload_ref is not None
    assert data.payload_ref() is None


class _WatchedPipeline(PortfolioDesignService):
    """파이프라인 결과를 weakref 로 지켜본다. 계산은 그대로 한다."""

    result_ref: weakref.ref[PortfolioPipelineResult] | None = None

    def run_pipeline(
        self,
        request: PortfolioPreviewRequest,
        *,
        options: PortfolioPipelineOptions | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[float, str], None] = lambda fraction, message: None,
    ) -> PortfolioPipelineResult:
        result = super().run_pipeline(
            request, options=options, cancelled=cancelled, progress=progress
        )
        self.result_ref = weakref.ref(result)
        return result


class _EngineWatch(BacktestEngineExecutorAdapter):
    """엔진 단계에 들어설 때 파이프라인 결과가 아직 살아 있는지 적는다."""

    def __init__(self, pipeline: _WatchedPipeline) -> None:
        super().__init__(build_default_metric_registry())
        self._pipeline = pipeline
        self.pipeline_alive: bool | None = None

    def execute(
        self,
        request: BacktestExecutionRequest,
        *,
        progress: ProgressCallback,
        cancelled: CancellationCheck,
    ) -> BacktestRunResult:
        ref = self._pipeline.result_ref
        self.pipeline_alive = ref is not None and ref() is not None
        return super().execute(request, progress=progress, cancelled=cancelled)


def test_a_committed_run_reports_its_input_size_and_drops_the_raw_observations_before_the_engine(
    tmp_path: Path,
) -> None:
    """spec D8: 커밋은 읽은 입력의 크기(세션·필드·팩터·관측 행)를 로그용으로 싣는다.

    그 크기를 구하려고 파이프라인 결과를 쥐면 원시 관측 수백만 객체가 data·engine 단계 내내 살아
    최대 RSS 가 커진다 — 엔진 앞에서는 이미 놓았다.
    """
    adapter = MockEquityDataAdapter.demo()
    pipeline = _WatchedPipeline(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="test-registry",
    )
    executor = _EngineWatch(pipeline)
    job = _job()
    # 월간 리밸런싱이 두 번 드는 mock 구간, 벤치마크 없음.
    environment = replace(job.spec.environment, start=date(2026, 1, 2), end=date(2026, 2, 20))
    job = replace(job, spec=replace(job.spec, environment=environment, benchmark_security_id=None))

    outcome = RunComputation(pipeline, adapter, executor, LocalArtifactStore(tmp_path)).compute(
        job, progress=lambda stage, fraction, message: None, cancelled=lambda: False
    )

    assert isinstance(outcome, RunCommitted), outcome
    assert executor.pipeline_alive is False
    expected = PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="test-registry",
    ).run_pipeline(PortfolioPreviewRequest(job.spec.strategy, environment=job.spec.environment))
    rows = expected.raw_observations
    assert outcome.footprint == RunFootprint(
        sessions=len({row.as_of for row in rows}),
        fields=len({field.field_id for row in rows for field in row.fields}),
        factors=len(job.spec.strategy.factors),
        observation_rows=len(rows),
    )
