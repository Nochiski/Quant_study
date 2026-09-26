"""P1.5-04 truthful pipeline parity: preview factor values are FactorGraph outputs.

Phase 1.5 exit criteria: the current backtest uses real FactorGraph definitions; PIT, factor
output, TargetTape and backtest inputs agree. Every-session rebalance keeps the fixture window
(5 sessions) producing frames, so the CandidateDecision-level assertions cannot pass vacuously.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._execution_error_contract import (
    Portfolio422Response,
)
from strategy_workbench.adapters.inbound.http_api._trace_contract import Trace422Response
from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunSpec,
)
from strategy_workbench.application.factor_research.facade.ports import (
    FactorMetadataPort,
    FactorMetadataSnapshot,
)
from strategy_workbench.application.factor_research.facade.research import (
    FactorGraphRequest,
    FactorResearchService,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    InvalidPortfolioRequestError,
    LookAheadViolationError,
    PortfolioDesignService,
    PortfolioPreviewRequest,
    PortfolioSnapshotMismatchError,
    RawObservationContractError,
    RawObservationUnavailableError,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationPort,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.portfolio_design.facade.trace import (
    StrategyTraceRequest,
    StrategyTraceService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    CrossSectionalNode,
    CrossSectionalOperator,
    FactorGraph,
    FieldNode,
    ParameterNode,
    SavedFactorNode,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry
from strategy_workbench.domain.factor.facade.trace import trace_factor_graph
from strategy_workbench.domain.portfolio.facade.construction import ExclusionReason
from strategy_workbench.domain.strategy.facade.provenance import InlineDraft
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    EligibilityOperator,
    EligibilityRule,
    EligibilityStep,
    FactorDirection,
    FactorSignal,
    RebalanceFrequency,
    SignalNormalization,
    StrategySpec,
    WeightingMethod,
)
from strategy_workbench.domain.strategy.facade.validation import validate_strategy
from tests.backtest_run_wait import wait_for_terminal_run, wait_for_terminal_state

WINDOW = (date(2024, 1, 8), date(2024, 1, 12))


def _environment() -> RunEnvironment:
    """실행 설정은 1.2 부터 문서가 아니라 요청이 싣는다(P2-03)."""
    return RunEnvironment(start=WINDOW[0], end=WINDOW[1], universe_id="krx.common-stock")


# HTTP 테스트는 `/api/v1/strategies/template` 문서를 그대로 쓴다. 템플릿의 기본 리밸런싱이
# 월간이라 5 세션짜리 `WINDOW` 로는 프레임이 하나도 안 나온다 — 1.1 에서는 템플릿이 5년 구간을
# 문서에 갖고 있었고, 1.2 에서는 그 구간을 요청이 싣는다.
HTTP_WINDOW = (date(2023, 1, 2), date(2024, 1, 12))


def _environment_json(**overrides: object) -> dict[str, Any]:
    """HTTP 요청 본문에 싣는 실행 설정."""
    return {
        "start": HTTP_WINDOW[0].isoformat(),
        "end": HTTP_WINDOW[1].isoformat(),
        "universe_id": "krx.common-stock",
        **overrides,
    }


def _template() -> StrategySpec:
    return StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "unused").template()


def _momentum() -> FactorSignal:
    return FactorSignal(
        factor_id="momentum_3",
        label="3세션 모멘텀",
        direction=FactorDirection.HIGH,
        weight=0.6,
        graph=FactorGraph(
            nodes=(
                FieldNode("close", "price.close", "field"),
                TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 3, "time_series"),
            ),
            output_node_id="mom",
        ),
    )


def _spec(*factors: FactorSignal) -> StrategySpec:
    template = _template()
    market_cap = FactorSignal(
        factor_id="size",
        label="시가총액",
        direction=FactorDirection.LOW,
        weight=0.4,
        graph=FactorGraph(
            nodes=(FieldNode("cap", "price.market_cap", "field"),), output_node_id="cap"
        ),
    )
    return replace(
        template,
        factors=factors or (_momentum(), market_cap),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _service(
    source: RawObservationPort | None = None,
    *,
    metadata: FactorMetadataPort | None = None,
    registry_version: str = "test-registry",
) -> PortfolioDesignService:
    return PortfolioDesignService(
        source or MockEquityDataAdapter.demo(),
        BacktestEnginePortfolioAdapter(),
        factor_metadata=metadata or MockEquityDataAdapter.demo(),
        factor_registry_version=registry_version,
    )


def test_candidate_factor_values_equal_factor_graph_outputs() -> None:
    result = _service().run_pipeline(PortfolioPreviewRequest(_spec(), environment=_environment()))

    by_factor = {record.factor_id: record for record in result.factor_evaluations}
    assert set(by_factor) == {"momentum_3", "size"}
    assert result.observations and result.preview.tape.frames
    for observation in result.observations:
        for factor_value in observation.factor_values:
            expected = next(
                v.value
                for v in by_factor[factor_value.factor_id].values
                if (v.as_of, v.security_id) == (observation.as_of, observation.security_id)
            )
            assert factor_value.value == expected
    # A 3-session momentum needs warm-up: the first in-range session must not be synthetic.
    first = min(result.observations, key=lambda item: item.as_of)
    assert by_factor["momentum_3"].plan.minimum_history_sessions >= 3
    assert any(
        v.value is not None for v in by_factor["momentum_3"].values if v.as_of == first.as_of
    )


def test_explain_and_portfolio_compile_identical_plans_from_one_metadata_contract() -> None:
    adapter = MockEquityDataAdapter.demo()
    registry = build_default_factor_registry()
    spec = _spec()
    research = FactorResearchService(registry, adapter, adapter)
    result = _service(adapter, metadata=adapter, registry_version=registry.version).run_pipeline(
        PortfolioPreviewRequest(spec, environment=_environment())
    )

    factor_ids = tuple(factor.factor_id for factor in spec.factors)
    plans = {record.factor_id: record.plan for record in result.factor_evaluations}
    for factor in spec.factors:
        explanation = research.explain(
            FactorGraphRequest(
                graph=factor.graph,
                parameter_ids=tuple(parameter.parameter_id for parameter in spec.parameters),
                factor_ids=factor_ids,
            )
        )
        assert explanation.validation.valid
        assert explanation.data_snapshot_id == result.data_snapshot_id
        assert explanation.plan == plans[factor.factor_id]


def test_group_field_contract_is_shared_by_explain_portfolio_and_backtest() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()

    def request_spec(group_field_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        factor = template["factors"][0]
        graph = {
            "nodes": [
                {"node_id": "close", "field_id": "price.close", "kind": "field"},
                {
                    "node_id": "neutral",
                    "operator": "neutralize",
                    "input_node_id": "close",
                    "group_field_id": group_field_id,
                    "kind": "group",
                },
            ],
            "output_node_id": "neutral",
        }
        return ({**template, "factors": [{**factor, "graph": graph}]}, graph)

    invalid_spec, invalid_graph = request_spec("price.market_cap")
    explain_invalid = client.post("/api/v1/factors/explain", json={"graph": invalid_graph})
    preview_invalid = client.post(
        "/api/v1/portfolio/preview", json={"spec": invalid_spec, "environment": _environment_json()}
    )
    backtest_invalid = client.post(
        "/api/v1/backtests",
        json={"strategy": invalid_spec, "core": "python", "environment": _environment_json()},
    )
    assert explain_invalid.status_code == 200
    assert preview_invalid.status_code == backtest_invalid.status_code == 422
    assert {item["code"] for item in explain_invalid.json()["validation"]["issues"]} == {
        "factor.graph.group_field_type"
    }
    for response in (preview_invalid, backtest_invalid):
        assert {item["code"] for item in response.json()["detail"]["validation"]["issues"]} == {
            "factor.graph.group_field_type"
        }

    valid_spec, valid_graph = request_spec("classification.sector")
    explain_valid = client.post("/api/v1/factors/explain", json={"graph": valid_graph})
    preview_valid = client.post(
        "/api/v1/portfolio/preview", json={"spec": valid_spec, "environment": _environment_json()}
    )
    backtest_valid = client.post(
        "/api/v1/backtests",
        json={"strategy": valid_spec, "core": "python", "environment": _environment_json()},
    )
    assert explain_valid.status_code == preview_valid.status_code == 200
    assert explain_valid.json()["validation"]["valid"] is True
    assert backtest_valid.status_code == 202, backtest_valid.text
    run_id = backtest_valid.json()["run"]["run_id"]
    state = wait_for_terminal_state(client, run_id)
    assert state["status"] == "completed", state
    result = client.get(f"/api/v1/backtests/{run_id}/result")
    assert result.status_code == 200
    assert (
        result.json()["manifest"]["target_tape_hash"] == preview_valid.json()["tape"]["tape_hash"]
    )


@pytest.mark.parametrize(
    ("graph", "output_type"),
    (
        (
            {
                "nodes": [
                    {
                        "node_id": "sector",
                        "field_id": "classification.sector",
                        "kind": "field",
                    }
                ],
                "output_node_id": "sector",
            },
            "group_series",
        ),
        (
            {
                "nodes": [
                    {"node_id": "close", "field_id": "price.close", "kind": "field"},
                    {"node_id": "zero", "value": 0.0, "kind": "constant"},
                    {
                        "node_id": "positive",
                        "operator": "gt",
                        "left_node_id": "close",
                        "right_node_id": "zero",
                        "kind": "comparison",
                    },
                ],
                "output_node_id": "positive",
            },
            "boolean_series",
        ),
        (
            {
                "nodes": [{"node_id": "constant", "value": 1.0, "kind": "constant"}],
                "output_node_id": "constant",
            },
            "scalar",
        ),
    ),
)
def test_non_numeric_factor_signal_output_is_explainable_but_not_executable(
    graph: dict[str, Any], output_type: str
) -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    factor = template["factors"][0]
    spec = {**template, "factors": [{**factor, "graph": graph}]}

    explanation = client.post("/api/v1/factors/explain", json={"graph": graph})
    preview = client.post(
        "/api/v1/portfolio/preview", json={"spec": spec, "environment": _environment_json()}
    )
    backtest = client.post(
        "/api/v1/backtests",
        json={"strategy": spec, "core": "python", "environment": _environment_json()},
    )

    assert explanation.status_code == 200
    assert explanation.json()["validation"]["valid"] is True
    assert explanation.json()["plan"]["steps"][-1]["output_type"] == output_type
    assert preview.status_code == backtest.status_code == 422
    for response in (preview, backtest):
        detail = response.json()["detail"]
        assert detail["code"] == "portfolio.strategy.invalid"
        assert {item["code"] for item in detail["validation"]["issues"]} == {
            "strategy.expression.output_type"
        }


class _DriftedMetadata:
    def __init__(self, source: FactorMetadataPort) -> None:
        self._source = source

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        return replace(
            self._source.resolve_factor_fields(field_ids),
            data_snapshot_id="stale-metadata-snapshot",
        )


class _LegacyRawPort:
    """Pre-P5 public port implementation: no checkpoint keyword or trace capability."""

    def __init__(self, delegate: MockEquityDataAdapter) -> None:
        self._delegate = delegate
        self.calls = 0

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        self.calls += 1
        return self._delegate.load_raw_observations(query)


class _NonFiniteRawPort:
    """Malicious/stale adapter fixture that violates the current immutable port contract."""

    def __init__(
        self,
        delegate: MockEquityDataAdapter,
        *,
        value: float,
        field_id: str | None,
    ) -> None:
        self._delegate = delegate
        self._value = value
        self._field_id = field_id

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        result = self._delegate.load_raw_observations(query)
        observations = list(result.observations)
        for index, observation in enumerate(observations):
            if self._field_id is None:
                observations[index] = replace(observation, previous_weight=self._value)
                return replace(result, observations=tuple(observations))
            if any(field.field_id == self._field_id for field in observation.fields):
                observations[index] = replace(
                    observation,
                    fields=tuple(
                        replace(field, value=self._value)
                        if field.field_id == self._field_id
                        else field
                        for field in observation.fields
                    ),
                )
                return replace(result, observations=tuple(observations))
        raise AssertionError(f"fixture field was not loaded: {self._field_id!r}")

    def load_raw_observations_cancellable(
        self, query: RawObservationQuery, *, checkpoint
    ) -> RawObservationSet:
        checkpoint()
        return self.load_raw_observations(query)


def _spec_using_market_cap_outside_the_factor(role: str) -> StrategySpec:
    spec = _spec(_momentum())
    field_id = "price.market_cap"
    if role == "eligibility":
        return replace(
            spec,
            eligibility=EligibilityStep(
                (EligibilityRule(field_id, EligibilityOperator.GREATER_THAN, 0.0),)
            ),
        )
    if role == "liquidity":
        return replace(
            spec,
            portfolio=replace(
                spec.portfolio,
                liquidity_field_id=field_id,
                minimum_liquidity=0.0,
            ),
        )
    if role == "risk":
        return replace(
            spec,
            portfolio=replace(spec.portfolio, weighting=WeightingMethod.RISK),
            risk=replace(spec.risk, risk_field_id=field_id),
        )
    raise AssertionError(role)


@pytest.mark.parametrize("role", ["eligibility", "liquidity", "risk"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_unused_by_factor_non_finite_raw_field_fails_preview_trace_and_the_backtest_run(
    role: str,
    value: float,
    tmp_path: Path,
) -> None:
    delegate = MockEquityDataAdapter.demo()
    source = _NonFiniteRawPort(delegate, value=value, field_id="price.market_cap")
    portfolio = _service(source, metadata=delegate)
    spec = _spec_using_market_cap_outside_the_factor(role)
    assert validate_strategy(spec).valid

    with pytest.raises(RawObservationContractError, match="raw numeric field value must be finite"):
        portfolio.preview(PortfolioPreviewRequest(spec, environment=_environment()))

    trace = StrategyTraceService(portfolio, InMemoryStrategyRepository())
    with pytest.raises(RawObservationContractError, match="raw numeric field value must be finite"):
        trace.trace(
            StrategyTraceRequest(
                strategy_source=InlineDraft(spec, "inline_draft", "raw-contract-probe"),
                environment=_environment(),
                as_of=_environment().end,
                security_ids=("sec-005930-1",),
                factor_id=spec.factors[0].factor_id,
                include_raw=True,
            )
        )

    backtests = BacktestRunService(
        portfolio,
        InMemoryStrategyRepository(),
        delegate,
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        new_id=lambda: "raw-contract-run",
    )
    # 시작 요청은 데이터를 읽지 않으므로 접수되고, 계약 위반은 tape 단계에서 run 을 실패시킨다.
    accepted = backtests.start(
        BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON, environment=_environment())
    )
    state = wait_for_terminal_run(backtests, accepted.run.run_id)
    assert state.status.value == "failed", state
    assert state.error_code == "portfolio.raw_observation.invalid"
    assert state.error is not None
    assert "raw numeric field value must be finite" in state.error


def test_non_finite_raw_opening_book_is_not_normalized_to_missing() -> None:
    delegate = MockEquityDataAdapter.demo()
    portfolio = _service(
        _NonFiniteRawPort(delegate, value=float("inf"), field_id=None),
        metadata=delegate,
    )

    with pytest.raises(RawObservationContractError, match="previous_weight must be finite"):
        portfolio.preview(PortfolioPreviewRequest(_spec(), environment=_environment()))


def test_duplicate_raw_fields_fail_closed_with_one_code_on_every_http_execution_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = MockEquityDataAdapter.load_raw_observations_cancellable

    def duplicate_one_field(
        adapter: MockEquityDataAdapter,
        query: RawObservationQuery,
        *,
        checkpoint,
    ) -> RawObservationSet:
        result = original(adapter, query, checkpoint=checkpoint)
        observation = next(item for item in result.observations if item.fields)
        field = observation.fields[0]
        # A stale/foreign adapter can bypass frozen construction; the application consumer
        # boundary must still reject its ambiguous execution input before calculation.
        object.__setattr__(
            observation,
            "fields",
            (field, field, *observation.fields[1:]),
        )
        return result

    monkeypatch.setattr(
        MockEquityDataAdapter,
        "load_raw_observations_cancellable",
        duplicate_one_field,
    )
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    factor = spec["factors"][0]
    responses = (
        (
            client.post(
                "/api/v1/portfolio/preview", json={"spec": spec, "environment": _environment_json()}
            ),
            Portfolio422Response,
        ),
        (
            client.post(
                "/api/v1/strategies/debug/trace",
                json={
                    "strategy_source": {"kind": "inline_draft", "spec": spec},
                    "environment": _environment_json(),
                    "security_ids": ["sec-005930-1"],
                    "factor_id": factor["factor_id"],
                    "node_ids": [factor["graph"]["output_node_id"]],
                },
            ),
            Trace422Response,
        ),
    )

    for response, contract in responses:
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "portfolio.raw_observation.invalid"
        assert "field_ids must be unique" in response.json()["detail"]["message"]
        TypeAdapter(contract).validate_python(response.json())

    # 백테스트 시작은 데이터를 읽지 않아 접수되고, 같은 위반이 tape 단계에서 run 을 실패시킨다.
    backtest = client.post(
        "/api/v1/backtests",
        json={"strategy": spec, "core": "python", "environment": _environment_json()},
    )
    assert backtest.status_code == 202, backtest.text
    state = wait_for_terminal_state(client, backtest.json()["run"]["run_id"])
    assert state["status"] == "failed", state
    assert state["error_code"] == "portfolio.raw_observation.invalid"
    assert "field_ids must be unique" in state["error"]


def test_legacy_raw_port_keeps_preview_and_backtest_compatible(tmp_path: Path) -> None:
    adapter = MockEquityDataAdapter.demo()
    legacy = _LegacyRawPort(adapter)
    portfolio = _service(legacy, metadata=adapter)
    spec = _spec()

    assert portfolio.preview(PortfolioPreviewRequest(spec, environment=_environment())).tape.frames
    backtests = BacktestRunService(
        portfolio,
        InMemoryStrategyRepository(),
        adapter,
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        new_id=lambda: "legacy-port-run",
    )

    accepted = backtests.start(
        BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON, environment=_environment())
    )

    assert accepted.run.run_id == "legacy-port-run"
    state = wait_for_terminal_run(backtests, "legacy-port-run")
    assert state.status.value == "completed", state
    # preview 1회 + run 의 tape 단계 1회. 시작 요청 자체는 관측 포트를 부르지 않는다(#158).
    assert legacy.calls == 2


def test_metadata_raw_snapshot_mismatch_blocks_portfolio_and_backtest(tmp_path: Path) -> None:
    adapter = MockEquityDataAdapter.demo()
    portfolio = _service(adapter, metadata=_DriftedMetadata(adapter))
    spec = _spec()
    with pytest.raises(PortfolioSnapshotMismatchError, match="snapshot mismatch"):
        portfolio.preview(PortfolioPreviewRequest(spec, environment=_environment()))

    backtests = BacktestRunService(
        portfolio,
        InMemoryStrategyRepository(),
        adapter,
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        new_id=lambda: "must-not-complete",
    )
    # 스냅샷 대조는 원시 관측을 읽은 뒤에만 가능하므로 tape 단계에서 run 을 실패시킨다(#158).
    accepted = backtests.start(
        BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON, environment=_environment())
    )
    state = wait_for_terminal_run(backtests, accepted.run.run_id)
    assert state.status.value == "failed", state
    assert state.error_code == "portfolio.raw_observation.invalid"
    assert state.error is not None
    assert "snapshot mismatch" in state.error


def test_composite_score_is_the_direction_signed_weighted_sum_of_graph_outputs() -> None:
    """`normalization: none` 은 1.1 과 수치가 같다 — 원시값 가중 합 그대로다(P2-04)."""
    spec = _spec()
    spec = replace(spec, signal=replace(spec.signal, normalization=SignalNormalization.NONE))
    result = _service().run_pipeline(PortfolioPreviewRequest(spec, environment=_environment()))
    weights = {f.factor_id: (f.weight, f.direction) for f in spec.factors}

    assert result.preview.tape.frames, "every-session rebalance must yield frames"
    scored = 0
    for frame in result.preview.tape.frames:
        by_id = {o.security_id: o for o in result.observations if o.as_of == frame.signal_as_of}
        assert frame.candidates
        for decision in frame.candidates:
            values: dict[str, float] = {}
            missing = False
            for factor_value in by_id[decision.security_id].factor_values:
                if factor_value.value is None:
                    missing = True
                else:
                    values[factor_value.factor_id] = factor_value.value
            if missing:
                assert ExclusionReason.MISSING_FACTOR in decision.exclusion_reasons
                continue
            expected = sum(
                (1.0 if direction is FactorDirection.HIGH else -1.0) * weight * values[factor_id]
                for factor_id, (weight, direction) in weights.items()
            ) / sum(abs(weight) for weight, _ in weights.values())
            assert decision.composite_score is not None
            assert abs(decision.composite_score - expected) < 1e-12
            scored += 1
    assert scored > 0, "no candidate was scored — the parity check ran on nothing"


def test_graph_evaluation_matches_a_direct_evaluation_over_raw_pit_fields() -> None:
    spec = _spec()
    result = _service().run_pipeline(PortfolioPreviewRequest(spec, environment=_environment()))
    momentum = spec.factors[0]
    raw = MockEquityDataAdapter.demo().load_raw_observations(
        RawObservationQuery(
            market="KRX",
            universe_id="krx.common-stock",
            start=_environment().start,
            end=_environment().end,
            field_ids=("price.close", "price.market_cap"),
            history_sessions_before_start=2,
        )
    )
    observations = tuple(
        FactorObservation(
            as_of=item.as_of,
            security_id=item.security_id,
            fields=tuple(FactorFieldValue(f.field_id, f.value) for f in item.fields),
        )
        for item in raw.observations
    )
    # 파이프라인과 같은 결측 정책으로 직접 평가한다: 정책의 owner 는 실행 설정이다(P2-02).
    missing = _environment().missing
    direct = evaluate_factor_graph(
        momentum.graph, observations=observations, missing=missing
    ).values
    pipeline = next(r for r in result.factor_evaluations if r.factor_id == "momentum_3").values
    assert direct == pipeline
    trace = trace_factor_graph(momentum.graph, observations=observations, missing=missing)
    traced = {(v.as_of, v.security_id): v.value for v in trace.nodes[-1].values}
    assert all(traced[(v.as_of, v.security_id)] == v.value for v in pipeline)


def test_factor_value_publication_date_is_the_latest_input_publication() -> None:
    result = _service().run_pipeline(PortfolioPreviewRequest(_spec(), environment=_environment()))
    raw_by_key = {
        (o.as_of, o.security_id): o
        for o in MockEquityDataAdapter.demo()
        .load_raw_observations(
            RawObservationQuery(
                "KRX", "krx.common-stock", *WINDOW, ("price.close", "price.market_cap"), 2
            )
        )
        .observations
    }
    for observation in result.observations:
        raw = raw_by_key[(observation.as_of, observation.security_id)]
        size = next(v for v in observation.factor_values if v.factor_id == "size")
        cap = next((f for f in raw.fields if f.field_id == "price.market_cap"), None)
        assert size.available_date == (cap.available_date if cap else observation.as_of)
        assert size.available_date <= observation.as_of


class _LeakyPort:
    """Adapter that violates the contract: one field published after its observation date."""

    def load_raw_observations(
        self, query: RawObservationQuery, *, checkpoint=lambda: None
    ) -> RawObservationSet:
        result = MockEquityDataAdapter.demo().load_raw_observations(query)
        first = result.observations[0]
        leaked = replace(
            first,
            fields=tuple(
                replace(f, available_date=first.as_of + timedelta(days=365)) for f in first.fields
            ),
        )
        return replace(result, observations=(leaked, *result.observations[1:]))


def test_future_dated_raw_field_fails_closed_and_loud() -> None:
    with pytest.raises(LookAheadViolationError, match="available_date=2025"):
        _service(_LeakyPort()).run_pipeline(
            PortfolioPreviewRequest(_spec(), environment=_environment())
        )


def test_choice_parameter_referenced_by_a_parameter_node_is_a_validation_issue() -> None:
    graph = FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            ParameterNode("mode", "mode", "parameter"),
            BinaryNode("scaled", BinaryOperator.MULTIPLY, "close", "mode", "binary"),
        ),
        output_node_id="scaled",
    )
    factor = replace(_momentum(), graph=graph)
    spec = replace(
        _spec(factor), parameters=(ChoiceParameter("mode", "fast", ("fast", "slow"), "choice"),)
    )

    issues = {issue.code: issue for issue in validate_strategy(spec).issues}
    assert "strategy.expression.parameter_type" in issues
    assert issues["strategy.expression.parameter_type"].path == "factors.0.graph.nodes.1"
    with pytest.raises(InvalidPortfolioRequestError):
        _service().run_pipeline(PortfolioPreviewRequest(spec, environment=_environment()))

    numeric = replace(spec, parameters=(ChoiceParameter("mode", 2, (1, 2), "choice"),))
    assert "strategy.expression.parameter_type" not in {
        issue.code for issue in validate_strategy(numeric).issues
    }
    assert (
        _service()
        .run_pipeline(PortfolioPreviewRequest(numeric, environment=_environment()))
        .preview.tape.frames
    )


def test_saved_factor_reference_is_rejected_up_front_not_silently_missing() -> None:
    referencing = FactorSignal(
        factor_id="twin",
        label="참조",
        direction=FactorDirection.HIGH,
        weight=0.5,
        graph=FactorGraph(
            nodes=(SavedFactorNode("ref", "momentum_3", "saved_factor"),), output_node_id="ref"
        ),
    )
    spec = _spec(_momentum(), referencing)
    assert validate_strategy(spec).valid

    with pytest.raises(InvalidPortfolioRequestError) as excinfo:
        _service().run_pipeline(PortfolioPreviewRequest(spec, environment=_environment()))
    (issue,) = excinfo.value.validation.issues
    assert issue.code == "strategy.expression.reference_unsupported"
    assert issue.path == "factors.1.graph"


def test_unknown_universe_is_a_422_with_the_adapter_detail() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()

    response = client.post(
        "/api/v1/portfolio/preview",
        json={
            "spec": template,
            "environment": _environment_json(universe_id="nope.universe"),
        },
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "portfolio.data.unavailable"
    assert detail["status"] == "invalid_query"
    assert "nope.universe" in detail["detail"]


def test_structural_rejections_share_a_code_and_data_failures_fail_the_backtest_run() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    unknown_universe = _environment_json(universe_id="nope.universe")
    first_factor = template["factors"][0]
    referencing = {
        **first_factor,
        "factor_id": "twin",
        "graph": {
            "nodes": [
                {"node_id": "ref", "factor_id": first_factor["factor_id"], "kind": "saved_factor"}
            ],
            "output_node_id": "ref",
        },
    }
    saved_reference = {
        **template,
        "factors": [first_factor, referencing],
    }

    # 구조적 거부(저장 팩터 참조)는 데이터를 읽기 전에 판정되므로 두 경로가 같은 422 코드를 낸다.
    preview = client.post(
        "/api/v1/portfolio/preview",
        json={"spec": saved_reference, "environment": _environment_json()},
    )
    run = client.post(
        "/api/v1/backtests",
        json={"strategy": saved_reference, "core": "python", "environment": _environment_json()},
    )
    assert preview.status_code == 422 and run.status_code == 422, (preview.text, run.text)
    assert (
        preview.json()["detail"]["code"]
        == run.json()["detail"]["code"]
        == "portfolio.strategy.invalid"
    )

    # 데이터 부재(미지 유니버스)는 관측을 읽어야 알 수 있다. preview 는 422, 백테스트 시작은
    # 데이터를 읽지 않아 접수되고(이슈 #158) run 의 tape 단계가 같은 사유로 실패한다.
    preview = client.post(
        "/api/v1/portfolio/preview", json={"spec": template, "environment": unknown_universe}
    )
    assert preview.status_code == 422, preview.text
    assert preview.json()["detail"]["code"] == "portfolio.data.unavailable"
    run = client.post(
        "/api/v1/backtests",
        json={"strategy": template, "core": "python", "environment": unknown_universe},
    )
    assert run.status_code == 202, run.text
    state = wait_for_terminal_state(client, run.json()["run"]["run_id"])
    assert state["status"] == "failed", state
    assert state["error_code"] == "portfolio.data.unavailable"
    assert "nope.universe" in state["error"]


def test_unknown_universe_raises_a_typed_error_in_the_service() -> None:
    bogus = replace(_environment(), universe_id="nope.universe")
    with pytest.raises(RawObservationUnavailableError, match="nope.universe"):
        _service().run_pipeline(PortfolioPreviewRequest(_spec(), environment=bogus))


def test_backtest_consumes_the_same_truthful_tape_as_preview() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    preview = client.post(
        "/api/v1/portfolio/preview", json={"spec": template, "environment": _environment_json()}
    ).json()
    accepted = client.post(
        "/api/v1/backtests",
        json={"strategy": template, "core": "python", "environment": _environment_json()},
    )

    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["run_id"]
    state = wait_for_terminal_state(client, run_id)
    assert state["status"] == "completed", state
    manifest = client.get(f"/api/v1/backtests/{run_id}/result").json()["manifest"]
    # The run consumed the very tape the preview showed: same hash, same adapter snapshot.
    assert manifest["target_tape_hash"] == preview["tape"]["tape_hash"]
    assert manifest["data_snapshot_id"] == preview["tape"]["data_snapshot_id"]


class _NonMemberNoisePort:
    """Adds one *non-member* row per session carrying an extreme value (D-001 probe).

    A correct pipeline scores members against members only, so every member's factor value and
    composite score must be byte-identical to the run without these rows.
    """

    def __init__(self, value: float = 1e6) -> None:
        self._value = value

    def load_raw_observations(
        self, query: RawObservationQuery, *, checkpoint=lambda: None
    ) -> RawObservationSet:
        result = MockEquityDataAdapter.demo().load_raw_observations(query)
        template = result.observations[0]
        extra = tuple(
            replace(
                template,
                as_of=as_of,
                security_id="zzz.outsider",
                universe_member=False,
                fields=tuple(
                    replace(f, value=self._value, available_date=as_of) for f in template.fields
                ),
                previous_weight=0.0,
            )
            for as_of in sorted({o.as_of for o in result.observations})
        )
        merged = tuple(
            sorted((*result.observations, *extra), key=lambda o: (o.as_of, o.security_id))
        )
        return replace(result, observations=merged)


def test_non_members_do_not_enter_the_member_cross_section() -> None:
    zscored = replace(
        _momentum(),
        graph=FactorGraph(
            nodes=(
                FieldNode("close", "price.close", "field"),
                CrossSectionalNode("z", CrossSectionalOperator.ZSCORE, "close", "cross_sectional"),
            ),
            output_node_id="z",
        ),
    )
    spec = _spec(zscored)

    clean = _service().run_pipeline(PortfolioPreviewRequest(spec, environment=_environment()))
    noisy = _service(_NonMemberNoisePort()).run_pipeline(
        PortfolioPreviewRequest(spec, environment=_environment())
    )

    def member_values(result: object) -> dict[tuple[date, str], float | None]:
        return {
            (o.as_of, o.security_id): o.factor_values[0].value
            for o in result.observations  # pyright: ignore[reportAttributeAccessIssue]  # reason: PortfolioPipelineResult
            if o.universe_member
        }

    assert member_values(clean), "the probe ran on an empty member panel"
    assert member_values(noisy) == member_values(clean)

    # The extra row is recorded as a NOT_IN_UNIVERSE candidate, so the tape hash legitimately
    # differs; what must not move is the selection and the weights.
    def targets(result: object) -> list[tuple[date, tuple[object, ...]]]:
        return [
            (frame.signal_as_of, tuple((t.security_id, t.weight) for t in frame.targets))
            for frame in result.preview.tape.frames  # pyright: ignore[reportAttributeAccessIssue]  # reason: PortfolioPipelineResult
        ]

    assert any(entry[1] for entry in targets(clean)), "the probe produced no targets"
    assert targets(noisy) == targets(clean)


class _WideRangePort:
    """Adapter that answers a wider window than asked (D-004 probe)."""

    def load_raw_observations(
        self, query: RawObservationQuery, *, checkpoint=lambda: None
    ) -> RawObservationSet:
        widened = replace(query, end=query.end + timedelta(days=14))
        return MockEquityDataAdapter.demo().load_raw_observations(widened)


def test_sessions_outside_the_run_range_fail_closed() -> None:
    spec = _spec()
    with pytest.raises(RawObservationContractError, match="outside the requested run range"):
        _service(_WideRangePort()).run_pipeline(
            PortfolioPreviewRequest(spec, environment=_environment())
        )


def test_the_widened_window_would_otherwise_have_produced_extra_frames() -> None:
    """The probe is not vacuous: without the guard the wider answer reaches the compiler."""
    widened = MockEquityDataAdapter.demo().load_raw_observations(
        RawObservationQuery(
            market="KRX",
            universe_id="krx.common-stock",
            start=_environment().start,
            end=_environment().end + timedelta(days=14),
            field_ids=("price.close", "price.market_cap"),
            history_sessions_before_start=2,
        )
    )
    assert any(session > _environment().end for session in widened.sessions)


_WARNING_START = date(2024, 1, 2)  # the mock lacks calendar for the market-cap lag here


def test_raw_observation_warnings_reach_the_preview() -> None:
    """D-005: the adapter's caveats are part of the answer, not something the service drops."""
    spec = _spec()
    early = replace(_environment(), start=_WARNING_START)

    preview = _service().preview(PortfolioPreviewRequest(spec, environment=early))

    assert any("insufficient mock calendar for lag" in item for item in preview.warnings)


