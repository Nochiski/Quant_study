"""Capability 협상.

타입을 문서에 정의해 두었다고 해서 엔진 구현까지 끝난 것은 아니다.
전략이 요구한 기능을 현재 엔진이 실제로 처리할 수 있는지
백테스트를 시작하기 전에 확인하고, 미구현 요구는 데이터 루프 전에
구체적인 오류로 거절한다. DEFINED ≠ IMPLEMENTED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backtest_engine.errors import CapabilityNotImplemented
from backtest_engine.types.actions import ActionKind
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    Schedule,
    StrategyRequirements,
)
from backtest_engine.types.strategy import Strategy


class SupportLevel(Enum):
    IMPLEMENTED = "implemented"  # 실제 코드와 테스트가 있어 실행할 수 있다.
    NOT_IMPLEMENTED = "not_implemented"  # 스키마 의미는 확정됐지만 처리 코드가 없다.
    UNSUPPORTED = "unsupported"  # 현재 데이터 해상도·체결 모델로 의미 있는 시뮬레이션 불가.


@dataclass(frozen=True)
class ActionCapability:
    kind: ActionKind
    support: SupportLevel
    reason: str | None = None


@dataclass(frozen=True)
class FeatureCapability:
    feature: EngineFeature
    support: SupportLevel
    reason: str | None = None


@dataclass(frozen=True)
class EventCapability:
    """엔진이 해당 종류의 이벤트를 전략에 전달할 수 있는지."""

    kind: EventKind
    support: SupportLevel
    reason: str | None = None


@dataclass(frozen=True)
class ScheduleCapability:
    """schedule 타입 이름 기준 지원 상태 (예: "EverySession")."""

    schedule_type: str
    support: SupportLevel
    reason: str | None = None


@dataclass(frozen=True)
class EngineCapabilities:
    schema_version: int
    actions: tuple[ActionCapability, ...]
    features: tuple[FeatureCapability, ...]
    events: tuple[EventCapability, ...]
    schedules: tuple[ScheduleCapability, ...]

    def action_support(self, kind: ActionKind) -> ActionCapability:
        for capability in self.actions:
            if capability.kind is kind:
                return capability
        return ActionCapability(kind, SupportLevel.NOT_IMPLEMENTED, "not registered")

    def feature_support(self, feature: EngineFeature) -> FeatureCapability:
        for capability in self.features:
            if capability.feature is feature:
                return capability
        return FeatureCapability(feature, SupportLevel.NOT_IMPLEMENTED, "not registered")

    def event_support(self, kind: EventKind) -> EventCapability:
        for capability in self.events:
            if capability.kind is kind:
                return capability
        return EventCapability(kind, SupportLevel.NOT_IMPLEMENTED, "not registered")

    def schedule_support(self, schedule: Schedule) -> ScheduleCapability:
        name = type(schedule).__name__
        for capability in self.schedules:
            if capability.schedule_type == name:
                return capability
        return ScheduleCapability(name, SupportLevel.NOT_IMPLEMENTED, "not registered")


class ViolationCategory(Enum):
    ACTION = "action"
    FEATURE = "feature"
    EVENT = "event"
    SCHEDULE = "schedule"


@dataclass(frozen=True)
class CapabilityViolation:
    category: ViolationCategory
    name: str
    support: SupportLevel
    reason: str | None

    def describe(self) -> str:
        reason = f" ({self.reason})" if self.reason else ""
        return f"{self.category.value}={self.name} support={self.support.value}{reason}"


@dataclass(frozen=True)
class CapabilityReport:
    """요구사항 검증 결과. 첫 위반에서 멈추지 않고 전체 위반을 모아서 보고한다."""

    violations: tuple[CapabilityViolation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


def validate_requirements(
    requirements: StrategyRequirements,
    capabilities: EngineCapabilities,
) -> CapabilityReport:
    """전략 요구사항 전체를 검사해 모든 위반을 수집한다."""
    violations: list[CapabilityViolation] = []

    for action_kind in sorted(requirements.actions, key=lambda kind: kind.value):
        capability = capabilities.action_support(action_kind)
        if capability.support is not SupportLevel.IMPLEMENTED:
            violations.append(
                CapabilityViolation(
                    ViolationCategory.ACTION,
                    action_kind.value,
                    capability.support,
                    capability.reason,
                )
            )

    for feature in sorted(requirements.features, key=lambda item: item.value):
        capability = capabilities.feature_support(feature)
        if capability.support is not SupportLevel.IMPLEMENTED:
            violations.append(
                CapabilityViolation(
                    ViolationCategory.FEATURE, feature.value, capability.support, capability.reason
                )
            )

    for event_kind in sorted(requirements.events, key=lambda kind: kind.value):
        capability = capabilities.event_support(event_kind)
        if capability.support is not SupportLevel.IMPLEMENTED:
            violations.append(
                CapabilityViolation(
                    ViolationCategory.EVENT, event_kind.value, capability.support, capability.reason
                )
            )

    schedule_capability = capabilities.schedule_support(requirements.schedule)
    if schedule_capability.support is not SupportLevel.IMPLEMENTED:
        violations.append(
            CapabilityViolation(
                ViolationCategory.SCHEDULE,
                schedule_capability.schedule_type,
                schedule_capability.support,
                schedule_capability.reason,
            )
        )

    return CapabilityReport(violations=tuple(violations))


@dataclass(frozen=True)
class ValidatedStrategy:
    """모든 요구가 통과한 전략만 Context를 받을 수 있다."""

    strategy: Strategy
    requirements: StrategyRequirements


def prepare_strategy(strategy: Strategy, capabilities: EngineCapabilities) -> ValidatedStrategy:
    """전략 등록 직후, 데이터 루프보다 먼저 요구사항을 한 번만 읽고 검증한다."""
    requirements = strategy.requirements()
    report = validate_requirements(requirements, capabilities)
    if not report.ok:
        details = "; ".join(violation.describe() for violation in report.violations)
        raise CapabilityNotImplemented(
            f"strategy requirements exceed engine capabilities — "
            f"{len(report.violations)} violation(s): {details}"
        )
    return ValidatedStrategy(strategy=strategy, requirements=requirements)


def reference_engine_capabilities() -> EngineCapabilities:
    """Python reference engine v1의 정직한 구현 상태.

    3단계의 NO_ACTION, SET_PORTFOLIO_TARGET, LIQUIDATE_POSITION, 4a의
    SET_POSITION_TARGET, ADJUST_POSITION, 4b의 SUBMIT_ORDER(LIMIT/STOP 기능 포함),
    4c의 CANCEL_ORDER, REPLACE_ORDER와 FILL/ORDER_UPDATE 이벤트 전달, 4d의
    PARTIAL_FILL(참여율 캡·IOC/FOK), MARKET 이벤트, EverySession 일정만 IMPLEMENTED다.
    나머지는 스키마만 정의된 NOT_IMPLEMENTED 상태로, handler와 테스트가 추가될 때 승격한다.
    """
    not_implemented_actions = {
        ActionKind.BASKET: "roadmap step 5",
    }
    implemented_actions = (
        ActionKind.NO_ACTION,
        ActionKind.SET_PORTFOLIO_TARGET,
        ActionKind.SET_POSITION_TARGET,
        ActionKind.ADJUST_POSITION,
        ActionKind.LIQUIDATE_POSITION,
        ActionKind.SUBMIT_ORDER,
        ActionKind.CANCEL_ORDER,
        ActionKind.REPLACE_ORDER,
    )
    actions = tuple(
        ActionCapability(kind, SupportLevel.IMPLEMENTED) for kind in implemented_actions
    ) + tuple(
        ActionCapability(kind, SupportLevel.NOT_IMPLEMENTED, reason)
        for kind, reason in not_implemented_actions.items()
    )
    features = (
        FeatureCapability(EngineFeature.LIMIT_ORDER, SupportLevel.IMPLEMENTED),
        FeatureCapability(EngineFeature.STOP_ORDER, SupportLevel.IMPLEMENTED),
        FeatureCapability(EngineFeature.PARTIAL_FILL, SupportLevel.IMPLEMENTED),
    ) + tuple(
        FeatureCapability(feature, SupportLevel.NOT_IMPLEMENTED, reason)
        for feature, reason in {
            EngineFeature.SHORT_SELLING: "roadmap step 5",
            EngineFeature.MARGIN: "roadmap step 5 — no margin accounting",
            EngineFeature.PROPORTIONAL_BASKET: "roadmap step 5",
        }.items()
    )
    events = (
        EventCapability(EventKind.MARKET, SupportLevel.IMPLEMENTED),
        EventCapability(EventKind.TIMER, SupportLevel.NOT_IMPLEMENTED, "no timer scheduler in v1"),
        EventCapability(EventKind.FILL, SupportLevel.IMPLEMENTED),
        EventCapability(EventKind.ORDER_UPDATE, SupportLevel.IMPLEMENTED),
        EventCapability(
            EventKind.CORPORATE_ACTION,
            SupportLevel.UNSUPPORTED,
            "daily OHLCV feed has no corporate action data",
        ),
    )
    schedules = (
        ScheduleCapability(type(EverySession()).__name__, SupportLevel.IMPLEMENTED),
        ScheduleCapability(
            "MonthEndSession", SupportLevel.NOT_IMPLEMENTED, "calendar rule not built"
        ),
    )
    return EngineCapabilities(
        schema_version=1,
        actions=actions,
        features=features,
        events=events,
        schedules=schedules,
    )
