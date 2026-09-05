from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import DataWarning


@dataclass(frozen=True)
class BacktestDataQuery:
    start: date
    end: date
    security_ids: tuple[str, ...]
    benchmark_security_id: str | None


@dataclass(frozen=True)
class MarketBarRecord:
    session: date
    security_id: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class UniverseMembershipRecord:
    security_id: str
    first_session: date
    last_session: date


@dataclass(frozen=True)
class CorporateActionRecord:
    session: date
    security_id: str
    action_type: str
    ratio: str
    detail: str


@dataclass(frozen=True)
class BacktestDataset:
    data_snapshot_id: str
    bars: tuple[MarketBarRecord, ...]
    memberships: tuple[UniverseMembershipRecord, ...]
    corporate_actions: tuple[CorporateActionRecord, ...]
    benchmark_security_id: str | None
    warnings: tuple[DataWarning, ...] = ()


class BacktestDataPort(Protocol):
    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset: ...
