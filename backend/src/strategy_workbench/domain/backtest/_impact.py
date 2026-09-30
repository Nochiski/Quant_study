"""√ 시장충격 → 엔진 bar 의 충격 척도(spec D7, 검증 랩 V2-03).

계수·변동성 창·기준 거래량 규칙의 owner 는 이 파일 하나다. 엔진은 받은 척도에 체결가와 √체결 수량을
곱할 뿐(`체결가 × 척도 × √Q`) σ·ADV 를 모른다.

체결가 대비 충격 = k × σ일 × √(Q / ADV)(Tóth 외 2011 식 (1)의 Y σ √(Q/V)). 척도는 k × σ일 / √ADV 다.
- k 는 `impact_coefficient`. 기본 1.0 은 Tóth 외 2011 의 "Y 는 1 안팎"과 도쿄증권거래소 전 종목 평균
  0.84(Sato·Kanazawa 2024)를 보수적으로 올린 값이다.
- σ일은 판단일(그 종목의 t 직전 행)까지 최근 20개 종가 대 종가 수익률의 표본 표준편차다. 가격이
  원주가라 자본변동 정산 행(엔진처럼 사건 세션 이후 그 종목의 첫 행, `settlement_multipliers`)에서
  끝나는 수익률은 변동성이 아니므로 뺀다.
- ADV 는 참여 기준 `adv20` 과 같은 주식 수(`adv_shares`, 내림하지 않음)다. 참여 기준과 무관하게 늘
  ADV 를 쓴다 — 문헌의 V 는 일평균 거래량이고, 체결 세션 거래량은 체결 시점에 모르는 값이다.
- 수익률이 2개 미만이면(측정 구간 안 신규 상장의 첫 세션들) 척도는 0 이다. ADV 가 0 인 경우(앞선
  행이 없거나 20행 거래대금이 모두 없거나 0)도 척도 0 이다. 둘 다 충격 없이 체결되는 낙관 쪽이다.
- 체결 한 건의 충격은 체결가의 `MAX_IMPACT_FRACTION` 에서 자른다(엔진이 √Q 를 곱한 뒤 적용).
"""

from __future__ import annotations

import math
import operator
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from ._models import ImpactModel, RunEnvironment
from ._participation import ADV_SESSIONS, adv_shares, participation_history_sessions

VOLATILITY_SESSIONS = 20
# 체결 한 건의 충격 상한(체결가 대비 비율). √ 법칙은 Q/ADV 가 작은 영역의 경험칙이다. 조용하던
# 종목의 체결 세션 거래량이 ADV 의 수백 배가 되는 `session_volume` 참여나 큰 k 에서는 충격이
# 체결가를 넘어 매도 체결가가 0 이하가 된다(run 이 죽는다). 상한은 1 미만이어야 매도가가 양수로
# 남는다. 충격을 깎는 쪽은 낙관 편향이므로 매도가가 양수로 남는 선까지만 자르도록 크게 잡는다.
MAX_IMPACT_FRACTION = 0.99


def cost_history_sessions(environment: RunEnvironment) -> int:
    """비용 계산(참여 기준 ADV·√ 충격 σ)에 필요한 측정 구간 앞 워밍업 세션 수.

    `sqrt` 는 ADV(20행)와 σ(수익률 20개 = 종가 21개)를 함께 쓴다. 참여 기준이 요구하는 수는
    `ADV_SESSIONS` 이하라 이 식에 포함된다.
    """
    if environment.impact_model is ImpactModel.SQRT:
        return max(ADV_SESSIONS, VOLATILITY_SESSIONS + 1)
    return participation_history_sessions(environment)


def impact_scales(
    environment: RunEnvironment,
    bars: Iterable[tuple[date, str, float, float | None]],
    settled: Mapping[tuple[date, str], float],
) -> dict[tuple[date, str], float] | None:
    """`(세션, 종목, 종가, 거래대금)` 행마다 √ 충격 척도 k × σ일 / √ADV.

    `fixed_bps` 면 `None` 이다 — 엔진이 고정 bp 슬리피지를 쓴다. `settled` 는 자본변동 정산 행과
    가격 배수(`settlement_multipliers`)다. 워밍업 행과 워밍업 구간 자본변동을 함께 넘겨야 첫
    세션들의 창이 찬다.
    """
    if environment.impact_model is not ImpactModel.SQRT:
        return None
    rows = sorted(bars, key=lambda row: row[0])
    adv = adv_shares(rows, settled)
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
        if previous_close and key not in settled:
            window.append(close / previous_close - 1)
        last_close[security_id] = close
    return scales


def _sample_stdev(values: Sequence[float]) -> float:
    """표본 표준편차(ddof=1). 행마다 부르므로 `statistics.stdev` 대신 합·제곱합으로 센다."""
    count = len(values)
    total = sum(values)
    squares = sum(map(operator.mul, values, values))
    return math.sqrt(max(squares - total * total / count, 0.0) / (count - 1))
