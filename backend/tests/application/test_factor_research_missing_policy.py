"""은퇴한 sandbox 대신 실행 파이프라인에서 계획과 평가의 결측 정책 일치를 검증한다."""

from dataclasses import replace
from datetime import date

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.application.portfolio_design.facade.design import PortfolioPreviewRequest
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.domain.backtest.facade.environment import DEFAULT_MISSING_POLICY
from strategy_workbench.domain.factor.facade.expression import FactorGraph, FieldNode, MissingPolicy
from strategy_workbench.domain.strategy.facade.specification import FactorDirection, FactorSignal
from tests.integration.test_truthful_pipeline import _environment, _service, _spec


class _SparseSource:
    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        raw = MockEquityDataAdapter.demo().load_raw_observations(query)
        return replace(
            raw,
            observations=tuple(
                replace(row, fields=() if row.security_id == "sec-000660-1" else row.fields)
                for row in raw.observations
            ),
        )


def _preview(missing: MissingPolicy | None) -> tuple[str, float | None]:
    factor = FactorSignal(
        factor_id="close",
        label="종가",
        direction=FactorDirection.HIGH,
        graph=FactorGraph(
            nodes=(FieldNode("close", "price.close", "field"),), output_node_id="close"
        ),
    )
    environment = _environment()
    if missing is not None:
        environment = replace(environment, missing=missing)
    result = _service(_SparseSource()).run_pipeline(
        PortfolioPreviewRequest(_spec(factor), environment=environment)
    )
    record = result.factor_evaluations[0]
    value = next(
        v.value
        for v in record.values
        if v.security_id == "sec-000660-1" and v.as_of == date(2024, 1, 8)
    )
    return record.plan.missing_policy, value


def test_preview_without_an_explicit_missing_uses_the_run_default() -> None:
    assert _preview(None) == (DEFAULT_MISSING_POLICY.value, None)


def test_preview_plan_and_values_read_the_same_explicit_missing() -> None:
    assert _preview(MissingPolicy.DROP) == ("drop", None)
    assert _preview(MissingPolicy.ZERO) == ("zero", 0.0)
