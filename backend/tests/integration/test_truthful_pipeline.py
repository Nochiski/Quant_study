"""P1.5-04 truthful pipeline parity: preview factor values are FactorGraph outputs.

Phase 1.5 exit criteria: the current backtest uses real FactorGraph definitions; PIT, factor
output, TargetTape and backtest inputs agree. Every-session rebalance keeps the fixture window
(5 sessions) producing frames, so the CandidateDecision-level assertions cannot pass vacuously.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    InvalidPortfolioRequestError,
    LookAheadViolationError,
    PortfolioDesignService,
    PortfolioPreviewRequest,
    RawObservationUnavailableError,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationPort,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    FactorGraph,
    FieldNode,
    ParameterNode,
    SavedFactorNode,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.trace import trace_factor_graph
from strategy_workbench.domain.portfolio.facade.construction import ExclusionReason
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    DataStep,
    FactorDirection,
    FactorSignal,
    FactorStep,
    Market,
    RebalanceFrequency,
    StrategySpec,
)
from strategy_workbench.domain.strategy.facade.validation import validate_strategy

WINDOW = (date(2024, 1, 8), date(2024, 1, 12))


def _template() -> StrategySpec:
    return StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2024, 1, 12)
    ).template()


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
        data=DataStep(
            market=Market.KRX, start=WINDOW[0], end=WINDOW[1], universe_id="krx.common-stock"
        ),
        factors=FactorStep(factors=factors or (_momentum(), market_cap)),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _service(source: RawObservationPort | None = None) -> PortfolioDesignService:
    return PortfolioDesignService(
        source or MockEquityDataAdapter.demo(),
        BacktestEnginePortfolioAdapter(),
        factor_registry_version="test-registry",
    )


def test_candidate_factor_values_equal_factor_graph_outputs() -> None:
    result = _service().run_pipeline(PortfolioPreviewRequest(_spec()))

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


def test_composite_score_is_the_direction_signed_weighted_sum_of_graph_outputs() -> None:
    spec = _spec()
    result = _service().run_pipeline(PortfolioPreviewRequest(spec))
    weights = {f.factor_id: (f.weight, f.direction) for f in spec.factors.factors}

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
    result = _service().run_pipeline(PortfolioPreviewRequest(spec))
    momentum = spec.factors.factors[0]
    raw = MockEquityDataAdapter.demo().load_raw_observations(
        RawObservationQuery(
            market="KRX",
            universe_id="krx.common-stock",
            start=spec.data.start,
            end=spec.data.end,
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
    direct = evaluate_factor_graph(momentum.graph, observations=observations).values
    pipeline = next(r for r in result.factor_evaluations if r.factor_id == "momentum_3").values
    assert direct == pipeline
    trace = trace_factor_graph(momentum.graph, observations=observations)
    traced = {(v.as_of, v.security_id): v.value for v in trace.nodes[-1].values}
    assert all(traced[(v.as_of, v.security_id)] == v.value for v in pipeline)


def test_factor_value_publication_date_is_the_latest_input_publication() -> None:
    result = _service().run_pipeline(PortfolioPreviewRequest(_spec()))
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

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
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
        _service(_LeakyPort()).run_pipeline(PortfolioPreviewRequest(_spec()))


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
    assert issues["strategy.expression.parameter_type"].path == "factors.factors.0.graph.nodes.1"
    with pytest.raises(InvalidPortfolioRequestError):
        _service().run_pipeline(PortfolioPreviewRequest(spec))

    numeric = replace(spec, parameters=(ChoiceParameter("mode", 2, (1, 2), "choice"),))
    assert "strategy.expression.parameter_type" not in {
        issue.code for issue in validate_strategy(numeric).issues
    }
    assert _service().run_pipeline(PortfolioPreviewRequest(numeric)).preview.tape.frames


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
        _service().run_pipeline(PortfolioPreviewRequest(spec))
    (issue,) = excinfo.value.validation.issues
    assert issue.code == "strategy.expression.reference_unsupported"
    assert issue.path == "factors.factors.1.graph"


def test_unknown_universe_is_a_422_with_the_adapter_detail() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    template["data"]["universe_id"] = "nope.universe"

    response = client.post("/api/v1/portfolio/preview", json={"spec": template})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "portfolio.data.unavailable"
    assert detail["status"] == "invalid_query"
    assert "nope.universe" in detail["detail"]


def test_backtest_route_rejects_what_preview_rejects_with_the_same_codes() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    unknown_universe = {**template, "data": {**template["data"], "universe_id": "nope.universe"}}
    first_factor = template["factors"]["factors"][0]
    referencing = {
        **first_factor,
        "factor_id": "twin",
        "graph": {
            "nodes": [
                {"node_id": "ref", "factor_id": first_factor["factor_id"], "kind": "saved_factor"}
            ],
            "output_node_id": "ref",
            "missing_policy": first_factor["graph"]["missing_policy"],
        },
    }
    saved_reference = {
        **template,
        "factors": {"factors": [first_factor, referencing]},
    }

    for spec, code in (
        (unknown_universe, "portfolio.data.unavailable"),
        (saved_reference, "portfolio.strategy.invalid"),
    ):
        preview = client.post("/api/v1/portfolio/preview", json={"spec": spec})
        run = client.post("/api/v1/backtests", json={"strategy": spec, "core": "python"})
        assert preview.status_code == 422 and run.status_code == 422, (preview.text, run.text)
        assert preview.json()["detail"]["code"] == run.json()["detail"]["code"] == code


def test_unknown_universe_raises_a_typed_error_in_the_service() -> None:
    spec = replace(_spec(), data=replace(_spec().data, universe_id="nope.universe"))
    with pytest.raises(RawObservationUnavailableError, match="nope.universe"):
        _service().run_pipeline(PortfolioPreviewRequest(spec))


def test_backtest_consumes_the_same_truthful_tape_as_preview() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    preview = client.post("/api/v1/portfolio/preview", json={"spec": template}).json()
    accepted = client.post("/api/v1/backtests", json={"strategy": template, "core": "python"})

    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["run_id"]
    state: dict[str, object] = {}
    for _ in range(200):
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        if state["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.025)
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

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
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
                UnaryNode("z", UnaryOperator.ZSCORE, "close", "unary"),
            ),
            output_node_id="z",
        ),
    )
    spec = _spec(zscored)

    clean = _service().run_pipeline(PortfolioPreviewRequest(spec))
    noisy = _service(_NonMemberNoisePort()).run_pipeline(PortfolioPreviewRequest(spec))

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
