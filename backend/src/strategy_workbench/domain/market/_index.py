"""홈 시장 블록의 지수 카드: 기준 장의 값과 최근 종가 선.

지수 이름의 정본은 `domain.backtest`의 `MARKET_REFERENCE_INDICES`(코스피·코스닥)다. 여기서는
원장 행을 카드 값으로 묶기만 하고 새로 계산하지 않는다(등락·등락률·거래대금은 원장 값 그대로).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

# 3개월 선: 기준 장을 포함한 최근 세션 수(한 달 ≈ 21세션).
INDEX_HISTORY_SESSIONS = 63


@dataclass(frozen=True)
class IndexBar:
    """원장 `index_daily` 한 행. 값이 없는 칸은 None 그대로 둔다."""

    session: date
    close: float
    change: float | None
    change_pct: float | None
    value_krw: int | None


@dataclass(frozen=True)
class IndexPoint:
    session: date
    close: float


@dataclass(frozen=True)
class IndexSummary:
    index_id: str
    index_name: str
    session: date
    close: float
    change: float | None
    change_pct: float | None
    value_krw: int | None
    history: tuple[IndexPoint, ...]
    # 선(history) 안의 종가 최고·최저. 당일 고가·저가가 아니다.
    high_close: float
    low_close: float


def summarize_index(
    index_id: str, index_name: str, bars: Sequence[IndexBar]
) -> IndexSummary | None:
    """세션 순서로 정렬한 최근 `INDEX_HISTORY_SESSIONS`개로 카드를 만든다. 행이 없으면 None."""
    ordered = sorted(bars, key=lambda bar: bar.session)[-INDEX_HISTORY_SESSIONS:]
    if not ordered:
        return None
    last = ordered[-1]
    closes = [bar.close for bar in ordered]
    return IndexSummary(
        index_id=index_id,
        index_name=index_name,
        session=last.session,
        close=last.close,
        change=last.change,
        change_pct=last.change_pct,
        value_krw=last.value_krw,
        history=tuple(IndexPoint(bar.session, bar.close) for bar in ordered),
        high_close=max(closes),
        low_close=min(closes),
    )
