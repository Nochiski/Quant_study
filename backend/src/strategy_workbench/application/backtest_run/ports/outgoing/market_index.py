from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class MarketIndexCloses:
    """지수 이름(원장 `index_daily.index_name`) → 세션 → 종가.

    원천에 그 지수·날짜 행이 없으면 빠진다(값을 지어내지 않는다). 원천 자체가 지수를 주지 않으면
    `unavailable_reason` 에 사용자 대면 한글 사유를 싣고 `closes` 는 비운다.
    """

    closes: Mapping[str, Mapping[date, float]] = field(default_factory=dict)
    unavailable_reason: str | None = None


class MarketIndexPort(Protocol):
    def load_market_index_closes(
        self, index_names: Sequence[str], start: date, end: date
    ) -> MarketIndexCloses: ...
