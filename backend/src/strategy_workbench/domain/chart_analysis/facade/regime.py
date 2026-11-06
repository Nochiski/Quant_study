"""국면 판정. `WINDOW_PRESETS`는 판정 창 기본·제안 칩 어휘(4.2)라 노드 안 소비자 없이 PR2가
소비한다."""

from strategy_workbench.domain.chart_analysis._regime import (
    BOX_RATIO_MAX,
    WINDOW_PRESETS,
    RegimeJudgement,
    RegimeReason,
    RegimeVerdict,
    Structure,
    WindowPreset,
    judge_regime,
)

__all__ = [
    "BOX_RATIO_MAX",
    "WINDOW_PRESETS",
    "RegimeJudgement",
    "RegimeReason",
    "RegimeVerdict",
    "Structure",
    "WindowPreset",
    "judge_regime",
]
