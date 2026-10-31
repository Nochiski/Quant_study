"""Outgoing port: S07 일일 위험 보고의 가격 이력과 보고 기록.

가격 이력은 KRX 정본(원장)의 수정주가와 코스피 종가다. 보고는 판단에 쓰지 않으므로 읽지 못하면
보고만 만들지 않는다 — 주문·리스크 판정은 이 포트를 읽지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol

from strategy_workbench.domain.risk.facade.risk import RiskReport

__all__ = ["PriceHistory", "PriceHistorySource", "PriceHistoryStatus", "RiskReportStore"]


class PriceHistoryStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PriceHistory:
    """`closes`: 종목별 수정주가(날짜 → 값, 수준은 원주가와 다를 수 있어 비율로만 쓴다).
    `index`: 코스피 종가(날짜 → 값). `source`: 읽은 원장 빌드(재현용)."""

    status: PriceHistoryStatus
    closes: Mapping[str, Mapping[date, float]] = field(default_factory=dict)
    index: Mapping[date, float] = field(default_factory=dict)
    source: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is PriceHistoryStatus.OK


class PriceHistorySource(Protocol):
    def history(self, symbols: Sequence[str], since: date) -> PriceHistory:
        """`since` 이후 날짜만. 없는 종목은 `closes`에서 빠진다(값을 지어내지 않는다)."""
        ...


class RiskReportStore(Protocol):
    def save_report(self, report: RiskReport) -> None:
        """그날 보고로 갈아 끼운다. 쓰지 못하면 `RiskUnavailableError`."""
        ...

    def report(self, day: date) -> RiskReport | None:
        """없으면 None, 읽지 못하면 `RiskUnavailableError`."""
        ...
