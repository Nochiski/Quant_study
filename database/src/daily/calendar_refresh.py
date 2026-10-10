"""휴장 달력 직접 갱신 — KIS 국내휴장일조회(chk-holiday, TR CTCA0903R)를 quant-ledger 가 직접 부른다.

결정 Q-2 = N-31 ②(10-08), 범위 N-40 ④(10-09). 게이트 RM K1-9 ①③④⑥ 을 여기서 판정한다
(② 는 `ledger_health.check_krx_holiday_traded`, ⑦ 은 `calendar.load`). 플랜
`docs/plans/2026-10-10-holiday-calendar-direct.md`.

하루 1콜 — KIS 예제 docstring: "국내휴장일조회(TCA0903R) 서비스는 당사 원장서비스와 연관되어 있어 단시간 내
다수 호출시 서비스에 영향을 줄 수 있어 가급적 1일 1회 호출 부탁드립니다."
  - 창: 06:00 체인에서 오늘(KST)을 BASS_DT 로 1페이지(연속조회 없음). 응답이 덮는 날짜(약 24일)만 판정
    연도 파일에 덧씌운다 — 월중에 지정되는 임시공휴일을 그날 잡는다.
  - 이듬해: 11-21 부터 이듬해 판이 게시될 때까지 하루 1페이지씩 이어 받아(약 16페이지) 1년치가 차면 게시한다.
    올해 판이 없으면(연도 경계 사고) 올해를 같은 방식으로 이어 받는다 — 빠른 복구는 플랜 §8 수동 절차.
  같은 날 같은 용도의 두 번째 호출은 하지 않는다(런 로그 `source=kis_holiday`·`kis_holiday_next` 로 센다).
  순서는 이어 받기 → 창이다 — 연말에 이듬해 판이 게시된 날 창의 이듬해 부분도 같은 날 반영된다.

원장: 응답 원문은 `data/raw/kis.db` 표 `kis_holiday` 에 append-only 로 남긴다(PK = 응답 열만의 해시,
`INSERT OR IGNORE` → `collected_at` = 최초 관측, 값이 바뀌면 새 행). 판정 연도 파일은 파생물이다.

`--check` 는 읽기 전용이다 — 창 1콜만 부르고 원장·달력 파일·누적 파일·런 로그·보고서에 쓰지 않는다.
rc: 0 정상 · 1 경고(warn) · 2 crit. 예상 못한 예외도 `main` 이 rc 2 로 올린다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import os
import sqlite3
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from daily import calendar as cal_mod
from daily import runlog

URL = "/uapi/domestic-stock/v1/quotations/chk-holiday"
TR_ID = "CTCA0903R"                 # 국내휴장일조회(KIS 예제 chk_holiday.py)
RAW_TABLE = "kis_holiday"
SRC_WINDOW = "kis_holiday"          # 런 로그 source — 창 1콜
SRC_NEXT = "kis_holiday_next"       # 런 로그 source — 이듬해 이어 받기 1콜
NEXT_YEAR_FROM = (11, 21)           # 이듬해 이어 받기 시작(월, 일) — 로드맵 §4 '11월 하순', 약 16일 + 여유
NEXT_YEAR_DEADLINE = (12, 15)       # ④ 이듬해 판 기한(월, 일)
RECOVERY_HINT = ("복구: 플랜 2026-10-10-holiday-calendar-direct §8 '연도 경계 수동 복구' — 사람이 확인한 뒤 "
                 "data/calendar/v3/kis_holidays_<연도>.json 을 판정 디렉터리 data/calendar/ 로 복사")
MIN_YEAR_HOLIDAYS = 100             # 1년치 휴장 건수 하한 — v3 holiday.py 검증 규칙(sync_calendar.sh 와 같다)
KST = dt.timezone(dt.timedelta(hours=9))


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    CRIT = "crit"


@dataclass(frozen=True)
class Finding:
    name: str
    severity: Severity
    detail: str
    value: object = None


@dataclass(frozen=True)
class RefreshReport:
    today: str
    check: bool
    findings: tuple[Finding, ...]
    n_calls: int

    @property
    def rc(self) -> int:
        sev = {f.severity for f in self.findings}
        return 2 if Severity.CRIT in sev else 1 if Severity.WARN in sev else 0

    def summary(self) -> str:
        parts = []
        for f in self.findings:
            bad = f.severity in (Severity.WARN, Severity.CRIT)
            parts.append(f"{f.name}={f.severity.value}" + (f"({f.detail[:200]})" if bad else ""))
        mode = "점검" if self.check else "갱신"
        line = f"휴장 달력 {self.today} {mode}: rc={self.rc} calls={self.n_calls} | " + "; ".join(parts)
        # 셸이 마지막 한 줄만 요약으로 읽으므로(daily_ledger.sh) 개행이 섞이면 요약이 잘린다
        return " ".join(line.splitlines())


class PageStatus(Enum):
    OK = "ok"
    CALL_FAILED = "call_failed"
    INVALID = "invalid"


@dataclass(frozen=True)
class Page:
    """chk-holiday 1페이지. `days` 는 검증을 통과했을 때만 채운다((YYYYMMDD, opnd_yn) 날짜순)."""

    status: PageStatus
    start: str
    days: tuple[tuple[str, str], ...]
    rows: tuple[dict[str, object], ...]
    n_calls: int
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is PageStatus.OK


# ── 호출·① 검증 ───────────────────────────────────────────────────────────────
def fetch_page(start: str) -> Page:
    """BASS_DT=start 로 1페이지를 받아 ① 검증한다. 같은 날 재시도는 하지 않는다.

    토큰 만료(EGW00121·EGW00123)만 캐시를 버리고 1회 재발급 후 다시 부른다 — 휴장 서비스에 닿기 전
    게이트웨이 거절이다(`kis_daily.fetch_credit` 와 같은 규칙). 호출 판정 어휘는 `backfill_kis.classify`.
    """
    # 지연 import — api·backfill_kis 는 import 시점에 kael .env 를 요구한다(api.py:13-20).
    import api
    from backfill_kis import classify, normalize

    params = {"BASS_DT": start, "CTX_AREA_NK": "", "CTX_AREA_FK": ""}
    n_calls = 0
    for attempt in (1, 2):
        n_calls += 1
        try:
            body: object = api.kis(URL, TR_ID, params)
        except Exception as e:  # noqa: BLE001  # reason: 네트워크·JSON 예외도 '호출 실패' 결과 값으로 올려 crit 판정한다
            return Page(PageStatus.CALL_FAILED, start, (), (), n_calls,
                        f"exception {type(e).__name__}: {str(e)[:120]} — BASS_DT={start} tr={TR_ID}")
        if not isinstance(body, dict):
            return Page(PageStatus.CALL_FAILED, start, (), (), n_calls,
                        f"response is {type(body).__name__}, not an object — BASS_DT={start} tr={TR_ID}")
        verdict, code = classify(200, body)
        if verdict == "token" and attempt == 1:
            print(f"    · 토큰 만료({code}) — 재발급 후 1회 재시도 (chk-holiday BASS_DT={start})")
            api._kis_tok = None
            try:
                os.remove(api._KIS_CACHE)
            except OSError:
                pass
            time.sleep(1.0)
            continue
        if verdict != "ok":
            msg = str(body.get("msg1", ""))[:80]
            return Page(PageStatus.CALL_FAILED, start, (), (), n_calls,
                        f"verdict={verdict} code={code} msg={msg!r} — BASS_DT={start} tr={TR_ID}")
        rows, odd = normalize(body, "output")
        return validate_page(start, rows, odd, n_calls)
    return Page(PageStatus.CALL_FAILED, start, (), (), n_calls,
                f"token still expired after one reissue — BASS_DT={start} tr={TR_ID}")


def validate_page(start: str, rows: Sequence[dict[str, object]], odd: str | None = None,
                  n_calls: int = 1) -> Page:
    """① — 응답 1페이지 검증. 하나라도 어기면 페이지 전체가 무효(부분 반영 없음).

    1행 이상 · 모든 행이 실제 날짜와 opnd_yn Y/N · 첫 날짜 = 요청한 BASS_DT · 하루씩 빈틈·중복 없이 이어짐
    (구간의 모든 날짜) · 토·일은 전부 휴장(opnd_yn='N').
    """
    def bad(why: str) -> Page:
        return Page(PageStatus.INVALID, start, (), tuple(rows), n_calls,
                    f"{why} — BASS_DT={start} n_rows={len(rows)}")

    if odd:
        return bad(f"응답 형태 이상({odd})")
    if not rows:
        return bad("빈 응답(output 0행) — 정상 달력을 덮지 않는다(v3 2026-06-05 사고)")
    days: list[tuple[str, str]] = []
    for i, r in enumerate(rows):
        d = str(r.get("bass_dt") or "").strip()
        o = str(r.get("opnd_yn") or "").strip()
        try:
            _ymd(d)
        except ValueError:
            return bad(f"{i}번째 행 bass_dt={d!r} 가 YYYYMMDD 날짜가 아니다")
        if o not in ("Y", "N"):
            return bad(f"{i}번째 행 bass_dt={d} opnd_yn={o!r} 가 Y/N 이 아니다")
        days.append((d, o))
    if days[0][0] != start:
        return bad(f"첫 날짜 {days[0][0]} 가 요청한 BASS_DT 와 다르다")
    for (a, _), (b, _) in itertools.pairwise(days):
        if _ymd(b) != _ymd(a) + dt.timedelta(days=1):
            return bad(f"날짜가 하루씩 이어지지 않는다: {a} 다음 {b}")
    open_weekend = [d for d, o in days if _ymd(d).weekday() >= 5 and o == "Y"]
    if open_weekend:
        return bad(f"토·일이 개장(opnd_yn='Y')으로 왔다: {open_weekend[:5]}")
    return Page(PageStatus.OK, start, tuple(days), tuple(rows), n_calls)


def _ymd(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").replace(tzinfo=KST).date()


# ── 판정 연도 파일 덧씌움 · 이듬해 판 ───────────────────────────────────────────────
def overlay(by_year: dict[str, frozenset[str]], days: Sequence[tuple[str, str]]
            ) -> tuple[dict[str, frozenset[str]], tuple[str, ...]]:
    """창 응답을 판정 연도 파일에 덧씌운다 — 응답이 덮는 날짜만 바꾼다(휴장 = opnd_yn 'N').

    연도 파일이 없는 해의 날짜는 반영하지 않고 그 해를 돌려준다(이듬해 판은 이어 받기로만 만든다 —
    며칠치로 연도 파일을 만들면 나머지 날이 전부 거래일로 판정된다).
    """
    out: dict[str, frozenset[str]] = {}
    skipped: list[str] = []
    for year in sorted({d[:4] for d, _ in days}):
        mine = [(d, o) for d, o in days if d[:4] == year]
        if year not in by_year:
            skipped.append(year)
            continue
        covered = {d for d, _ in mine}
        out[year] = frozenset((by_year[year] - covered) | {d for d, o in mine if o == "N"})
    return out, tuple(skipped)


def next_year_target(today: dt.date, years: frozenset[str]) -> str | None:
    """이어 받을 연도. 올해 판이 없으면 올해(연도 경계 사고 — 스스로 회복), 아니면 11-21 부터 이듬해 판이 없을 때 이듬해."""
    this = str(today.year)
    if this not in years:
        return this
    nxt = str(today.year + 1)
    if (today.month, today.day) >= NEXT_YEAR_FROM and nxt not in years:
        return nxt
    return None


def _year_days(year: str) -> list[str]:
    d, out = dt.date(int(year), 1, 1), []
    while d.year == int(year):
        out.append(d.strftime("%Y%m%d"))
        d += dt.timedelta(days=1)
    return out


def full_year_problem(year: str, days: dict[str, str]) -> str | None:
    """1년치 게시 검증. 통과면 None, 아니면 사유.

    1/1~12/31 전 날짜 · 토·일 전부 휴장 · 휴장 ≥ 100건(v3 규칙) · 1/1(신정)·12/31(연말휴장) 휴장.
    """
    want = _year_days(year)
    missing = [d for d in want if d not in days]
    if missing:
        return f"빠진 날 {len(missing)}일(앞 5개 {missing[:5]})"
    problems = []
    open_weekend = [d for d in want if _ymd(d).weekday() >= 5 and days[d] == "Y"]
    if open_weekend:
        problems.append(f"토·일 개장 {len(open_weekend)}일(앞 5개 {open_weekend[:5]})")
    n_closed = sum(1 for d in want if days[d] == "N")
    if n_closed < MIN_YEAR_HOLIDAYS:
        problems.append(f"휴장 {n_closed}건 < {MIN_YEAR_HOLIDAYS}")
    fixed_open = [d for d in (f"{year}0101", f"{year}1231") if days[d] == "Y"]
    if fixed_open:
        problems.append(f"신정·연말휴장이 개장으로 왔다: {fixed_open}")
    return "; ".join(problems) or None


def deadline_finding(today: dt.date, years: frozenset[str]) -> Finding:
    """④ — 올해 판이 없으면 crit(연도 경계를 이미 넘었다), 12-15 부터 이듬해 판이 없어도 crit."""
    nxt = str(today.year + 1)
    name = "next_year.deadline"
    if str(today.year) not in years:
        return Finding(name, Severity.CRIT,
                       f"올해 {today.year} 판정 연도 파일이 없다(오늘 {today.isoformat()}, 판정 달력 연도 "
                       f"{sorted(years)}) — 올해 날짜 판정이 KeyError 로 멈춘다. 이어 받기가 올해를 1/1 부터 "
                       f"다시 받지만 약 16일 걸린다. {RECOVERY_HINT}", {"year": str(today.year), "missing": True})
    if nxt in years:
        return Finding(name, Severity.OK, f"이듬해 {nxt} 판 있음")
    due = f"{today.year}-{NEXT_YEAR_DEADLINE[0]:02d}-{NEXT_YEAR_DEADLINE[1]:02d}"
    if (today.month, today.day) >= NEXT_YEAR_DEADLINE:
        return Finding(name, Severity.CRIT,
                       f"이듬해 {nxt} 판이 기한 {due} 까지 게시되지 않았다(오늘 {today.isoformat()}) — "
                       f"1/1 부터 판정이 KeyError 로 멈춘다. 누적 파일 direct/next_{nxt}.json · 원장 "
                       f"{RAW_TABLE} · 런 로그 {SRC_NEXT} 를 확인할 것. {RECOVERY_HINT}",
                       {"year": nxt, "deadline": due})
    return Finding(name, Severity.OK, f"이듬해 {nxt} 판 없음 — 기한 {due} 전")


# ── ③ 지난 거래일 = trading_calendar ─────────────────────────────────────────────
def read_trading_calendar(equity_root: Path) -> list[dt.date]:
    """equity `trading_calendar` 현판(MANIFEST current_build 의 파티션만)의 날짜 — 옛 판 glob 금지."""
    import duckdb
    from equity import inputs as eq_inputs

    pb = eq_inputs.resolve(Path(equity_root), "trading_calendar")
    globs = ", ".join("'" + g.replace("'", "''") + "'" for g in pb.globs)
    con = duckdb.connect()
    try:
        rows = con.execute(f"SELECT DISTINCT CAST(date AS DATE) FROM read_parquet([{globs}]) "
                           "WHERE date IS NOT NULL ORDER BY 1").fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def compare_trading_calendar(cal: cal_mod.Calendar, dates: Sequence[dt.date]) -> Finding:
    """③ — 판정 달력이 덮는 첫 해 1/1 ~ max(trading_calendar) 에서 두 거래일 집합이 같아야 한다."""
    name = "trading_calendar"
    if not dates or not cal.years:
        return Finding(name, Severity.CRIT,
                       f"판정 불가 — trading_calendar 날짜 {len(dates)}개, 판정 달력 연도 {sorted(cal.years)}")
    lo = dt.date(min(int(y) for y in cal.years), 1, 1)
    hi = max(dates)
    if hi < lo:
        return Finding(name, Severity.CRIT, f"판정 불가 — 겹치는 구간 없음: 달력 {lo} ~, trading_calendar ~ {hi}")
    mine: set[dt.date] = set()
    d = lo
    try:
        while d <= hi:
            if cal.is_trading_day(d):
                mine.add(d)
            d += dt.timedelta(days=1)
    except KeyError as e:
        return Finding(name, Severity.CRIT, f"판정 불가 — {lo}~{hi} 에서 달력이 연도를 덮지 않는다: {e}")
    theirs = {x for x in dates if lo <= x <= hi}
    only_cal = sorted(x.strftime("%Y%m%d") for x in mine - theirs)
    only_tc = sorted(x.strftime("%Y%m%d") for x in theirs - mine)
    if only_cal or only_tc:
        return Finding(name, Severity.CRIT,
                       f"{lo}~{hi} 두 거래일 집합 불일치: 달력에만 {len(only_cal)}일 {only_cal[:10]}, "
                       f"trading_calendar(KRX 지수 날짜)에만 {len(only_tc)}일 {only_tc[:10]}",
                       {"only_calendar": only_cal[:10], "only_trading_calendar": only_tc[:10]})
    return Finding(name, Severity.OK, f"{lo}~{hi} 거래일 {len(theirs)}일 일치")


# ── v3 사본 병행 대조(기록) ──────────────────────────────────────────────────────
def compare_v3(by_year: dict[str, frozenset[str]], v3_dir: Path) -> Finding:
    """판정 연도별 휴장 집합 vs v3 사본 — 일치·불일치를 기록만 한다(끄는 결정은 일치 30일 뒤, 로드맵 §4)."""
    name = "v3_compare"
    if not v3_dir.is_dir():
        return Finding(name, Severity.INFO, f"v3 사본 디렉터리 없음({v3_dir}) — 대조 못 함", {})
    try:
        v3 = cal_mod.read_year_files(v3_dir)
    except (cal_mod.CalendarUnavailable, OSError, ValueError, KeyError, TypeError) as e:
        return Finding(name, Severity.INFO, f"v3 사본을 읽지 못했다: {type(e).__name__}: {e}", {})
    value: dict[str, dict[str, object]] = {}
    for year in sorted(set(by_year) | set(v3)):
        if year not in v3:
            value[year] = {"v3": "missing"}
            continue
        if year not in by_year:
            value[year] = {"judging": "missing", "n_v3": len(v3[year])}
            continue
        only_v3 = sorted(v3[year] - by_year[year])
        only_direct = sorted(by_year[year] - v3[year])
        value[year] = {"match": not only_v3 and not only_direct, "only_direct": only_direct, "only_v3": only_v3}
    n_bad = sum(1 for v in value.values() if v.get("match") is False)
    return Finding(name, Severity.INFO, f"연도 {len(value)}개 중 불일치 {n_bad}", value)


# ── 쓰기(적용 모드만) ─────────────────────────────────────────────────────────────
def _now_utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write_json(path: Path, payload: object, mode: int = 0o600) -> None:
    """같은 디렉터리 임시 파일 → mode(기본 0600) → os.replace(원자 교체). 중간에 죽어도 절단된 JSON 이 남지 않는다.

    `mode` 는 남의 파일 내용만 바꿀 때 원래 권한을 지키려고 받는다
    (`calendar_export` — v3 휴장 파일).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_year_file(cal_dir: Path, year: str, holidays: frozenset[str]) -> None:
    _atomic_write_json(cal_dir / cal_mod.YEAR_FILE.format(year=year),
                       {"year": int(year), "holidays": sorted(holidays), "updated_at": _now_utc(),
                        "source": "kis_direct"})


