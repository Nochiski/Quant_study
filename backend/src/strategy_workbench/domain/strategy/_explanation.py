from __future__ import annotations

from dataclasses import dataclass

from ._models import StrategySpec


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
    factor_names = tuple(factor.label for factor in spec.factors)
    return StrategyExplanation(
        steps=(
            StrategyExplanationStep(
                "signal",
                f"팩터 {len(factor_names)}개를 방향·가중치 가중합으로 결합",
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
                ),
            ),
        )
    )
