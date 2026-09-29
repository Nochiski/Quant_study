"""√ 시장충격 → 엔진 bar 의 충격 척도(spec D7, 검증 랩 V2-03).

계수·변동성 창·기준 거래량 규칙의 owner 는 이 파일 하나다. 엔진은 받은 척도에 체결가와 √체결 수량을
곱할 뿐(`체결가 × 척도 × √Q`) σ·ADV 를 모른다.

체결가 대비 충격 = k × σ일 × √(Q / ADV)(Tóth 외 2011 식 (1)의 Y σ √(Q/V)). 척도는 k × σ일 / √ADV 다.
- k 는 `impact_coefficient`. 기본 1.0 은 Tóth 외 2011 의 "Y 는 1 안팎"과 도쿄증권거래소 전 종목 평균
  0.84(Sato·Kanazawa 2024)를 보수적으로 올린 값이다.
- σ일은 판단일(그 종목의 t 직전 행)까지 최근 20개 종가 대 종가 수익률의 표본 표준편차다. 가격이
  원주가라 자본변동 세션에서 끝나는 수익률은 변동성이 아니므로 뺀다.
- ADV 는 참여 기준 `adv20` 과 같은 주식 수(`adv_shares`)다. 참여 기준과 무관하게 늘 ADV 를 쓴다 —
  문헌의 V 는 일평균 거래량이고, 체결 세션 거래량은 체결 시점에 모르는 값이다.
- 수익률이 2개 미만이거나 ADV 가 0주면(측정 구간 안 신규 상장의 첫 세션들) 척도는 0 이다.
"""

from __future__ import annotations

import math
import operator
from collections import deque
from collections.abc import Iterable, Sequence, Set
from datetime import date

from ._models import ImpactModel, RunEnvironment
from ._participation import adv_shares, participation_history_sessions

VOLATILITY_SESSIONS = 20


def cost_history_sessions(environment: RunEnvironment) -> int:
    """비용 계산(참여 기준 ADV·√ 충격 σ)에 필요한 측정 구간 앞 워밍업 세션 수.

    수익률 20개에는 종가 21개가 필요하다.
    """
    if environment.impact_model is ImpactModel.SQRT:
        return VOLATILITY_SESSIONS + 1
    return participation_history_sessions(environment)


def impact_scales(
    environment: RunEnvironment,
    bars: Iterable[tuple[date, str, float, float | None]],
    action_sessions: Set[tuple[date, str]],
) -> dict[tuple[date, str], float] | None:
    """`(세션, 종목, 종가, 거래대금)` 행마다 √ 충격 척도 k × σ일 / √ADV.

    `fixed_bps` 면 `None` 이다 — 엔진이 고정 bp 슬리피지를 쓴다. `action_sessions` 는 자본변동이
    적용된 `(세션, 종목)` 이다. 워밍업 행과 워밍업 구간 자본변동을 함께 넘겨야 첫 세션들의 창이
    찬다.
    """
    if environment.impact_model is not ImpactModel.SQRT:
        return None
    rows = sorted(bars, key=lambda row: row[0])
    adv = adv_shares(rows)
    scales: dict[tuple[date, str], float] = {}
    returns: dict[str, deque[float]] = {}
    last_close: dict[str, float] = {}
    for session, security_id, close, _value in rows:
        key = (session, security_id)
        window = returns.setdefault(security_id, deque(maxlen=VOLATILITY_SESSIONS))
        scales[key] = (
            environment.impact_coefficient * _sample_stdev(window) / math.sqrt(adv[key])
            if len(window) >= 2 and adv[key] > 0
            else 0.0
        )
        previous_close = last_close.get(security_id)
        if previous_close and key not in action_sessions:
            window.append(close / previous_close - 1)
        last_close[security_id] = close
    return scales


def _sample_stdev(values: Sequence[float]) -> float:
    """표본 표준편차(ddof=1). 행마다 부르므로 `statistics.stdev` 대신 합·제곱합으로 센다."""
    count = len(values)
    total = sum(values)
    squares = sum(map(operator.mul, values, values))
    return math.sqrt(max(squares - total * total / count, 0.0) / (count - 1))
