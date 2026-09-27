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
class InvalidBarRecord:
    """원장 행은 거래(`price_kind='trade'`)인데 OHLC가 무효라 bar로 내지 않은 세션(GAP-14).

    거래정지(기준가 행)와 달리 실제로 거래된 날이다. 벤치마크 경고가 둘을 가르는 데 쓴다(이슈 #241).
    """

    session: date
    security_id: str


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
    # 무효 OHLC 행으로 뺀 (세션, 종목). 원천에 그런 행이 없으면 비어 있다.
    invalid_bars: tuple[InvalidBarRecord, ...] = ()


class BacktestDataPort(Protocol):
    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset: ...