def store_raw(db: Path, page: Page) -> int:
    """응답 원문을 원장 `kis_holiday` 에 넣는다 — 같은 사실은 한 행(최초 관측), 바뀐 값은 새 행. 늘어난 행 수."""
    # 해시 산식 정본을 공유한다(지연 import 이유는 fetch_page 주석)
    from backfill_dart import row_hash

    rows = page.rows
    if not rows:
        return 0
    keys = sorted(set().union(*(set(r) for r in rows)))
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db, timeout=60)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        have = {str(r[1]) for r in con.execute(f"PRAGMA table_info({RAW_TABLE})")}
        if not have:
            ddl = ", ".join(f'"{k}" TEXT' for k in keys)
            con.execute(f"CREATE TABLE {RAW_TABLE} (row_hash TEXT PRIMARY KEY, {ddl}, "
                        "req_bass_dt TEXT NOT NULL, collected_at TEXT NOT NULL)")
        else:
            for k in keys:
                if k not in have:
                    print(f"    [{RAW_TABLE}] 새 응답 열 {k} — 컬럼 추가")
                    con.execute(f'ALTER TABLE {RAW_TABLE} ADD COLUMN "{k}" TEXT')
        now = _now_utc()
        vals = []
        for r in rows:
            body = [r.get(k) for k in keys]
            vals.append([row_hash(keys, body), *body, page.start, now])
        before = con.execute(f"SELECT COUNT(*) FROM {RAW_TABLE}").fetchone()[0]
        quoted = ",".join(f'"{k}"' for k in keys)
        # IGNORE 여야 collected_at 이 최초 관측 시각으로 남는다(kis_daily·백필과 같은 이유)
        con.executemany(f"INSERT OR IGNORE INTO {RAW_TABLE} (row_hash, {quoted}, req_bass_dt, collected_at) "
                        f"VALUES ({','.join('?' * (len(keys) + 3))})", vals)
        con.commit()
        return int(con.execute(f"SELECT COUNT(*) FROM {RAW_TABLE}").fetchone()[0]) - int(before)
    finally:
        con.close()


