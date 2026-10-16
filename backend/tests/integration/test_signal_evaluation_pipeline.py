"""실제 팩터 평가·선정 경로의 값을 미래 평가에 연결하되 결과를 선정 입력으로 돌려보내지 않는다."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TypedDict

import pytest

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawFieldValue,
    RawObservation,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.factor.facade.expression import FactorGraph, FieldNode
from strategy_workbench.domain.portfolio.facade.construction import PortfolioObservation
from strategy_workbench.domain.signal_evaluation.facade.evaluation import (
    AdjustedCloseObservation,
    ListingInterval,
    PriceLabelSpec,
    RankICStatus,
    evaluate_signal,
)
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorSignal,
    RebalanceFrequency,
)

D0, D1 = date(2024, 1, 8), date(2024, 1, 9)


class _EvaluationInputs(TypedDict):
    observations: tuple[PortfolioObservation, ...]
    factor_id: str
    signal_snapshot_id: str
    price_snapshot_id: str
    sessions: tuple[date, ...]
    prices: tuple[AdjustedCloseObservation, ...]
    listings: tuple[ListingInterval, ...]
    label_spec: PriceLabelSpec
    evaluation_as_of: date


class _SyntheticSource:
    def __init__(self, snapshot_id: str) -> None:
        self.snapshot_id = snapshot_id

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        assert query.field_ids == ("price.close",)
        return RawObservationSet(
            status=DataLoadStatus.OK,
            data_snapshot_id=self.snapshot_id,
            sessions=(D0, D1),
            history_sessions=(),
            observations=tuple(
                RawObservation(
                    session,
                    security_id,
                    True,
                    (RawFieldValue("price.close", close, session),),
                    "synthetic-sector",
                )
                for session, closes in ((D0, (10.0, 20.0, 30.0)), (D1, (11.0, 24.0, 39.0)))
                for security_id, close in zip(("a", "b", "c"), closes, strict=True)
            ),
        )


def test_actual_factor_pipeline_values_feed_rank_ic_without_future_feedback() -> None:
    metadata = MockEquityDataAdapter.demo()
    source = _SyntheticSource(metadata.snapshot().snapshot_id)
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    spec = replace(
        template,
        factors=(
            FactorSignal(
                factor_id="synthetic-close",
                label="합성 종가",
                direction=FactorDirection.HIGH,
                weight=1.0,
                graph=FactorGraph((FieldNode("close", "price.close", "field"),), "close"),
            ),
        ),
        portfolio=replace(
            template.portfolio,
            selection_count=1,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
        risk=replace(template.risk, max_name_weight=1.0, max_sector_weight=1.0),
    )
    service = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=metadata,
        factor_registry_version="synthetic",
    )
    request = PortfolioPreviewRequest(
        spec, environment=RunEnvironment(start=D0, end=D1, universe_id="krx.common-stock")
    )
    pipeline = service.run_pipeline(request)
    first_signals = tuple(row for row in pipeline.observations if row.as_of == D0)
    assert [row.factor_values[0].value for row in first_signals] == [10.0, 20.0, 30.0]
    assert [target.security_id for target in pipeline.preview.tape.frames[0].targets] == ["c"]
    original_tape = pipeline.preview.tape
    price_rows: list[AdjustedCloseObservation] = []
    for row in pipeline.raw_observations:
        value = row.fields[0].value
        assert isinstance(value, float)
        price_rows.append(AdjustedCloseObservation(row.as_of, row.security_id, value, row.as_of))
    prices = tuple(price_rows)
    inputs = _EvaluationInputs(
        observations=first_signals,
        factor_id="synthetic-close",
        signal_snapshot_id=pipeline.data_snapshot_id,
        price_snapshot_id=pipeline.data_snapshot_id,
        sessions=(D0, D1),
        prices=prices,
        listings=tuple(ListingInterval(security_id, D0, D1) for security_id in ("a", "b", "c")),
        label_spec=PriceLabelSpec((1,)),
        evaluation_as_of=D1,
    )
    result = evaluate_signal(**inputs)
    assert [sample.signal_value for sample in result.samples] == [10.0, 20.0, 30.0]
    assert [sample.label.return_fraction for sample in result.samples] == pytest.approx(
        [0.1, 0.2, 0.3]
    )
    assert result.daily[0].rank_ic == pytest.approx(1.0)
    assert result.daily[0].status is RankICStatus.OK
    # 미래 결과만 뒤집어도 이미 만든 매매계획과 다시 계산한 신호·선정은 같다.
    reverse_prices = tuple(
        replace(price, close={"a": 13.0, "b": 24.0, "c": 33.0}[price.security_id])
        if price.session == D1
        else price
        for price in prices
    )
    inputs["prices"] = reverse_prices
    reversed_result = evaluate_signal(**inputs)
    assert reversed_result.daily[0].rank_ic == pytest.approx(-1.0)
    assert service.run_pipeline(request).preview.tape == original_tape
    assert pipeline.preview.tape == original_tape
