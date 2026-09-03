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
    factor_names = tuple(factor.label for factor in spec.factors.factors)
    return StrategyExplanation(
        steps=(
            StrategyExplanationStep(
                "data",
                f"{spec.data.market} {spec.data.universe_id}",
                (f"{spec.data.start} ~ {spec.data.end}",),
            ),
            StrategyExplanationStep(
                "signal",
                f"팩터 {len(factor_names)}개를 {spec.signal.method} 방식으로 결합",
                factor_names,
            ),
            StrategyExplanationStep(
                "portfolio",
                f"{spec.portfolio.side} · {spec.portfolio.selection_count}종목 · "
                f"{spec.portfolio.rebalance}",
                (f"가중 방식: {spec.portfolio.weighting}",),
            ),
            StrategyExplanationStep(
                "risk",
                f"gross {spec.risk.gross_exposure:.2f} / net {spec.risk.net_exposure:.2f}",
                (
                    f"종목당 최대 {spec.risk.max_name_weight:.1%}",
                    f"섹터당 최대 {spec.risk.max_sector_weight:.1%}",
                ),
            ),
            StrategyExplanationStep(
                "execution",
                f"{spec.execution.timing} · {spec.execution.order_style}",
                (
                    f"수수료 {spec.execution.fee_bps:.1f}bp",
                    f"슬리피지 {spec.execution.slippage_bps:.1f}bp",
                ),
            ),
        )
    )