def _runs_today(run_db: Path, source: str, date: str) -> int:
    """그날 그 용도로 이미 시도한 실행 수 — 읽기 전용(점검 모드에서도 파일을 만들지 않는다)."""
    if not run_db.exists():
        return 0
    con = sqlite3.connect(run_db.absolute().as_uri() + "?mode=ro", uri=True)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run'").fetchone() is None:
            return 0
        row = con.execute("SELECT COUNT(*) FROM run WHERE source=? AND date=?", (source, date)).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def _fail_run(run_db: Path, rid: int, page: Page | None, e: BaseException) -> None:
    """`runlog.start` 뒤 예외 — 'running' 으로 남기지 않고 failed 로 마감한다(예외는 호출부가 다시 올린다)."""
    try:
        runlog.finish(run_db, rid, status="failed", n_calls=None if page is None else page.n_calls, n_rows=0,
                      detail=f"exception {type(e).__name__}: {str(e)[:200]}")
    except Exception as e2:  # noqa: BLE001  # reason: 마감 실패가 원래 예외를 가리면 안 된다 — 출력만 남기고 원래 예외를 올린다
        print(f"    ! 런 로그 마감 실패(run_id={rid}): {type(e2).__name__}: {e2}", file=sys.stderr)


def _next_year(target: str, today_s: str, *, check: bool, cal_dir: Path, raw_db: Path, run_db: Path,
               by_year: dict[str, frozenset[str]]) -> tuple[Finding, int]:
    """연도 판 이어 받기 1콜 — 누적이 1년치를 채우고 검증을 통과하면 게시한다(`by_year` 를 갱신).

    대상은 보통 이듬해, 올해 판이 없으면 올해다(`next_year_target`). 누적 파일 `direct/next_<year>.json` =
    {"year", "days": {YYYYMMDD: opnd_yn}, "pages": [...]}. 커서 = 누적 마지막 날 + 1(처음엔 1/1).
    12/31 을 넘는 행은 버린다(그 해의 판만 만든다). 1/1(신정)·12/31(연말휴장)이 개장(Y)으로 온 페이지는
    KIS 가 그 해 휴장을 아직 싣지 않은 것으로 보고 누적하지 않는다(원장 적재는 한다) — 다음 날 같은 커서로 다시 받는다.
    """
    name = "next_year"
    path = cal_dir / "direct" / f"next_{target}.json"
    need = len(_year_days(target))
    try:
        staged = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        days: dict[str, str] = {str(k): str(v) for k, v in dict(staged.get("days", {})).items()}
        pages: list[object] = list(staged.get("pages", []))
    except (OSError, ValueError, TypeError, AttributeError) as e:
        return Finding(name, Severity.CRIT,
                       f"{target} 판 누적 파일을 읽지 못했다: {path} {type(e).__name__}: {e} — 원문 "
                       f"({RAW_TABLE})과 대조한 뒤 지우면 1/1 부터 다시 받는다"), 0
    used = 0
    last = f"{target}1231"

    def state() -> dict[str, object]:
        return {"year": target, "n_days": len(days), "need": need}

    if not (days and max(days) >= last):
        cursor = (_ymd(max(days)) + dt.timedelta(days=1)).strftime("%Y%m%d") if days else f"{target}0101"
        if check:
            return Finding(name, Severity.INFO,
                           f"{target} 판 이어 받기 대상 — 커서 {cursor}, 누적 {len(days)}/{need}일"
                           f"(점검 모드는 부르지 않는다)", state()), 0
        prior = _runs_today(run_db, SRC_NEXT, today_s)
        if prior:
            return Finding(name, Severity.INFO,
                           f"{target} 판 오늘 이미 {prior}회 시도 — 다시 부르지 않는다(누적 {len(days)}/{need}일)",
                           state()), 0
        rid = runlog.start(run_db, date=today_s, source=SRC_NEXT)
        page: Page | None = None
        try:
            page = fetch_page(cursor)
            used = page.n_calls
            if not page.ok:
                detail = (f"{target} 판 페이지 실패({page.status.value}) — {page.detail} | 다음 날 같은 커서로 "
                          f"이어 받는다(누적 {len(days)}/{need}일, 기한 12-15)")
                runlog.finish(run_db, rid, status="failed", n_calls=used, n_rows=0, detail=detail)
                return Finding(name, Severity.WARN, detail, state()), used
            n_new = store_raw(raw_db, page)
            kept = [(d, o) for d, o in page.days if d[:4] == target]
            fixed_open = [d for d, o in kept if d in (f"{target}0101", last) and o == "Y"]
            if fixed_open:
                detail = (f"{target} 판 미게시로 보임 — {fixed_open} 가 개장(Y)으로 왔다(신정·연말휴장). "
                          f"누적하지 않고(원장 {RAW_TABLE} 에는 적재) 다음 날 같은 커서 {cursor} 로 다시 받는다")
                runlog.finish(run_db, rid, status="ok", n_calls=used, n_rows=n_new, detail=detail)
                return Finding(name, Severity.WARN, detail, state()), used
            days.update(kept)
            pages.append({"start": page.start, "end": kept[-1][0] if kept else page.start,
                          "n_days": len(kept), "collected_at": _now_utc()})
            _atomic_write_json(path, {"year": target, "days": dict(sorted(days.items())), "pages": pages})
            runlog.finish(run_db, rid, status="ok", n_calls=used, n_rows=n_new,
                          detail=f"{target} 판 {page.start}~ {len(kept)}일 누적 → {len(days)}/{need}일")
        except Exception as e:
            _fail_run(run_db, rid, page, e)
            raise
    if not (days and max(days) >= last):
        return Finding(name, Severity.OK, f"{target} 판 누적 {len(days)}/{need}일", state()), used
    problem = full_year_problem(target, days)
    if problem:
        return Finding(name, Severity.CRIT,
                       f"{target} 판 1년치 검증 실패 — {problem} | 게시하지 않는다. 누적 파일 {path} 를 원문"
                       f"({RAW_TABLE})과 대조한 뒤 지우면 1/1 부터 다시 받는다", state()), used
    closed = frozenset(d for d, o in days.items() if o == "N")
    n_weekday = sum(1 for d in closed if _ymd(d).weekday() < 5)
    if not check:
        write_year_file(cal_dir, target, closed)
    by_year[target] = closed
    return Finding(name, Severity.OK,
                   f"{target} 판 게시 — 휴장 {len(closed)}건(평일 {n_weekday})",
                   {**state(), "n_holidays": len(closed), "n_weekday_holidays": n_weekday}), used


