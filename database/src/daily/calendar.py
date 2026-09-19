"""거래일 판정 — v3 KIS 휴장 캐시 복사본(`data/calendar/kis_holidays*.json`) + 주말.

캐시가 없거나 검증에 실패하면 **영업일 가정**(주말만 제외)으로 간다 — `COLLECT_PLAN.md §4-1` 0단계:
"둘 다 실패면 영업일 가정하고 진행". KRX 빈 응답을 휴장 확정 근거로 쓰는 순환(DEFECT-A-01)을 끊는다.
어느 쪽으로 판정했는지는 `Calendar.source` 에 남는다(조용한 폴백 금지 — python.md 원칙 3).

원천(v3)은 **단일 연도** 파일이라 12월에 이듬해 판으로 교체되면 그해 성탄절·연말이 목록에서 사라졌다
(DEFECT-A07). 그래서 `sync_calendar.sh` 가 연도별 파일로 쌓고 `load()` 는 디렉터리의 `kis_holidays*.json`
을 전부 합친다. 캐시가 덮지 않는 연도를 물으면 주말만 거르는 폴백으로 눙치지 않고 `KeyError` 를 낸다 —
신정·설 연휴를 통째로 거래일로 판정하느니 체인을 세우는 쪽이 낫다.
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
    years: frozenset[str] = frozenset()            # 캐시가 덮는 연도. 비어 있으면 연도 검사를 하지 않는다

    def _require_year(self, d: dt.date) -> None:
        year = str(d.year)
        if self.years and year not in self.years:
            raise KeyError(
                f"holiday cache does not cover {year}: have={sorted(self.years)} date={d.isoformat()} "
                f"— sync_calendar.sh 가 그 해 파일을 아직 받지 못했다. 주말만 거르는 폴백으로 "
                f"넘어가지 않는다(DEFECT-A07: 신정·설 연휴가 거래일로 판정된다)")

    def is_trading_day(self, d: dt.date) -> bool:
        self._require_year(d)
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
    """캐시를 읽어 Calendar 를 만든다. 실패하면 weekend_only 폴백(사유는 detail).

    `path` 는 파일이어도 디렉터리여도 된다 — 어느 쪽이든 **그 디렉터리의 `kis_holidays*.json` 전부**를
    합쳐 읽는다(연 경계, DEFECT-A07). 옛 호출부(`load(".../kis_holidays.json")`)는 그대로 동작한다.
    """
    p = os.fspath(path)
    directory = p if os.path.isdir(p) else (os.path.dirname(p) or ".")
    try:
        names = sorted(n for n in os.listdir(directory)
                       if n.startswith("kis_holidays") and n.endswith(".json"))
        if not names:
            raise FileNotFoundError(f"no kis_holidays*.json under {directory}")
        hol: set[str] = set()
        years: set[str] = set()
        for name in names:
            full = os.path.join(directory, name)
            with open(full, encoding="utf-8") as f:
                data = json.load(f)
            one = {str(x) for x in data["holidays"]}
            year = str(data["year"])
            # 건수(≥100)·주말 수 검증은 sync_calendar.sh 가 복사 시점에 한다. 여기서는 형식만 본다.
            if any(len(x) != 8 or x[:4] != year for x in one):
                raise ValueError(f"holiday cache has entries outside year {year}: "
                                 f"n={len(one)} path={full}")
            hol |= one
            years.add(year)
        return Calendar(frozenset(hol), "kis_cache", years=frozenset(years))
    except (OSError, KeyError, ValueError, TypeError) as e:
        return Calendar(frozenset(), "weekend_only",
                        detail=f"holiday cache unusable, assuming business days: path={path} {type(e).__name__}: {e}")
