"""Commands and immutable projections for the scoped strategy trace API (WORKFLOW P5-01)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, TypeAlias

from strategy_workbench.domain.factor.facade.trace import TraceValueStatus
from strategy_workbench.domain.portfolio.facade.construction import (
    CandidateDecision,
    TargetPosition,
)
from strategy_workbench.domain.strategy.facade.provenance import (
    StrategyProvenance,
    StrategySource,
)

from ._models import PortfolioStartingHolding

TraceComputedValue: TypeAlias = float | bool | None


@dataclass(frozen=True)
class StrategyTraceRequest:
    strategy_source: StrategySource
    as_of: date
    security_ids: tuple[str, ...]
    factor_id: str
    node_ids: tuple[str, ...] = ()
    include_raw: bool = False
    starting_holdings: tuple[PortfolioStartingHolding, ...] | None = None
    offset: int = 0
    limit: int = 200

    MAX_SECURITY_IDS: ClassVar[int] = 100
    MAX_NODE_IDS: ClassVar[int] = 100
    MAX_LIMIT: ClassVar[int] = 500
    MAX_SCAN_ROWS: ClassVar[int] = 10_000

    def __post_init__(self) -> None:
        if not self.factor_id.strip():
            raise ValueError("trace factor_id must not be blank")
        if not 1 <= len(self.security_ids) <= self.MAX_SECURITY_IDS:
            raise ValueError(
                "trace security_ids count is out of range — "
                f"count={len(self.security_ids)} max={self.MAX_SECURITY_IDS}"
            )
        if any(not item.strip() for item in self.security_ids):
            raise ValueError("trace security_ids must not contain blank ids")
        if len(self.security_ids) != len(set(self.security_ids)):
            raise ValueError("trace security_ids must be unique")
        if len(self.node_ids) > self.MAX_NODE_IDS:
            raise ValueError(
                f"trace node_ids exceed cap — count={len(self.node_ids)} max={self.MAX_NODE_IDS}"
            )
        if any(not item.strip() for item in self.node_ids):
            raise ValueError("trace node_ids must not contain blank ids")
        if len(self.node_ids) != len(set(self.node_ids)):
            raise ValueError("trace node_ids must be unique")
        if self.offset < 0 or not 1 <= self.limit <= self.MAX_LIMIT:
            raise ValueError(
                "trace page is out of range — "
                f"offset={self.offset} limit={self.limit} max_limit={self.MAX_LIMIT}"
            )
        if self.offset + self.limit > self.MAX_SCAN_ROWS:
            raise ValueError(
                "trace page exceeds bounded scan window — "
                f"offset={self.offset} limit={self.limit} max={self.MAX_SCAN_ROWS}"
            )
        if self.starting_holdings is not None:
            ids = [item.security_id for item in self.starting_holdings]
            if len(ids) > self.MAX_SECURITY_IDS or len(ids) != len(set(ids)):
                raise ValueError(
                    "trace starting_holdings must be unique and within the security cap — "
                    f"count={len(ids)} max={self.MAX_SECURITY_IDS}"
                )


@dataclass(frozen=True)
class StrategyTraceInput:
    node_id: str
    value: TraceComputedValue


@dataclass(frozen=True)
class StrategyTraceRow:
    node_id: str
    operation: str
    as_of: date
    security_id: str
    value: TraceComputedValue
    status: TraceValueStatus
    inputs: tuple[StrategyTraceInput, ...]


@dataclass(frozen=True)
class StrategyTracePage:
    rows: tuple[StrategyTraceRow, ...]
    offset: int
    limit: int
    returned: int
    has_more: bool


@dataclass(frozen=True)
class RawStrategyTraceRow:
    as_of: date
    security_id: str
    field_id: str
    value: float | str | bool | None
    available_date: date


@dataclass(frozen=True)
class StrategyTargetTrace:
    signal_as_of: date
    execution_on: date
    targets: tuple[TargetPosition, ...]
    candidates: tuple[CandidateDecision, ...]


@dataclass(frozen=True)
class StrategyTraceResponse:
    spec_hash: str
    snapshot_id: str
    registry_version: str
    plan_hash: str
    provenance: StrategyProvenance
    factor_id: str
    as_of: date
    trace: StrategyTracePage
    raw: tuple[RawStrategyTraceRow, ...]
    raw_truncated: bool
    target: StrategyTargetTrace | None
    warnings: tuple[str, ...] = ()
