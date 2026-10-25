"""팩터 미리보기는 PIT 모집단의 평가 결과를 방향별 상위 다섯 개로 투영한다."""

from collections.abc import Callable
from dataclasses import replace

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.portfolio_design.facade.trace import (
    StrategyTraceRequest,
    StrategyTraceService,
)
from strategy_workbench.domain.factor.facade.expression import FactorGraph, FieldNode
from strategy_workbench.domain.strategy.facade.provenance import InlineDraft
from strategy_workbench.domain.strategy.facade.specification import FactorDirection, FactorSignal
from tests.integration.test_truthful_pipeline import _environment, _service, _spec


class _RankingSource:
    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        raw = MockEquityDataAdapter.demo().load_raw_observations(query)
        # 비구성원의 극단값은 어느 방향의 순위·유효수에도 들어가지 않는다.
        values = (30.0, 10.0, 20.0, 20.0, 50.0, 40.0, 1_000_000.0, -1_000_000.0)
        rows = []
        for row in raw.observations:
            if row.security_id != "sec-005930-1":
                continue
            for index, value in enumerate(values):
                rows.append(
                    replace(
                        row,
                        security_id=f"sec-test-{index}",
                        universe_member=index < 6,
                        fields=tuple(
                            replace(field, value=value)
                            if field.field_id == "price.close"
                            else field
                            for field in row.fields
                        ),
                    )
                )
        return replace(raw, observations=tuple(rows))

    def load_raw_observations_cancellable(
        self, query: RawObservationQuery, *, checkpoint: Callable[[], None]
    ) -> RawObservationSet:
        checkpoint()
        return self.load_raw_observations(query)


@pytest.mark.parametrize(
    "direction,indices",
    [
        (FactorDirection.HIGH, [4, 5, 0, 2, 3]),
        (FactorDirection.LOW, [1, 2, 3, 0, 5]),
    ],
)
def test_factor_preview_direction_cutoff_and_pit_membership(
    direction: FactorDirection, indices: list[int]
) -> None:
    factor = FactorSignal(
        factor_id="close",
        label="종가",
        direction=direction,
        graph=FactorGraph(
            nodes=(FieldNode("close", "price.close", "field"),), output_node_id="close"
        ),
    )
    spec = _spec(factor)
    preview = (
        StrategyTraceService(
            _service(_RankingSource()), InMemoryStrategyRepository(), MockEquityDataAdapter.demo()
        )
        .trace(
            StrategyTraceRequest(
                strategy_source=InlineDraft(spec, "inline_draft", "ranking-preview"),
                environment=_environment(),
                as_of=_environment().end,
                security_ids=(),
                factor_id="close",
            )
        )
        .factor_preview
    )
    assert preview.valid_count == 6
    assert preview.missing_count == 0
    assert [row.security_id for row in preview.top] == [f"sec-test-{index}" for index in indices]


@pytest.mark.parametrize(
    "missing,first",
    [("drop", "sec-test-2"), ("zero", "sec-test-1"), ("cross_sectional_median", "sec-test-2")],
)
def test_tie_breaker_uses_evaluated_missing_policy_in_trace(missing: str, first: str) -> None:
    from strategy_workbench.domain.equity.facade.research_data import CellKind
    from strategy_workbench.domain.factor.facade.expression import (
        BinaryNode,
        BinaryOperator,
        MissingPolicy,
    )

    class MissingSource(_RankingSource):
        def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
            raw = super().load_raw_observations(query)
            return replace(
                raw,
                observations=tuple(
                    replace(
                        row,
                        fields=tuple(
                            replace(field, value=None, kind=CellKind.MISSING)
                            if row.security_id == "sec-test-1" and field.field_id == "price.close"
                            else field
                            for field in row.fields
                        ),
                    )
                    for row in raw.observations
                ),
            )

    primary = FactorSignal(
        factor_id="primary",
        label="주 점수",
        direction=FactorDirection.HIGH,
        graph=FactorGraph(
            nodes=(
                FieldNode("volume", "price.adj_close", "field"),
                BinaryNode("fixed", BinaryOperator.DIVIDE, "volume", "volume", "binary"),
            ),
            output_node_id="fixed",
        ),
    )
    auxiliary = FactorSignal(
        factor_id="aux",
        label="보조",
        direction=FactorDirection.HIGH,
        graph=FactorGraph(
            nodes=(FieldNode("close", "price.close", "field"),), output_node_id="close"
        ),
    )
    spec = _spec(primary, auxiliary)
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            tie_breaker_factor_id="aux",
            tie_breaker_direction=FactorDirection.LOW,
            selection_count=2,
        ),
    )
    from strategy_workbench.domain.strategy.facade.validation import validate_strategy

    assert validate_strategy(spec).valid, validate_strategy(spec).issues
    response = StrategyTraceService(
        _service(MissingSource()), InMemoryStrategyRepository(), MockEquityDataAdapter.demo()
    ).trace(
        StrategyTraceRequest(
            strategy_source=InlineDraft(spec, "inline_draft", "missing-tie"),
            environment=replace(_environment(), missing=MissingPolicy(missing)),
            as_of=_environment().start,
            security_ids=tuple(f"sec-test-{i}" for i in range(6)),
            factor_id="aux",
        )
    )
    assert response.summary is not None
    assert response.summary.targets[0].position.security_id == first
    assert response.factor_preview.missing_count == (1 if missing == "drop" else 0)
    assert response.target is not None
    assert all(candidate.eligible for candidate in response.target.candidates)
