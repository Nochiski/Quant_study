"""거래일 판정 — 판정 연도 파일(`data/calendar/kis_holidays_<YYYY>.json`) + 주말.

연도 파일은 KIS 휴장 조회 직접 갱신(`daily.calendar_refresh`)이 쓴다(결정 Q-2 = N-31 ②). v3 사본은
병행 대조용으로 `data/calendar/v3/` 에만 쌓이고(`sync_calendar.sh`) 판정에는 섞이지 않는다.

달력을 못 읽으면 `CalendarUnavailable` 로 멈춘다 — 예전의 '영업일 가정'(주말만 제외) 폴백(R7)은
평일 휴장을 거래일로 판정해 체인을 돌렸다(K1-9 ⑦). 옛 경로 `kis_holidays.json`(v3 사본 호환용)은
읽지 않는다 — 합집합으로 섞으면 직접 갱신이 지운 휴장일(지정 취소)이 되살아난다.

원천(v3)은 **단일 연도** 파일이라 12월에 이듬해 판으로 교체되면 그해 성탄절·연말이 목록에서 사라졌다
(DEFECT-A07). 그래서 연도별 파일을 합쳐 읽는다. 연도 파일이 덮지 않는 연도를 물으면 주말만 거르는
폴백으로 눙치지 않고 `KeyError` 를 낸다 — 신정·설 연휴를 통째로 거래일로 판정하느니 체인을 세우는 쪽이 낫다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from typing import Literal

DEFAULT_PATH = os.path.join(os.environ.get("QL_HOME", os.path.expanduser("~/quant-ledger")),
                            "data", "calendar", "kis_holidays.json")
# 판정 연도 파일 이름 — 읽기(`load`)와 쓰기(`calendar_refresh`)가 함께 쓰는 정본
YEAR_FILE = "kis_holidays_{year}.json"
_YEAR_FILE_RE = re.compile(r"^kis_holidays_(\d{4})\.json$")
# 세션 예외 표(정규장 시각이 바뀌는 거래일 — 수능일 등). 운영 표는 판정 연도 파일과 같은 디렉터리
# (`data/calendar/`)에 두고, 저장소 기본 표는 배포로 코드와 함께 나가는 `config/calendar/` 에 둔다
SESSION_EXCEPTIONS_FILE = "session_exceptions.json"
DEFAULT_SESSION_EXCEPTIONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "calendar", SESSION_EXCEPTIONS_FILE)


class CalendarUnavailable(RuntimeError):
    """판정 달력을 읽지 못했다 — 호출부는 영업일을 가정하지 말고 멈춘다(K1-9 ⑦)."""


@dataclass(frozen=True)
class Calendar:
    holidays: frozenset[str]                       # 'YYYYMMDD'
    source: Literal["kis_cache"]
    years: frozenset[str] = frozenset()            # 연도 파일이 덮는 연도. 비어 있으면 연도 검사를 하지 않는다

    def _require_year(self, d: dt.date) -> None:
        year = str(d.year)
        if self.years and year not in self.years:
            raise KeyError(
                f"holiday cache does not cover {year}: have={sorted(self.years)} date={d.isoformat()} "
                f"— calendar_refresh 가 그 해 판을 아직 게시하지 못했다(이듬해 판 기한 12-15). 주말만 거르는 폴백으로 "
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


def read_year_files(directory: str | os.PathLike[str]) -> dict[str, frozenset[str]]:
    """디렉터리의 `kis_holidays_<YYYY>.json` 을 연도별 휴장 집합으로 읽는다(형식만 검사).

    건수·주말 검증은 쓰는 쪽(`calendar_refresh`·`sync_calendar.sh`)이 쓰기 전에 한다. 여기서는 파일 이름의
    연도 = 내용의 `year` 이고 모든 항목이 그 연도의 YYYYMMDD 인지만 본다. 연도 파일이 하나도 없거나
    한 파일이라도 읽기·형식 오류면 `CalendarUnavailable` — 메시지에 문제의 연도 파일 전체 경로를 싣는다.
    """
    d = os.fspath(directory)
    names = sorted(n for n in os.listdir(d) if _YEAR_FILE_RE.match(n))
    if not names:
        raise CalendarUnavailable(f"no {YEAR_FILE.format(year='<YYYY>')} under {d} — 판정 달력 없음")
    out: dict[str, frozenset[str]] = {}
    for name in names:
        full = os.path.abspath(os.path.join(d, name))
        try:
            with open(full, encoding="utf-8") as f:
                data = json.load(f)
            year = str(data["year"])
            m = _YEAR_FILE_RE.match(name)
            if m is None or m.group(1) != year:
                raise ValueError(f"year in file name != content: name={name} content_year={year}")
            one = frozenset(str(x) for x in data["holidays"])
            if any(len(x) != 8 or x[:4] != year for x in one):
                raise ValueError(f"holiday file has entries outside year {year}: n={len(one)}")
        except (OSError, ValueError, KeyError, TypeError) as e:
            # 어느 연도 파일이 깨졌는지 운영자가 바로 알게 전체 경로를 싣는다(error-messages.md)
            raise CalendarUnavailable(f"holiday year file unusable: path={full} {type(e).__name__}: {e}") from e
        out[year] = one
    return out


def load(path: str | os.PathLike[str] = DEFAULT_PATH) -> Calendar:
    """판정 연도 파일을 합쳐 Calendar 를 만든다. 못 읽으면 `CalendarUnavailable`(영업일 가정 없음).

    `path` 는 파일이어도 디렉터리여도 된다 — 어느 쪽이든 **그 디렉터리의 `kis_holidays_<YYYY>.json`**
    전부를 합쳐 읽는다(연 경계, DEFECT-A07). 옛 호출부(`load(".../kis_holidays.json")`)는 디렉터리만 쓴다.
    """
    p = os.fspath(path)
    directory = p if os.path.isdir(p) else (os.path.dirname(p) or ".")
    try:
        by_year = read_year_files(directory)
    except (CalendarUnavailable, OSError, KeyError, ValueError, TypeError) as e:
        raise CalendarUnavailable(
            f"holiday calendar unusable: dir={os.path.abspath(directory)} {type(e).__name__}: {e} — 영업일 "
            f"가정으로 넘어가지 않는다(K1-9 ⑦, N-31 ②)") from e
    return Calendar(frozenset().union(*by_year.values()), "kis_cache", years=frozenset(by_year))


def _read_session_table(path: str) -> dict[str, str]:
    """세션 예외 표 하나 — `{"days": {YYYYMMDD: 사유}}`. 형식 오류는 `CalendarUnavailable`(경로를 싣는다)."""
    full = os.path.abspath(path)
    try:
        with open(full, encoding="utf-8") as f:
            days = json.load(f)["days"]
        if not isinstance(days, dict):
            raise TypeError(f"days must be an object: got {type(days).__name__}")
        out: dict[str, str] = {}
        for k, v in days.items():
            dt.datetime.strptime(str(k), "%Y%m%d")            # 없는 날(20261131)도 여기서 걸린다
            if len(str(k)) != 8 or not isinstance(v, str) or not v.strip():
                raise ValueError(f"entry must be YYYYMMDD → non-empty reason: {k!r}: {v!r}")
            out[str(k)] = v
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CalendarUnavailable(f"session_exceptions table unusable: path={full} "
                                  f"{type(e).__name__}: {e}") from e
    return out


def load_session_exceptions(directory: str | os.PathLike[str],
                            default: str | os.PathLike[str] = DEFAULT_SESSION_EXCEPTIONS
                            ) -> dict[str, str]:
    """정규장 시각이 바뀌는 거래일 `{YYYYMMDD: 사유}` — 장 마감 직후 수집(15:41~16:00)을 하지 않는 날.

    저장소 기본 표(`default`, 배포로 코드와 함께 나간다)와 운영 표(`<directory>/session_exceptions.json`,
    있을 때만)를 합친다. 같은 날짜면 운영 표의 사유. 합치는 이유: 운영 표가 기본 표를 통째로 가리면
    나중에 기본 표에 더한 날(이듬해 수능일)을 놓친다. 기본 표가 없거나 어느 표든 형식이 틀리면
    `CalendarUnavailable` — 빈 표로 넘어가면 시각이 바뀐 날 장중 값을 첫 관측으로 굳힌다(P1).
    """
    out = _read_session_table(os.fspath(default))
    ops = os.path.join(os.fspath(directory), SESSION_EXCEPTIONS_FILE)
    if os.path.exists(ops):
        out.update(_read_session_table(ops))
    return out
