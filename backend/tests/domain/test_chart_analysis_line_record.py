"""채널 방향의 출처(4.7): 추천을 따랐나·바꿨나, 추천이 없으면 출처도 없다."""

from __future__ import annotations

from strategy_workbench.domain.chart_analysis.facade.levels import TrendDirection
from strategy_workbench.domain.chart_analysis.facade.line_record import ChosenBy, chosen_by

UP, DOWN = TrendDirection.UP, TrendDirection.DOWN


def test_direction_source_follows_the_recommendation_or_is_absent() -> None:
    assert chosen_by(UP, UP) is ChosenBy.RECOMMENDATION
    assert chosen_by(UP, DOWN) is ChosenBy.USER_OVERRIDE
    # 박스권·보류처럼 추천이 없는 국면에서 고른 방향은 따르거나 바꾼 것이 아니다
    assert chosen_by(None, DOWN) is None
