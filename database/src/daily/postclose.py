"""장 마감 직후 수집기 — 키움 ka10060(KRX 코드)의 오늘(T) 행 하나를 15:41~16:00 에 받는다.

컷오버 트랙 PR-1(정본 `docs/plans/2026-10-10-cutover-track.md` §3) · N-13 · N-35 · N-42 Q3 · T-2 · T-4.

  대상 순서  ① 직전 거래일 D' 의 아침 fi 판(`data/factor_inputs/_runs/<D'>_morning.json`)의 모델 후보
               (`fi_universe.eligible`) → ② v3 소비자 유니버스 나머지 — 인계 이력
               `data/deliver/history/<D'>_morning.json`(`equity.handoff`)이 가리키는 `universe_daily`
               판의 D' 행 중 compat `V3_STOCK_FILTER`(보통주·스팩, KOSPI·KOSDAQ). 각 묶음 안은 코드 순.
               D' 는 `daily.calendar` 의 직전 거래일. 대상 목록만 만들므로 인계 이력 health 는 보지 않고
               런 로그에 남긴다. 원장에 이미 dt=T 행이 있는 종목은 뺀다(다시 돌 때 남은 종목부터).
  원장       `data/raw/postclose.db` 표 `ka10060_investor_flows` — 키움 원장의 같은 TR 표 열(전부 TEXT,
               PK (ticker, dt)) + `fetched_at`(런 시작, UTC) + `price_valid`('1'/'0').
               `collected_at` 은 그 행을 받은 시각(UTC)이고 `INSERT OR IGNORE` 라 첫 관측이 남는다.
               키움 원장 표에 넣지 않는 이유는 T-4(첫 관측 규칙이 21:05 의 하루 전체 수급을 버리게 된다).
  락         자체 락 `LOCK_FILE` 만 비대기로 잡는다(쥐고 있으면 rc 3). 원장 락(`scripts/raw_lock.sh`)은
               기다리지 않는다 — 16:00 창(T-4).
  창         15:41:00 KST 전이면 받지 않는다(rc 3) — 크론 시각과 같다. KRX 정규장 수급 확정이 15:40
               (N-35 ②)이라 그 전 값을 첫 관측으로 굳히면 안 된다. 16:00:00 KST 부터 받은 행은
               `price_valid='0'`(가격 필드가 애프터마켓 값, N-35 ①) — 수급만 유효하다. 16:00 이 되면 남은
               종목은 부르지 않고 런 로그·요약에 남긴다(그 종목은 QL-D 가 21:05 저녁 값을 쓴다).
               세션 시각이 바뀌는 날(`daily.calendar.load_session_exceptions`, 수능일 등)은 rc 3 으로 건너뛴다.
  런 로그    `data/raw/daily_run.db` source `kiwoom_postclose` — 머리에 오류 비율, 콜 수 · 받은 T 행 수 ·
               후보/나머지 커버 · 못 받은 종목. 예상 밖 예외도 `error` 로 닫는다(running 으로 남기지 않음).

콜·재시도·속도 제한·T 행 고르기는 운영 수집기(`daily.kw_daily`)의 `TRS["ka10060"]`·`call_tr`·
`RATE_PER_SEC`·`pick_rows` 를 그대로 쓴다. 토큰 8005 는 `api.kiwoom` 이 1회 강제 재발급 뒤 재시도한다 —
그래도 실패하면 남은 종목을 두드리지 않고 rc 2.

종료코드: 0 완료·16:00 컷오프(cutoff)·16:00 뒤 시작(late)·거래일 아님 / 2 토큰 실패·전부 오류·
예상 밖 예외(error)·대상 판 없음 / 3 15:41 전·세션 예외일·락 경합·거래일 15:40~16:00 의 dry-run.
`--dry-run` 은 대상 미리보기다 — 판·대상 순서·이미 받은 종목을 출력하고 원장·런 로그에 쓰지 않는다.
실호출은 없다(15:41 전은 too_early, 거래일 15:40~16:00 은 거부, 16:00 뒤는 late). 실호출 점검은
`--check` — 창·세션 예외를 보지 않고 앞쪽 몇 종목(기본 `CHECK_DEFAULT_N`)만 불러 받은 행을 출력한다.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import sqlite3
import sys
import time
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import ModuleType

import duckdb
from compat.mappings import V3_STOCK_FILTER
from equity import handoff, inputs

from daily import calendar as trading_calendar
from daily import kw_daily as KW
from daily import runlog

KST = KW.KST
SOURCE = "kiwoom_postclose"
SPEC = KW.TRS["ka10060"]
TABLE = SPEC.table                         # 키움 원장과 같은 이름 — 파일(postclose.db)로 가른다
# 키움 원장 `ka10060_investor_flows` 의 응답 열(실측 survey v2) — 응답 전에 스키마를 세운다
COLS: tuple[str, ...] = ("dt", "cur_prc", "pred_pre", "acc_trde_prica", *KW.FLOW_KEYS)
EXTRA: tuple[str, ...] = ("fetched_at", "price_valid")
LOCK_FILE = "/tmp/quant_ledger_postclose.lock"
RUN_FLOOR_KST = dt.time(15, 41)            # 크론 시각과 같다. 수급 확정 15:40(N-35 ②) 뒤
DRY_RUN_BLOCK_KST = dt.time(15, 40)        # 거래일 [15:40, 16:00) dry-run 거부 — 운영 수집과 락·콜 한도를 다툰다
PRICE_CUTOFF_KST = dt.time(16, 0)          # N-35 ① 16:00 부터 가격이 애프터마켓 값
SEC_PER_CALL = 0.32                        # N-35 ③ 실측 ka10060 종목당 소요(소요 추정용)
CHECK_DEFAULT_N = 3
BASIS = "morning"
# check 모드가 받은 행을 출력하는 자리(종목, T 행, price_valid)
RowHook = Callable[[str, Sequence[Mapping[str, object]], bool], None]


class InputUnavailable(RuntimeError):
    """D' 판을 날짜로 찾지 못했다 — 그 묶음은 비우고 사유를 런 로그에 남긴다."""


