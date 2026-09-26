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
        """실행에 필요한 엔진 능력. 참여율은 전략 문서가 아니라 실행 설정이 소유한다(1.2).

        틀리는 방향이 한쪽으로 치우쳐 있어서 인자를 받는다: 문서
        `execution.participation_rate = 1.0` 에 명시 `environment.participation_rate = 0.1` 이면
        실제 실행은 부분 체결을 쓰는데 요구 집합에 `PARTIAL_FILL` 이 빠져 능력 게이트가 조용히
        약해진다(반대 조합은 과다 선언이라 무해하다).
        """
        features: set[EngineFeature] = set()
        if spec.portfolio.side is PortfolioSide.LONG_SHORT:
            features.add(EngineFeature.SHORT_SELLING)
        if spec.risk.gross_exposure > 1.0:
            features.add(EngineFeature.MARGIN)
        if environment.participation_rate < 1.0:
            features.add(EngineFeature.PARTIAL_FILL)
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
