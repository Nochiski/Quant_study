"""계열 검증 통계 — 시도 횟수로 깎은 샤프(DSR)와 알파 t 기준(검증 랩 spec D8, V4-02).

DSR 은 PSR(`probabilistic_sharpe`)에 기준 샤프만 운 기준선 SR₀ 로 바꿔 넣은 값이다(Bailey·López de
Prado 2014). SR₀ 는 계열에서 N 번 시도했을 때 운만으로 기대되는 최대 샤프이고, N 과 시도 대표 샤프의
분산은 시도 원장이 준다. 모든 샤프는 세션 단위(연율화 전)로 계산한다 — 원장 대표 샤프가 세션
단위라, 실행 지표(연율)는 샤프와 표준오차를 함께 `session_sharpe` 로 되돌린다.
"""

from __future__ import annotations

import math
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
    trial_count = ledger.trial_count
    trial_sharpes = [
        trial.representative_sharpe
        for trial in ledger.trials
        if trial.representative_sharpe is not None
    ]
    if trial_count < 1 or standard_error <= 0:
        return None
    if trial_count == 1:
        trial_variance = 0.0
    elif len(trial_sharpes) < 2:
        return None
    else:
        trial_variance = variance(trial_sharpes)
    return probabilistic_sharpe(
        session_sharpe(sharpe, annualization_days),
        session_sharpe(standard_error, annualization_days),
        expected_maximum_sharpe(trial_count, trial_variance),
    )


def run_deflated_sharpe(result: BacktestRunResult, ledger: TrialLedger) -> float | None:
    """완료된 실행 하나의 DSR. 분자는 그 실행의 전체 구간 `sharpe`·`sharpe_standard_error` 지표다.

    둘 중 하나라도 비면 None 이다. 후보 선택 스냅숏과 결과 화면(V5-02)이 이 함수 하나를 쓴다.
    """
    metrics = {m.metric_id: m.value for m in result.metrics if m.scope is MetricScope.FULL}
    sharpe, error = metrics.get("sharpe"), metrics.get("sharpe_standard_error")
    if sharpe is None or error is None:
        return None
    return deflated_sharpe(sharpe, error, result.manifest.annualization_days, ledger)


def alpha_t_threshold(trial_count: int) -> float:
    """팩터 회귀 알파 t 값의 기준 — 계열 N 이 20 이상이면 3.0, 아니면 2.0(Harvey·Liu·Zhu 2016)."""
    return 3.0 if trial_count >= 20 else 2.0