class Status(Enum):
    OK = "ok"                          # 대상 전부를 16:00 전에 불렀다(이미 다 받았으면 콜 0)
    CUTOFF = "cutoff"                  # 16:00 에 남은 종목을 두고 멈췄다 — 정상 종료(warn)
    LATE = "late"                      # 16:00 뒤 시작 — 콜 0, 정상 종료(warn)
    SESSION_EXCEPTION = "session_exception"   # 세션 시각이 바뀌는 날 — 건너뜀(warn)
    TOKEN_FAILED = "token_failed"
    ERROR = "error"                    # 모든 콜이 오류 · 예상 밖 예외
    NO_TARGETS = "no_targets"
    TOO_EARLY = "too_early"


_RC = {Status.OK: 0, Status.CUTOFF: 0, Status.LATE: 0, Status.SESSION_EXCEPTION: 3,
       Status.TOKEN_FAILED: 2, Status.ERROR: 2, Status.NO_TARGETS: 2, Status.TOO_EARLY: 3}


@dataclass(frozen=True)
class Targets:
    """대상 순서 = ① 후보 → ② 유니버스 나머지. 판을 못 찾은 묶음은 비고 `notes` 에 사유.
    `have` = 원장에 이미 dt=T 행이 있어 뺀 종목 수."""

    d_prev: str
    candidates: tuple[str, ...]
    rest: tuple[str, ...]
    fi_build: str | None
    universe_build: str | None
    health: str = "없음"
    notes: tuple[str, ...] = ()
    have: int = 0

    @property
    def order(self) -> list[str]:
        return [*self.candidates, *self.rest]

    def keep(self, wanted: Callable[[str], bool]) -> Targets:
        return replace(self, candidates=tuple(t for t in self.candidates if wanted(t)),
                       rest=tuple(t for t in self.rest if wanted(t)))


