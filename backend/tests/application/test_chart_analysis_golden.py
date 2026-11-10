"""알고리즘 버전 golden: 자동 선 결과가 바뀌면 `ALGORITHM_VERSION`도 올린다(설계 문서 4.7).

규칙 상수만 묶는 `test_chart_analysis_version.py`는 피벗 확정·후보 순위·제안 구간·수평 띠
기준 봉 같은 로직 변경을 잡지 못한다. 저장한 자동 선의 생긴 날·근거는 같은 버전이면 같은
결과라는 약속 위에서 다시 계산되므로, 고정 seed로 만든 계열의 일·주·월 찾기 결과를 버전과
함께 고정한다.

이 표가 깨지면 자동 선 결과를 바꾼 것이다. 의도한 변경이면 `ALGORITHM_VERSION`을 올리고 표를
새 값으로 고친다(실패 메시지가 새 요약을 보인다). 버전을 그대로 두고 표만 고치지 않는다.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta

import numpy as np

from strategy_workbench.application.stock_chart.facade.chart import (
    StockChartService,
    WindowRequest,
)
from strategy_workbench.application.stock_chart.facade.ports import (
    ChartHistory,
    ChartInstrument,
    ChartLine,
    ChartLineContent,
    InstrumentKind,
    InstrumentRef,
)
from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar, Timeframe
from strategy_workbench.domain.chart_analysis.facade.levels import SwingChoice, TrendDirection
from strategy_workbench.domain.chart_analysis.facade.line_record import ALGORITHM_VERSION

REF = InstrumentRef(InstrumentKind.STOCK, "000001")
_START = date(2023, 1, 2)
SESSIONS = tuple(
    day for offset in range(1100) if (day := _START + timedelta(days=offset)).weekday() < 5
)
AS_OF = SESSIONS[-1]


def _bars() -> tuple[PriceBar, ...]:
    """고정 seed 로그 수익률(작은 추세 + 주기) 계열. 테스트 안에서 만든 입력이다."""
    rng = np.random.default_rng(20261110)
    steps = 0.0004 + 0.012 * rng.standard_normal(len(SESSIONS))
    steps += 0.004 * np.sin(np.arange(len(SESSIONS)) / 23.0)
    closes = 100.0 * np.exp(np.cumsum(steps))
    wicks = np.abs(rng.standard_normal((len(SESSIONS), 2))) * 0.006
    bars, prev = [], 100.0
    for day, close, (up, down) in zip(SESSIONS, closes, wicks, strict=True):
        high = max(prev, close) * (1 + up)
        low = min(prev, close) * (1 - down)
        bars.append(PriceBar(day, round(prev, 2), round(high, 2), round(low, 2), round(close, 2)))
        prev = float(close)
    return tuple(bars)


class _Prices:
    def __init__(self) -> None:
        self.history = ChartHistory(_bars(), None)

    def confirmed_session(self) -> date | None:
        return AS_OF

    def chart_instruments(self) -> tuple[ChartInstrument, ...]:
        return (ChartInstrument(REF, "골든", "common"),)

    def chart_history(self, instrument: InstrumentRef, end: date) -> ChartHistory | None:
        return self.history if instrument == REF else None


class _Drawings:
    def __init__(self) -> None:
        self.lines: dict[str, ChartLine] = {}

    def list_lines(self, instrument: InstrumentRef) -> tuple[ChartLine, ...]:
        return tuple(self.lines.values())

    def get_line(self, line_id: str) -> ChartLine | None:
        return self.lines.get(line_id)

    def add_line(self, line: ChartLine) -> None:
        self.lines[line.line_id] = line

    def update_content(
        self, line_id: str, content: ChartLineContent, updated_at: datetime
    ) -> ChartLine:
        raise AssertionError("golden never edits")

    def delete_line(self, line_id: str) -> None:
        raise AssertionError("golden never deletes")


def _price(value: float) -> float:
    return round(value, 4)


def _summary() -> dict[str, object]:
    """일·주·월 찾기 결과 요약. 날짜는 ISO, 가격·비율은 넷째 자리로 줄인다."""
    ids = iter(f"line-{n}" for n in range(1, 1000))
    service = StockChartService(
        _Prices(),
        _Drawings(),
        practice_data=False,
        new_id=lambda: next(ids),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    out: dict[str, object] = {"version": ALGORITHM_VERSION}
    for timeframe in Timeframe:
        regime = service.regime(REF, timeframe)
        judgement = regime.judgement
        channels = {
            direction.value: [
                [
                    [(p.bar_date.isoformat(), _price(p.price)) for p in c.drawing.anchors],
                    c.created.isoformat(),
                ]
                for c in view.candidates[:3]
            ]
            + [view.total_candidates]
            for direction in TrendDirection
            for view in [service.channels(REF, timeframe, direction)]
        }
        retracements = {
            choice.value: None
            if view.drawing is None or view.created is None
            else [
                [(p.bar_date.isoformat(), _price(p.price)) for p in view.drawing.anchors],
                view.created.isoformat(),
            ]
            for choice in SwingChoice
            for view in [service.retracement(REF, timeframe, choice)]
        }
        zones = [
            [
                _price(z.low),
                _price(z.high),
                z.zone.touches,
                z.formed.isoformat(),
                z.zone.held,
                z.zone.broken,
                z.side.value,
                [p.bar_date.isoformat() for p in z.drawing.anchors],
            ]
            for z in service.zones(REF, timeframe).zones
        ]
        strength = judgement.strength
        out[timeframe.value] = {
            "window": [regime.window.start.isoformat(), regime.window.end.isoformat()],
            "regime": [
                judgement.verdict.value,
                None if strength is None or not math.isfinite(strength) else round(strength, 4),
                judgement.structure.value,
            ],
            # 제안 칩마다 그 구간(기준일에서 끝남)과 판정
            "suggestions": [
                [
                    s.bars,
                    s.start.isoformat(),
                    s.end.isoformat(),
                    service.regime(
                        REF, timeframe, WindowRequest(s.start, s.end)
                    ).judgement.verdict.value,
                ]
                for s in regime.suggestions
            ],
            "channels": channels,
            "retracements": retracements,
            "zones": zones,
        }
    return out


GOLDEN: dict[str, object] = {
    "version": "chart_analysis@1",
    "day": {
        "window": ["2025-07-22", "2026-01-05"],
        "regime": ["unclear", 0.785, "up"],
        "suggestions": [
            [60, "2025-10-14", "2026-01-05", "up"],
            [120, "2025-07-22", "2026-01-05", "unclear"],
            [250, "2025-01-21", "2026-01-05", "unclear"],
        ],
        "channels": {
            "up": [
                [
                    [["2025-10-10", 90.53], ["2025-11-21", 97.37], ["2025-12-30", 121.55]],
                    "2026-01-01",
                ],
                [
                    [["2025-11-21", 97.37], ["2025-12-10", 105.81], ["2025-12-04", 111.38]],
                    "2025-12-12",
                ],
                [
                    [["2025-11-21", 97.37], ["2025-12-22", 111.59], ["2025-12-04", 111.38]],
                    "2025-12-24",
                ],
                4,
            ],
            "down": [0],
        },
        "retracements": {
            "range_extremes": [[["2025-10-10", 90.53], ["2025-12-30", 121.55]], "2026-01-01"],
            "latest_major": [[["2025-12-04", 111.38], ["2025-12-10", 105.81]], "2025-12-16"],
            "latest_confirmed": [[["2025-12-22", 111.59], ["2025-12-30", 121.55]], "2026-01-01"],
        },
        "zones": [
            [111.38, 111.59, 2, "2025-12-24", 0, 0, "support", ["2025-12-04", "2025-12-04"]],
            [101.79, 102.56, 3, "2025-11-17", 0, 1, "support", ["2025-07-30", "2025-07-30"]],
            [100.77, 101.24, 2, "2025-11-20", 0, 1, "support", ["2025-08-27", "2025-08-27"]],
            [96.9, 97.37, 3, "2025-11-25", 0, 0, "support", ["2025-09-05", "2025-09-05"]],
            [95.05, 95.31, 3, "2025-09-29", 0, 1, "support", ["2025-08-21", "2025-08-21"]],
            [93.63, 94.23, 3, "2025-10-09", 0, 1, "support", ["2025-09-04", "2025-09-04"]],
            [91.65, 92.01, 3, "2025-10-06", 1, 0, "support", ["2025-09-24", "2025-09-24"]],
            [90.53, 91.06, 2, "2025-10-14", 0, 0, "support", ["2025-09-15", "2025-09-15"]],
        ],
    },
    "week": {
        "window": ["2025-01-06", "2025-12-29"],
        "regime": ["unclear", -0.3179, "insufficient"],
        "suggestions": [
            [26, "2025-07-07", "2025-12-29", "unclear"],
            [52, "2025-01-06", "2025-12-29", "unclear"],
            [104, "2024-01-08", "2025-12-29", "unclear"],
        ],
        "channels": {
            "up": [
                [
                    [["2025-10-06", 90.53], ["2025-11-10", 97.16], ["2025-10-27", 102.56]],
                    "2025-11-24",
                ],
                1,
            ],
            "down": [
                [
                    [["2025-06-09", 118.08], ["2025-10-27", 102.56], ["2025-10-06", 90.53]],
                    "2025-11-10",
                ],
                1,
            ],
        },
        "retracements": {
            "range_extremes": [[["2025-06-09", 118.08], ["2025-10-06", 90.53]], "2025-10-20"],
            "latest_major": [[["2025-06-09", 118.08], ["2025-10-06", 90.53]], "2025-10-20"],
            "latest_confirmed": [[["2025-10-27", 102.56], ["2025-11-10", 97.16]], "2025-11-24"],
        },
        "zones": [[96.69, 97.16, 2, "2025-11-24", 0, 0, "support", ["2025-04-21", "2025-04-21"]]],
    },
    "month": {
        "window": ["2023-01-02", "2025-12-01"],
        "regime": ["unclear", 0.2444, "down"],
        "suggestions": [
            [12, "2025-01-01", "2025-12-01", "undecidable"],
            [36, "2023-01-02", "2025-12-01", "unclear"],
            [60, "2023-01-02", "2025-12-01", "unclear"],
        ],
        "channels": {
            "up": [0],
            "down": [
                [
                    [["2024-11-01", 151.26], ["2025-06-02", 118.08], ["2025-04-01", 96.69]],
                    "2025-08-01",
                ],
                1,
            ],
        },
        "retracements": {
            "range_extremes": [[["2024-06-03", 152.83], ["2025-10-01", 90.53]], "2025-12-01"],
            "latest_major": [[["2024-11-01", 151.26], ["2025-10-01", 90.53]], "2025-12-01"],
            "latest_confirmed": [[["2025-06-02", 118.08], ["2025-10-01", 90.53]], "2025-12-01"],
        },
        "zones": [
            [144.52, 151.26, 2, "2025-01-01", 0, 0, "resistance", ["2023-10-02", "2023-10-02"]],
            [108.19, 111.02, 2, "2024-04-01", 1, 1, "support", ["2023-08-01", "2023-08-01"]],
            [96.69, 98.33, 2, "2025-06-02", 1, 0, "support", ["2023-01-02", "2023-01-02"]],
        ],
    },
}


def test_auto_line_results_are_pinned_to_the_algorithm_version() -> None:
    # JSON으로 한 번 돌려 튜플·리스트 차이를 없앤다(표는 JSON 모양이다)
    summary = json.loads(json.dumps(_summary()))
    assert summary == GOLDEN, (
        "자동 선 결과가 바뀌었다 — 의도한 변경이면 ALGORITHM_VERSION을 올리고 GOLDEN을 새 요약으로 "
        f"고친다:\n{json.dumps(summary, ensure_ascii=False)}"
    )
