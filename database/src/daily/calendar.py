"""거래일 판정 — v3 KIS 휴장 캐시 복사본(`data/calendar/kis_holidays.json`) + 주말.

캐시가 없거나 검증에 실패하면 **영업일 가정**(주말만 제외)으로 간다 — `COLLECT_PLAN.md §4-1` 0단계:
"둘 다 실패면 영업일 가정하고 진행". KRX 빈 응답을 휴장 확정 근거로 쓰는 순환(DEFECT-A-01)을 끊는다.
어느 쪽으로 판정했는지는 `Calendar.source` 에 남는다(조용한 폴백 금지 — python.md 원칙 3).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from typing import Literal

DEFAULT_PATH = os.path.join(os.environ.get("QL_HOME", "/home/kael/quant-ledger"),
                            "data", "calendar", "kis_holidays.json")


@dataclass(frozen=True)
class Calendar:
    holidays: frozenset[str]                       # 'YYYYMMDD'
    source: Literal["kis_cache", "weekend_only"]
    detail: str = ""                               # 폴백 사유(있을 때)

    def is_trading_day(self, d: dt.date) -> bool:
        return d.weekday() < 5 and d.strftime("%Y%m%d") not in self.holidays

    def count_trading_days(self, a: dt.date, b: dt.date) -> int:
        """`a` 초과 `b` 이하 구간의 거래일 수. `b <= a` 면 0.

        유예 카운터(`universe.requested`)가 쓴다 — 달력일로 세면 주말·연휴가 유예를 잡아먹는다
        (DEFECT-A04: 5거래일 약속이 주말에 3거래일, 추석 연휴에 1거래일로 줄었다).
        """
        n = 0
        cur = a
        while cur < b:
            cur += dt.timedelta(days=1)
            if self.is_trading_day(cur):
                n += 1
        return n

    def prev_trading_day(self, d: dt.date, n: int = 1) -> dt.date:
        """d 직전 n번째 거래일(d 자신은 제외)."""
        if n < 1:
            raise ValueError(f"n must be >= 1: n={n} d={d}")
        cur = d
        for _ in range(n):
            cur -= dt.timedelta(days=1)
            while not self.is_trading_day(cur):
                cur -= dt.timedelta(days=1)
        return cur


def load(path: str | os.PathLike[str] = DEFAULT_PATH) -> Calendar:
    """캐시를 읽어 Calendar 를 만든다. 실패하면 weekend_only 폴백(사유는 detail)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        hol = {str(x) for x in data["holidays"]}
        year = str(data["year"])
        # 건수(≥100)·주말 수 검증은 sync_calendar.sh 가 복사 시점에 한다. 여기서는 형식만 본다.
        if any(len(x) != 8 or x[:4] != year for x in hol):
            raise ValueError(f"holiday cache has entries outside year {year}: n={len(hol)} path={path}")
        return Calendar(frozenset(hol), "kis_cache")
    except (OSError, KeyError, ValueError, TypeError) as e:
        return Calendar(frozenset(), "weekend_only",
                        detail=f"holiday cache unusable, assuming business days: path={path} {type(e).__name__}: {e}")
