from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.portfolio.facade.construction import PortfolioObservation, TargetTape
from strategy_workbench.domain.signal_evaluation.facade.evaluation import (
    AdjustedCloseObservation,
    AdjustmentGapObservation,
    ListingInterval,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec


@dataclass(frozen=True)
class SnapshotProof:
    root_reference: str
    ledger_snapshot_id: str
    contract_sources_sha256: str
    descriptor_sha256: str


class SnapshotGuardPort(Protocol):
    def verify(self) -> SnapshotProof:
        """S01 전체 무결성과 현재 코드·환경을 검증한다. 실패하면 실행을 중단한다."""
        ...


@dataclass(frozen=True)
class PreparedSignals:
    data_snapshot_id: str
    observations: tuple[PortfolioObservation, ...]
    target_tape: TargetTape
    warnings: tuple[str, ...]


class SignalPipelinePort(Protocol):
    def prepare(self, strategy: StrategySpec, environment: RunEnvironment) -> PreparedSignals: ...


@dataclass(frozen=True)
class LabelDataQuery:
    start: date
    evaluation_as_of: date
    calendar_through: date
    security_ids: tuple[str, ...]


@dataclass(frozen=True)
class ListingEvidence:
    security_id: str
    first_session: date
    last_session: date
    end_reason: str


@dataclass(frozen=True)
class SignalLabelData:
    data_snapshot_id: str
    sessions: tuple[date, ...]
    prices: tuple[AdjustedCloseObservation, ...]
    listings: tuple[ListingInterval, ...]
    listing_evidence: tuple[ListingEvidence, ...]
    warnings: tuple[str, ...]
    adjustment_gaps: tuple[AdjustmentGapObservation, ...] = ()


class LabelDataStatus(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class LabelDataLoad:
    status: LabelDataStatus
    data: SignalLabelData | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, LabelDataStatus):
            raise ValueError(f"unknown label data status — status={self.status!r}")
        if self.status is LabelDataStatus.READY and self.detail is not None:
            raise ValueError("ready label load must not carry a failure reason")
        if (self.status is LabelDataStatus.READY) != (self.data is not None):
            raise ValueError("ready label load requires data; failure must not carry data")
        if self.status is not LabelDataStatus.READY and (
            not self.detail or not self.detail.strip()
        ):
            raise ValueError("failed label load requires a reason")


class SignalLabelDataPort(Protocol):
    def load(self, query: LabelDataQuery) -> LabelDataLoad: ...
