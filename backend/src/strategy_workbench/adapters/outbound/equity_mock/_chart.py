"""연습용(mock) 원천의 개별종목차트 행(`ChartPricePort`) — 결정적으로 만든 합성 일봉.

실제 시세가 아니다. 화면 흐름(e2e)을 돌리려고 지수 하나·종목 둘의 평일 일봉을 고정 seed로 만든다.
국면이 바뀌는 구간(상승·박스·하락)을 이어 붙여 자동 찾기 후보가 나오게 했고, 응답의 연습용 표시는
application이 붙인다(bootstrap이 이 원천에 `practice_data=True`를 준다). 원천 판·기준일이 고정이라
같은 요청은 늘 같은 봉을 돌려준다.

- 대상 코드는 모두 `PRACTICE_CODE_PREFIX`로 시작한다 — 같은 연구 DB에 저장한 연습 선이 실데이터
  종목(`005930`)의 선과 섞이지 않는다.
- 기준일(원장 최신 확정 거래일)은 목요일 `MOCK_CHART_AS_OF`라 주봉의 마지막 주가 진행 중이다.
- 삼성전자 계획은 `split` 세션부터 원주가가 1/5로 떨어지는 분할을 흉내 낸다. 일봉은 전방 조정
  공간(첫 관측 수준 고정)이라 이어지고, 마지막 실제 종가는 조정 종가 ÷ 5라 표시 배율 k = 0.2다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta

from strategy_workbench.application.stock_chart.facade.ports import (
    PRACTICE_CODE_PREFIX,
    ChartHistory,
    ChartInstrument,
    InstrumentKind,
    InstrumentRef,
)
from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar

MOCK_CHART_START = date(2023, 1, 2)
MOCK_CHART_AS_OF = date(2025, 10, 30)


@dataclass(frozen=True)
class _Plan:
    """합성 계열 하나: 시작가와 (세션 수, 국면) 구간들. 국면은 일평균 로그수익률(drift)이고 None이면
    시작 수준으로 되돌아가는 박스다. 마지막 구간은 남은 세션 전부다. `split`은 (분할 세션 번호,
    비율) — 그 세션부터 실제 종가가 조정 종가 ÷ 비율이다."""

    instrument: ChartInstrument
    seed: int
    start_price: float
    legs: tuple[tuple[int, float | None], ...]
    split: tuple[int, float] | None = None


def _ref(kind: InstrumentKind, code: str) -> InstrumentRef:
    return InstrumentRef(kind, f"{PRACTICE_CODE_PREFIX}{code}")


_PLANS = (
    _Plan(
        ChartInstrument(_ref(InstrumentKind.INDEX, "KOSPI:코스피"), "코스피", "KOSPI"),
        seed=20230102,
        start_price=2_300.0,
        legs=((200, 0.0012), (250, None), (150, -0.0015), (0, None)),
    ),
    _Plan(
        ChartInstrument(
            _ref(InstrumentKind.STOCK, "005930"), "삼성전자", "common", ("삼성전자보통주",)
        ),
        seed=5930,
        start_price=12_000.0,
        legs=((200, None), (180, -0.0018), (0, 0.0020)),
        split=(450, 5.0),
    ),
    _Plan(
        ChartInstrument(_ref(InstrumentKind.STOCK, "000660"), "SK하이닉스", "common"),
        seed=660,
        start_price=90_000.0,
        legs=((250, 0.0016), (200, None), (0, -0.0017)),
    ),
)


def _sessions() -> tuple[date, ...]:
    days = (MOCK_CHART_AS_OF - MOCK_CHART_START).days + 1
    return tuple(
        day
        for offset in range(days)
        if (day := MOCK_CHART_START + timedelta(days=offset)).weekday() < 5
    )


def _bars(plan: _Plan, sessions: tuple[date, ...]) -> tuple[PriceBar, ...]:
    rng = random.Random(plan.seed)
    drifts: list[float | None] = []
    for count, drift in plan.legs:
        drifts += [drift] * (count or max(0, len(sessions) - len(drifts)))
    bars: list[PriceBar] = []
    close = level = plan.start_price
    for session, drift in zip(sessions, drifts, strict=True):
        if drift is None:
            step = -0.06 * math.log(close / level) + rng.gauss(0.0, 0.009)
        else:
            step = drift + rng.gauss(0.0, 0.012)
            level = close * math.exp(step)
        open_price = close * math.exp(rng.gauss(0.0, 0.002))
        close = close * math.exp(step)
        high = max(open_price, close) * math.exp(abs(rng.gauss(0.0, 0.006)))
        low = min(open_price, close) * math.exp(-abs(rng.gauss(0.0, 0.006)))
        bars.append(PriceBar(session, open_price, high, low, close))
    return tuple(bars)


class MockChartPrices:
    """연습용 차트 원천. 봉은 처음 물을 때 한 번 만든다."""

    def __init__(self) -> None:
        self._bars: dict[InstrumentRef, tuple[PriceBar, ...]] | None = None

    def confirmed_session(self) -> date | None:
        return MOCK_CHART_AS_OF

    def chart_instruments(self) -> tuple[ChartInstrument, ...]:
        return tuple(plan.instrument for plan in _PLANS)

    def chart_history(self, instrument: InstrumentRef, end: date) -> ChartHistory | None:
        if self._bars is None:
            sessions = _sessions()
            self._bars = {plan.instrument.ref: _bars(plan, sessions) for plan in _PLANS}
        plan = next((plan for plan in _PLANS if plan.instrument.ref == instrument), None)
        if plan is None:
            return None
        bars = tuple(bar for bar in self._bars[instrument] if bar.session <= end)
        if not bars or instrument.kind is InstrumentKind.INDEX:
            return ChartHistory(bars, None)
        return ChartHistory(bars, _actual_close(plan, len(bars) - 1, bars[-1].close))


def _actual_close(plan: _Plan, session_index: int, adjusted_close: float) -> float:
    """전방 조정 종가 → 그날 실제(원주가) 종가. 분할 뒤 원주가는 조정 종가 ÷ 비율이다."""
    if plan.split is not None and session_index >= plan.split[0]:
        return adjusted_close / plan.split[1]
    return adjusted_close
