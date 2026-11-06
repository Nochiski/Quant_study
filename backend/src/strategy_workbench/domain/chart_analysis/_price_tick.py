"""화면 가격 한 호가(가격 구간별 최소 가격 단위)의 owner.

개별종목차트가 키보드로 선을 옮길 때(↑/↓ 한 호가, Shift 1% 뒤 호가 맞춤) 이 표를 쓴다. 표는 저장 선
목록·캔들 응답의 대상 머리(`ChartInstrumentBody.tick_steps`)로 나가고 frontend는 읽기만 한다. 분석
규칙이 아니라 표시·입력 단위라 `ALGORITHM_VERSION`과 무관하다.

- 주식(유가증권·코스닥, 2023-01-25 호가가격단위 개편 이후): 2,000원 미만 1원, 5,000원 미만 5원,
  20,000원 미만 10원, 50,000원 미만 50원, 200,000원 미만 100원, 500,000원 미만 500원, 그 위 1,000원.
- 상장지수상품(ETF·ETN): 2,000원 미만 1원, 그 위 5원.
- 지수: 0.01.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TickTable(Enum):
    """어느 호가표를 쓰는가. 원천(어댑터)이 증권 종류를 보고 정한다."""

    STOCK = "stock"
    FUND = "fund"
    INDEX = "index"


@dataclass(frozen=True)
class TickStep:
    """`below` 미만 가격의 한 호가. `below`가 None이면 그 위 전부다(표의 마지막 칸)."""

    below: float | None
    tick: float


TICK_STEPS: dict[TickTable, tuple[TickStep, ...]] = {
    TickTable.STOCK: (
        TickStep(2_000, 1),
        TickStep(5_000, 5),
        TickStep(20_000, 10),
        TickStep(50_000, 50),
        TickStep(200_000, 100),
        TickStep(500_000, 500),
        TickStep(None, 1_000),
    ),
    TickTable.FUND: (TickStep(2_000, 1), TickStep(None, 5)),
    TickTable.INDEX: (TickStep(None, 0.01),),
}
