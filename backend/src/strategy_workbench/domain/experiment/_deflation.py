"""계열 검증 통계 — 시도 횟수로 깎은 샤프(DSR)와 알파 t 기준(검증 랩 spec D8, V4-02).

DSR 은 PSR(`probabilistic_sharpe`)에 기준 샤프만 운 기준선 SR₀ 로 바꿔 넣은 값이다(Bailey·López de
Prado 2014). SR₀ 는 계열에서 N 번 시도했을 때 운만으로 기대되는 최대 샤프이고, N 과 시도 대표 샤프의
분산은 시도 원장이 준다. 모든 샤프는 세션 단위(연율화 전)로 계산한다 — 원장 대표 샤프가 세션
단위라, 실행 지표(연율)는 샤프와 표준오차를 함께 `session_sharpe` 로 되돌린다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import NormalDist, variance

from strategy_workbench.domain.analytics.facade.metrics import (
    MetricScope,
    probabilistic_sharpe,
    session_sharpe,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult
from strategy_workbench.domain.backtest.facade.trials import TrialLedger

# 오일러-마스케로니 상수 γ.
_EULER_MASCHERONI = 0.5772156649015329


def expected_maximum_sharpe(trial_count: int, trial_variance: float) -> float:
    """N 번 시도한 샤프 중 운만으로 기대되는 최대값(세션 단위) SR₀.

    SR₀ = √V · ((1 − γ)Φ⁻¹(1 − 1/N) + γΦ⁻¹(1 − 1/(N·e))). 시도가 하나면 고른 것이 없어 기대값은
    귀무가설의 샤프 0 이다(식은 N = 1 에서 정의되지 않는다).

    Args:
        trial_count: 계열 시도 수 N(1 이상).
        trial_variance: 시도 대표 샤프(세션 단위)의 분산 V.
    """
    if trial_count == 1:
        return 0.0
    normal = NormalDist()
    spread = (1 - _EULER_MASCHERONI) * normal.inv_cdf(1 - 1 / trial_count)
    spread += _EULER_MASCHERONI * normal.inv_cdf(1 - 1 / (trial_count * math.e))
    return math.sqrt(trial_variance) * spread


def deflated_sharpe(
    sharpe: float, standard_error: float, annualization_days: int, ledger: TrialLedger
) -> float | None:
    """진짜 샤프가 운 기준선 SR₀ 보다 클 확률(DSR). 판정할 수 없으면 None.

    N 은 원장 시도 수(재확인·결과 없음 실행은 세지 않는다)이고, V 는 대표 샤프가 있는 시도들의
    대표 샤프(세션 단위) 표본분산(n − 1)이다 — 모분산보다 커서 SR₀ 가 높아지는 보수적인 쪽이다.
    N 이 2 이상인데 대표 샤프가 둘 미만이면 분산을 낼 수 없어 None 이다(분산 0 으로 두면 SR₀ 가 0 이
    돼 DSR 이 PSR 로 부푼다). 표준오차가 0 이하여도 None 이다.

    Args:
        sharpe: 판정할 실행의 `sharpe` 지표(연율).
        standard_error: 같은 실행의 `sharpe_standard_error` 지표(연율).
        annualization_days: 그 실행의 연환산 거래일. 두 지표를 세션 단위로 되돌린다.
        ledger: 그 실행이 속한 계열의 원장.
    """
    return _deflation_evidence(sharpe, standard_error, annualization_days, ledger).value


class DeflatedSharpeUnavailableReason(StrEnum):
    NO_LINEAGE = "no_lineage"
    NO_COUNTED_TRIALS = "no_counted_trials"
    SHARPE_UNAVAILABLE = "sharpe_unavailable"
    STANDARD_ERROR_UNAVAILABLE = "standard_error_unavailable"
    NONPOSITIVE_STANDARD_ERROR = "nonpositive_standard_error"
    INSUFFICIENT_REPRESENTATIVES = "insufficient_representatives"


@dataclass(frozen=True)
class DeflatedSharpeEvidence:
    """조회한 계열 원장을 근거로 한 세션 단위 통계.

    원래 실행 결과·선택 당시 스냅숏을 변경하지 않는다. 대표 행 자체는 기존 원장 조회가 제공한다.
    판본 목록과 미상 수는 분산에 쓰는 대표 샤프가 있는 시도만 대상으로 한다.
    """

    value: float | None = field(
        metadata={
            "description": (
                "현재 계열 원장으로 계산한 DSR(0~1). 저장된 PSR이나 후보 선택 당시 스냅숏이 아니다."
            )
        }
    )
    unavailable_reason: DeflatedSharpeUnavailableReason | None = field(
        metadata={"description": ("값이 없을 때의 단일 판정 사유. 값이 있으면 null이다.")}
    )
    lineage_id: str | None = field(
        metadata={"description": ("계열 합치기를 해소한 생존 계열. 계열 없는 초안은 null이다.")}
    )
    merged_lineage_ids: tuple[str, ...] = field(
        metadata={"description": ("이 생존 계열에 합쳐진 계열 식별자.")}
    )
    trial_count: int | None = field(
        metadata={
            "description": (
                "현재 계열 원장의 시도 수 N. 재확인/결과 없음은 제외하고 파산 시도는 포함한다. "
                "계열이 없으면 null이다."
            )
        }
    )
    representative_count: int = field(
        metadata={
            "description": (
                "샤프가 있는 시도 대표 자료 수. N과 다를 수 있으며 재확인 실행은 포함하지 않는다."
            )
        }
    )
    trial_sharpe_variance: float | None = field(
        metadata={
            "description": (
                "계산한 대표 세션 샤프의 표본분산(n−1). N=1이면 0, 계산 전 부재 판정이면 null이다."
            )
        }
    )
    expected_maximum_sharpe: float | None = field(
        metadata={
            "description": (
                "계산한 운 기준선 SR₀(세션 단위). N=1이면 0, 계산 전 부재 판정이면 null이다."
            )
        }
    )
    session_sharpe: float | None = field(
        metadata={
            "description": (
                "해당 실행의 FULL 연율 샤프를 연환산 거래일로 환산한 세션 샤프. 계산하지 않았으면 "
                "null이다."
            )
        }
    )
    session_standard_error: float | None = field(
        metadata={
            "description": (
                "같은 실행의 FULL 샤프 표준오차를 환산한 세션 단위 값. 계산하지 않았으면 null이다."
            )
        }
    )
    annualization_days: int = field(metadata={"description": ("해당 실행에 기록된 연환산 거래일.")})
    representative_registry_versions: tuple[str, ...] = field(
        metadata={
            "description": (
                "대표 샤프가 있는 시도들의 알려진 지표 판본 집합(정렬). 현재 실행의 판본 목록이 "
                "아니다."
            )
        }
    )
    unknown_registry_count: int = field(
        metadata={
            "description": (
                "대표 샤프가 있지만 지표 판본이 기록되지 않은 시도 수. 판본 일치를 추측하지 않는다."
            )
        }
    )
    registry_mixed: bool = field(
        metadata={
            "description": (
                "알려진 대표 판본들과 해당 실행 판본을 합친 집합에 둘 이상 판본이 있는지 여부."
            )
        }
    )


def _deflation_evidence(
    sharpe: float | None,
    standard_error: float | None,
    annualization_days: int,
    ledger: TrialLedger | None,
    *,
    metric_registry_version: str | None = None,
) -> DeflatedSharpeEvidence:
    """값·부재 사유·입력 근거를 한 번 판정한다. 기존 scalar API도 같은 경로를 쓴다."""
    representatives = (
        []
        if ledger is None
        else [trial for trial in ledger.trials if trial.representative_sharpe is not None]
    )
    sharpes = [
        trial.representative_sharpe
        for trial in representatives
        if trial.representative_sharpe is not None
    ]
    versions = tuple(
        sorted(
            {
                trial.metric_registry_version
                for trial in representatives
                if trial.metric_registry_version is not None
            }
        )
    )
    count = None if ledger is None else ledger.trial_count
    if ledger is None:
        reason = DeflatedSharpeUnavailableReason.NO_LINEAGE
    elif sharpe is None:
        reason = DeflatedSharpeUnavailableReason.SHARPE_UNAVAILABLE
    elif standard_error is None:
        reason = DeflatedSharpeUnavailableReason.STANDARD_ERROR_UNAVAILABLE
    elif count is not None and count < 1:
        reason = DeflatedSharpeUnavailableReason.NO_COUNTED_TRIALS
    elif standard_error <= 0:
        reason = DeflatedSharpeUnavailableReason.NONPOSITIVE_STANDARD_ERROR
    elif count != 1 and len(sharpes) < 2:
        reason = DeflatedSharpeUnavailableReason.INSUFFICIENT_REPRESENTATIVES
    else:
        reason = None
    # 기존 scalar의 조기 종료를 보존한다. 계산 불가 입력에서 분산·단위 변환을 먼저 하지 않는다.
    trial_variance = None
    baseline = None
    session_sr = None
    session_error = None
    value = None
    if reason is None and sharpe is not None and standard_error is not None and count is not None:
        trial_variance = 0.0 if count == 1 else variance(sharpes)
        baseline = expected_maximum_sharpe(count, trial_variance)
        session_sr = session_sharpe(sharpe, annualization_days)
        session_error = session_sharpe(standard_error, annualization_days)
        value = probabilistic_sharpe(session_sr, session_error, baseline)
    compared_versions = set(versions)
    if metric_registry_version is not None:
        compared_versions.add(metric_registry_version)
    return DeflatedSharpeEvidence(
        value=value,
        unavailable_reason=reason,
        lineage_id=None if ledger is None else ledger.lineage_id,
        merged_lineage_ids=() if ledger is None else ledger.merged_lineage_ids,
        trial_count=count,
        representative_count=len(sharpes),
        trial_sharpe_variance=trial_variance,
        expected_maximum_sharpe=baseline,
        session_sharpe=session_sr,
        session_standard_error=session_error,
        annualization_days=annualization_days,
        representative_registry_versions=versions,
        unknown_registry_count=sum(
            trial.metric_registry_version is None for trial in representatives
        ),
        registry_mixed=len(compared_versions) > 1,
    )


def run_deflated_sharpe_evidence(
    result: BacktestRunResult, ledger: TrialLedger | None
) -> DeflatedSharpeEvidence:
    """완료 실행의 전체 구간 샤프와 현재 원장으로 DSR 값·근거를 함께 낸다.

    인라인 여부로 배제하지 않는다. 명시한 계열이 없는 초안만 계열 없음이며, 실행 판본이
    대표 샤프 판본과 다르면 경고한다. 저장된 PSR을 재계산하거나 N=1의 DSR로 대체하지 않는다.
    """
    metrics = {m.metric_id: m.value for m in result.metrics if m.scope is MetricScope.FULL}
    return _deflation_evidence(
        metrics.get("sharpe"),
        metrics.get("sharpe_standard_error"),
        result.manifest.run_spec.annualization_days,
        ledger,
        metric_registry_version=result.manifest.metric_registry_version,
    )


def run_deflated_sharpe(result: BacktestRunResult, ledger: TrialLedger) -> float | None:
    """완료 실행 DSR의 기존 scalar 계약. 값·근거의 단일 판정을 재사용한다."""
    return run_deflated_sharpe_evidence(result, ledger).value


def alpha_t_threshold(trial_count: int) -> float:
    """팩터 회귀 알파 t 값의 기준 — 계열 N 이 20 이상이면 3.0, 아니면 2.0(Harvey·Liu·Zhu 2016)."""
    return 3.0 if trial_count >= 20 else 2.0
