from __future__ import annotations

from dataclasses import replace
from datetime import date

from backtest_engine.types.actions import ExecutionTiming, TargetScope, WeightTarget
from backtest_engine.types.requirements import EngineFeature
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.portfolio.facade.construction import (
    CandidateSide,
    TargetFrame,
    TargetPosition,
)
from strategy_workbench.domain.strategy.facade.specification import PortfolioSide


def _spec():
    return StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "unused",
        today=lambda: date(2026, 9, 3),
    ).template()


def test_requirements_are_negotiated_before_engine_execution() -> None:
    adapter = BacktestEnginePortfolioAdapter()
    spec = _spec()
    leveraged_long_short = replace(
        spec,
        portfolio=replace(spec.portfolio, side=PortfolioSide.LONG_SHORT),
        risk=replace(spec.risk, gross_exposure=1.5, net_exposure=0.0),
    )

    requirements = adapter.requirements(leveraged_long_short)
    compatibility = adapter.assess(leveraged_long_short)

    assert requirements.features == frozenset((EngineFeature.SHORT_SELLING, EngineFeature.MARGIN))
    assert compatibility.compatible
    assert compatibility.issues == ()
    assert compatibility.requirements.schedule == "EverySession"
    assert compatibility.requirements.actions == ("set_portfolio_target",)


def test_target_frame_maps_to_replace_action_at_next_open() -> None:
    frame = TargetFrame(
        signal_as_of=date(2026, 1, 2),
        execution_on=date(2026, 1, 5),
        targets=(
            TargetPosition(
                security_id="005930",
                weight=0.4,
                composite_score=1.5,
                rank=1,
                side=CandidateSide.LONG,
            ),
        ),
        candidates=(),
    )

    action = BacktestEnginePortfolioAdapter().to_target_action(
        frame,
        max_participation=0.07,
    )

    assert action.scope is TargetScope.REPLACE
    assert action.execution.timing is ExecutionTiming.NEXT_OPEN
    assert action.execution.max_participation == 0.07
    assert action.targets == (WeightTarget(action.targets[0].instrument, 0.4),)
    assert action.targets[0].instrument.symbol == "005930"
