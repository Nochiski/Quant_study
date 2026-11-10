"""판정 문장: 판정·사유마다 맞는 문장을 고르고, 판정 불가에는 S·B 수치를 싣지 않는다(4.2)."""

from __future__ import annotations

import pytest

from strategy_workbench.domain.chart_analysis.facade.levels import ZoneParams
from strategy_workbench.domain.chart_analysis.facade.regime import (
    RegimeJudgement,
    RegimeReason,
    RegimeVerdict,
    Structure,
)
from strategy_workbench.domain.chart_analysis.facade.series import SwingParams, Window
from strategy_workbench.domain.chart_analysis.facade.wording import (
    confluence_rules,
    regime_rules,
    regime_sentence,
    zone_rules,
)


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


def test_regime_rules_quote_the_atr_period_of_the_series() -> None:
    # 박스 비율 문장의 ATR 기간은 박아 둔 14가 아니라 이번 계열의 값이다
    (box,) = (rule for rule in regime_rules(SwingParams(atr_period=20)) if "박스 비율" in rule)

    assert "ATR(20)" in box


def test_zone_and_confluence_rules_quote_the_request_values() -> None:
    # 묶는 폭·최소 접촉·ATR 기간·허용 폭은 이번 요청의 값을 인용한다(기본값을 박아 두지 않는다)
    zones = " ".join(
        zone_rules(ZoneParams(width_atr=0.75, min_touches=3), SwingParams(atr_period=20))
    )
    confluence = " ".join(confluence_rules(0.0125))

    assert ("k=0.75" in zones, "3회 이상" in zones, "ATR(20)" in zones) == (True, True, True)
    assert "±1.25%" in confluence
