"""비용 모델별 입력 창 요구를 합성하는 워밍업 세션 수의 정본."""

from ._impact import VOLATILITY_SESSIONS
from ._models import ImpactModel, ParticipationBasis, RunEnvironment
from ._participation import ADV_SESSIONS

# 워밍업 여유 배수. 창이 요구하는 세션 수의 이 배를 읽어, 워밍업 안 거래정지로 행이 빈 날을 더 앞
# 행이 메운다 — 같은 세션·종목의 참여 한도·σ 가 측정 시작일에 따라 갈리지 않게 한다(#396 리뷰
# P2-2). 정지가 여유보다 길면 첫 세션들의 창이 차지 않는다(알려진 한계).
WARMUP_MARGIN_FACTOR = 2


def cost_history_sessions(environment: RunEnvironment) -> int:
    """비용 계산(참여 기준 ADV·√ 충격 σ)을 위해 읽을 측정 구간 앞 워밍업 세션 수. 워밍업 여유의
    정본이다.

    창이 요구하는 행 수는 참여 기준 `adv20` 이 20행(`ADV_SESSIONS`), `sqrt` 가
    ADV(20행)와 σ(수익률 20개 = 종가 21개)다. 그 최대의 `WARMUP_MARGIN_FACTOR` 배를 읽는다.
    """
    needed = ADV_SESSIONS if environment.participation_basis is ParticipationBasis.ADV20 else 0
    if environment.impact_model is ImpactModel.SQRT:
        needed = max(needed, ADV_SESSIONS, VOLATILITY_SESSIONS + 1)
    return WARMUP_MARGIN_FACTOR * needed