@dataclass
class Collected:
    """한 런의 결과. 호출부가 만들어 넘기면 예외가 나도 그때까지의 진행이 남는다(M1).
    `got` 은 T 행을 받은 종목(dry-run 이면 받았을 뿐 쓰지 않은 종목)."""

    status: Status = Status.OK
    n_calls: int = 0
    n_rows: int = 0
    n_new: int = 0
    got: dict[str, bool] = field(default_factory=dict)       # 종목 → price_valid
    no_row: list[str] = field(default_factory=list)
    nodata: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    not_called: list[str] = field(default_factory=list)
    first_error: str = ""


# ── 시각 ──────────────────────────────────────────────────────────────────────
def _now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def _utc(t: dt.datetime) -> str:
    """원장 `collected_at` 형식(`kw_daily._now_utc` 와 같은 모양) — 초 미만은 버린다."""
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _at(day: dt.date, hm: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, hm, tzinfo=KST)


# ── 대상 판(날짜 고정 읽기 — 인계 이력은 equity.handoff, 판 해석은 equity.inputs.resolve) ──────
def _tickers(globs: Sequence[str], where: str, what: str) -> list[str]:
    """판 파티션(별칭 `u`)에서 조건에 맞는 종목(코드 순). 0 종목이면 판이 비었다고 본다(P1)."""
    lit = ", ".join("'" + g.replace("'", "''") + "'" for g in globs)
    con = duckdb.connect()
    try:
        got = con.execute(f"SELECT DISTINCT u.ticker FROM read_parquet([{lit}], "
                          f"hive_partitioning=false) u WHERE {where} ORDER BY u.ticker").fetchall()
    finally:
        con.close()
    if not got:
        raise InputUnavailable(f"{what} 0 종목")
    return [str(t) for (t,) in got]


def board_universe(base: Path, d_prev: str) -> tuple[list[str], str, str]:
    """② v3 소비자 유니버스 — 인계 이력 `<D'>_morning.json` 의 `universe_daily` 판, D' 행.
    돌려주는 값: (종목, 판 id, health 표기)."""
    h = handoff.load(base / "data" / "deliver" / "history" / f"{d_prev}_{BASIS}.json")
    health = ",".join(f"{k}:{v}" for k, v in sorted(h.health.items())) or "없음"
    bid = h.equity_builds.get("universe_daily")
    if not bid:
        raise InputUnavailable(f"인계 이력에 equity_builds.universe_daily 가 없다: D'={d_prev}")
    pb = inputs.resolve(base / "data" / "equity", "universe_daily", bid)
    iso = f"{d_prev[:4]}-{d_prev[4:6]}-{d_prev[6:]}"
    where = f"u.date = DATE '{iso}' AND {V3_STOCK_FILTER}"
    return _tickers(pb.globs, where, f"universe_daily {bid} 의 D'={d_prev} v3 유니버스"), bid, health


def board_candidates(base: Path, d_prev: str) -> tuple[list[str], str]:
    """① 모델 후보 — fi 판 manifest `_runs/<D'>_morning.json`(status ok)의 `fi_universe.eligible`."""
    rel = f"data/factor_inputs/_runs/{d_prev}_{BASIS}.json"
    path = base / rel
    if not path.exists():
        raise InputUnavailable(f"없음: {rel}")
    run = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(run, dict) or run.get("status") != "ok" or not run.get("build_id"):
        status = run.get("status") if isinstance(run, dict) else None
        raise InputUnavailable(f"D'={d_prev} fi 판이 성공 판이 아니다: status={status!r}")
    bid = str(run["build_id"])
    pb = inputs.resolve(base / "data" / "factor_inputs", "fi_universe", bid)
    return _tickers(pb.globs, "u.eligible", f"fi_universe {bid} 의 eligible"), bid


