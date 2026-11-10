"""KIS 관심종목 등록 상위 · HTS 조회 상위 스냅숏 — 거래일 08:30~20:30 KST 30분마다(대안데이터 묶음 1).

결정 N-66 · 계획 `docs/plans/2026-11-10-kis-rank-snapshot.md`.

  TR       관심종목등록 상위 `FHPST01800000` — J(KRX) · 범위 0001 코스피 · 1001 코스닥 · 0000 전체, 각 1위부터 30행
           (31위부터는 받을 수 없다). HTS 조회 상위 `HHMCM000100C0` — 20행. 둘 다 응답에 날짜 칸이 없어 지난 값은
           다시 받을 수 없다. 관심 등록 건수는 장중에 안 바뀌고 한꺼번에 갱신된다(갱신 시각 미상 — 30분 격자로 잡는다).
  슬롯     `SLOTS` — 거래일(KST 날짜, `daily.calendar`) 08:30 ~ 20:30 의 30분 격자 25개. 크론이 슬롯 시각에 부르고,
           수집기는 지금 시각이 든 슬롯(슬롯 ≤ 지금 < 슬롯 + 30분)을 받는다. 격자 밖이면 rc 3 으로 아무것도 안 한다 —
           크론 줄과 이 표가 어긋나면 워치독(`summarize`)이 빠진 슬롯으로 잡는다.
  원장     `data/raw/kis_rank.db` — `kis_rank_runs`(슬롯 · TR · 범위마다 한 줄) · `kis_top_interest` ·
           `kis_hts_top_view`(응답 칸 이름 그대로, 전부 TEXT — 형 변환 · 이름은 stage 몫). PIT = `received_utc`(응답을
           받은 시각, 값이 만들어진 시각이 아니다). 응답을 받은 (슬롯, TR, 범위)는 — 칸 이상(shape)이어도 — 다시 부르지
           않는다(첫 관측을 늦은 값으로 바꾸거나 두 응답을 섞지 않는다). 다시 부르는 것은 응답을 못 받은 칸(토큰 · 유량 ·
           호출 실패)뿐이다. 빠진 슬롯은 행으로 쓰지 않는다 — `summarize` 가 격자와 대조해 센다.
  락       자체 락만 비대기(`scripts/kis_rank.sh` 의 flock). 원장 락(`scripts/raw_lock.sh`)은 기다리지 않는다 —
           기다리면 슬롯 시각이 밀려 그날 값을 잃는다(장 마감 수집기 T-4 와 같은 이유). 그래서 `kis.db` 가 아니라 파일을 따로 쓴다.
  호출     토큰 1회 재발급 · 재시도 · 유량 물러섬은 `kis_daily.call`(신용잔고와 같은 규칙). 토큰이 끝내 만료면 그 슬롯의
           나머지 TR 을 두드리지 않는다.
  약관     KIS 고객 이용약관 제5조 ③(시세를 제3자에게 제공하지 않는다) — 이 값은 발송물 · 공유물에 싣지 않는다.

종료코드: 0 슬롯의 모든 TR 을 받음(또는 이미 받음) · 2 칸 · 행 수 이상(래퍼가 crit — 받은 행은 저장하고 원문은
`rows_json` 에 남긴다) · 3 격자 밖 · 휴장 · 5 토큰 · 유량 · 호출 실패가 있음(래퍼가 warn). warn 이 1 이 아닌 것은
파이썬이 예상 밖 예외로 죽을 때 1 을 내기 때문이다 — 래퍼는 1 을 포함한 나머지를 crit 으로 본다.
`--summary [YYYYMMDD]` 는 그날 ok 슬롯을 TR 마다 세어 0 통과 · 5 warn(23/25 미만) · 2 crit(0) · 3 휴장.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from daily import calendar as trading_calendar
from daily.runlog import TS_FORMAT

KST = dt.timezone(dt.timedelta(hours=9))
SLOT_FIRST = dt.time(8, 30)
SLOT_STEP = dt.timedelta(minutes=30)
N_SLOTS = 25                                     # 08:30 ~ 20:30
SLOTS: tuple[dt.time, ...] = tuple(
    (dt.datetime.combine(dt.date.min, SLOT_FIRST) + i * SLOT_STEP).time() for i in range(N_SLOTS))
WARN_MIN_OK = 23                                 # 하루 ok 슬롯이 이보다 적으면 warn, 0 이면 crit(PLAN §3-6)

INTEREST_COLUMNS: tuple[str, ...] = (
    "mksc_shrn_iscd", "hts_kor_isnm", "mrkt_div_cls_name", "data_rank", "inter_issu_reg_csnu",
    "stck_prpr", "prdy_vrss", "prdy_vrss_sign", "prdy_ctrt", "acml_vol", "acml_tr_pbmn", "askp", "bidp")
HTS_COLUMNS: tuple[str, ...] = ("mrkt_div_cls_code", "mksc_shrn_iscd")


@dataclass(frozen=True)
class Target:
    """슬롯마다 부르는 콜 하나 — (TR, 범위)."""

    endpoint: str                                # kis_rank_runs.endpoint
    scope: str
    url: str
    tr: str
    out_key: str
    params: Mapping[str, str]
    table: str
    columns: tuple[str, ...]                     # 응답 칸 — 이 집합과 다르면 shape
    n_rows: int                                  # 응답 행 수 — 다르면 shape


def _interest(scope: str) -> Target:
    params = {"fid_input_iscd_2": "000000", "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20180",
              "fid_input_iscd": scope, "fid_trgt_cls_code": "0", "fid_trgt_exls_cls_code": "0",
              "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_div_cls_code": "0",
              "fid_input_cnt_1": "1"}
    return Target("top_interest", scope, "/uapi/domestic-stock/v1/ranking/top-interest-stock", "FHPST01800000",
                  "output", params, "kis_top_interest", INTEREST_COLUMNS, 30)


TARGETS: tuple[Target, ...] = (
    _interest("0001"), _interest("1001"), _interest("0000"),
    Target("hts_top_view", "all", "/uapi/domestic-stock/v1/ranking/hts-top-view", "HHMCM000100C0",
           "output1", {}, "kis_hts_top_view", HTS_COLUMNS, 20),
)
ENDPOINT_SCOPES: dict[str, tuple[str, ...]] = {
    e: tuple(t.scope for t in TARGETS if t.endpoint == e) for e in dict.fromkeys(t.endpoint for t in TARGETS)}

_DDL = (
    """CREATE TABLE IF NOT EXISTS kis_rank_runs (
  slot_kst TEXT NOT NULL, endpoint TEXT NOT NULL, scope TEXT NOT NULL,
  started_utc TEXT NOT NULL, received_utc TEXT, n_calls INTEGER NOT NULL,
  verdict TEXT NOT NULL, msg_cd TEXT, n_rows INTEGER, rows_json TEXT,
  PRIMARY KEY (slot_kst, endpoint, scope))""",
    "CREATE TABLE IF NOT EXISTS kis_top_interest (slot_kst TEXT NOT NULL, scope TEXT NOT NULL, pos INTEGER NOT NULL, "
    + ", ".join(f"{c} TEXT" for c in INTEREST_COLUMNS)
    + ", received_utc TEXT NOT NULL, PRIMARY KEY (slot_kst, scope, pos))",
    "CREATE TABLE IF NOT EXISTS kis_hts_top_view (slot_kst TEXT NOT NULL, pos INTEGER NOT NULL, "
    + ", ".join(f"{c} TEXT" for c in HTS_COLUMNS)
    + ", received_utc TEXT NOT NULL, PRIMARY KEY (slot_kst, pos))",
)


class SlotStatus(Enum):
    OK = "ok"
    FAILED = "failed"            # 토큰 · 유량 · 호출 실패 — warn
    SHAPE = "shape"              # 칸 · 행 수 이상 — crit
    OUTSIDE = "outside"          # 격자 밖
    HOLIDAY = "holiday"


class Verdict(Enum):
    OK = "ok"
    SHAPE = "shape"
    TOKEN = "token"
    QUOTA = "quota"
    FAIL = "fail"


@dataclass(frozen=True)
class SlotPick:
    status: SlotStatus           # OK · OUTSIDE · HOLIDAY
    slot_kst: str | None         # 'YYYY-MM-DDTHH:MM'(KST)
    detail: str = ""


@dataclass(frozen=True)
class TargetResult:
    endpoint: str
    scope: str
    verdict: Verdict | None      # None = 이미 받은 칸이라 부르지 않음
    n_rows: int = 0
    n_calls: int = 0
    code: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SlotResult:
    status: SlotStatus
    slot_kst: str | None
    results: tuple[TargetResult, ...] = ()
    detail: str = ""

    @property
    def rc(self) -> int:
        return {SlotStatus.OK: 0, SlotStatus.FAILED: 5, SlotStatus.SHAPE: 2}.get(self.status, 3)

    def line(self) -> str:
        if not self.results:
            return f"[kis_rank] {self.status.value} — {self.detail}"
        parts = [f"{r.endpoint}/{r.scope}={r.verdict.value if r.verdict else 'skip'}"
                 + (f"({r.code})" if r.verdict not in (None, Verdict.OK) else "") for r in self.results]
        return f"[kis_rank] 슬롯 {self.slot_kst} {self.status.value} — {' · '.join(parts)}{self.detail}"


class DayStatus(Enum):
    OK = "ok"
    WARN = "warn"
    CRIT = "crit"


@dataclass(frozen=True)
class DaySummary:
    day: dt.date
    status: DayStatus
    ok_interest: int = 0
    ok_hts: int = 0
    detail: str = ""

    @property
    def rc(self) -> int:
        return {DayStatus.OK: 0, DayStatus.WARN: 5, DayStatus.CRIT: 2}[self.status]

    def line(self) -> str:
        return (f"D={self.day:%Y%m%d} KIS 순위 스냅숏 ok 슬롯 관심 {self.ok_interest}/{N_SLOTS} · "
                f"HTS {self.ok_hts}/{N_SLOTS}{self.detail}")


def pick_slot(now: dt.datetime, cal: trading_calendar.Calendar) -> SlotPick:
    """`now`(시간대 있는 시각)가 든 슬롯. 날짜 · 거래일은 KST 로 본다 — 08:30 KST 는 전날 23:30 UTC 다."""
    k = now.astimezone(KST)
    if not cal.is_trading_day(k.date()):
        return SlotPick(SlotStatus.HOLIDAY, None, f"{k:%Y-%m-%d} 거래일 아님(판정 달력)")
    for t in reversed(SLOTS):
        start = dt.datetime.combine(k.date(), t, tzinfo=KST)
        if start <= k < start + SLOT_STEP:
            return SlotPick(SlotStatus.OK, f"{start:%Y-%m-%dT%H:%M}")
    return SlotPick(SlotStatus.OUTSIDE, None,
                    f"now={k:%Y-%m-%d %H:%M} KST — 슬롯 격자 {SLOTS[0]:%H:%M}~{SLOTS[-1]:%H:%M} 30분 밖")


def ensure_schema(con: sqlite3.Connection) -> None:
    for ddl in _DDL:
        con.execute(ddl)
    con.commit()


def _shape_problems(target: Target, rows: Sequence[Mapping[str, object]]) -> list[str]:
    probs = []
    if len(rows) != target.n_rows:
        probs.append(f"rows={len(rows)} expected={target.n_rows}")
    missing = sorted({c for r in rows for c in target.columns if c not in r})
    extra = sorted({str(k) for r in rows for k in r if k not in target.columns})
    if missing:
        probs.append(f"missing={','.join(missing)}")
    if extra:
        probs.append(f"unknown={','.join(extra)}")
    return probs


def _verdict(call_verdict: str) -> Verdict:
    return {"token": Verdict.TOKEN, "quota": Verdict.QUOTA}.get(call_verdict, Verdict.FAIL)


def _store_rows(con: sqlite3.Connection, slot: str, target: Target, rows: Sequence[Mapping[str, object]],
                received: str) -> None:
    keys = ("slot_kst", *(("scope",) if target.table == "kis_top_interest" else ()), "pos",
            *target.columns, "received_utc")
    lead: tuple[object, ...] = (slot, target.scope) if target.table == "kis_top_interest" else (slot,)
    vals = [(*lead, pos, *(None if r.get(c) is None else str(r.get(c)) for c in target.columns), received)
            for pos, r in enumerate(rows)]
    con.executemany(f"INSERT OR IGNORE INTO {target.table} ({', '.join(keys)}) "
                    f"VALUES ({', '.join('?' * len(keys))})", vals)


def _record(con: sqlite3.Connection, slot: str, target: Target, *, started: str, received: str | None,
            n_calls: int, verdict: Verdict, code: str, rows: Sequence[Mapping[str, object]] | None) -> None:
    rows_json = None if verdict is Verdict.OK or rows is None else json.dumps(list(rows), ensure_ascii=False)
    con.execute("INSERT OR REPLACE INTO kis_rank_runs (slot_kst, endpoint, scope, started_utc, received_utc, "
                "n_calls, verdict, msg_cd, n_rows, rows_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (slot, target.endpoint, target.scope, started, received, n_calls, verdict.value, code,
                 None if rows is None else len(rows), rows_json))


def _done(con: sqlite3.Connection, slot: str, target: Target) -> bool:
    row = con.execute("SELECT 1 FROM kis_rank_runs WHERE slot_kst=? AND endpoint=? AND scope=? "
                      "AND verdict IN ('ok', 'shape')", (slot, target.endpoint, target.scope)).fetchone()
    return row is not None


def collect(con: sqlite3.Connection, slot: str, clock: Callable[[], dt.datetime]) -> SlotResult:
    """슬롯 하나 — 아직 응답을 받지 못한 TR · 범위를 차례로 부르고 저장한다. 표는 `ensure_schema` 로 미리 만든다."""
    # 지연 import — 워치독 판정(summarize)은 원장 수집 모듈 없이 돈다
    from daily import kis_daily

    out: list[TargetResult] = []
    for target in TARGETS:
        if _done(con, slot, target):
            out.append(TargetResult(target.endpoint, target.scope, None))
            continue
        started = clock().strftime(TS_FORMAT)
        res = kis_daily.call(target.url, target.tr, target.params, target.out_key,
                             f"{target.endpoint} {target.scope} {slot}")
        if res.verdict in ("ok", "empty"):
            received = clock().strftime(TS_FORMAT)
            probs = _shape_problems(target, res.rows)
            verdict = Verdict.SHAPE if probs else Verdict.OK
            _store_rows(con, slot, target, res.rows, received)
            _record(con, slot, target, started=started, received=received, n_calls=res.n_calls,
                    verdict=verdict, code=res.code, rows=res.rows)
            out.append(TargetResult(target.endpoint, target.scope, verdict, len(res.rows), res.n_calls, res.code,
                                    " · ".join(probs)))
        else:
            verdict = _verdict(res.verdict)
            _record(con, slot, target, started=started, received=None, n_calls=res.n_calls, verdict=verdict,
                    code=res.code, rows=None)
            out.append(TargetResult(target.endpoint, target.scope, verdict, 0, res.n_calls, res.code))
        con.commit()
        print(f"[kis_rank] {slot} {target.endpoint} {target.scope} {out[-1].verdict.value if out[-1].verdict else ''} "
              f"rows={out[-1].n_rows} calls={out[-1].n_calls} code={out[-1].code} {out[-1].detail}".rstrip())
        if out[-1].verdict is Verdict.TOKEN:     # 재발급 1회 뒤에도 만료 — 나머지 TR 을 두드리지 않는다
            break
    verdicts = {r.verdict for r in out}
    if Verdict.SHAPE in verdicts:
        status = SlotStatus.SHAPE
    elif verdicts & {Verdict.TOKEN, Verdict.QUOTA, Verdict.FAIL}:
        status = SlotStatus.FAILED
    else:
        status = SlotStatus.OK
    probs = [f"{r.endpoint}/{r.scope} {r.detail}" for r in out if r.detail]
    return SlotResult(status, slot, tuple(out), f" | {' ; '.join(probs)}" if probs else "")


def run(db_path: Path, now: dt.datetime, cal: trading_calendar.Calendar,
        clock: Callable[[], dt.datetime] | None = None) -> SlotResult:
    """`now` 가 든 슬롯을 받는다. 격자 밖 · 휴장이면 파일을 열지 않는다."""
    pick = pick_slot(now, cal)
    if pick.slot_kst is None:
        return SlotResult(pick.status, None, (), pick.detail)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        ensure_schema(con)
        return collect(con, pick.slot_kst, clock or (lambda: dt.datetime.now(dt.UTC)))
    finally:
        con.close()


def summarize(db_path: Path, day: dt.date) -> DaySummary:
    """그날 격자 슬롯 중 ok 슬롯을 TR 마다 센다(관심은 범위가 모두 ok 여야 그 슬롯이 ok). 읽기만 한다."""
    if not db_path.exists():
        return DaySummary(day, DayStatus.CRIT, detail=f" | {db_path.name} 없음 — 수집기가 한 번도 돌지 않았다")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT slot_kst, endpoint, scope FROM kis_rank_runs WHERE verdict='ok' "
                           "AND slot_kst LIKE ?", (f"{day.isoformat()}T%",)).fetchall()
    except sqlite3.Error as e:
        return DaySummary(day, DayStatus.CRIT, detail=f" | {db_path.name} 읽기 실패 ({type(e).__name__}: {e})")
    finally:
        con.close()
    ok = set(rows)
    slots = [f"{day.isoformat()}T{t:%H:%M}" for t in SLOTS]
    n_ok: dict[str, int] = {}
    missing: list[str] = []
    for endpoint, scopes in ENDPOINT_SCOPES.items():
        bad = [s for s in slots if not all((s, endpoint, sc) in ok for sc in scopes)]
        n_ok[endpoint] = len(slots) - len(bad)
        if bad:
            missing.append(f"{endpoint} 빠짐 {','.join(s[-5:] for s in bad[:8])}{' …' if len(bad) > 8 else ''}")
    counts = n_ok.values()
    status = (DayStatus.CRIT if min(counts) == 0
              else DayStatus.WARN if min(counts) < WARN_MIN_OK else DayStatus.OK)
    return DaySummary(day, status, n_ok["top_interest"], n_ok["hts_top_view"],
                      f" | {' ; '.join(missing)}" if missing else "")


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="daily.kis_rank",
                                description="KIS 관심종목 등록 상위 · HTS 조회 상위 스냅숏 → data/raw/kis_rank.db (N-66)")
    p.add_argument("--db", default=None, help="원장 경로 (기본 $QL_HOME/data/raw/kis_rank.db)")
    p.add_argument("--summary", nargs="?", const="today", default=None, metavar="YYYYMMDD",
                   help="그날(기본 오늘 KST) ok 슬롯 수만 센다 — 수집하지 않는다")
    a = p.parse_args(argv)
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    db = Path(a.db) if a.db else base / "data" / "raw" / "kis_rank.db"
    cal = trading_calendar.load(base / "data" / "calendar" / "kis_holidays.json")
    now = dt.datetime.now(dt.UTC)
    if a.summary is not None:
        day = now.astimezone(KST).date() if a.summary == "today" else dt.datetime.strptime(  # noqa: DTZ007  # reason: 날짜만 쓴다
            a.summary, "%Y%m%d").date()
        if not cal.is_trading_day(day):
            print(f"[kis_rank] D={day:%Y%m%d} 거래일 아님 — 요약 없음")
            return 3
        s = summarize(db, day)
        print(s.line())
        return s.rc
    res = run(db, now, cal)
    print(res.line())
    return res.rc


if __name__ == "__main__":
    sys.exit(main())
