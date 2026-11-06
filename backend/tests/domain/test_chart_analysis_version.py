"""알고리즘 버전 묶음: 자동 선 결과를 바꾸는 규칙 상수를 바꾸면 `ALGORITHM_VERSION`도 올린다(4.7).

이 표가 깨지면 규칙을 바꾼 것이다. 버전을 올리고 표를 새 값으로 고친다.
"""

from __future__ import annotations

from strategy_workbench.domain.chart_analysis._channel import MIN_CHANNEL_BARS, OVERSHOOT_ATR
from strategy_workbench.domain.chart_analysis._regime import (
    FLAT_STRENGTH,
    MIN_REGIME_BARS,
    TREND_STRENGTH,
)
from strategy_workbench.domain.chart_analysis._zones import TOUCH_GAP_BARS
from strategy_workbench.domain.chart_analysis.facade.confluence import DEFAULT_CONFLUENCE_TOLERANCE
from strategy_workbench.domain.chart_analysis.facade.geometry import DEFAULT_FIB_LEVELS
from strategy_workbench.domain.chart_analysis.facade.levels import ZoneParams
from strategy_workbench.domain.chart_analysis.facade.line_record import ALGORITHM_VERSION
from strategy_workbench.domain.chart_analysis.facade.regime import BOX_RATIO_MAX
from strategy_workbench.domain.chart_analysis.facade.series import SwingParams


def test_rule_constants_are_pinned_to_the_algorithm_version() -> None:
    rules = {
        "swing": SwingParams(),
        "regime": (MIN_REGIME_BARS, FLAT_STRENGTH, TREND_STRENGTH, BOX_RATIO_MAX),
        "channel": (OVERSHOOT_ATR, MIN_CHANNEL_BARS),
        "zone": (ZoneParams(), TOUCH_GAP_BARS),
        "confluence": DEFAULT_CONFLUENCE_TOLERANCE,
        "fib_levels": DEFAULT_FIB_LEVELS,
    }

    assert (ALGORITHM_VERSION, rules) == (
        "chart_analysis@1",
        {
            "swing": SwingParams(14, 1.0, 2.0, 2),
            "regime": (20, 1.0, 2.0, 0.73),
            "channel": (0.25, 20),
            "zone": (ZoneParams(0.5, 2), 2),
            "confluence": 0.005,
            "fib_levels": (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0),
        },
    )
