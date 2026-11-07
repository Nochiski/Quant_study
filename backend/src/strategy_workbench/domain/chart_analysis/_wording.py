"""개별종목차트의 사용자 대면 문장(해요체)의 owner(설계 문서 4.2).

판정·사유 코드와 수치에서 문장을 완성한다. 문장이 인용하는 임계(`FLAT_STRENGTH`·`TREND_STRENGTH`·
`BOX_RATIO_MAX`·`MIN_REGIME_BARS`)를 이 노드가 소유하므로 여기서 만든다 — application은 그대로 실어
나르고 frontend는 표시만 한다(다시 조립하지 않는다).
"""

from __future__ import annotations

from strategy_workbench.domain.chart_analysis._channel import AnchorIssue
from strategy_workbench.domain.chart_analysis._confluence import MIN_CONFLUENCE_KINDS
from strategy_workbench.domain.chart_analysis._regime import (
    BOX_RATIO_MAX,
    FLAT_STRENGTH,
    MIN_REGIME_BARS,
    TREND_STRENGTH,
    RegimeJudgement,
    RegimeReason,
    RegimeVerdict,
    Structure,
)
from strategy_workbench.domain.chart_analysis._series import SwingParams
from strategy_workbench.domain.chart_analysis._zones import TOUCH_GAP_BARS, ZoneParams

STARTS_NEAR_EXTREME_WARNING = (
    "판정 구간이 확정된 고점·저점 근처에서 시작해요. 극값에서 재면 강도가 한쪽으로 치우치니 시작을 "
    "조금 옮겨 보세요."
)
ENDS_NEAR_EXTREME_WARNING = (
    "판정 구간이 확정된 고점·저점 근처에서 끝나요. 극값에서 재면 강도가 한쪽으로 치우치니 "
    "끝을 조금 옮겨 보세요."
)
CONFLUENCE_CAUTION = (
    "겹친 선들은 같은 가격 기록에서 나와 서로 독립된 증거가 아니에요. 겹침이 많다고 확률이 그만큼 "
    "오르지는 않아요."
)
ZONE_BASIS = "어떤 점을 묶을지는 판정 구간 끝 폭으로 고르고, 생긴 뒤에는 유지된 것으로 봐요."
NO_SWING_MESSAGE = "이 구간에는 되돌림을 그릴 확정 고점·저점이 모자라요. 구간을 넓혀 보세요."

_ANCHOR_ISSUE_MESSAGES = {
    AnchorIssue.OUTSIDE_WINDOW: "첫 점이 판정 구간 밖에 있어요. 구간 안의 점을 골라 주세요.",
    AnchorIssue.NOT_A_PIVOT: (
        "첫 점이 그 방향의 확정된 고점·저점이 아니에요(상승은 저점, 하락은 고점). 차트의 피벗 점을 "
        "골라 주세요."
    ),
}

_STRUCTURE_LABELS = {
    Structure.UP: "높아지는 고점·저점(HH·HL)",
    Structure.DOWN: "낮아지는 고점·저점(LH·LL)",
    Structure.MIXED: "고점과 저점이 서로 다른 방향",
    Structure.INSUFFICIENT: "확정된 큰 고점·저점이 둘씩 없음",
}


def _plain(value: float) -> str:
    """임계 상수 표기 — 정수인 실수는 `.0`을 뗀다(2.0 → 2, 0.73 → 0.73)."""
    return f"{value:g}"


def _regime_rules() -> tuple[str, ...]:
    flat, trend, box_max = _plain(FLAT_STRENGTH), _plain(TREND_STRENGTH), _plain(BOX_RATIO_MAX)
    return (
        "강도 S = ln(끝 종가 ÷ 시작 종가) ÷ (봉별 로그수익률 표준편차 × √(봉 수 − 1))예요. "
        "순변화가 같은 기간의 흔들림보다 몇 배 큰지라 봉 수가 달라도 뜻이 같아요.",
        "박스 비율 B = (구간 최고가 − 최저가) ÷ (구간 끝 ATR(14) × √봉 수)예요.",
        f"봉이 {MIN_REGIME_BARS}개보다 적거나 가격이 움직이지 않으면 판정할 수 없어요.",
        f"|S| < {flat}이면 B ≤ {box_max}일 때 박스권(채널 대신 수평 구간 권장), "
        "아니면 방향 미확인(구간을 나눠 보세요)이에요.",
        f"{flat} ≤ |S| < {trend}이면 보류예요(방향은 있지만 약해요).",
        f"|S| ≥ {trend}이고 큰 스윙의 확정 고점 2·저점 2가 같은 방향(HH·HL 또는 LH·LL)이면 "
        "상승·하락, 아니면 보류예요.",
    )


# 판정 규칙 접힘 설명(채널 생성 패널). 임계를 이 노드의 상수에서 그대로 인용한다.
REGIME_RULES = _regime_rules()


def pivot_rule(params: SwingParams) -> str:
    """피벗 확정 규칙 한 문장 — 확인 봉 수와 ATR 기간은 그 계열의 파라미터 값이다."""
    return (
        f"고점·저점은 극값 뒤 {params.confirm_bars}번째 봉부터, 반대쪽 끝이 민감도 × "
        f"ATR({params.atr_period})만큼 벌어진 첫 봉에서 확정돼요. 확정 전에는 쓰지 않아요."
    )