def resolve_targets(base: Path, d_prev: str) -> Targets:
    """두 묶음을 각각 읽는다. 하나가 없어도 나머지는 받는다(순서만 잃는다) — 사유는 `notes`."""
    notes: list[str] = []
    cand: list[str] = []
    uni: list[str] = []
    fi_build = uni_build = None
    health = "없음"
    # 판 파일이 없거나 깨져도(JSON·MANIFEST·parquet) 16:00 창의 다른 묶음 수집은 막지 않는다
    unusable = (InputUnavailable, OSError, ValueError, KeyError, TypeError, duckdb.Error)
    try:
        cand, fi_build = board_candidates(base, d_prev)
    except unusable as e:
        notes.append(f"fi 후보 판 없음 — {type(e).__name__}: {e}")
    try:
        uni, uni_build, health = board_universe(base, d_prev)
    except unusable as e:
        notes.append(f"v3 유니버스 판 없음 — {type(e).__name__}: {e}")
    seen = set(cand)
    return Targets(d_prev, tuple(cand), tuple(t for t in uni if t not in seen), fi_build,
                   uni_build, health, tuple(notes))


# ── 원장 ──────────────────────────────────────────────────────────────────────
def connect(db_path: Path) -> sqlite3.Connection:
    """원장을 열고 스키마를 보장한다(멱등 — 키움 원장과 같은 `kw_daily.ensure_table` 규약)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=60)
    KW.ensure_table(con, TABLE, COLS, extra=EXTRA)
    return con


def ledger_has(db_path: Path, t: str) -> set[str]:
    """원장에 이미 dt=T 행이 있는 종목(읽기 전용). 파일·표가 없으면 빈 집합."""
    if not db_path.exists():
        return set()
    con = sqlite3.connect(db_path.absolute().as_uri() + "?mode=ro", uri=True)
    try:
        if not KW._table_exists(con, TABLE):
            return set()
        return {str(r[0]) for r in con.execute(
            f'SELECT DISTINCT ticker FROM "{TABLE}" WHERE dt = ?', (t,))}
    finally:
        con.close()


def insert_first(con: sqlite3.Connection, cols: Sequence[str], ticker: str,
                 rows: Sequence[Mapping[str, object]], *, collected_at: str, fetched_at: str,
                 price_valid: bool) -> int:
    """T 행을 `INSERT OR IGNORE` 로 넣는다 — 이미 있는 (ticker, dt) 는 첫 관측을 지킨다.
    돌려주는 값은 새로 넣은 행 수."""
    names = ["ticker", *cols, "src_api", "collected_at", *EXTRA]
    quoted = ",".join(f'"{n}"' for n in names)
    holes = ",".join("?" * len(names))
    payload = [[ticker, *[None if r.get(c) is None else str(r.get(c)) for c in cols], SPEC.api_id,
                collected_at, fetched_at, "1" if price_valid else "0"] for r in rows]
    before = con.total_changes
    con.executemany(f'INSERT OR IGNORE INTO "{TABLE}" ({quoted}) VALUES ({holes})', payload)
    con.commit()
    return con.total_changes - before


# ── 수집 ──────────────────────────────────────────────────────────────────────
def collect(order: Sequence[str], *, date: str, cutoff: dt.datetime, client: ModuleType,
            fetched_at: str, con: sqlite3.Connection | None, stop_at_cutoff: bool = True,
            on_row: RowHook | None = None, res: Collected | None = None) -> Collected:
    """종목당 1콜(운영 수집기와 같은 간격). `con` 이 None 이면 쓰지 않는다(dry-run·check).

    `stop_at_cutoff` 면 부르기 직전 시각이 16:00 이상일 때 멈추고 남은 종목을 `not_called` 에 둔다
    (한 콜도 못 했으면 late). `price_valid` 는 응답을 **받은** 시각으로 판정한다(콜이 16:00 을 넘겨
    끝나면 그 행은 무효). 토큰 실패(`api.kiwoom` 의 1회 강제 재발급까지 실패)면 그 종목은 `errors`,
    뒤는 `not_called`. 부른 콜이 전부 오류면 error.
    """
    res = Collected() if res is None else res
    cols = list(COLS)
    gap = 1.0 / KW.RATE_PER_SEC
    for i, ticker in enumerate(order):
        if stop_at_cutoff and _now_kst() >= cutoff:
            res.status = Status.CUTOFF if res.n_calls else Status.LATE
            res.not_called = list(order[i:])
            break
        started = time.time()
        out = KW.call_tr(client, SPEC, ticker, date)
        received = _now_kst()
        res.n_calls += 1
        if out.status is KW.CallStatus.TOKEN:
            res.status, res.not_called = Status.TOKEN_FAILED, list(order[i + 1:])
            res.errors.append(ticker)
            res.first_error = f"token code={out.code} ticker={ticker} {out.detail}"
            break
        if out.status is KW.CallStatus.ERROR:
            res.errors.append(ticker)
            res.first_error = res.first_error or out.detail
            print(f"[postclose] 콜 실패 — date={date} {out.detail}", file=sys.stderr)
        elif out.status is KW.CallStatus.NODATA:
            res.nodata.append(ticker)
        else:
            rows = KW.pick_rows(SPEC.api_id, out.rows, date)
            if not rows:
                res.no_row.append(ticker)
            else:
                valid = received < cutoff
                res.got[ticker] = valid
                res.n_rows += len(rows)
                if on_row is not None:
                    on_row(ticker, rows, valid)
                if con is not None:
                    new = [k for k in rows[0] if k not in cols]
                    if new:                     # 응답에 새 필드 — 열을 덧붙인다(키움 원장 규약)
                        cols += new
                        KW.ensure_table(con, TABLE, cols, extra=EXTRA)
                    res.n_new += insert_first(con, cols, ticker, rows, collected_at=_utc(received),
                                              fetched_at=fetched_at, price_valid=valid)
        time.sleep(max(0.0, gap - (time.time() - started)))
    if (res.status in (Status.OK, Status.CUTOFF) and res.n_calls
            and len(res.errors) == res.n_calls):
        res.status = Status.ERROR
    return res


def _cover(group: Sequence[str], got: Mapping[str, bool]) -> str:
    n_got = sum(1 for t in group if t in got)
    n_valid = sum(1 for t in group if got.get(t))
    return f"{n_got}/{len(group)}(price_valid {n_valid})"


def detail(t: str, tg: Targets, res: Collected) -> str:
    """런 로그·요약 한 줄 — 머리에 오류 비율, 못 받은 종목은 사유별 목록으로 끝에 싣는다."""
    rate = 100.0 * len(res.errors) / res.n_calls if res.n_calls else 0.0
    head = (f"errors={len(res.errors)}/{res.n_calls}({rate:.1f}%) T={t} Dp={tg.d_prev} "
            f"targets={len(tg.order)} have={tg.have} cand={_cover(tg.candidates, res.got)} "
            f"rest={_cover(tg.rest, res.got)} calls={res.n_calls} rows={res.n_rows} "
            f"new={res.n_new} "
            f"price_invalid={sum(1 for v in res.got.values() if not v)} no_row={len(res.no_row)} "
            f"nodata={len(res.nodata)} not_called={len(res.not_called)} "
            f"fi_build={tg.fi_build} universe_build={tg.universe_build} health={tg.health}")
    parts = [head]
    if tg.notes:
        parts.append("inputs: " + "; ".join(tg.notes))
    if res.not_called:
        parts.append(f"not_called({res.status.value})=" + ",".join(res.not_called))
    for name, lst in (("no_row", res.no_row), ("nodata", res.nodata),
                      ("error_tickers", res.errors)):
        if lst:
            parts.append(f"{name}=" + ",".join(lst))
    if res.first_error:
        parts.append(f"first_error={res.first_error}")
    return " | ".join(parts)


# ── CLI ───────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def own_lock(path: str) -> Iterator[bool]:
    """자체 락을 비대기로 잡는다. 못 잡으면 False(기다리지 않는다 — 16:00 창)."""
    with open(path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _print_check_row(ticker: str, rows: Sequence[Mapping[str, object]], valid: bool) -> None:
    r = rows[0]
    keys = ("cur_prc", "pred_pre", "acc_trde_prica", "ind_invsr", "frgnr_invsr", "orgn")
    extra = [k for k in r if k not in COLS]
    print(f"[postclose] check {ticker} T행={len(rows)} price_valid(16:00 기준)={int(valid)} "
          + " ".join(f"{k}={r.get(k)}" for k in keys) + f" 새 필드={extra}")


def _skip(run_db: Path, t: str, status: Status, why: str, write: bool) -> int:
    """하루를 건너뛰는 종료(15:41 전 · 세션 예외일) — 사유를 요약·런 로그에 남긴다."""
    print(f"[postclose] T={t} {status.value} — {why}", file=sys.stderr)
    if write:
        rid = runlog.start(run_db, date=t, source=SOURCE)
        runlog.finish(run_db, rid, status=status.value, detail=why)
    return _RC[status]


def _run(*, t: str, t_day: dt.date, d_prev: str, base: Path, db_path: Path, run_db: Path,
         limit: int, dry_run: bool, check: bool) -> int:
    """창·세션 판정 → 대상 → 수집 → 런 로그. 예상 밖 예외는 이 경계 한 곳에서 `error`·rc 2 로 닫는다
    (M1 — 토큰 재발급 응답에 token 이 없으면 `api._kw_token` 이 KeyError 를 낸다)."""
    write = not (dry_run or check)
    rid: int | None = None
    tg: Targets | None = None
    res = Collected()
    try:
        sessions = trading_calendar.load_session_exceptions(base / "data" / "calendar")
        start = _now_kst()
        if t in sessions:
            if not check:
                return _skip(run_db, t, Status.SESSION_EXCEPTION,
                             f"세션 예외일 — {sessions[t]} (장 마감 직후 수집 안 함)", write)
            print(f"[postclose] T={t} 세션 예외일({sessions[t]}) — check 는 계속한다")
        if not check and start < _at(t_day, RUN_FLOOR_KST):
            return _skip(run_db, t, Status.TOO_EARLY,
                         f"now={start:%H:%M:%S} KST < {RUN_FLOOR_KST:%H:%M} (크론 시각, 수급 확정 "
                         f"15:40 뒤 — N-35 ②)", write)
        if write:
            rid = runlog.start(run_db, date=t, source=SOURCE)
        con = connect(db_path) if write else None      # 대상 0 이어도 스키마는 선다
        try:
            tg = resolve_targets(base, d_prev)
            if not tg.order:
                res.status = Status.NO_TARGETS
            elif not check:
                have = ledger_has(db_path, t)
                tg = replace(tg.keep(lambda x: x not in have),
                             have=sum(1 for x in tg.order if x in have))
            if check or limit > 0:
                order = tg.order[:limit or CHECK_DEFAULT_N]
                tg = tg.keep(lambda x: x in order)
            mode = "check" if check else ("dry-run" if dry_run else "run")
            print(f"[postclose] T={t} D'={d_prev} mode={mode} 후보={len(tg.candidates)} "
                  f"나머지={len(tg.rest)} 대상={len(tg.order)} "
                  f"이미 받음={tg.have} 예상 소요≈{SEC_PER_CALL}초×{len(tg.order)}="
                  f"{SEC_PER_CALL * len(tg.order):.0f}초 fi_build={tg.fi_build} "
                  f"universe_build={tg.universe_build} health={tg.health}")
            for note in tg.notes:
                print(f"[postclose] 입력 판 결손 — {note}", file=sys.stderr)
            if res.status is Status.OK and tg.order:
                collect(tg.order, date=t, cutoff=_at(t_day, PRICE_CUTOFF_KST),
                        client=KW._kiwoom_module(), fetched_at=_utc(start), con=con,
                        stop_at_cutoff=not check,
                        on_row=_print_check_row if check else None, res=res)
        finally:
            if con is not None:
                con.close()
        line = detail(t, tg, res)
        print(f"[postclose] status={res.status.value} {line}")
        if rid is not None:
            runlog.finish(run_db, rid, status=res.status.value, n_calls=res.n_calls,
                          n_rows=res.n_rows, detail=line)
        return _RC[res.status]
    except Exception as e:  # noqa: BLE001  # reason: 어떤 예외도 런 로그를 running 으로 남기지 않는다(M1)
        traceback.print_exc()
        why = f"{type(e).__name__}: {e}"
        line = f"{detail(t, tg, res)} | error={why}" if tg is not None else f"error={why}"
        print(f"[postclose] status=error {line}", file=sys.stderr)
        if write:
            if rid is None:
                rid = runlog.start(run_db, date=t, source=SOURCE)
            runlog.finish(run_db, rid, status=Status.ERROR.value, n_calls=res.n_calls,
                          n_rows=res.n_rows, detail=line)
        return _RC[Status.ERROR]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="daily.postclose",
        description="15:41 장 마감 수집 — 키움 ka10060(KRX) T 행 → data/raw/postclose.db (PR-1)")
    p.add_argument("--date", default=None, help="대상 거래일 T YYYYMMDD (기본: 오늘 KST)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="대상 미리보기(판·대상 순서·이미 받은 종목), 실호출 없음 — 원장·daily_run.db 에 "
                           "쓰지 않는다. 거래일 15:40~16:00 에는 거부(rc 3). 실호출 점검은 --check")
    mode.add_argument("--check", action="store_true",
                      help=f"읽기 전용 실호출 점검 — 창·세션 예외를 보지 않고 앞쪽 --limit "
                           f"(기본 {CHECK_DEFAULT_N})종목만 불러 받은 T 행을 출력, "
                           f"아무 데도 쓰지 않는다")
    p.add_argument("--limit", type=int, default=0, help="앞쪽 N 종목만(시험용)")
    p.add_argument("--db", default=None, help="원장 경로 (기본 data/raw/postclose.db)")
    a = p.parse_args(argv)

    base = Path(KW._base())
    cal = trading_calendar.load(base / "data" / "calendar" / "kis_holidays.json")
    now = _now_kst()
    t = a.date or now.strftime("%Y%m%d")
    t_day = KW._parse_date(t)
    if not cal.is_trading_day(t_day):
        print(f"[postclose] T={t} 거래일 아님(판정 달력) — 건너뜀")
        return 0
    if (a.dry_run and cal.is_trading_day(now.date())
            and _at(now.date(), DRY_RUN_BLOCK_KST) <= now < _at(now.date(), PRICE_CUTOFF_KST)):
        print(f"[postclose] 거래일 {DRY_RUN_BLOCK_KST:%H:%M}~{PRICE_CUTOFF_KST:%H:%M} 에는 dry-run 을 "
              f"하지 않는다 — 운영 수집과 락·콜 한도를 다툰다(--check 를 쓴다)", file=sys.stderr)
        return 3
    d_prev = cal.prev_trading_day(t_day).strftime("%Y%m%d")
    with own_lock(LOCK_FILE) as held:
        if not held:
            print(f"[postclose] T={t} 다른 장 마감 수집이 {LOCK_FILE} 을 쥐고 있다 — 기다리지 않고 "
                  f"종료(16:00 창, T-4)", file=sys.stderr)
            return 3
        return _run(t=t, t_day=t_day, d_prev=d_prev, base=base,
                    db_path=Path(a.db) if a.db else base / "data" / "raw" / "postclose.db",
                    run_db=base / "data" / "raw" / "daily_run.db", limit=a.limit,
                    dry_run=a.dry_run, check=a.check)


if __name__ == "__main__":
    sys.exit(main())