def _window(today: dt.date, *, check: bool, cal_dir: Path, raw_db: Path, run_db: Path,
            by_year: dict[str, frozenset[str]]) -> tuple[list[Finding], int]:
    """창 1콜 → ① → 원장 → 판정 연도 파일 덧씌움(`by_year` 를 갱신). (판정 목록, 호출 수)."""
    today_s = today.strftime("%Y%m%d")
    year = str(today.year)
    prior = _runs_today(run_db, SRC_WINDOW, today_s)
    if year not in by_year:
        return [Finding("window", Severity.CRIT,
                        f"올해({year}) 판정 연도 파일이 없다 — 덧씌울 바탕이 없어 부르지 않는다: "
                        f"have={sorted(by_year)} dir={cal_dir}. {RECOVERY_HINT}")], 0
    if prior and not check:
        return [Finding("window", Severity.INFO,
                        f"오늘({today_s}) 이미 {prior}회 시도 — KIS '1일 1회' 권고로 다시 부르지 않는다")], 0
    rid = None if check else runlog.start(run_db, date=today_s, source=SRC_WINDOW)
    page: Page | None = None
    try:
        page = fetch_page(today_s)
        if not page.ok:
            detail = (f"창 갱신 실패({page.status.value}) — {page.detail} | 직전 판정 달력 유지, "
                      f"같은 날 재시도 없음(다음 06:00)")
            if rid is not None:
                runlog.finish(run_db, rid, status="failed", n_calls=page.n_calls, n_rows=0, detail=detail)
            return [Finding("window", Severity.CRIT, detail)], page.n_calls
        changed, skipped = overlay(by_year, page.days)
        added = sorted(d for y, s in changed.items() for d in s - by_year[y])
        removed = sorted(d for y, s in changed.items() for d in by_year[y] - s)
        value = {"start": page.start, "end": page.days[-1][0], "n_days": len(page.days),
                 "added": added, "removed": removed, "not_applied_years": list(skipped)}
        note = f" — 오늘 {prior + 1}번째 호출(점검 모드는 런 로그에 남기지 않는다)" if check else ""
        detail = (f"창 {page.start}~{page.days[-1][0]} {len(page.days)}일 반영: 추가 {added} 제거 {removed}"
                  + (f" · 연도 파일 없는 해 {list(skipped)} 는 반영 안 함" if skipped else "") + note)
        if not check:
            n_new = store_raw(raw_db, page)
            for y, s in changed.items():
                if s != by_year[y]:
                    write_year_file(cal_dir, y, s)
            if rid is not None:
                runlog.finish(run_db, rid, status="ok", n_calls=page.n_calls, n_rows=n_new, detail=detail)
    except Exception as e:
        if rid is not None:
            _fail_run(run_db, rid, page, e)
        raise
    by_year.update(changed)
    out = [Finding("window", Severity.OK, detail, value)]
    if removed:
        out.append(Finding(
            "holidays.removed", Severity.WARN,
            f"휴장 → 개장으로 바뀐 날 {removed} — 반영했다. 지정 취소인지 원장 {RAW_TABLE}(같은 bass_dt "
            f"의 두 행 collected_at)과 공지로 확인할 것(⑥)", removed))
    return out, page.n_calls


