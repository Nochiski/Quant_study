"""판정 달력 → v3·uni 휴장 파일 내보내기(컷오버 트랙 T-24, PR QL-Q).

컷오버로 v3 의 KIS 휴장 갱신(연 1회 12-30 이듬해 판·매월 1일 점검)을 끄면 v3 `data/.kis_holidays.json` 이 멈춘다.
그 파일을 quant-ledger 판정 달력(`data/calendar/kis_holidays_<YYYY>.json`, `daily.calendar_refresh` 가 갱신)에서
v3 가 쓰던 형식 그대로 만든다. 대상 경로는 `--target` 으로만 받는다(서버 경로를 코드에 적지 않는다).

형식 — v3 `backend/clients/kis/holiday.py` `fetch_year_holidays` 의 쓰기와 같은 키 5개, **단일 연도**:
  {"year": <int>, "fetched_at": ISO, "last_reviewed_at": ISO, "holidays": ["YYYYMMDD", ... 정렬], "review_history": []}
  `holidays` = 그해 휴장(opnd_yn='N') 전부(토·일 포함). 판정 연도 파일의 `holidays` 와 의미가 같다.
읽는 쪽:
  - `year`(int) = 조회 연도일 때만 `holidays` 를 쓴다: v3 `check_holiday`·`daily_pipeline._holiday_from_cache`,
    uni `unitelegram/sources/holiday.py` `_kael_is_trading_day`(다르면 None → 자체 캐시).
  - `holidays` 만 읽는다: v3 `scripts/check_today_business.py`·`news/events/holidays.py`, kael-wiki `lint.sh`.

연도 = 실행 시각(KST)의 해. v3 는 12-30 에 이듬해 판으로 바꿔 12-30·31 조회가 연도 불일치가 됐다(12-31 연말휴장을
영업일로 판정, `check_holiday` 는 캐시를 놓쳐 KIS 를 부르고 파일을 옛 형식으로 덮음) — 그 교체 시점은 따르지 않는다.
호출 조건(연결 PR 의 전제): 연도가 실행 시각을 따르므로 **매일(주말·휴장 포함) KST 00:00 이후, v3 20:05 체인 전에
1회 이상** 돌아야 한다 — 거래일에만 도는 체인에 넣으면 1-1 에 파일이 지난해 판으로 남아 v3 가 1-1 을 영업일로 보고
`check_holiday` 가 KIS 를 부른다. v3 서버는 UTC 라 1-1 00:00~09:00 KST 에는 v3 리더의 `date.today()` 가 12-31 이어서
연도가 어긋나지만, 지금 v3 도 12-30 에 판을 바꾸므로 회귀가 아니고 그 창에 `check_holiday` 를 부르는 v3 잡도 없다.

판정 달력을 못 읽거나(`calendar.read_year_files` 실패 — 연도 파일 하나라도 깨짐) 올해를 덮지 않으면 대상 파일을
건드리지 않고 rc 2(P1). 쓰기는 같은 디렉터리 임시 파일 → `os.replace`(`calendar_refresh._atomic_write_json`)라
중간에 실패해도 반쪽이 남지 않고, 대상 파일의 권한은 그대로 둔다. rc: 0 정상 · 2 실패(대상 무변경).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import stat
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from daily import calendar as cal_mod
from daily import calendar_refresh as cr


class ExportStatus(Enum):
    OK = "ok"
    CALENDAR_UNAVAILABLE = "calendar_unavailable"   # 판정 연도 파일이 없거나 하나라도 깨졌다
    YEAR_NOT_COVERED = "year_not_covered"           # 판정 달력에 올해 판이 없다
    TARGET_DIR_MISSING = "target_dir_missing"       # `--target` 의 디렉터리가 없다(경로 오타)


@dataclass(frozen=True)
class ExportResult:
    status: ExportStatus
    year: int
    holidays: tuple[str, ...] = ()   # 쓴 휴장 목록 — OK 일 때만 채운다
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ExportStatus.OK


def _now_kst() -> dt.datetime:
    """KST 벽시계(시간대 없는 값) — v3 가 `fetched_at` 에 쓰던 `datetime.now().isoformat()` 과 같은 모양."""
    return dt.datetime.now(cr.KST).replace(tzinfo=None)


def export(cal_dir: Path, target: Path, now: dt.datetime) -> ExportResult:
    """`now` 의 해 판정 휴장 목록을 v3 형식으로 `target` 에 원자 교체한다. OK 가 아니면 대상은 건드리지 않았다.

    쓰기 도중의 OS 오류(디스크 등)는 예외로 올라간다 — 그때도 원자 교체라 대상은 옛 내용 그대로다.
    """
    year = now.year
    try:
        # 판정 달력 읽기 정본(`calendar.load` 도 이것을 쓴다) — 연도 파일 하나라도 깨지면 실패(K1-9 ⑦)
        by_year = cal_mod.read_year_files(cal_dir)
    except (cal_mod.CalendarUnavailable, OSError) as e:
        return ExportResult(ExportStatus.CALENDAR_UNAVAILABLE, year, detail=f"{type(e).__name__}: {e}")
    if str(year) not in by_year:
        return ExportResult(ExportStatus.YEAR_NOT_COVERED, year,
                            detail=f"판정 달력이 {year} 년을 덮지 않는다: have={sorted(by_year)} dir={cal_dir}. "
                                   f"{cr.RECOVERY_HINT}")
    if not target.parent.is_dir():
        # 경로 오타면 v3 가 읽지 않는 곳에 조용히 파일이 생긴다 — 디렉터리를 만들지 않는다
        return ExportResult(ExportStatus.TARGET_DIR_MISSING, year, detail=f"대상 디렉터리가 없다: {target.parent}")
    holidays = tuple(sorted(by_year[str(year)]))
    ts = now.isoformat(timespec="seconds")
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    cr._atomic_write_json(target, {"year": year, "fetched_at": ts, "last_reviewed_at": ts,
                                   "holidays": list(holidays), "review_history": []}, mode=mode)
    return ExportResult(ExportStatus.OK, year, holidays)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="daily.calendar_export",
                                 description="판정 달력 → v3·uni 휴장 파일(.kis_holidays.json 형식) 내보내기")
    ap.add_argument("--target", required=True, help="쓸 파일 경로(v3 data/.kis_holidays.json 자리)")
    ap.add_argument("--home", default=None, help="quant-ledger 루트 (기본 $QL_HOME)")
    a = ap.parse_args(argv)
    home = Path(a.home or os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    target = Path(a.target).absolute()
    now = _now_kst()
    head = f"휴장 달력 내보내기 {now:%Y%m%d}"
    try:
        r = export(home / "data" / "calendar", target, now)
    except Exception as e:  # noqa: BLE001  # reason: 쓰기 중 예상 못한 실패도 rc 2·대상 무변경 한 경로로 올린다
        traceback.print_exc()
        print(f"{head}: rc=2 {type(e).__name__}: {str(e)[:300]} — 대상 파일 무변경 target={target}")
        return 2
    if not r.ok:
        print(f"{head}: rc=2 {r.status.value}: {r.detail[:300]} — 대상 파일 무변경 target={target}")
        return 2
    n_weekday = sum(1 for h in r.holidays if dt.date(int(h[:4]), int(h[4:6]), int(h[6:])).weekday() < 5)
    print(f"{head}: rc=0 year={r.year} holidays={len(r.holidays)}(평일 {n_weekday}) target={target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
