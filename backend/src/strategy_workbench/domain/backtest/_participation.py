"""참여 기준 → 엔진 유동성 캡의 기준 거래량(spec D7, 검증 랩 V2-02).

ADV 계산과 원화 → 주식 수 환산 규칙의 owner 는 이 파일 하나다. 엔진은 받은 기준 거래량에 참여율을
곱해 내림할 뿐(`floor(기준 거래량 × 참여율)`) ADV 를 모른다.

`adv20` 에서 세션 t 에 체결되는 주문의 기준 거래량은, 판단일(그 종목의 t 직전 거래 행)까지
거래대금이 있는 최근 20개 행의 평균을 판단일 종가로 나눈 주식 수(내림)다. 체결 세션의 값을 쓰지
않으므로 체결 시점에 모르는 정보가 들어가지 않는다. 앞선 행이 없으면(측정 구간 안 신규 상장 등)
0주라 그 세션은 체결하지 않고 잔량을 넘긴다. 첫 세션들의 20행은 측정 구간 앞 워밍업 bar 가
채운다(`participation_history_sessions`). 워밍업은 측정이 아니라 연구 구간 판정을 받지 않는다.

한도는 floor(floor(ADV / 종가) × 참여율)로 두 번 내림한다. 한 번만 내림한 floor(ADV × 참여율 / 종가)
보다 많아야 1주 적고, 대신 두 엔진 코어가 세션 거래량과 같은 정수 산술을 그대로 쓴다.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from datetime import date

from ._models import ParticipationBasis, RunEnvironment

ADV_SESSIONS = 20


def participation_history_sessions(environment: RunEnvironment) -> int:
    """참여 기준 계산에 필요한 측정 구간 앞 워밍업 세션 수."""
    return ADV_SESSIONS if environment.participation_basis is ParticipationBasis.ADV20 else 0


def participation_volumes(
    environment: RunEnvironment,
    bars: Iterable[tuple[date, str, float, float | None]],
) -> dict[tuple[date, str], int] | None:
    """`(세션, 종목, 종가, 거래대금)` 행마다 유동성 캡의 기준 거래량(주).

    `session_volume` 이면 `None` 이다 — 엔진이 체결 세션 거래량을 그대로 쓴다. 워밍업 행을 함께
    넘겨야 첫 세션들의 평균이 20행을 채운다.
    """
    if environment.participation_basis is ParticipationBasis.SESSION_VOLUME:
        return None
    volumes: dict[tuple[date, str], int] = {}
    values: dict[str, deque[float]] = {}
    last_close: dict[str, float] = {}
    for session, security_id, close, trading_value in sorted(bars, key=lambda row: row[0]):
        window = values.setdefault(security_id, deque(maxlen=ADV_SESSIONS))
        previous_close = last_close.get(security_id)
        volumes[(session, security_id)] = (
            math.floor(sum(window) / len(window) / previous_close)
            if window and previous_close
            else 0
        )
        if trading_value is not None:
            window.append(trading_value)
        last_close[security_id] = close
    return volumes
