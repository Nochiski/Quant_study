from __future__ import annotations

from dataclasses import dataclass

from ._models import (
    SignalNormalization,
    StrategySpec,
    composite_factors,
    inverse_risk_factor_id,
)

# 결합 전 정규화를 문장으로 옮기는 결합자. 진단 메시지와 같은 규칙으로 backend 가 한글 문장을
# 완성해 내보낸다(SoT: authoring 진단 코드 행). 로케일별 문구는 frontend i18n 이 따로 갖는다.
_NORMALIZATION_SUMMARY: dict[SignalNormalization, str] = {
    SignalNormalization.NONE: "원시값 그대로 방향·가중치 가중합으로",
    SignalNormalization.RANK: "횡단면 순위로 맞춘 뒤 방향·가중치 가중합으로",
    SignalNormalization.ZSCORE: "횡단면 표준화 뒤 방향·가중치 가중합으로",
}


@dataclass(frozen=True)
class StrategyExplanationStep:
    stage: str
    summary: str
    details: tuple[str, ...]


@dataclass(frozen=True)
class StrategyExplanation:
    steps: tuple[StrategyExplanationStep, ...]


def explain_strategy(spec: StrategySpec) -> StrategyExplanation:
    """전략 문서가 말하는 단계만 설명한다.

    `data`·`execution` 단계는 1.2 에서 문서를 떠나 실행 설정(`RunEnvironment`)이 소유하므로
    여기서 설명하지 않는다 — 전략 설명이 실행 설정을 문장으로 복제하면 같은 사실의 owner 가
    둘이 된다(P2-03).
    """
    # 합성에 들어가는 팩터만 센다. 리스크 역가중 팩터는 합성에서 빠지므로(spec D3 S6) 세면
    # 설명이 실제 계산보다 팩터를 하나 더 결합한다고 말한다.
    factor_names = tuple(factor.label for factor in composite_factors(spec))
    risk_factor_id = inverse_risk_factor_id(spec)
    risk_factor_details = tuple(
        f"리스크 역가중 팩터: {factor.label} (원시값의 역수로 비중)"
        for factor in spec.factors
        if factor.factor_id == risk_factor_id
    )
    return StrategyExplanation(
        steps=(
            StrategyExplanationStep(
                "signal",
                f"팩터 {len(factor_names)}개를 "
                f"{_NORMALIZATION_SUMMARY[spec.signal.normalization]} 결합",
                factor_names,
            ),
            StrategyExplanationStep(
                "portfolio",
                f"{spec.portfolio.side} · {spec.portfolio.selection_count}종목 · "
                f"{spec.portfolio.rebalance}",
                (
                    f"선택 방식: {spec.portfolio.selection_method}",
                    f"가중 방식: {spec.portfolio.weighting}",
                    f"최소 거래 비중: {spec.portfolio.minimum_trade_weight:.2%}",
                ),
            ),
            StrategyExplanationStep(
                "risk",
                f"gross {spec.risk.gross_exposure:.2f} / net {spec.risk.net_exposure:.2f}",
                (
                    f"종목당 최대 {spec.risk.max_name_weight:.1%}",
                    f"섹터당 최대 {spec.risk.max_sector_weight:.1%}",
                    f"섹터 중립: {spec.risk.sector_neutral}",
                    *risk_factor_details,
                ),
            ),
        )
    )
