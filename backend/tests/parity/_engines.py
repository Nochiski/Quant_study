"""엔진별 층 결과(columnar-tape spec 5절). 지금은 객체 경로 하나뿐이고, 열 경로가 들어오면 `ENGINES`
에 항목을 더한다(spec D8 의 `tape_engine` 인자).

오류도 결과다. 예외 종류와 진단 내용이 엔진마다 같아야 하므로(spec D6) `Failure` 로 접어 비교한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.factor_research.facade.ports import FactorMetadataPort
from strategy_workbench.application.portfolio_design.facade.design import (
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPreviewRequest,
    RawObservationContractError,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationPort,
    RawObservationQuery,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.factor._evaluation import _compute_nodes
from strategy_workbench.domain.factor.facade.evaluation import NonFiniteFactorCalculationError
from strategy_workbench.domain.portfolio.facade.construction import (
    NonFinitePortfolioCalculationError,
    PortfolioTraceSelection,
    compile_target_tape_with_trace,
)

from ._corpus import (
    REGISTRY,
    SECURITIES,
    CompilerCase,
    EvaluatorCase,
    PipelineCase,
    SyntheticRawSource,
)


@dataclass(frozen=True)
class Failure:
    """층 결과 대신 난 예상된 실패. `detail` 은 예외가 싣는 진단 전부다."""

    error_type: str
    detail: object


@dataclass(frozen=True)
class NodeCache:
    """L1 결과: 노드마다 칸 값과, 원장이 가린 칸 때문에 결측이 된 칸."""

    computed: dict[str, list[float | bool | None]]
    masked: dict[str, frozenset[int]]


@dataclass(frozen=True)
class TapeEngine:
    raw: Callable[[RawObservationPort, RawObservationQuery], object]
    nodes: Callable[[EvaluatorCase], object]
    compile: Callable[[CompilerCase], object]
    # 원천 어댑터를 받는다. 테스트는 `pipeline_source`, 실원장 스크립트는 duckdb 어댑터를 넘긴다.
    pipeline: Callable[[PipelineCase, RawObservationPort, FactorMetadataPort], object]


def pipeline_source(name: str) -> tuple[RawObservationPort, FactorMetadataPort]:
    """말뭉치 사례의 `source` 이름이 가리키는 원천(원시 관측 포트, 필드 계약 포트)."""
    if name == "mock":
        adapter = MockEquityDataAdapter.demo()
        return adapter, adapter
    source = SyntheticRawSource(
        17,
        lookahead=name == "synthetic-lookahead",
        securities=40 if name == "synthetic-wide" else len(SECURITIES),
    )
    return source, source


def _object_nodes(case: EvaluatorCase) -> object:
    try:
        evaluator = _compute_nodes(
            case.graph,
            observations=case.observations,
            missing=case.missing,
            parameters=case.parameters,
        )
    except NonFiniteFactorCalculationError as error:
        return Failure(
            type(error).__name__, (error.node_id, error.as_of, error.security_id, error.value)
        )
    return NodeCache(evaluator.computed, evaluator.masked)


def _object_compile(case: CompilerCase) -> object:
    try:
        return compile_target_tape_with_trace(
            case.spec,
            environment=_compile_environment(case),
            data_snapshot_id="parity",
            sessions=case.sessions,
            observations=case.observations,
            trace_selection=PortfolioTraceSelection(
                as_of=None, security_ids=SECURITIES, include_order_delta=True
            ),
        )
    except NonFinitePortfolioCalculationError as error:
        return Failure(type(error).__name__, str(error))


def _compile_environment(case: CompilerCase) -> RunEnvironment:
    return RunEnvironment(start=case.sessions[0], end=case.sessions[-1], universe_id="parity")


def _object_pipeline(
    case: PipelineCase, source: RawObservationPort, metadata: FactorMetadataPort
) -> object:
    service = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=metadata,
        factor_registry_version=REGISTRY.version,
    )
    try:
        return service.run_pipeline(
            PortfolioPreviewRequest(case.spec, environment=case.environment), options=case.options
        )
    except (InvalidPortfolioRequestError, RawObservationContractError) as error:
        return Failure(type(error).__name__, (str(error), getattr(error, "validation", None)))


ENGINES: dict[str, TapeEngine] = {
    "object": TapeEngine(
        raw=lambda source, query: source.load_raw_observations(query),
        nodes=_object_nodes,
        compile=_object_compile,
        pipeline=_object_pipeline,
    ),
}
