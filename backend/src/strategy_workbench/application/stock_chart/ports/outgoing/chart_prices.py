"""Outgoing port: 개별종목차트가 읽는 종목·지수 목록과 일봉.

어댑터는 원장 행만 나른다. 검색 순서·묶기(주·월)·표시 배율·분석은 하지 않는다(application·
`domain.chart_analysis`). 종목 일봉은 전방 조정 공간(`price_adj_daily`)이고, 지수는 지수값 그대로다.

한 포트 인스턴스는 사는 동안 같은 질문에 같은 답을 준다(원장 판본은 부팅 때 고정이고 새 판은 새
컨테이너가 읽는다) — 서비스가 묶은 계열을 캐시한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar
from strategy_workbench.domain.chart_analysis.facade.price_tick import TickTable

__all__ = [
    "PRACTICE_CODE_PREFIX",
    "STOCK_GROUPS",
    "ChartHistory",
    "ChartInstrument",
    "ChartPricePort",
    "InstrumentKind",
    "InstrumentRef",
]


# 연습용(합성) 원천의 대상 코드 접두. 실데이터 티커(숫자·영문 6자)·지수 코드(`<시장>:<지수명>`)와
# 겹치지 않아, 같은 연구 DB에 저장한 연습 선이 실데이터 차트·중첩에 섞이지 않는다. 서비스는 연습
# 모드에서 이 접두가 없는 대상을, 실데이터 모드에서 이 접두가 있는 대상을 모르는 대상으로 본다.
PRACTICE_CODE_PREFIX = "practice:"


class InstrumentKind(StrEnum):
    STOCK = "stock"
    INDEX = "index"


@dataclass(frozen=True)
class InstrumentRef:
    """차트 대상의 식별자. 종목은 `code` = 티커, 지수는 `code` = `<index_class>:<index_name>`이다
    (같은 이름의 업종 지수가 코스피·코스닥에 하나씩 있다)."""

    kind: InstrumentKind
    code: str


# 종목 `ChartInstrument.group`의 어휘(포트 계약) 중 검색 순위를 가진 낱말 — 보통주·우선주·ETF.
# 원천은 자기 증권 종류를 이 낱말로 싣는다. 원장 `security.sec_type`(어휘는
# `database/src/equity/rules_s01.py`의 `SEC_TYPE_VOCAB`)은 같은 낱말이라 duckdb 원천은 그대로
# 싣고, 그 밖의 종류(리츠·외국주·DR 등)도 원장 낱말 그대로다. 차례는 이름 검색의 같은 순위 안
# 순서다(`rank_instruments`).
STOCK_GROUPS: tuple[str, ...] = ("common", "preferred", "etf")


@dataclass(frozen=True)
class ChartInstrument:
    """목록의 한 줄. `name`은 화면 이름, `aliases`는 검색에만 쓰는 다른 이름(정식 이름 등),
    `group`은 종목이면 증권 종류(`STOCK_GROUPS`, 그 밖은 원천 낱말), 지수면 시장 구분(`KOSPI`
    …)이다.
    `tick_table`은 어느 호가표를 쓰는지다 — 원천이 증권 종류를 보고 정하고(상장지수상품이면 FUND),
    None이면 대상 종류의 기본(종목 STOCK, 지수 INDEX)이다. 화면은 이 칸만 읽고 `group` 글자로 호가를
    정하지 않는다."""

    ref: InstrumentRef
    name: str
    group: str | None = None
    aliases: tuple[str, ...] = ()
    tick_table: TickTable | None = None

    @property
    def tick(self) -> TickTable:
        """쓸 호가표. 정하지 않았으면 대상 종류의 기본이다."""
        if self.tick_table is not None:
            return self.tick_table
        return TickTable.INDEX if self.ref.kind is InstrumentKind.INDEX else TickTable.STOCK


@dataclass(frozen=True)
class ChartHistory:
    """`end` 이전(포함)의 일봉 오름차순과, 종목이면 마지막 일봉 날의 실제(원주가) 종가.

    `last_actual_close`가 배율 k(= 마지막 실제 종가 ÷ 마지막 전방 조정 종가)의 근거다. 지수는 None
    (k = 1). 일봉이 없으면 `bars`가 비고 `last_actual_close`도 None이다.
    """

    bars: tuple[PriceBar, ...]
    last_actual_close: float | None


class ChartPricePort(Protocol):
    def confirmed_session(self) -> date | None:
        """원장의 최신 확정 거래일 — 가격이 확정(`basis = 'krx'`)된 마지막 장. 달력 날짜도, 저녁
        잠정 행만 있는 날도 아니다. 진행 중 봉 판정의 기준일이고 종목·지수가 같은 날을 쓴다."""
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
