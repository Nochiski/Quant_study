"""알고리즘 버전 golden: 자동 선 결과가 바뀌면 `ALGORITHM_VERSION`도 올린다(설계 문서 4.7).

규칙 상수만 묶는 `test_chart_analysis_version.py`는 피벗 확정·후보 순위·제안 구간·수평 띠
기준 봉 같은 로직 변경을 잡지 못한다. 저장한 자동 선의 생긴 날·근거는 같은 버전이면 같은
결과라는 약속 위에서 다시 계산되므로, 결정식으로 만든 계열의 일·주·월 찾기 결과(기본·바꾼
조건), 중첩, 저장한 선의 생긴 날을 버전과 함께 고정한다.

입력은 정수 LCG로 센트 단위 가격을 만든다 — 난수 라이브러리·초월 함수에 기대지 않아 의존성
판이나 플랫폼이 바뀌어도 입력이 같다. 표가 깨지면 실패 메시지의 안내를 따른다.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta

from strategy_workbench.application.stock_chart.facade.chart import (
    ChartLineDraft,
    ChartPoint,
    LineEdit,
    StockChartService,
    SwingSettings,
    WindowRequest,
)
from strategy_workbench.application.stock_chart.facade.ports import (
    ChartHistory,
    ChartInstrument,
    InstrumentKind,
    InstrumentRef,
)
from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar, Timeframe
from strategy_workbench.domain.chart_analysis.facade.levels import SwingChoice, TrendDirection
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ALGORITHM_VERSION,
    LineKind,
    LineSource,
)

from ._stock_chart_fakes import FakeDrawings, SeriesPrices

REF = InstrumentRef(InstrumentKind.STOCK, "000001")
AS_OF = date(2025, 10, 30)
# 월봉 긴 제안 칩(60)이 잘리지 않게 5년 반(평일 1,430일)을 둔다
SESSIONS = tuple(
    day for offset in range(2000, -1, -1) if (day := AS_OF - timedelta(days=offset)).weekday() < 5
)


def _lcg(seed: int) -> Iterator[int]:
    """32비트 선형 합동 수열(Numerical Recipes 계수) — 정수 연산뿐이라 어디서나 같다."""
    state = seed
    while True:
        state = (1664525 * state + 1013904223) % 2**32
        yield state >> 8


def _bars() -> tuple[PriceBar, ...]:
    """센트 단위 정수 가격. 하루 걸음 −1.80%~+1.85%에 60봉마다 바뀌는 ±0.4% 추세를 더하고,
    꼬리는 0~0.6%다. 시가 = 전날 종가."""
    draws = _lcg(20261110)
    bars, prev = [], 10_000
    for i, day in enumerate(SESSIONS):
        step_bp = next(draws) % 366 - 180 + (40 if (i // 60) % 2 == 0 else -40)
        close = max(100, prev + prev * step_bp // 10_000)
        high = max(prev, close) + max(prev, close) * (next(draws) % 61) // 10_000
        low = min(prev, close) - min(prev, close) * (next(draws) % 61) // 10_000
        bars.append(PriceBar(day, prev / 100, high / 100, low / 100, close / 100))
        prev = close
    return tuple(bars)


def _price(value: float) -> float:
    return round(value, 4)


def _points(points: Sequence[ChartPoint]) -> list[list[object]]:
    return [[p.bar_date.isoformat(), _price(p.price)] for p in points]


def _summary() -> dict[str, object]:
    """찾기 결과 요약. 날짜는 ISO, 가격·비율은 넷째 자리로 줄인다."""
    ids = iter(f"line-{n}" for n in range(1, 1000))
    prices = SeriesPrices(
        (ChartInstrument(REF, "골든", "common"),), {REF: ChartHistory(_bars(), None)}, AS_OF
    )
    service = StockChartService(
        prices,
        FakeDrawings(),
        practice_data=False,
        new_id=lambda: next(ids),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    def channels(timeframe: Timeframe) -> dict[str, object]:
        return {
            direction.value: {
                "total": view.total_candidates,
                "candidates": [
                    [_points(c.drawing.anchors), c.created.isoformat()] for c in view.candidates
                ],
            }
            for direction in TrendDirection
            for view in [service.channels(REF, timeframe, direction)]
        }

    out: dict[str, object] = {"version": ALGORITHM_VERSION}
    for timeframe in Timeframe:
        regime = service.regime(REF, timeframe)
        judgement = regime.judgement
        strength = judgement.strength
        out[timeframe.value] = {
            "window": [regime.window.start.isoformat(), regime.window.end.isoformat()],
            "regime": [
                judgement.verdict.value,
                None if strength is None or not math.isfinite(strength) else round(strength, 4),
                judgement.structure.value,
            ],
            # 제안 칩마다 길이·기본 여부·구간과 그 구간의 판정
            "suggestions": [
                [
                    s.bars,
                    s.default,
                    s.start.isoformat(),
                    s.end.isoformat(),
                    service.regime(
                        REF, timeframe, WindowRequest(s.start, s.end)
                    ).judgement.verdict.value,
                ]
                for s in regime.suggestions
            ],
            "channels": channels(timeframe),
            "retracements": {
                choice.value: None
                if view.drawing is None or view.created is None
                else [_points(view.drawing.anchors), view.created.isoformat()]
                for choice in SwingChoice
                for view in [service.retracement(REF, timeframe, choice)]
            },
            "zones": [
                [
                    _price(z.low),
                    _price(z.high),
                    z.zone.touches,
                    z.formed.isoformat(),
                    z.zone.held,
                    z.zone.broken,
                    z.side.value,
                    z.drawing.anchors[0].bar_date.isoformat(),  # 띠 기준 봉(두 점이 같은 봉)
                ]
                for z in service.zones(REF, timeframe).zones
            ],
        }

    # 일봉에서 바꾼 조건: 첫 점 지정 채널, 민감도를 바꾼 되돌림, 묶는 폭·최소 접촉을 바꾼 수평 구간
    day = Timeframe.DAY
    down = service.channels(REF, day, TrendDirection.DOWN)
    first = down.candidates[-1].drawing.anchors[0].bar_date
    pinned = service.channels(REF, day, TrendDirection.DOWN, first_anchor=first)
    loose = service.retracement(REF, day, SwingChoice.LATEST_MAJOR, swing=SwingSettings(1.5, 2.5))
    wide = service.zones(REF, day, width_atr=1.0, min_touches=3)
    out["day_options"] = {
        "first_anchor": [first.isoformat(), pinned.total_candidates]
        + [[_points(c.drawing.anchors), c.created.isoformat()] for c in pinned.candidates],
        "retracement_k1.5": None
        if loose.drawing is None or loose.created is None
        else [_points(loose.drawing.anchors), loose.created.isoformat()],
        "zones_k1_touch3": [
            [_price(z.low), _price(z.high), z.zone.touches, z.formed.isoformat()]
            for z in wide.zones
        ],
    }

    # 위 후보를 자동 선으로 저장한 뒤 다시 읽은 생긴 날(저장 기록으로 다시 찾는 경로)과
    # 그 선들의 중첩
    saved = []
    if pinned.candidates:
        saved.append((LineKind.FIB_CHANNEL, pinned.candidates[0].drawing.anchors, pinned.record))
    if loose.drawing is not None:
        saved.append((LineKind.FIB_RETRACEMENT, loose.drawing.anchors, loose.record))
    saved.extend((LineKind.HLINE, z.drawing.anchors, wide.record) for z in wide.zones)
    for kind, anchors, record in saved:
        service.create_line(
            ChartLineDraft(REF, day, kind, LineSource.AUTO, LineEdit(anchors), record)
        )
    out["saved_created"] = [
        [view.line.kind.value, None if view.created is None else view.created.isoformat()]
        for view in service.lines(REF).lines
    ]
    out["confluence"] = [
        [_price(g.low), _price(g.high), [k.value for k in g.kinds], len(g.members)]
        for g in service.confluence(REF).groups
    ]
    return out


GOLDEN: dict[str, object] = {
    "version": "chart_analysis@1",
    "day": {
        "window": ["2025-05-16", "2025-10-30"],
        "regime": ["hold", -1.3443, "mixed"],
        "suggestions": [
            [60, False, "2025-08-08", "2025-10-30", "hold"],
            [120, True, "2025-05-16", "2025-10-30", "hold"],
            [250, False, "2024-11-15", "2025-10-30", "hold"],
        ],
        "channels": {
            "up": {"total": 0, "candidates": []},
            "down": {
                "total": 2,
                "candidates": [
                    [
                        [["2025-08-27", 103.28], ["2025-10-23", 80.43], ["2025-09-16", 87.64]],
                        "2025-10-27",
                    ],
                    [
                        [["2025-09-23", 90.18], ["2025-10-23", 80.43], ["2025-10-08", 78.81]],
                        "2025-10-27",
                    ],
                ],
            },
        },
        "retracements": {
            "range_extremes": [[["2025-08-27", 103.28], ["2025-10-24", 78.3]], "2025-10-28"],
            "latest_major": [[["2025-08-04", 93.71], ["2025-08-27", 103.28]], "2025-08-29"],
            "latest_confirmed": [[["2025-10-23", 80.43], ["2025-10-24", 78.3]], "2025-10-28"],
        },
        "zones": [
            [97.15, 97.82, 3, "2025-08-19", 0, 2, "resistance", "2025-06-30"],
            [96.17, 96.94, 3, "2025-09-03", 0, 1, "resistance", "2025-07-22"],
            [94.91, 95.5, 3, "2025-08-20", 0, 1, "resistance", "2025-05-19"],
            [93.71, 94.18, 3, "2025-08-06", 0, 1, "resistance", "2025-07-18"],
            [87.64, 87.88, 2, "2025-09-18", 1, 1, "resistance", "2025-06-11"],
            [80.43, 80.43, 2, "2025-10-27", 0, 0, "support", "2025-10-15"],
            [78.3, 78.81, 4, "2025-10-28", 0, 0, "support", "2025-10-08"],
        ],
    },
    "week": {
        "window": ["2024-10-28", "2025-10-20"],
        "regime": ["hold", -1.9724, "down"],
        "suggestions": [
            [26, False, "2025-04-28", "2025-10-20", "hold"],
            [52, True, "2024-10-28", "2025-10-20", "hold"],
            [104, False, "2023-10-30", "2025-10-20", "down"],
        ],
        "channels": {
            "up": {
                "total": 1,
                "candidates": [
                    [
                        [["2025-06-09", 87.88], ["2025-08-04", 93.71], ["2025-07-07", 99.92]],
                        "2025-08-18",
                    ]
                ],
            },
            "down": {
                "total": 1,
                "candidates": [
                    [
                        [["2025-03-10", 120.85], ["2025-08-25", 103.28], ["2025-05-05", 90.89]],
                        "2025-09-08",
                    ]
                ],
            },
        },
        "retracements": {
            "range_extremes": [[["2025-03-10", 120.85], ["2025-06-09", 87.88]], "2025-06-23"],
            "latest_major": [[["2025-06-09", 87.88], ["2025-08-25", 103.28]], "2025-09-08"],
            "latest_confirmed": [[["2025-08-04", 93.71], ["2025-08-25", 103.28]], "2025-09-08"],
        },
        "zones": [[99.92, 99.92, 2, "2025-07-21", 1, 0, "resistance", "2024-12-23"]],
    },
    "month": {
        "window": ["2022-10-03", "2025-09-01"],
        "regime": ["unclear", -0.9622, "down"],
        "suggestions": [
            [12, False, "2024-10-01", "2025-09-01", "undecidable"],
            [36, True, "2022-10-03", "2025-09-01", "unclear"],
            [60, False, "2020-10-01", "2025-09-01", "unclear"],
        ],
        "channels": {
            "up": {"total": 0, "candidates": []},
            "down": {
                "total": 3,
                "candidates": [
                    [
                        [["2024-09-02", 141.52], ["2025-03-03", 120.85], ["2024-12-02", 99.92]],
                        "2025-05-01",
                    ],
                    [
                        [["2024-04-01", 154.48], ["2025-03-03", 120.85], ["2024-07-01", 110.54]],
                        "2025-05-01",
                    ],
                    [
                        [["2024-04-01", 154.48], ["2024-09-02", 141.52], ["2024-07-01", 110.54]],
                        "2024-11-01",
                    ],
                ],
            },
        },
        "retracements": {
            "range_extremes": [[["2022-11-01", 160.13], ["2025-06-02", 87.88]], "2025-08-01"],
            "latest_major": [[["2024-07-01", 110.54], ["2024-09-02", 141.52]], "2024-11-01"],
            "latest_confirmed": [[["2025-03-03", 120.85], ["2025-06-02", 87.88]], "2025-08-01"],
        },
        "zones": [
            [154.48, 160.13, 2, "2024-06-03", 0, 0, "resistance", "2022-11-01"],
            [141.52, 151.87, 3, "2025-09-01", 0, 0, "resistance", "2023-05-01"],
            [120.85, 125.45, 3, "2025-05-01", 0, 0, "resistance", "2023-02-01"],
            [110.53, 110.54, 2, "2024-09-02", 0, 3, "resistance", "2023-07-03"],
        ],
    },
    "day_options": {
        "first_anchor": [
            "2025-09-23",
            1,
            [[["2025-09-23", 90.18], ["2025-10-23", 80.43], ["2025-10-08", 78.81]], "2025-10-27"],
        ],
        "retracement_k1.5": [[["2025-08-04", 93.71], ["2025-08-27", 103.28]], "2025-08-29"],
        "zones_k1_touch3": [
            [97.15, 97.82, 3, "2025-08-19"],
            [95.44, 96.94, 5, "2025-09-01"],
            [93.71, 94.91, 4, "2025-08-06"],
            [80.43, 81.49, 3, "2025-10-27"],
            [78.3, 78.81, 4, "2025-10-28"],
        ],
    },
    "saved_created": [
        ["fib_channel", "2025-10-27"],
        ["fib_retracement", "2025-08-29"],
        ["hline", "2025-08-19"],
        ["hline", "2025-09-01"],
        ["hline", "2025-08-06"],
        ["hline", "2025-10-27"],
        ["hline", "2025-10-28"],
    ],
    "confluence": [
        [77.9085, 78.6052, ["hline", "fib_channel"], 2],
        [95.2792, 96.2368, ["hline", "fib_retracement"], 3],
        [96.8789, 97.8526, ["hline", "fib_retracement"], 3],
        [98.0025, 98.3091, ["hline", "fib_retracement"], 2],
    ],
}


def test_auto_line_results_are_pinned_to_the_algorithm_version() -> None:
    # JSON으로 한 번 돌려 튜플·리스트 차이를 없앤다(표는 JSON 모양이다). NaN은 바로 실패한다
    summary = json.loads(json.dumps(_summary(), allow_nan=False))
    assert summary == GOLDEN, (
        "자동 선 결과가 바뀌었다. 찾기 규칙(domain chart_analysis·application 기록 재계산)을 "
        "바꿨으면 먼저 `_line_record.ALGORITHM_VERSION`을 올리고 이 테스트를 다시 돌려 나온 "
        "요약으로 GOLDEN을 고친다(옛 버전으로 저장한 선은 생긴 날을 다시 계산하지 않게 된다). "
        "이 테스트의 입력 만들기나 요약 모양만 바꿨으면 버전은 두고 GOLDEN만 고친다.\n"
        f"{json.dumps(summary, ensure_ascii=False)}"
    )