# ── 한 번 실행 ──────────────────────────────────────────────────────────────────
def run(home: Path, today: dt.date, *, check: bool = False, equity_root: Path | None = None,
        out_dir: Path | None = None,
        read_trading_days: Callable[[Path], list[dt.date]] = read_trading_calendar) -> RefreshReport:
    """이어 받기 → 창 갱신 → ④ → ③ → v3 대조 → 보고서. `check` 면 아무것도 쓰지 않는다."""
    home = Path(home)
    cal_dir = home / "data" / "calendar"
    raw_db = home / "data" / "raw" / "kis.db"
    run_db = home / "data" / "raw" / "daily_run.db"
    out_dir = out_dir or home / "logs" / "calendar"
    equity_root = equity_root or home / "data" / "equity"
    today_s = today.strftime("%Y%m%d")
    findings: list[Finding] = []
    n_calls = 0

    def finish() -> RefreshReport:
        rep = RefreshReport(today_s, check, tuple(findings), n_calls)
        report = out_dir / f"{today_s}.json"
        # 같은 날 재실행이 호출 없이 끝나면(이미 부름) 그날 첫 보고서(호출 결과)를 덮지 않는다
        if not check and (n_calls > 0 or not report.exists()):
            _atomic_write_json(report,
                               {"today": today_s, "rc": rep.rc, "n_calls": n_calls, "summary": rep.summary(),
                                "findings": [{**asdict(f), "severity": f.severity.value} for f in findings]})
        return rep

    try:
        by_year = dict(cal_mod.read_year_files(cal_dir))
    except (cal_mod.CalendarUnavailable, OSError, ValueError, KeyError, TypeError) as e:
        findings.append(Finding("calendar.read", Severity.CRIT,
                                f"판정 달력을 읽지 못했다: dir={cal_dir} {type(e).__name__}: {e} — 창 갱신·대조를 "
                                f"하지 않는다(체인은 D 산출에서 멈춘다, K1-9 ⑦). {RECOVERY_HINT}"))
        return finish()

    # e 연도 판 이어 받기 — 창보다 먼저: 연말에 이듬해 판이 게시된 날 창의 이듬해 부분도 같은 날 반영된다
    target = next_year_target(today, frozenset(by_year))
    if target is not None:
        found, used = _next_year(target, today_s, check=check, cal_dir=cal_dir, raw_db=raw_db,
                                 run_db=run_db, by_year=by_year)
        findings.append(found)
        n_calls += used

    # a~d 창 1콜
    win, used = _window(today, check=check, cal_dir=cal_dir, raw_db=raw_db, run_db=run_db, by_year=by_year)
    findings += win
    n_calls += used

    # f ④ · g ③ · h v3 대조
    years = frozenset(by_year)
    findings.append(deadline_finding(today, years))
    calendar = cal_mod.Calendar(frozenset().union(*by_year.values()), "kis_cache", years=years)
    try:
        tc = read_trading_days(Path(equity_root))
    except Exception as e:  # noqa: BLE001  # reason: equity 를 못 읽는 모든 경우를 '판정 불가 = 실패'(공통 3)로 올린다
        findings.append(Finding("trading_calendar", Severity.CRIT,
                                f"판정 불가 — equity trading_calendar 를 읽지 못했다: root={equity_root} "
                                f"{type(e).__name__}: {str(e)[:200]}"))
    else:
        findings.append(compare_trading_calendar(calendar, tc))
    findings.append(compare_v3(by_year, cal_dir / "v3"))
    return finish()


