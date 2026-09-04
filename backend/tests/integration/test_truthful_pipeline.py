"""P1.5-04 truthful pipeline parity: preview factor values are FactorGraph outputs.

Phase 1.5 exit criteria: the current backtest uses real FactorGraph definitions; PIT, factor
output, TargetTape and backtest inputs agree.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

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
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldNode,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.factor.facade.trace import trace_factor_graph
from strategy_workbench.domain.portfolio.facade.construction import ExclusionReason
from strategy_workbench.domain.strategy.facade.specification import (
    DataStep,
    FactorDirection,
    FactorSignal,
    FactorStep,
    Market,
    StrategySpec,
)


def _spec() -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2024, 1, 12)
    ).template()
    momentum = FactorSignal(
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
            market=Market.KRX,
            start=date(2024, 1, 8),
            end=date(2024, 1, 12),
            universe_id="krx.common-stock",
        ),
        factors=FactorStep(factors=(momentum, market_cap)),
    )


def _service() -> PortfolioDesignService:
    return PortfolioDesignService(
        MockEquityDataAdapter.demo(),
        BacktestEnginePortfolioAdapter(),
        factor_registry_version="test-registry",
    )


def test_candidate_factor_values_equal_factor_graph_outputs() -> None:
    spec = _spec()
    result = _service().run_pipeline(PortfolioPreviewRequest(spec))

    by_factor = {record.factor_id: record for record in result.factor_evaluations}
    assert set(by_factor) == {"momentum_3", "size"}
    for observation in result.observations:
        for factor_value in observation.factor_values:
            expected = next(
                v.value
                for v in by_factor[factor_value.factor_id].values
                if (v.as_of, v.security_id) == (observation.as_of, observation.security_id)
            )
            assert factor_value.value == expected
    # A 3-session momentum needs warm-up: the first in-range session must not be a synthetic number.
    first = min(result.observations, key=lambda item: item.as_of)
    assert by_factor["momentum_3"].plan.minimum_history_sessions >= 3
    assert any(
        v.value is not None for v in by_factor["momentum_3"].values if v.as_of == first.as_of
    )


def test_composite_score_is_the_direction_signed_weighted_sum_of_graph_outputs() -> None:
    spec = _spec()
    result = _service().run_pipeline(PortfolioPreviewRequest(spec))
    weights = {f.factor_id: (f.weight, f.direction) for f in spec.factors.factors}

    for frame in result.preview.tape.frames:
        by_id = {o.security_id: o for o in result.observations if o.as_of == frame.signal_as_of}
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


def test_graph_evaluation_matches_a_direct_evaluation_over_raw_pit_fields() -> None:
    spec = _spec()
    service = _service()
    result = service.run_pipeline(PortfolioPreviewRequest(spec))
    momentum = spec.factors.factors[0]
    raw = MockEquityDataAdapter.demo().load_raw_observations(
        RawObservationQuery(
            start=spec.data.start,
            end=spec.data.end,
            field_ids=("price.close", "price.market_cap"),
            minimum_history_sessions=3,
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


def test_backtest_consumes_the_same_truthful_tape_as_preview() -> None:
    import time

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
