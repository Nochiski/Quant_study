from __future__ import annotations

from backtest_engine import reference_engine_capabilities, validate_requirements
from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    ExecutionStyle,
    ExecutionTiming,
    SetPortfolioTarget,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.orders import TimeInForce
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    StrategyRequirements,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    EngineCapabilityIssue,
    EngineCompatibility,
    EngineRequirementSummary,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.portfolio.facade.construction import TargetFrame
from strategy_workbench.domain.strategy.facade.specification import (
    PortfolioSide,
    StrategySpec,
)


class BacktestEnginePortfolioAdapter:
    """Translate strategy SoT into engine requirements and executable target actions."""

    def requirements(self, spec: StrategySpec, environment: RunEnvironment) -> StrategyRequirements:
        """참여율이 1이어도 주문이 기준 거래량을 넘으면 부분 체결 능력이 필요하다."""
        features: set[EngineFeature] = {EngineFeature.PARTIAL_FILL}
        if spec.portfolio.side is PortfolioSide.LONG_SHORT:
            features.add(EngineFeature.SHORT_SELLING)
        if spec.risk.gross_exposure > 1.0:
            features.add(EngineFeature.MARGIN)
        return StrategyRequirements(
            histories=(),
            schedule=EverySession(),
            events=frozenset((EventKind.MARKET, EventKind.CORPORATE_ACTION)),
            actions=frozenset((ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET)),
            features=frozenset(features),
        )

    def assess(self, spec: StrategySpec, environment: RunEnvironment) -> EngineCompatibility:
        requirements = self.requirements(spec, environment)
        report = validate_requirements(requirements, reference_engine_capabilities())
        return EngineCompatibility(
            compatible=report.ok,
            requirements=EngineRequirementSummary(
                schedule=type(requirements.schedule).__name__,
                actions=tuple(sorted(item.value for item in requirements.actions)),
                events=tuple(sorted(item.value for item in requirements.events)),
                features=tuple(sorted(item.value for item in requirements.features)),
            ),
            issues=tuple(
                EngineCapabilityIssue(
                    category=violation.category.value,
                    name=violation.name,
                    support=violation.support.value,
                    reason=violation.reason,
                )
                for violation in report.violations
            ),
        )

    def to_target_action(
        self,
        frame: TargetFrame,
        *,
        max_participation: float | None = None,
    ) -> SetPortfolioTarget:
        execution = (
            ExecutionPolicy.market_next_open()
            if max_participation is None
            else ExecutionPolicy(
                style=ExecutionStyle.MARKET,
                timing=ExecutionTiming.NEXT_OPEN,
                time_in_force=TimeInForce.DAY,
                max_participation=max_participation,
            )
        )
        return SetPortfolioTarget(
            targets=tuple(
                WeightTarget(
                    instrument=InstrumentId(
                        venue="XKRX",
                        symbol=target.security_id,
                        asset_class=AssetClass.EQUITY,
                        currency="KRW",
                    ),
                    weight=target.weight,
                )
                for target in frame.targets
            ),
            scope=TargetScope.REPLACE,
            execution=execution,
        )