def test_preview_warnings_are_recorded_in_the_run_manifest() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    close_factor = template["factors"][0]
    spec = {
        **template,
        "factors": [
            close_factor,
            {
                **close_factor,
                "factor_id": "size",
                "graph": {
                    **close_factor["graph"],
                    "nodes": [{"node_id": "cap", "field_id": "price.market_cap", "kind": "field"}],
                    "output_node_id": "cap",
                },
            },
        ],
        "portfolio": {
            **template["portfolio"],
            "rebalance": "every_n_sessions",
            "rebalance_every_n_sessions": 1,
        },
    }

    early = _environment_json(start=_WARNING_START.isoformat())
    preview = client.post("/api/v1/portfolio/preview", json={"spec": spec, "environment": early})
    assert preview.status_code == 200, preview.text
    assert preview.json()["warnings"], "the probe strategy produced no adapter warning"

    accepted = client.post(
        "/api/v1/backtests", json={"strategy": spec, "core": "python", "environment": early}
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["run_id"]
    state = wait_for_terminal_state(client, run_id)
    assert state["status"] == "completed", state

    manifest = client.get(f"/api/v1/backtests/{run_id}/result").json()["manifest"]
    recorded = {item["code"]: item["message"] for item in manifest["warnings"]}
    assert "portfolio.raw_observation" in recorded
    assert recorded["portfolio.raw_observation"] in preview.json()["warnings"]


def test_run_pipeline_reports_monotonic_progress_through_every_phase() -> None:
    """이슈 #162: tape 단계가 실행 시간의 97% 를 쓰는데 진행 콜백이 없어 2% 에 고정됐다.

    원시 로딩·팩터 평가(팩터별, 노드 안)·TargetTape 컴파일을 지나며 0 에서 1 까지 단조 증가하고,
    진행 콜백 유무가 산출 tape 를 바꾸지 않는다.
    """
    reported: list[tuple[float, str]] = []

    result = _service().run_pipeline(
        PortfolioPreviewRequest(_spec(), environment=_environment()),
        progress=lambda fraction, message: reported.append((fraction, message)),
    )

    fractions = [fraction for fraction, _ in reported]
    assert fractions == sorted(fractions)
    assert fractions[0] == 0.0
    assert fractions[-1] == 1.0
    messages = " ".join(message for _, message in reported)
    assert "momentum_3" in messages and "size" in messages
    # 팩터 2개인데 팩터 경계만이 아니라 노드 안에서도 올라간다.
    assert len(set(fractions)) > 2 + 4
    baseline = _service().run_pipeline(PortfolioPreviewRequest(_spec(), environment=_environment()))
    assert result.preview.tape.tape_hash == baseline.preview.tape.tape_hash


class _ProgressReportingRawPort:
    """로딩 진행을 보고하는 선택 능력을 가진 테스트 포트. 관측은 mock 어댑터에 위임한다."""

    def __init__(self) -> None:
        self._delegate = MockEquityDataAdapter.demo()

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        return self._delegate.load_raw_observations(query)

    def load_raw_observations_reporting(
        self, query: RawObservationQuery, *, checkpoint, progress
    ) -> RawObservationSet:
        progress(0.25)
        result = self._delegate.load_raw_observations_cancellable(query, checkpoint=checkpoint)
        progress(1.0)
        return result


def test_run_pipeline_maps_raw_load_progress_into_the_loading_band() -> None:
    """이슈 #162: 로딩 진행을 보고하는 포트면 로딩 구간(0~0.171) 안에서 막대가 오른다."""
    reported: list[tuple[float, str]] = []

    _service(_ProgressReportingRawPort()).run_pipeline(
        PortfolioPreviewRequest(_spec(), environment=_environment()),
        progress=lambda fraction, message: reported.append((fraction, message)),
    )

    loading = [fraction for fraction, message in reported if message == "Loading raw observations"]
    assert loading == pytest.approx([0.0, 0.04275, 0.171])
    fractions = [fraction for fraction, _ in reported]
    assert fractions == sorted(fractions)


@pytest.mark.parametrize("factor_count", [3, 6])
def test_run_pipeline_progress_never_steps_back_across_factor_boundaries(
    factor_count: int,
) -> None:
    """이슈 #162 리뷰 P3-2: 팩터 구간을 누적 덧셈으로 나누면 3·6개에서 경계가 1ulp 역행했다."""
    factors = tuple(
        replace(_momentum(), factor_id=f"momentum_{index}", weight=1.0 / factor_count)
        for index in range(factor_count)
    )
    reported: list[float] = []

    _service().run_pipeline(
        PortfolioPreviewRequest(_spec(*factors), environment=_environment()),
        progress=lambda fraction, message: reported.append(fraction),
    )

    assert reported == sorted(reported)
