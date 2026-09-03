"""소스 무관 자본변동 검출: 상장주식수 변화로 액면분할·병합을 찾는다.

규칙 (스펙 `2026-08-29-data-followups-design.md` 결정 1):
- 연속 세션의 상장주식수 비율 r = cur / prev. r ≥ 1.5 또는 r ≤ 1/1.5면 사건.
- 종가가 둘 다 양수이고 p = cur.close / prev.close에 대해 0.5 ≤ p × r ≤ 2면
  가격이 반비례로 확인된 것 → SPLIT(r > 1) / REVERSE_SPLIT(r < 1).
- 확인되지 않으면 SHARE_COUNT_CHANGE — 엔진은 기록·알림만 하고 포지션을 건드리지 않는다.
- 사건 시각은 새 주식 수가 처음 나타난 세션. 거래정지 행도 입력에 포함한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from backtest_engine.types.events import CorporateActionEvent, CorporateActionType
from backtest_engine.types.instruments import InstrumentId

SHARE_CHANGE_THRESHOLD = Decimal("1.5")
PRICE_CONFIRMATION_BAND = (Decimal("0.5"), Decimal(2))


@dataclass(frozen=True)
class ShareCountRow:
    """검출에 필요한 최소 정보. 세션 오름차순으로 넘겨야 한다."""

    session: date
    listed_shares: int
    close: int
    volume: int


def detect_share_count_events(
    rows: list[ShareCountRow], instrument: InstrumentId
) -> tuple[CorporateActionEvent, ...]:
    events: list[CorporateActionEvent] = []
    for previous, current in zip(rows, rows[1:], strict=False):
        if current.session <= previous.session:
            raise ValueError(
                f"share count rows must be sorted by session — instrument={instrument.symbol} "
                f"previous={previous.session} got={current.session}"
            )
        if previous.listed_shares <= 0 or current.listed_shares <= 0:
            continue
        ratio = Decimal(current.listed_shares) / Decimal(previous.listed_shares)
        if 1 / SHARE_CHANGE_THRESHOLD < ratio < SHARE_CHANGE_THRESHOLD:
            continue
        confirmed = False
        if previous.close > 0 and current.close > 0:
            price_ratio = Decimal(current.close) / Decimal(previous.close)
            product = price_ratio * ratio
            confirmed = PRICE_CONFIRMATION_BAND[0] <= product <= PRICE_CONFIRMATION_BAND[1]
        if not confirmed:
            action_type = CorporateActionType.SHARE_COUNT_CHANGE
        elif ratio > 1:
            action_type = CorporateActionType.SPLIT
        else:
            action_type = CorporateActionType.REVERSE_SPLIT
        events.append(
            CorporateActionEvent(
                ts=datetime.combine(current.session, datetime.min.time()),
                instrument=instrument,
                action_type=action_type,
                ratio=ratio,
                detail=(
                    f"listed_shares {previous.listed_shares} -> {current.listed_shares} "
                    f"(x{ratio:.6f}), close {previous.close} -> {current.close}, "
                    f"sessions {previous.session} -> {current.session}, "
                    f"price_confirmed={confirmed}"
                ),
            )
        )
    return tuple(events)