def _today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="daily.calendar_refresh",
                                 description="휴장 달력 직접 갱신(KIS chk-holiday 하루 1콜) + K1-9 ①③④⑥ 판정")
    ap.add_argument("--check", action="store_true",
                    help="읽기 전용 점검 — 창 1콜만 부르고 원장·달력 파일·런 로그·보고서에 쓰지 않는다")
    ap.add_argument("--home", default=None, help="quant-ledger 루트 (기본 $QL_HOME)")
    ap.add_argument("--equity-root", default=None, help="equity 루트 (기본 <home>/data/equity)")
    ap.add_argument("--out", default=None, help="보고서 디렉터리 (기본 <home>/logs/calendar, 점검 모드는 쓰지 않음)")
    a = ap.parse_args(argv)
    home = Path(a.home or os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    today = _today_kst()
    mode = "점검" if a.check else "갱신"
    try:
        rep = run(home, today, check=a.check,
                  equity_root=Path(a.equity_root) if a.equity_root else None,
                  out_dir=Path(a.out) if a.out else None)
    except Exception as e:  # noqa: BLE001  # reason: 예상 못한 실패는 경계 한 곳에서 crit(rc 2)로 올린다 — 셸은 rc 1 을 warn 으로 읽는다
        traceback.print_exc()
        print(f"휴장 달력 {today:%Y%m%d} {mode}: rc=2 예외 {type(e).__name__}: {str(e)[:300]} (home={home})")
        return 2
    for f in rep.findings:
        print(f"  [{f.severity.value}] {f.name}: {f.detail}")
    print(rep.summary())
    return rep.rc


if __name__ == "__main__":
    sys.exit(main())
