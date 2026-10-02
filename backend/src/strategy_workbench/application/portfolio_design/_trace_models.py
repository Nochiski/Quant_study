"""Commands and immutable projections for the scoped strategy trace API (WORKFLOW P5-01)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, TypeAlias

from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.equity.facade.research_data import CellKind, SecurityRef
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.factor.facade.trace import TraceValueStatus
from strategy_workbench.domain.portfolio.facade.construction import (
    CandidateDecision,
    PortfolioCandidateTrace,
    PortfolioFrameSummary,
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
    # 비우면 종목별 추적 없이 기준일 요약(`summary`)만 받는다(lang2 P4-03).
    security_ids: tuple[str, ...]
    factor_id: str
    # None asks the compiler-owned rebalance schedule for its latest executable signal frame.
    as_of: date | None = None
    node_ids: tuple[str, ...] = ()
    include_raw: bool = False
    starting_holdings: tuple[PortfolioStartingHolding, ...] | None = None
    offset: int = 0
    limit: int = 200
    # preview 와 같은 optional 실행 설정(P2-01). `as_of` 범위 판정과 관측 조회가 이 값을 읽는다.
    environment: RunEnvironment | None = None

    MAX_SECURITY_IDS: ClassVar[int] = 100
    MAX_NODE_IDS: ClassVar[int] = 100
    MAX_LIMIT: ClassVar[int] = 500
    MAX_SCAN_ROWS: ClassVar[int] = 10_000

    def __post_init__(self) -> None:
        if not self.factor_id.strip():
            raise ValueError("trace factor_id must not be blank")
        if len(self.security_ids) > self.MAX_SECURITY_IDS:
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
    kind: CellKind


@dataclass(frozen=True)
class StrategyTargetTrace:
    signal_as_of: date
    execution_on: date
    targets: tuple[TargetPosition, ...]
    candidates: tuple[CandidateDecision, ...]
    construction: tuple[PortfolioCandidateTrace, ...]


@dataclass(frozen=True)
class StrategyTraceSummaryTarget:
    """선정 종목 한 줄. 이름·티커는 실행 설정의 유니버스에서 찾고, 모르면 `security` 가 None."""

    position: TargetPosition
    security: SecurityRef | None


@dataclass(frozen=True)
class StrategyTraceSummary:
    """기준일 미리보기가 그리는 수와 그날의 선정(lang2 P4-03). 수는 컴파일러가 센 그대로다."""

    signal_as_of: date
    execution_on: date
    counts: PortfolioFrameSummary
    # N = 전략이 그 기준일에 고른 종목 전부, 순위 순
    targets: tuple[StrategyTraceSummaryTarget, ...]


@dataclass(frozen=True)
class StrategyFactorPreviewRow:
    security_id: str
    value: float
    security: SecurityRef | None


@dataclass(frozen=True)
class StrategyFactorPreview:
    valid_count: int
    missing_count: int
    missing: MissingPolicy
    top: tuple[StrategyFactorPreviewRow, ...]


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
    factor_preview: StrategyFactorPreview
    # `target` 처럼 기준일이 리밸런스 신호일일 때만 있다.
    summary: StrategyTraceSummary | None = None
    warnings: tuple[str, ...] = ()
