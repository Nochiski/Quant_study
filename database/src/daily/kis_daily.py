"""KIS 신용잔고(`kis_credit_balance`) 일일 증분 — 종목당 1콜 + 사실 중복 차단 저장.

플랜 `docs/plans/2026-09-09-daily-incremental.md` Task 1.4 / 결정 R6.
근거 `docs/reviews/2026-09-09-daily-findings-A-krx-kiwoom-kis.md` §1-4(DEFECT-A-03) · §2-3 · §6-3.

왜 새 저장 함수인가(DEFECT-A-03):
  `backfill_kis.py` 는 `backfill_dart.store()` 를 재사용하고, 그 `row_hash` 에는 요청창
  (`req_d1`·`req_d2`)이 섞여 있다. asof 축(30행/콜)은 창을 바꿔 다시 부르면 **같은
  (종목, deal_date) 사실이 새 행**으로 쌓인다 — 서버 실측으로 이미 1,084,443행(12.1%).
  일일 증분은 매일 창을 미루므로 그대로 두면 매일 30일치가 통째로 증식한다.
  그래서 삽입 직전에 `(req_ticker, deal_date)` 로 기존 행을 읽어 **payload(응답 컬럼) 가
  같으면 삽입하지 않는다**. 값이 다르면 새 행으로 남긴다(정정 보존).

왜 `row_hash` 산식은 그대로인가:
  기존 8,970,999행과 같은 테이블·같은 PK 를 쓴다. 해시 산식을 바꾸면 이미 있는 사실이
  전부 "새 행"이 되어 DEFECT-A-03 을 한 번 더 재현한다. 그래서 해시는
  `backfill_dart.row_hash`(req_* 포함) 를 **import 해서** 그대로 쓰고, 증식 차단은
  해시가 아니라 삽입 전 payload 대조로 한다.

창: `d1 = 오늘(T) − 40일`, `d2 = 오늘(T)`. `d2=T` 여야 `deal_date ≤ T−2세션 = D−1` 이 온다
  (KIS 신용잔고는 T+2 확정 — A §2-3 실측). `--date D` 는 창이 아니라 **완료 판정 기준일**을 옮긴다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import cast

from daily import calendar as trading_calendar
from daily import runlog, universe

TABLE = "kis_credit_balance"
DATE_COL = "deal_date"
REQ_NAME = "credit"                       # `req_name` — 백필이 남긴 값과 같아야 한다
META_COLS = frozenset({"row_hash", "dup_seq", "collected_at"})
WINDOW_DAYS = 40                          # 응답 30행(30영업일) 을 덮는 캘린더 폭
GATE_MIN_RATIO = 0.95                     # A §6-3 A · 플랜 Task 1.7 Step 2
KST = dt.timezone(dt.timedelta(hours=9))


class Status(Enum):
    """러너 종료 상태. rc 는 `CreditResult.rc`."""

    OK = "ok"
    PARTIAL = "partial"            # 일부 종목 실패, 그래도 완료 게이트는 통과
    GATE_FAILED = "gate_failed"
    DUP_GROWTH = "dup_growth"
    TOKEN_FAILED = "token_failed"
    QUOTA = "quota"
    DRY_RUN = "dry_run"


@dataclass(frozen=True)
class CreditResult:
    """한 번의 증분 실행 결과. 상태를 늘려도 `.rc`/`.ok` 를 읽는 호출부는 안 깨진다."""

    status: Status
    n_calls: int
    n_new_rows: int
    n_dup_skipped: int
    n_tickers: int
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    @property
    def rc(self) -> int:
        """0 = 성공(게이트 통과) · 2 = 게이트·토큰·유량 실패. 3(락·시각)은 셸이 낸다."""
        return 0 if self.status in (Status.OK, Status.PARTIAL, Status.DRY_RUN) else 2


@dataclass(frozen=True)
class CallOutcome:
    """단일 콜 결과. verdict 는 `backfill_kis.classify` 의 어휘를 그대로 쓴다."""

    verdict: str                   # ok · empty · token · quota · retry · error
    code: str
    rows: tuple[dict[str, object], ...]
    n_calls: int


@dataclass(frozen=True)
class StoreOutcome:
    n_new: int
    n_dup_skipped: int


@dataclass(frozen=True)
class GateResult:
    """A §6-3 A — `deal_date = D−1` 행수 / 요청 유니버스 ≥ 0.95 이고 종목당 1행."""

    ok: bool
    n_rows: int
    n_distinct_tickers: int
    n_requested: int
    ratio: float
    detail: str


def window(today: dt.date, days: int = WINDOW_DAYS) -> tuple[str, str]:
    """조회창 (d1, d2) — YYYYMMDD. d2 는 언제나 오늘(T)."""
    if days < 1:
        raise ValueError(f"window days must be >= 1: days={days} today={today}")
    return (today - dt.timedelta(days=days)).strftime("%Y%m%d"), today.strftime("%Y%m%d")


def fetch_credit(ticker: str, d1: str, d2: str) -> CallOutcome:
    """종목 1개 = 콜 1회. 재시도·토큰 재발급·유량 백오프는 `backfill_kis` 규칙을 그대로 쓴다.

    토큰 만료(EGW00121·EGW00123)는 캐시를 버리고 **1회** 재발급 후 재시도한다. 그래도
    만료면 조용히 넘기지 않고 verdict='token' 으로 올린다(호출부가 rc 2 로 만든다).
    `api.kis()` 가 콜당 0.3초를 쉬므로 별도 PACE 는 두지 않는다(실측 2.71콜/s).
    """
    # 지연 import — api·backfill_kis 는 import 시점에 kael .env 를 요구한다(api.py:14-21).
    # 자격증명이 없어도 --help·계획 출력은 살아 있어야 하므로 콜 직전에만 끌어온다.
    import api
    from backfill_kis import (
        QUOTA_RETRY,
        RETRY_BASE,
        RETRY_MAX,
        SPEC,
        classify,
        normalize,
    )

    spec = SPEC[REQ_NAME]
    # SPEC 은 값 타입이 섞인 평범한 dict 이라 호출·문자열 자리를 여기서 한 번만 좁힌다.
    url, tr, out_key = str(spec["url"]), str(spec["tr"]), str(spec["out"])
    make_params = cast("Callable[[str, str, str], dict[str, str]]", spec["params"])
    n_calls = 0
    token_retried = False
    for attempt in range(RETRY_MAX + 1):
        body: object = None
        status: int | None = 200
        try:
            body = api.kis(url, tr, make_params(ticker, d1, d2))
        except Exception as e:  # noqa: BLE001  # reason: 네트워크·JSON 예외는 재시도 대상 — classify(None, ...) 의 'retry/exc' 규약으로 흡수한다
            status, body = None, None
            print(f"    ! 콜 실패 — credit {ticker} {d1}~{d2} "
                  f"{type(e).__name__}: {str(e)[:90]}")
        n_calls += 1
        if status is None or isinstance(body, dict):
            verdict, code = classify(status, body)
        else:                                          # 응답이 dict 이 아니면 분류 자체가 불가능하다
            verdict, code = "error", f"resp_{type(body).__name__}"

        if verdict == "ok":
            rows, odd = normalize(body, out_key)
            if odd:
                print(f"    ? 응답 형태 이상({odd}) — credit {ticker} {d1}~{d2} rows={len(rows)}")
                code = f"{code}/{odd}"
            return CallOutcome("ok" if rows else "empty", code, tuple(rows), n_calls)
        if verdict == "token":
            if token_retried:
                return CallOutcome("token", code, (), n_calls)
            token_retried = True
            print(f"    · 토큰 만료({code}) — 재발급 후 1회 재시도 (credit {ticker})")
            api._kis_tok = None
            try:
                os.remove(api._KIS_CACHE)
            except OSError:
                pass
            time.sleep(1.0)
            continue
        cap = QUOTA_RETRY if verdict == "quota" else RETRY_MAX
        if verdict in ("retry", "quota") and attempt < cap:
            wait = max(RETRY_BASE * (2 ** attempt), 30.0) if verdict == "quota" \
                else RETRY_BASE * (2 ** attempt)
            print(f"    · {code} 재시도 {attempt + 1}/{cap} — {wait:.0f}초 (credit {ticker})")
            time.sleep(wait)
            continue
        return CallOutcome(verdict, code, (), n_calls)
    return CallOutcome("error", "retry_exhausted", (), n_calls)


def _table_exists(con: sqlite3.Connection) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (TABLE,)).fetchone()
    return row is not None


def _ensure_schema(con: sqlite3.Connection, cols: Sequence[str]) -> set[str]:
    """테이블·컬럼을 백필(`backfill_dart.store`)과 같은 규약으로 맞춘다(TEXT + row_hash PK).

    인덱스는 백필에 없던 것을 하나 더 건다 — 중복 차단이 매 콜마다 `(req_ticker, deal_date)`
    를 읽는데, PK 가 `row_hash` 단독이라 인덱스 없이는 종목마다 8,970,999행을 훑는다.
    컬럼·값은 그대로이므로 stage·survey 가 보는 스키마는 변하지 않는다.
    """
    have = {r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")}
    if not have:
        ddl = ", ".join(f'"{c}" TEXT' for c in cols)
        con.execute(f"CREATE TABLE {TABLE} (row_hash TEXT PRIMARY KEY, {ddl}, "
                    f"collected_at TEXT NOT NULL)")
        have = set(cols) | {"row_hash", "collected_at"}
    for c in cols:
        if c not in have:
            print(f"    [{TABLE}] 새 필드 {c} — 컬럼 추가")
            con.execute(f'ALTER TABLE {TABLE} ADD COLUMN "{c}" TEXT')
            have.add(c)
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_req_ticker_deal_date "
                f"ON {TABLE} (req_ticker, {DATE_COL})")
    return have


def _signature(values: Sequence[object]) -> tuple[str | None, ...]:
    """payload 비교용 정규화. None 과 빈 문자열은 구분한다(`row_hash` 와 같은 규약)."""
    return tuple(None if v is None else str(v) for v in values)


def store_new_facts(con: sqlite3.Connection, ticker: str, d1: str, d2: str,
                    rows: Sequence[dict[str, object]]) -> StoreOutcome:
    """응답을 적재하되 **이미 같은 사실이 있으면 넣지 않는다**(DEFECT-A-03).

    같은 `(req_ticker, deal_date)` 에 payload(요청 파라미터·`row_hash`·`dup_seq`·
    `collected_at` 을 뺀 응답 컬럼 전부)가 같은 행이 있으면 건너뛴다. 값이 하나라도
    다르면 새 행 — 정정을 지우지 않는다. 적재 컬럼·`row_hash` 산식은 백필과 동일해
    기존 행과 충돌하지 않는다.
    """
    if not rows:
        return StoreOutcome(0, 0)
    # 산식을 복제하지 않고 백필과 공유한다(지연 import 이유는 `fetch_credit` 주석 참고).
    from backfill_dart import row_hash

    keys = sorted(set().union(*(set(r) for r in rows)) - {"status", "message"})
    req = {"req_ticker": ticker, "req_d1": d1, "req_d2": d2, "req_name": REQ_NAME}
    req_keys = sorted(req)
    cols = keys + req_keys + ["dup_seq"]
    have = _ensure_schema(con, cols)
    payload_cols = sorted(c for c in have if c not in META_COLS and not c.startswith("req_"))
    quoted_payload = ",".join(f'"{c}"' for c in payload_cols)

    # 기존 사실을 (종목, deal_date) 단위로 읽어 다중집합으로 둔다. 같은 payload 가 응답에
    # 두 번 오면 원장에도 두 번 있어야 하므로 개수까지 센다.
    # 콜 1회당 조회도 1회다 — 날짜마다 따로 물으면 9백만 행을 30번 훑는다.
    dates = list({r.get(DATE_COL) for r in rows})
    real = [d for d in dates if d is not None]
    clauses: list[str] = []
    args: list[object] = [ticker]
    if real:
        clauses.append(f"{DATE_COL} IN ({','.join('?' * len(real))})")
        args += real
    if len(real) != len(dates):
        clauses.append(f"{DATE_COL} IS NULL")
    known: dict[object, Counter[tuple[str | None, ...]]] = {}
    for got in con.execute(f"SELECT {DATE_COL}, {quoted_payload} FROM {TABLE} "
                           f"WHERE req_ticker=? AND ({' OR '.join(clauses)})", args):
        known.setdefault(got[0], Counter())[_signature(got[1:])] += 1

    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
    dup_seq: Counter[str] = Counter()
    vals: list[list[object]] = []
    n_dup = 0
    for r in rows:
        sig = _signature([r.get(c) for c in payload_cols])
        pool = known.setdefault(r.get(DATE_COL), Counter())
        if pool[sig] > 0:                     # 이미 있는 사실 — 창만 다른 재수집이다
            pool[sig] -= 1
            n_dup += 1
            continue
        body = [r.get(k) for k in keys] + [req[k] for k in req_keys]
        base = row_hash(cols[:-1], body)
        n = dup_seq[base]                     # 이번 적재분 안에서의 내용 중복 순번(백필과 같은 규약)
        dup_seq[base] = n + 1
        v = [*body, str(n)]
        vals.append([row_hash(cols, v), *v, now])

    if not vals:
        return StoreOutcome(0, n_dup)
    ph = ",".join("?" * (len(cols) + 2))
    quoted = ",".join(f'"{c}"' for c in cols)
    before = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    # IGNORE 여야 collected_at 이 최초 관측 시각으로 남는다(백필과 같은 이유).
    con.executemany(
        f"INSERT OR IGNORE INTO {TABLE} (row_hash, {quoted}, collected_at) VALUES ({ph})", vals)
    con.commit()
    gained = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] - before
    return StoreOutcome(int(gained), n_dup + (len(vals) - int(gained)))


def payload_columns(con: sqlite3.Connection) -> list[str]:
    """응답 컬럼 = 테이블 컬럼 − 요청 파라미터(`req_*`) − 메타(`row_hash`·`dup_seq`·`collected_at`)."""
    cols = [str(r[1]) for r in con.execute(f"PRAGMA table_info({TABLE})")]
    return [c for c in cols if not c.startswith("req_") and c not in META_COLS]


def dup_pairs(con: sqlite3.Connection, since: str) -> int:
    """A §6-3 B — `deal_date >= since` 에서 **payload 까지 동일한** 행이 2건 이상인 `(req_ticker, deal_date)` 쌍의 수.

    값이 다른 정정(같은 키에 새 payload)은 세지 않는다 — 그것은 보존해야 할 사실이지 증식이 아니다.
    DEFECT-A-03 의 증식은 `req_d2` 만 다르고 payload 가 같은 행이므로 이 정의가 사고를 정확히 잡는다.
    """
    if not _table_exists(con):
        return 0
    pay = payload_columns(con)
    if not pay:
        return 0
    group = ", ".join(f'"{c}"' for c in pay)
    row = con.execute(
        f"SELECT COUNT(*) FROM (SELECT req_ticker, {group} FROM {TABLE} "
        f"WHERE {DATE_COL} >= ? GROUP BY req_ticker, {group} HAVING COUNT(*) > 1)", (since,)).fetchone()
    return 0 if row is None else int(row[0])


def gate(con: sqlite3.Connection, gate_date: str, n_requested: int,
         min_ratio: float = GATE_MIN_RATIO) -> GateResult:
    """완료 판정. `gate_date`(= D−2, 실측) 행수 / 요청 유니버스 ≥ min_ratio 이고 종목당 1행."""
    n_rows = n_tk = 0
    if _table_exists(con):
        row = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT req_ticker) FROM {TABLE} WHERE {DATE_COL} = ?",
            (gate_date,)).fetchone()
        if row is not None:
            n_rows, n_tk = int(row[0]), int(row[1])
    ratio = (n_rows / n_requested) if n_requested else 0.0
    ok = n_requested > 0 and ratio >= min_ratio and n_tk == n_rows
    detail = (f"deal_date={gate_date} n_rows={n_rows} n_tickers={n_tk} "
              f"requested={n_requested} ratio={ratio:.4f} min_ratio={min_ratio:.2f}")
    if not ok:
        if n_requested == 0:
            detail += " — 요청 유니버스가 비었다(키움 마스터 스냅샷 확인)"
        elif ratio < min_ratio:
            detail += (f" — 행수 부족(기대 >= {math.ceil(min_ratio * n_requested)}, "
                       f"실제 {n_rows})")
        else:
            detail += f" — 종목당 1행 위반(기대 n_tickers == n_rows, 실제 {n_tk} != {n_rows})"
    return GateResult(ok, n_rows, n_tk, n_requested, ratio, detail)


def run(con: sqlite3.Connection, *, date: str, gate_date: str, d1: str, d2: str,
        tickers: Sequence[str], run_db: str | os.PathLike[str] | None = None,
        dry_run: bool = False, limit: int = 0,
        min_ratio: float = GATE_MIN_RATIO) -> CreditResult:
    """유니버스 전 종목을 1콜씩 돌고, 완료 게이트와 중복 증가를 함께 판정한다.

    dry_run: 콜은 실제로 하되(--limit 만큼) 원장·runlog 에 쓰지 않는다. 게이트 수치는
    적재 전 원장 기준이라 판정 참고용이다(status 는 언제나 DRY_RUN, rc 0).
    """
    from backfill_kis import QUOTA_STREAK  # 지연 import 이유는 `fetch_credit` 주석 참고

    targets = list(tickers[:limit] if limit else tickers)
    dup_before = dup_pairs(con, d1)
    run_id = None
    if not dry_run and run_db is not None:
        run_id = runlog.start(run_db, date=date, source="kis_credit")

    n_calls = n_new = n_dup = 0
    failures: list[str] = []
    aborted: Status | None = None
    qstreak = 0
    for i, tk in enumerate(targets, 1):
        out = fetch_credit(tk, d1, d2)
        n_calls += out.n_calls
        if out.verdict == "token":
            aborted = Status.TOKEN_FAILED
            failures.append(f"{tk}:{out.code}")
            print(f"  ✖ 토큰 재발급 후에도 만료({out.code}) — {i}/{len(targets)} 번째 종목 {tk} "
                  f"에서 중단한다. 조용히 건너뛰지 않는다")
            break
        if out.verdict == "quota":
            qstreak += 1
            failures.append(f"{tk}:{out.code}")
            if qstreak >= QUOTA_STREAK:
                aborted = Status.QUOTA
                print(f"  ✖ 유량 초과가 {qstreak}종목 연속({out.code}) — 일일 한도로 보고 "
                      f"{i}/{len(targets)} 번째 종목 {tk} 에서 중단한다")
                break
            continue
        qstreak = 0
        if out.verdict in ("error", "retry"):
            failures.append(f"{tk}:{out.code}")
            continue
        if out.rows and not dry_run:
            st = store_new_facts(con, tk, d1, d2, out.rows)
            n_new += st.n_new
            n_dup += st.n_dup_skipped

    g = gate(con, gate_date, len(tickers), min_ratio)
    dup_after = dup_pairs(con, d1)
    detail = (f"{g.detail} | calls={n_calls} new_rows={n_new} dup_skipped={n_dup} "
              f"tickers={len(targets)}/{len(tickers)} window={d1}~{d2} "
              f"dup_pairs {dup_before}->{dup_after} failures={len(failures)}")
    if failures:
        detail += f" first_failures={failures[:5]}"

    if aborted is not None:
        status = aborted
    elif dry_run:
        status = Status.DRY_RUN
    elif dup_after > dup_before:
        status = Status.DUP_GROWTH
        detail += (f" — 사실 중복이 늘었다(DEFECT-A-03 재발): "
                   f"기대 {dup_before}, 실제 {dup_after}")
    elif not g.ok:
        status = Status.GATE_FAILED
    elif failures:
        status = Status.PARTIAL
    else:
        status = Status.OK

    if run_id is not None and run_db is not None:
        runlog.finish(run_db, run_id, status=status.value, n_calls=n_calls, n_rows=n_new,
                      detail=detail)
    return CreditResult(status, n_calls, n_new, n_dup, len(targets), detail)


def _parse_date(s: str) -> dt.date:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=KST).date()
        except ValueError:
            continue
    raise ValueError(f"--date 형식은 YYYY-MM-DD 또는 YYYYMMDD 여야 한다: got={s!r}")


def _requested_tickers(kw_db: str, *, state_path: str, seed_path: str,
                       dry_run: bool) -> universe.RequestedUniverse:
    """요청 유니버스. dry-run 은 유예 상태 파일도 건드리지 않는다(사본에 쓰게 한다)."""
    def _read(path: str) -> universe.RequestedUniverse:
        con = sqlite3.connect(f"file:{kw_db}?mode=ro", uri=True)
        try:
            return universe.requested(con, state_path=path, seed_path=seed_path)
        finally:
            con.close()

    if not dry_run:
        return _read(state_path)
    with tempfile.TemporaryDirectory(prefix="kis_daily_dry_") as tmp:
        copy = os.path.join(tmp, os.path.basename(state_path))
        if os.path.exists(state_path):
            shutil.copyfile(state_path, copy)
        return _read(copy)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="daily.kis_daily",
                                 description="KIS 신용잔고 일일 증분(종목당 1콜)")
    ap.add_argument("--date", help="완료 판정 기준 거래일 D (YYYY-MM-DD|YYYYMMDD). 기본 = 직전 거래일")
    ap.add_argument("--dry-run", action="store_true",
                    help="콜은 하되 원장·daily_run.db 에 쓰지 않고 판정만 출력")
    ap.add_argument("--limit", type=int, default=0, help="종목 수 상한(--dry-run 과 함께 쓴다)")
    ap.add_argument("--db", help="KIS 원장 (기본 $QL_HOME/data/raw/kis.db)")
    ap.add_argument("--kw-db", help="키움 원장 — 유니버스 마스터 (기본 $QL_HOME/data/raw/kiwoom.db)")
    a = ap.parse_args(argv)

    base = os.environ.get("QL_HOME") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db = a.db or os.path.join(base, "data", "raw", "kis.db")
    kw_db = a.kw_db or os.path.join(base, "data", "raw", "kiwoom.db")
    run_db = os.path.join(base, "data", "raw", "daily_run.db")
    cal = trading_calendar.load(os.path.join(base, "data", "calendar", "kis_holidays.json"))
    if cal.source != "kis_cache":
        print(f"  ! 캘린더 폴백 — {cal.detail}")

    today = dt.datetime.now(KST).date()
    d = _parse_date(a.date) if a.date else cal.prev_trading_day(today)
    # 실측(09-09 15:15·16:40 KST, T=09-09): d2=T 로 조회해도 max(deal_date)=09-04 = T-3 = D-2. 플랜 초안의 "D-1" 은
    # T+2 확정을 "T 에 T-2 까지 조회 가능" 으로 읽은 것이었다. 06:00 체인 기준으로 판정일은 D-2 다(프로브가 T-2 를 보이면 상향).
    gate_date = cal.prev_trading_day(d, n=2)
    d1, d2 = window(today)

    req = _requested_tickers(kw_db, state_path=os.path.join(base, "data", "daily",
                                                            "universe_kw.json"),
                             seed_path=os.path.join(base, "data", "jsonl", "tickers.txt"),
                             dry_run=a.dry_run)
    print(f"  요청 유니버스 {len(req.tickers):,}종목 (asof={req.asof} "
          f"seeded_only={len(req.seeded_only)} dropped={len(req.dropped)}) · "
          f"창 {d1}~{d2} · 판정일 {gate_date} · D={d:%Y%m%d}"
          + (" · dry-run" if a.dry_run else ""))

    con = sqlite3.connect(db, timeout=60)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        res = run(con, date=f"{d:%Y%m%d}", gate_date=gate_date.strftime("%Y%m%d"), d1=d1, d2=d2,
                  tickers=req.tickers, run_db=run_db, dry_run=a.dry_run, limit=a.limit)
    finally:
        con.close()
    mark = "✔" if res.rc == 0 else "✖"
    print(f"  {mark} status={res.status.value} calls={res.n_calls:,} new={res.n_new_rows:,} "
          f"dup_skipped={res.n_dup_skipped:,} tickers={res.n_tickers:,}\n     {res.detail}")
    return res.rc


if __name__ == "__main__":
    sys.exit(main())
