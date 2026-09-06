from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from strategy_workbench.domain.factor.facade.trace import TraceSelection
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioTraceSelection,
    TargetTape,
)
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
class PortfolioStartingHolding:
    security_id: str
    weight: float

    def __post_init__(self) -> None:
        if not self.security_id.strip() or not isfinite(self.weight):
            raise ValueError(
                "starting holding requires a security id and finite weight — "
                f"security_id={self.security_id!r} weight={self.weight!r}"
            )


@dataclass(frozen=True)
class PortfolioPipelineOptions:
    """Optional projections/inputs for the truthful pipeline, never a second calculator.

    `starting_holdings is None` preserves the observation adapter's opening book. An explicit
    tuple, including an empty one, replaces it and treats every omitted security as zero.
    """

    trace_factor_id: str | None = None
    trace_selection: TraceSelection | None = None
    construction_trace_selection: PortfolioTraceSelection | None = None
    starting_holdings: tuple[PortfolioStartingHolding, ...] | None = None
    require_engine_compatible: bool = False

    def __post_init__(self) -> None:
        if (self.trace_factor_id is None) != (self.trace_selection is None):
            raise ValueError("trace factor id and selection must be supplied together")
        if self.starting_holdings is not None:
            ids = [item.security_id for item in self.starting_holdings]
            if len(ids) != len(set(ids)):
                raise ValueError(f"starting holdings require unique security ids — ids={ids!r}")


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
