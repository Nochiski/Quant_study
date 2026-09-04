from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.portfolio.facade.construction import TargetTape
from strategy_workbench.domain.strategy.facade.specification import StrategySpec


@dataclass(frozen=True)
class EngineRequirementSummary:
    schedule: str
    actions: tuple[str, ...]
    events: tuple[str, ...]
    features: tuple[str, ...]


@dataclass(frozen=True)
class EngineCapabilityIssue:
    category: str
    name: str
    support: str
    reason: str | None


@dataclass(frozen=True)
class EngineCompatibility:
    compatible: bool
    requirements: EngineRequirementSummary
    issues: tuple[EngineCapabilityIssue, ...]


@dataclass(frozen=True)
class PortfolioPreviewRequest:
    spec: StrategySpec


@dataclass(frozen=True)
class PortfolioPreview:
    """The tape a run will consume, plus the caveats the observation source reported.

    `warnings` are the raw observation adapter's own messages, passed through verbatim. They ride
    into `RunManifest.warnings` so a caveat visible in the preview cannot disappear from the run
    that used the same data.
    """

    tape: TargetTape
    engine: EngineCompatibility
    warnings: tuple[str, ...] = ()
