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
from datetime import date

from ._models import ParticipationBasis, RunEnvironment

ADV_SESSIONS = 20


def participation_history_sessions(environment: RunEnvironment) -> int:
    """참여 기준 계산에 필요한 측정 구간 앞 워밍업 세션 수."""
    return ADV_SESSIONS if environment.participation_basis is ParticipationBasis.ADV20 else 0


def participation_volumes(
    environment: RunEnvironment,
    bars: Iterable[tuple[date, str, float, float | None]],
    settled: Mapping[tuple[date, str], float] | None = None,
) -> dict[tuple[date, str], int] | None:
    """`(세션, 종목, 종가, 거래대금)` 행마다 유동성 캡의 기준 거래량(주).

    `session_volume` 이면 `None` 이다 — 엔진이 체결 세션 거래량을 그대로 쓴다. 워밍업 행을 함께
    넘겨야 첫 세션들의 평균이 20행을 채운다. `settled` 는 `settlement_multipliers` 다.
    """
    if environment.participation_basis is ParticipationBasis.SESSION_VOLUME:
        return None
    return {key: math.floor(shares) for key, shares in adv_shares(bars, settled).items()}


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


def adv_shares(
    bars: Iterable[tuple[date, str, float, float | None]],
    settled: Mapping[tuple[date, str], float] | None = None,
) -> dict[tuple[date, str], float]:
    """`(세션, 종목, 종가, 거래대금)` 행마다 판단일까지 20행 평균 거래대금 ÷ 판단일 종가(주).

    참여 기준 `adv20` 과 √ 충격(`_impact.py`)이 같은 ADV 를 쓴다. 내림하지 않는다 — 참여 한도는
    `participation_volumes` 가 내림하고, √ 안의 분모는 1주 미만도 그대로 쓴다. 앞선 행이 없거나 창의
    거래대금이 모두 없으면(None) 0주다. 자본변동 정산 행(`settled`)에서는 판단일 종가를 가격 배수로
    나눠 정산 뒤 주식 단위로 센다 — 그 행의 주문·체결·거래량이 정산 뒤 단위다.
    """
    multipliers = settled or {}
    volumes: dict[tuple[date, str], float] = {}
    values: dict[str, deque[float]] = {}
    last_close: dict[str, float] = {}
    for session, security_id, close, trading_value in sorted(bars, key=lambda row: row[0]):
        key = (session, security_id)
        window = values.setdefault(security_id, deque(maxlen=ADV_SESSIONS))
        previous_close = last_close.get(security_id)
        volumes[key] = (
            sum(window) / len(window) / (previous_close / multipliers.get(key, 1.0))
            if window and previous_close
            else 0.0
        )
        if trading_value is not None:
            window.append(trading_value)
        last_close[security_id] = close
    return volumes
