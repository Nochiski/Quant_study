"""Capability 협상 테스트 (로드맵 2단계: 거절 경로).

미구현 요구는 데이터 루프를 시작하기 전에, 전체 위반 목록과 함께 거절된다.
"""

from __future__ import annotations

import pytest

from backtest_engine.capability import (
    SupportLevel,
    ViolationCategory,
    prepare_strategy,
    reference_engine_capabilities,
    validate_requirements,
)
from backtest_engine.errors import CapabilityNotImplemented
from backtest_engine.types.actions import ActionKind
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.market import PriceField
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    MonthEndSession,
    Schedule,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from tests.conftest import make_instrument


def golden_cross_requirements() -> StrategyRequirements:
    return StrategyRequirements(
        histories=(
            HistoryRequest(instruments=(make_instrument(),), field=PriceField.CLOSE, lookback=60),
        ),
        schedule=EverySession(),
        events=frozenset({EventKind.MARKET}),
        actions=frozenset(
            {ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET, ActionKind.LIQUIDATE_POSITION}
        ),
        features=frozenset(),
    )


def pairs_requirements() -> StrategyRequirements:
    """설계 노트의 페어 전략 요구: BASKET + 공매도 + 비례 체결."""
    return StrategyRequirements(
        histories=(
            HistoryRequest(
                instruments=(make_instrument("A"), make_instrument("B")),
                field=PriceField.CLOSE,
                lookback=120,
            ),
        ),
        schedule=EverySession(),
        events=frozenset({EventKind.MARKET}),
        actions=frozenset({ActionKind.BASKET, ActionKind.SET_POSITION_TARGET}),
        features=frozenset({EngineFeature.SHORT_SELLING, EngineFeature.PROPORTIONAL_BASKET}),
    )


class _RequirementsOnlyStrategy:
    """on_event가 호출되면 안 되는 상황을 검증하기 위한 테스트 전략."""

    def __init__(self, requirements: StrategyRequirements) -> None:
        self._requirements = requirements
        self.on_event_called = False

    def requirements(self) -> StrategyRequirements:
        return self._requirements

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        self.on_event_called = True
        return StrategyDecision.no_action(ctx.now)


def test_golden_cross_requirements_pass() -> None:
    report = validate_requirements(golden_cross_requirements(), reference_engine_capabilities())
    assert report.ok
    assert report.violations == ()


def test_pairs_requirements_collect_all_violations() -> None:
    report = validate_requirements(pairs_requirements(), reference_engine_capabilities())
    assert not report.ok
    names = {(violation.category, violation.name) for violation in report.violations}
    assert (ViolationCategory.ACTION, "basket") in names
    assert (ViolationCategory.FEATURE, "proportional_basket") in names
    # SET_POSITION_TARGET(4a)·SHORT_SELLING(5a)은 승격됐으므로 위반이 아니다.
    assert (ViolationCategory.ACTION, "set_position_target") not in names
    assert (ViolationCategory.FEATURE, "short_selling") not in names
    # 첫 위반에서 멈추지 않고 전부 수집한다.
    assert len(report.violations) == 2


def test_month_end_schedule_not_implemented() -> None:
    requirements = StrategyRequirements(
        histories=golden_cross_requirements().histories,
        schedule=MonthEndSession(),
        events=frozenset({EventKind.MARKET}),
        actions=frozenset({ActionKind.NO_ACTION}),
        features=frozenset(),
    )
    report = validate_requirements(requirements, reference_engine_capabilities())
    assert [violation.category for violation in report.violations] == [ViolationCategory.SCHEDULE]


def test_prepare_strategy_rejects_before_any_event() -> None:
    strategy = _RequirementsOnlyStrategy(pairs_requirements())
    with pytest.raises(CapabilityNotImplemented) as excinfo:
        prepare_strategy(strategy, reference_engine_capabilities())
    message = str(excinfo.value)
    assert "basket" in message
    assert "proportional_basket" in message
    assert not strategy.on_event_called


def test_prepare_strategy_passes_implemented_requirements() -> None:
    strategy = _RequirementsOnlyStrategy(golden_cross_requirements())
    validated = prepare_strategy(strategy, reference_engine_capabilities())
    assert validated.requirements == golden_cross_requirements()


def test_reference_capabilities_are_honest() -> None:
    """IMPLEMENTED는 handler·테스트가 있는 범위뿐이어야 한다 (DEFINED ≠ IMPLEMENTED)."""
    capabilities = reference_engine_capabilities()
    implemented = {
        capability.kind
        for capability in capabilities.actions
        if capability.support is SupportLevel.IMPLEMENTED
    }
    assert implemented == {
        ActionKind.NO_ACTION,
        ActionKind.SET_PORTFOLIO_TARGET,
        ActionKind.LIQUIDATE_POSITION,
        ActionKind.SET_POSITION_TARGET,
        ActionKind.ADJUST_POSITION,
        ActionKind.SUBMIT_ORDER,
        ActionKind.CANCEL_ORDER,
        ActionKind.REPLACE_ORDER,
    }
    implemented_events = {
        capability.kind
        for capability in capabilities.events
        if capability.support is SupportLevel.IMPLEMENTED
    }
    assert implemented_events == {
        EventKind.MARKET,
        EventKind.FILL,
        EventKind.ORDER_UPDATE,
        EventKind.CORPORATE_ACTION,
    }
    implemented_features = {
        capability.feature
        for capability in capabilities.features
        if capability.support is SupportLevel.IMPLEMENTED
    }
    assert implemented_features == {
        EngineFeature.LIMIT_ORDER,
        EngineFeature.STOP_ORDER,
        EngineFeature.PARTIAL_FILL,
        EngineFeature.SHORT_SELLING,
    }


def test_every_action_kind_has_registered_capability() -> None:
    capabilities = reference_engine_capabilities()
    registered = {capability.kind for capability in capabilities.actions}
    assert registered == set(ActionKind)


def test_schedule_support_lookup() -> None:
    capabilities = reference_engine_capabilities()
    every: Schedule = EverySession()
    month_end: Schedule = MonthEndSession()
    assert capabilities.schedule_support(every).support is SupportLevel.IMPLEMENTED
    assert capabilities.schedule_support(month_end).support is SupportLevel.NOT_IMPLEMENTED
