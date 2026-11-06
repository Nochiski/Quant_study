"""Outgoing port: 개별종목차트가 읽는 종목·지수 목록과 일봉.

어댑터는 원장 행만 나른다. 검색 순서·묶기(주·월)·표시 배율·분석은 하지 않는다(application·
`domain.chart_analysis`). 종목 일봉은 전방 조정 공간(`price_adj_daily`)이고, 지수는 지수값 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar

__all__ = [
    "ChartHistory",
    "ChartInstrument",
    "ChartPricePort",
    "InstrumentKind",
    "InstrumentRef",
]


class InstrumentKind(StrEnum):
    STOCK = "stock"
    INDEX = "index"


@dataclass(frozen=True)
class InstrumentRef:
    """차트 대상의 식별자. 종목은 `code` = 티커, 지수는 `code` = `<index_class>:<index_name>`이다
    (같은 이름의 업종 지수가 코스피·코스닥에 하나씩 있다)."""

    kind: InstrumentKind
    code: str


@dataclass(frozen=True)
class ChartInstrument:
    """목록의 한 줄. `name`은 화면 이름, `aliases`는 검색에만 쓰는 다른 이름(정식 이름 등),
    `group`은 종목이면 증권 종류(`common`·`preferred`·`etf` …), 지수면 시장 구분(`KOSPI` …)이다."""

    ref: InstrumentRef
    name: str
    group: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChartHistory:
    """`end` 이전(포함)의 일봉 오름차순과, 종목이면 마지막 일봉 날의 실제(원주가) 종가.

    `last_actual_close`가 배율 k(= 마지막 실제 종가 ÷ 마지막 전방 조정 종가)의 근거다. 지수는 None
    (k = 1). 일봉이 없으면 `bars`가 비고 `last_actual_close`도 None이다.
    """

    bars: tuple[PriceBar, ...]
    last_actual_close: float | None


class ChartPricePort(Protocol):
    def latest_session(self) -> date | None:
        """원장의 최신 거래일(달력 날짜가 아니다). 진행 중 봉 판정의 기준일이다."""
        ...

    def chart_instruments(self) -> tuple[ChartInstrument, ...]:
        """검색할 수 있는 종목·지수 전부(순서 없음)."""
        ...

    def chart_history(self, instrument: InstrumentRef, end: date) -> ChartHistory | None:
        """그 대상의 일봉. 목록에 없는 대상이면 None.

        종목은 지금 상장일 이후 행만 싣고(같은 티커를 쓰던 옛 종목의 가격을 섞지 않는다), 기준가·
        정지일 행과 확정 전(저녁 잠정) 행, 가격이 빈 행은 싣지 않는다 — 봉이 없는 날이다.
        """
        ...
