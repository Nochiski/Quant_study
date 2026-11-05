"""Outgoing port: 홈 시장 블록이 읽는 원장 행.

어댑터는 원장 행만 나르고 기간 합·가중·측정 비중 판정을 하지 않는다(`domain.market`).
표가 빌드되지 않았으면 `MarketTableMissingError`, 표를 읽다 실패하면(판 파일이 지워짐 등)
`MarketDataUnreadableError`를 던진다 — 빈 결과와 "표 없음"·"읽지 못함"을 구분한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from strategy_workbench.domain.market.facade.market import FlowDay, IndexBar, SectorMember

__all__ = [
    "MarketDataPort",
    "MarketDataUnreadableError",
    "MarketTableMissingError",
    "SectorSnapshot",
]


class MarketTableMissingError(Exception):
    def __init__(self, table: str) -> None:
        super().__init__(f"equity table not built — table={table}")
        self.table = table


class MarketDataUnreadableError(Exception):
    def __init__(self, table: str, detail: str) -> None:
        super().__init__(f"equity table could not be read — table={table} detail={detail}")
        self.table = table


@dataclass(frozen=True)
class SectorSnapshot:
    snapshot_date: date
    session: date
    members: tuple[SectorMember, ...]


class MarketDataPort(Protocol):
    def latest_session(self) -> date | None:
        """가격 원장의 최신 장. 행이 없으면 None."""
        ...

    def index_bars(self, index_name: str, end: date, sessions: int) -> tuple[IndexBar, ...]:
        """`end` 이전(포함) 최근 `sessions`개 거래일의 지수 행. 종가가 없는 행은 싣지 않는다."""
        ...

    def flow_days(self, end: date, sessions: int) -> tuple[FlowDay, ...]:
        """`end` 이전(포함) 최근 `sessions`개 거래일의 (세션, 시장)별 측정 셀 합.

        격자는 그날 상장한 코스피·코스닥 보통주(ETF·우선주 제외)다. 한 셀의 원천 행은 백테스트
        필드(`flow.*`)와 같은 규칙으로 하나만 고른다.
        """
        ...

    def sector_members(self, session: date) -> SectorSnapshot | None:
        """`session` 이전(포함) 최신 업종 스냅샷의 종목과 `session`의 가격. 스냅샷이 없으면 None."""
        ...
