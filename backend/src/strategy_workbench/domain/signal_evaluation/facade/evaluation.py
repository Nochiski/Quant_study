"""선정 입력과 분리한 미래 가격 라벨·날짜별 순위 상관의 공개 계약."""

from strategy_workbench.domain.signal_evaluation._evaluation import evaluate_signal
from strategy_workbench.domain.signal_evaluation._models import (
    AdjustedCloseObservation,
    AdjustmentGapObservation,
    DailyRankIC,
    ExclusionCount,
    ListingInterval,
    PriceLabel,
    PriceLabelSpec,
    PriceLabelStatus,
    RankICStatus,
    SignalEvaluationResult,
    SignalEvaluationSample,
    SignalStatus,
)

__all__ = [
    "AdjustedCloseObservation",
    "AdjustmentGapObservation",
    "DailyRankIC",
    "ExclusionCount",
    "ListingInterval",
    "PriceLabel",
    "PriceLabelSpec",
    "PriceLabelStatus",
    "RankICStatus",
    "SignalEvaluationResult",
    "SignalEvaluationSample",
    "SignalStatus",
    "evaluate_signal",
]
