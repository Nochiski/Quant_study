"""판정 문장: 판정·사유마다 맞는 문장을 고르고, 판정 불가에는 S·B 수치를 싣지 않는다(4.2)."""

from __future__ import annotations

import pytest

from strategy_workbench.domain.chart_analysis.facade.regime import (
    RegimeJudgement,
    RegimeReason,
    RegimeVerdict,
    Structure,
)
from strategy_workbench.domain.chart_analysis.facade.series import Window
from strategy_workbench.domain.chart_analysis.facade.wording import regime_sentence


def _judgement(
    verdict: RegimeVerdict,
    reason: RegimeReason | None,
    strength: float | None,
    box: float | None,
    structure: Structure = Structure.UP,
    bars: int = 30,
) -> RegimeJudgement:
    return RegimeJudgement(
        Window(0, bars - 1), verdict, reason, strength, box, structure, (), (), False, False
    )


@pytest.mark.parametrize(
    ("judgement", "expected"),
    [
        (_judgement(RegimeVerdict.UP, None, 2.345, 1.2), ("상승 국면", "(S=2.35)")),
        (
            _judgement(RegimeVerdict.DOWN, None, -2.5, 1.2, Structure.DOWN),
            ("하락 국면", "(S=-2.50)", "LH·LL"),
        ),
        (_judgement(RegimeVerdict.BOX, None, -0.4, 0.61), ("박스권", "|S|=0.40", "B=0.61", "0.73")),
        (_judgement(RegimeVerdict.UNCLEAR, None, 0.4, 0.9), ("방향이 아직", "B=0.90")),
        (
            _judgement(RegimeVerdict.HOLD, RegimeReason.WEAK_STRENGTH, -1.5, 0.9),
            ("보류", "|S|=1.50"),
        ),
        (
            _judgement(
                RegimeVerdict.HOLD, RegimeReason.STRUCTURE_MISMATCH, 2.2, 0.9, Structure.MIXED
            ),
            ("보류", "맞지 않아요", "S=2.20"),
        ),
        (
            _judgement(RegimeVerdict.UNDECIDABLE, RegimeReason.TOO_FEW_BARS, None, None, bars=12),
            ("판정할 수 없어요", "(N=12)"),
        ),
        (
            _judgement(RegimeVerdict.UNDECIDABLE, RegimeReason.DEGENERATE, None, None),
            ("판정할 수 없어요", "움직이지 않아"),
        ),
    ],
)
def test_each_verdict_gets_its_sentence(
    judgement: RegimeJudgement, expected: tuple[str, ...]
) -> None:
    sentence = regime_sentence(judgement)
    assert all(part in sentence for part in expected), sentence
    if judgement.verdict is RegimeVerdict.UNDECIDABLE:
        assert "S=" not in sentence and "B=" not in sentence