def zone_rules(params: ZoneParams, swing: SwingParams) -> tuple[str, ...]:
    """수평 구간을 묶고 세는 규칙 문장(설계 문서 4.5). 묶는 폭·최소 접촉은 이번 요청의 값이다."""
    return (
        f"묶는 폭 = k × ATR({swing.atr_period}) ÷ 판정 구간 끝 종가예요"
        f"(지금 k={_plain(params.width_atr)}). 한 구간 안의 가장 낮은 점과 높은 점이 "
        "이 비율을 넘지 않게 로그 가격으로 묶어요.",
        f"{TOUCH_GAP_BARS}봉 안에 붙은 고점·저점은 한 번으로 세고, "
        f"접촉이 {params.min_touches}회 이상인 구간만 남겨요.",
        "현재가(기준일 종가)보다 위면 저항, 아래면 지지, 현재가가 구간 안이면 걸침이에요. "
        "판정 구간을 과거로 옮겨도 구분은 현재가 기준이에요.",
        "생긴 날은 구간을 이루는 고점·저점이 모두 확정되고 그날 폭으로도 "
        "한 구간이 되는 첫 봉이에요. 그 뒤 다시 닿았다가 같은 쪽으로 돌아가면 지킴, "
        "반대쪽으로 넘어가면 뚫림이에요(판정 구간 안에서만 세요).",
        pivot_rule(swing),
    )


def confluence_rules(tolerance: float) -> tuple[str, ...]:
    """중첩을 세는 규칙 문장(설계 문서 4.6). 허용 폭은 이번 요청의 값이다."""
    return (
        f"서로 다른 종류 {MIN_CONFLUENCE_KINDS}개 이상이 덮는 가격대만 중첩이에요. "
        "같은 종류(자동 수평 구간과 내 수평선)는 1종으로 세요.",
        f"각 선을 가격의 ±{_plain(round(tolerance * 100, 6))}%만큼 넓혀 비교하고, "
        "채널·되돌림은 마지막 완성 봉의 레벨 가격으로 비교해요.",
    )


def anchor_issue_message(issue: AnchorIssue) -> str:
    return _ANCHOR_ISSUE_MESSAGES[issue]


def structure_label(structure: Structure) -> str:
    return _STRUCTURE_LABELS[structure]


def regime_sentence(judgement: RegimeJudgement) -> str:
    """판정 한 문장. 수치(S·B·N)는 괄호 안 `key=value`로 남기고(S·B는 소수 둘째 자리), 임계는 상수를
    그대로 인용한다."""
    verdict, s, b = judgement.verdict, judgement.strength, judgement.box_ratio
    if verdict is RegimeVerdict.UNDECIDABLE or s is None or b is None:
        if judgement.reason is RegimeReason.TOO_FEW_BARS:
            return (
                f"판정할 수 없어요. 구간 봉 수가 {MIN_REGIME_BARS}봉보다 적어요"
                f"(N={judgement.window.bars}). 구간을 넓혀 주세요."
            )
        return "판정할 수 없어요. 구간 안에서 가격이 움직이지 않아 강도와 박스 비율을 잴 수 없어요."
    structure = structure_label(judgement.structure)
    flat, trend, box_max = _plain(FLAT_STRENGTH), _plain(TREND_STRENGTH), _plain(BOX_RATIO_MAX)
    if verdict is RegimeVerdict.UP:
        return (
            f"상승 국면이에요. 강도가 기준 {trend} 이상이고(S={s:.2f}) 큰 스윙이 {structure}"
            "이에요. 상승 채널을 권해요."
        )
    if verdict is RegimeVerdict.DOWN:
        return (
            f"하락 국면이에요. 강도가 기준 −{trend} 이하이고(S={s:.2f}) 큰 스윙이 {structure}"
            "이에요. 하락 채널을 권해요."
        )
    if verdict is RegimeVerdict.BOX:
        return (
            f"박스권이에요. 강도가 {flat}보다 작고(|S|={abs(s):.2f}) 박스 비율도 {box_max} "
            f"이하예요(B={b:.2f}). 채널 대신 수평 지지·저항 구간을 권해요."
        )
    if verdict is RegimeVerdict.UNCLEAR:
        return (
            f"방향이 아직 안 보여요. 강도는 {flat}보다 작지만(|S|={abs(s):.2f}) 박스 비율이 "
            f"{box_max}보다 커서(B={b:.2f}) 박스권도 아니에요. 구간을 나눠 다시 보세요."
        )
    if judgement.reason is RegimeReason.WEAK_STRENGTH:
        return (
            f"판정을 보류해요. 강도가 {flat}과 {trend} 사이라(|S|={abs(s):.2f}) 추세라고 하기엔 "
            "약해요."
        )
    return (
        f"판정을 보류해요. 강도는 충분하지만(S={s:.2f}) 큰 스윙 구조가 방향과 맞지 않아요"
        f"({structure})."
    )
