"""참여 기준 → 엔진 유동성 캡의 기준 거래량(spec D7, 검증 랩 V2-02).

ADV 계산과 원화 → 주식 수 환산 규칙의 owner 는 이 파일 하나다. 엔진은 받은 기준 거래량에 참여율을
곱해 내림할 뿐(`floor(기준 거래량 × 참여율)`) ADV 를 모른다.

`adv20` 에서 세션 t 에 체결되는 주문의 기준 거래량은, 판단일(그 종목의 t 직전 거래 행)까지
거래대금이 있는 최근 20개 행의 평균을 판단일 종가로 나눈 주식 수(내림)다. 체결 세션의 값을 쓰지
않으므로 체결 시점에 모르는 정보가 들어가지 않는다. 거래대금이 있는 앞선 행이 20개가 안 되면(측정
구간 안 신규 상장 등) 0주라 그 세션은 체결하지 않고 잔량을 넘긴다 — 상장 초기 며칠의 큰 거래대금으로
한도를 부풀리지 않는 보수 쪽이다(#342). 첫 세션들의 20행은 측정 구간 앞 워밍업 bar 가
채운다(`ADV_SESSIONS` 가 필요한 행 수, 읽을 세션 수는 여유를 더한
`_cost_history.py` 의 `cost_history_sessions`). 워밍업은 측정이 아니라 연구 구간 판정을 받지 않는다.

한도는 floor(floor(ADV / 종가) × 참여율)로 두 번 내림한다. 한 번만 내림한 floor(ADV × 참여율 / 종가)
보다 많아야 1주 적고, 대신 두 엔진 코어가 세션 거래량과 같은 정수 산술을 그대로 쓴다.

자본변동(분할·병합)은 엔진처럼 그 종목의 사건 세션 이후 첫 행(정산 행)에 둔다 — 원장의 사건 세션이
거래정지라 bar 가 없으면 정지가 끝난 뒤 첫 거래 행이다(`settlement_multipliers`, #339). 정산 행의
판단일 종가는 분할 전 원주가라, 그 행의 ADV 는 종가를 정산 뒤 주식 단위로 바꿔 나눈다. √ 충격 σ 는
정산 행에서 끝나는 수익률을 뺀다(`_impact.py`).
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from ._models import ParticipationBasis, RunEnvironment

ADV_SESSIONS = 20
# 가격이 주식 수에 반비례해 바뀌는 사건만 판단일 종가와 벤치마크 단위를 보정한다.
PRICE_ADJUSTING_ACTIONS = frozenset({"split", "reverse_split"})


@dataclass(frozen=True)
class AdvObservation:
    shares: float
    available_rows: int


def participation_volumes(
    environment: RunEnvironment,
    observations: Mapping[tuple[date, str], AdvObservation],
) -> dict[tuple[date, str], int] | None:
    """참여 한도는 거래대금이 있는 앞선 행이 ADV 창을 채웠을 때만 연다."""
    if environment.participation_basis is ParticipationBasis.SESSION_VOLUME:
        return None
    return {
        key: math.floor(item.shares) if item.available_rows >= ADV_SESSIONS else 0
        for key, item in observations.items()
    }


def settlement_multipliers(
    bars: Iterable[tuple[date, str, float, float | None]],
    actions: Iterable[tuple[date, str, float]],
) -> dict[tuple[date, str], float]:
    """자본변동 `(사건 세션, 종목, 가격 배수)` 를 엔진처럼 그 종목의 사건 세션 이후 첫 행(정산 행)에
    둔다. 값은 정산 행에서 판단일 종가를 정산 뒤 주식 단위로 바꾸는 배수다.

    가격 배수는 구주 1주당 신주 수이고, 가격이 반비례로 확인되지 않은 사건은 1 이다. 같은 행에
    정산되는 사건은 곱한다. 사건 세션 뒤에 그 종목의 행이 없으면 빠진다.
    """
    sessions: dict[str, list[date]] = {}
    for session, security_id, _close, _value in bars:
        sessions.setdefault(security_id, []).append(session)
    for dates in sessions.values():
        dates.sort()
    settled: dict[tuple[date, str], float] = {}
    for session, security_id, multiplier in actions:
        dates = sessions.get(security_id, [])
        index = bisect_left(dates, session)
        if index < len(dates):
            key = (dates[index], security_id)
            settled[key] = settled.get(key, 1.0) * multiplier
    return settled


def adv_observations(
    bars: Iterable[tuple[date, str, float, float | None]],
    settled: Mapping[tuple[date, str], float],
) -> dict[tuple[date, str], AdvObservation]:
    """정렬된 행을 한 번 읽어 판단일까지 ADV와 관측 개수를 함께 낸다.

    두 소비자는 같은 ADV를 읽되 참여한도는 완전창, 충격은 최소 한 행을 요구한다.
    합산은 기존 좌→우 순서를 유지하여 부동소수 반올림 의미를 바꾸지 않는다.
    """
    observations: dict[tuple[date, str], AdvObservation] = {}
    values: dict[str, deque[float]] = {}
    last_close: dict[str, float] = {}
    for session, security_id, close, trading_value in bars:
        key = (session, security_id)
        window = values.setdefault(security_id, deque(maxlen=ADV_SESSIONS))
        previous_close = last_close.get(security_id)
        observations[key] = AdvObservation(
            sum(window) / len(window) / (previous_close / settled.get(key, 1.0))
            if window and previous_close
            else 0.0,
            len(window),
        )
        if trading_value is not None:
            window.append(trading_value)
        last_close[security_id] = close
    return observations
