"""키움 일일 증분 러너 — `--fetch`(06:00 체인) / `--merge`(08:10 체인). 플랜 Task 1.3 · R5.

백필(`backfill_kw.py`)은 동결이므로 TR 스펙 4개를 이 파일에 복사했다(출처는 각 항목 주석).
그쪽 모듈은 import 시점에 `TOK = A._kw_token()` 으로 실토큰을 발급하므로 import 재사용이 불가능하다.
`ingest_shard` 는 읽지도 쓰지도 않는다 — 백필의 재개 상태를 증분이 오염시키면 과거 구간 복구가 막힌다.

두 단계로 나뉜 이유(R1): 키움 4 TR 은 06:00 에 T-1 이 이미 차 있지만 대조 상대인 KRX 는 T+1 08:00
공표다. 그래서 06:00 에는 콜만 하고 `_kw_incoming_<tr>` 에 세워둔 뒤 오염 게이트 (b) 만 판정하고,
KRX 가 도착한 08:10 에 크로스소스 (c) 를 통과한 것만 원장에 머지한다 (d).

응답이 역방향(최신→과거)이라 1콜이 캡만큼(ka10014 372 · ka10060 100 · ka20068 100 · ka10008 50)
과거를 함께 준다. 그래서 갭이 며칠이든 **1회 실행 = 유니버스 × 4콜**이다.

종료코드(플랜 §4 공통 규약): 0 성공 / 2 게이트·토큰 실패 / 3 시각·대기 제약.
`--dry-run` 은 콜은 하되(`--limit` 만큼) 원장·incoming·`daily_run.db`·유니버스 상태에 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from types import ModuleType

from daily import calendar as trading_calendar
from daily import runlog, universe

KST = dt.timezone(dt.timedelta(hours=9))
RATE_PER_SEC = 4.4          # TR 당. 실측 상한 5.0 아래(backfill_kw.py:27). api.kiwoom 이 콜당 0.25s 쉰다
BACKOFF_SEC = 0.5           # 429 복구 실측 419~759ms(backfill_kw.py:28)
MAX_RETRY = 4
STALE_PCT_MAX = 30.0        # 오염 게이트 (b). 실측 정상일 9.7% / 오염일 99.0% (findings A §3-2)
GRACE_DAYS = 5              # 유니버스 유예(R5)
INCOMING_PREFIX = "_kw_incoming_"
FETCH_SOURCE = "kiwoom_fetch"
MERGE_SOURCE = "kiwoom_merge"
_META_COLS = ("ticker", "src_api", "collected_at", "fetched_at")


# ── TR 정의 (출처: backfill_kw.py:34-60 의 TRS. 백필 동결이라 복사한다) ─────────────
@dataclass(frozen=True)
class TrSpec:
    """1콜에 필요한 것 전부. `body(ticker, floor, end_dt)` 는 백필과 동일한 요청 본문을 만든다."""

    api_id: str
    url: str
    table: str
    rows_key: str | None        # None 이면 응답의 첫 list 값
    cap: int                    # 콜당 최대 행수(실측)
    floor: str                  # 요청 하한. 1콜이면 cap 이 실질 상한이라 최댓값 커버용으로만 쓴다
    cols: tuple[str, ...] | None  # None 이면 첫 응답에서 추론(ka10060)
    body: Callable[[str, str, str], dict[str, str]]


TRS: dict[str, TrSpec] = {
    "ka10014": TrSpec(
        api_id="ka10014", url="/api/dostk/shsa", table="ka10014_short_selling",
        # 응답 리스트 키는 `shrts_trnsn` 실측(probe_kw_timing.py:25). backfill_kw.py:36 은 "shrts" 로
        # 적혀 있으나 extract() 가 첫 list 로 폴백해 우연히 동작한 것이다.
        rows_key="shrts_trnsn", cap=372, floor="20080623",
        cols=("dt", "close_pric", "pred_pre_sig", "pred_pre", "flu_rt", "trde_qty",
              "shrts_qty", "ovr_shrts_qty", "trde_wght", "shrts_trde_prica", "shrts_avg_pric"),
        body=lambda tk, s, e: {"stk_cd": tk, "tm_tp": "1", "strt_dt": s, "end_dt": e}),
    "ka20068": TrSpec(
        api_id="ka20068", url="/api/dostk/slb", table="ka20068_lending_balance",
        rows_key=None, cap=100, floor="20110725",
        cols=("dt", "dbrt_trde_cntrcnt", "dbrt_trde_rpy", "dbrt_trde_irds", "rmnd", "remn_amt"),
        body=lambda tk, s, e: {"stk_cd": tk, "strt_dt": s, "end_dt": e, "all_tp": "0"}),
    "ka10060": TrSpec(
        api_id="ka10060", url="/api/dostk/chart", table="ka10060_investor_flows",
        rows_key=None, cap=100, floor="20100101", cols=None,
        # dt 는 조회 "종료일" 이다 — 구간이 아니라 끝점만 받는다
        body=lambda tk, s, e: {"dt": e, "stk_cd": tk, "amt_qty_tp": "1", "trde_tp": "0",
                               "unit_tp": "1000"}),
    "ka10008": TrSpec(
        api_id="ka10008", url="/api/dostk/frgnistt", table="ka10008_foreign_holdings",
        rows_key="stk_frgnr", cap=50, floor="20091101",
        cols=("dt", "close_pric", "pred_pre", "trde_qty", "chg_qty", "poss_stkcnt", "wght",
              "gain_pos_stkcnt", "frgnr_limit", "frgnr_limit_irds", "limit_exh_rt"),
        # 날짜 파라미터가 없다 — 호출 시점 최신 50영업일이 온다. 그래서 dt > D 행이 섞이고(머지에서 버림)
        # 장중에 돌리면 그날 행이 전일 잔고 복사본으로 온다(오염 게이트 (b) 가 잡는다).
        body=lambda tk, s, e: {"stk_cd": tk}),
}

GATE_TR = "ka10008"          # 오염·크로스소스 게이트의 판정 대상

# 키움 오류코드표 분류(backfill_kw.py:108-112)
_RATE_CODES = frozenset({"1700", "1701", "1702"})
_NODATA_CODES = frozenset({"1901", "1902", "1903"})       # 종목정보 없음 — 폐지종목 등, 정상
_TOKEN_CODES = frozenset({"8005", "8001"})
_CODE_RE = re.compile(r"\[(\d{4})[:\]]")


# ── 결과 값 타입 (python.md: 성공/실패는 위치형 튜플이 아니라 Result) ──────────────
class CallStatus(Enum):
    OK = "ok"
    NODATA = "nodata"
    TOKEN = "token"
    ERROR = "error"


@dataclass(frozen=True)
class CallOutcome:
    status: CallStatus
    rows: list[dict[str, object]]
    code: str | None
    detail: str


@dataclass(frozen=True)
class TrFetch:
    """TR 하나의 fetch 결과. `holdings_*` 는 ka10008 에서만 채워진다(오염 게이트 입력)."""

    api_id: str
    n_calls: int
    n_rows: int
    n_nodata: int
    n_error: int
    token_failed: bool
    detail: str
    holdings_d: dict[str, str] = field(default_factory=dict)
    holdings_prev: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StaleGate:
    """오염 게이트 (b) — `dt=D` 의 `poss_stkcnt` 가 `D-1` 과 같은 종목 비율."""

    n_compared: int
    n_stale: int
    basis: str                  # 'ledger' | 'incoming' | 'none'

    @property
    def pct(self) -> float | None:
        return None if self.n_compared == 0 else round(100.0 * self.n_stale / self.n_compared, 1)

    @property
    def passed(self) -> bool:
        pct = self.pct
        return True if pct is None else pct <= STALE_PCT_MAX


class FetchStatus(Enum):
    OK = "ok"
    GATE_FAILED = "gate_failed"
    TOKEN_FAILED = "token_failed"
    TOO_EARLY = "too_early"


@dataclass(frozen=True)
class FetchResult:
    status: FetchStatus
    date: str
    n_universe: int
    n_calls: int
    n_rows: int
    gate: StaleGate
    detail: str

    @property
    def ok(self) -> bool:
        return self.status is FetchStatus.OK


@dataclass(frozen=True)
class Quote:
    """대조용 종가·거래량. 파싱 실패는 None 으로 남기고 불일치로 센다(조용한 통과 금지)."""

    close_krw: int | None
    volume: int | None


@dataclass(frozen=True)
class CrossCheck:
    """크로스소스 게이트 (c) — KRX 원장 ↔ 키움 incoming."""

    n_matched: int
    n_same_close: int
    n_same_vol: int
    samples: tuple[str, ...] = ()      # 불일치 예시(최대 5)

    @property
    def passed(self) -> bool:
        return self.n_matched > 0 and self.n_same_close == self.n_matched and \
            self.n_same_vol == self.n_matched


@dataclass(frozen=True)
class TrMerge:
    api_id: str
    n_merged: int
    n_dropped_future: int


class MergeStatus(Enum):
    OK = "ok"
    NO_INCOMING = "no_incoming"
    CROSS_SOURCE_FAILED = "cross_source_failed"
    KRX_PENDING = "krx_pending"
    TOO_EARLY = "too_early"


@dataclass(frozen=True)
class MergeResult:
    status: MergeStatus
    date: str
    cross: CrossCheck
    merged: tuple[TrMerge, ...]
    detail: str

    @property
    def n_rows(self) -> int:
        return sum(m.n_merged for m in self.merged)

    @property
    def n_dropped_future(self) -> int:
        return sum(m.n_dropped_future for m in self.merged)

    @property
    def ok(self) -> bool:
        return self.status is MergeStatus.OK


# ── 저수준 헬퍼 ────────────────────────────────────────────────────────────────
def _now_utc() -> str:
    """원장 `collected_at` 형식 — 서버 TZ=UTC 의 기존 값과 같은 모양(`2026-08-24T00:19:33`)."""
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _text(v: object) -> str | None:
    return None if v is None else str(v)


def _to_int(v: object) -> int | None:
    """'1,234' / '+1234' / '-70000' → int. 실패는 None(불일치로 집계되어 게이트가 큰 소리로 막는다)."""
    s = str(v).replace(",", "").replace("+", "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:                       # '70000.0' 같은 실수 표기 방어
            return int(float(s))
        except ValueError:
            return None


def _rows(payload: Mapping[str, object], key: str | None) -> list[dict[str, object]]:
    """응답에서 행 리스트를 꺼낸다. 키가 없거나 list 가 아니면 첫 list 값(probe_kw_timing._rows 규칙)."""
    if key is not None:
        named = payload.get(key)
        if isinstance(named, list):
            return [r for r in named if isinstance(r, dict)]
    for v in payload.values():
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
    return []


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (table,)).fetchone() is not None


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in con.execute(f'PRAGMA table_info("{table}")')]


def ensure_table(con: sqlite3.Connection, table: str, cols: Sequence[str],
                 extra: Sequence[str] = ()) -> None:
    """`ticker + cols + src_api + collected_at + extra` 스키마를 보장한다(전 컬럼 TEXT, PK (ticker, dt)).

    백필과 같은 규약(`backfill_kw.ensure_table`) — 응답에 새 필드가 나오면 컬럼을 덧붙인다.
    첫 응답으로 컬럼셋을 고정하면 과거에만 있는 필드가 조용히 버려진다.
    """
    if "dt" not in cols:
        raise ValueError(f"kiwoom table needs a dt column for PK (ticker, dt): "
                         f"table={table} cols={list(cols)}")
    tail = [*cols, "src_api", "collected_at", *extra]
    if not _table_exists(con, table):
        ddl = ",\n  ".join(f'"{c}" TEXT' for c in tail)
        con.execute(f'CREATE TABLE "{table}" (\n  "ticker" TEXT NOT NULL,\n  {ddl},\n'
                    '  PRIMARY KEY ("ticker", "dt")\n)')
    have = set(_columns(con, table))
    for c in tail:
        if c not in have:
            con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}" TEXT')
    con.commit()


def insert_rows(con: sqlite3.Connection, table: str, cols: Sequence[str], ticker: str,
                rows: Iterable[Mapping[str, object]], api_id: str, stamp: str,
                extra: Mapping[str, str] | None = None) -> int:
    """응답 행을 그대로 적재한다(전 컬럼 TEXT, `INSERT OR REPLACE` — PK 로 과거 정정이 자연 반영)."""
    ex = dict(extra or {})
    names = ["ticker", *cols, "src_api", "collected_at", *ex]
    quoted = ",".join(f'"{n}"' for n in names)
    holes = ",".join("?" * len(names))
    payload = [[ticker, *[_text(r.get(c)) for c in cols], api_id, stamp, *ex.values()]
               for r in rows]
    con.executemany(f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({holes})', payload)
    return len(payload)


def _kiwoom_module() -> ModuleType:
    """`api` 는 import 시점에 .env 를 읽는다 — merge 경로에서는 로드하지 않는다(probe_kw_timing 과 동일)."""
    import api
    return api


def call_tr(client: ModuleType, spec: TrSpec, ticker: str, end_dt: str) -> CallOutcome:
    """종목 1개에 TR 1콜. 유량 코드만 백오프 재시도하고, 나머지는 사유를 담아 돌려준다.

    8005(토큰 무효)는 `api.kiwoom` 이 이미 1회 강제 재발급 후 재시도한다. 여기까지 오면 그 재시도도
    실패한 것이므로 조용히 건너뛰지 않고 TOKEN 으로 올려 실행 전체를 rc 2 로 세운다(플랜 §4).
    """
    body = spec.body(ticker, spec.floor, end_dt)
    last = "no attempt"
    for _ in range(MAX_RETRY):
        payload, _headers = client.kiwoom(spec.api_id, spec.url, body)
        if not isinstance(payload, dict):
            last = (f"non-dict response — api={spec.api_id} ticker={ticker} "
                    f"type={type(payload).__name__}")
            time.sleep(BACKOFF_SEC)
            continue
        rc = payload.get("return_code")
        msg = str(payload.get("return_msg", ""))[:120]
        found = _CODE_RE.search(msg)
        code = found.group(1) if found else None
        if rc == 0:
            return CallOutcome(CallStatus.OK, _rows(payload, spec.rows_key), code, "")
        if code in _NODATA_CODES:
            return CallOutcome(CallStatus.NODATA, [], code, msg)
        if code in _TOKEN_CODES:
            return CallOutcome(CallStatus.TOKEN, [], code, msg)
        if code in _RATE_CODES or rc == 5:
            last = f"rate — api={spec.api_id} ticker={ticker} code={code} msg={msg}"
            time.sleep(BACKOFF_SEC)
            continue
        return CallOutcome(CallStatus.ERROR, [], code,
                           f"api={spec.api_id} ticker={ticker} return_code={rc} msg={msg}")
    return CallOutcome(CallStatus.ERROR, [], None,
                       f"exhausted {MAX_RETRY} retries — api={spec.api_id} ticker={ticker} "
                       f"end_dt={end_dt} last={last}")


# ── fetch (06:00 체인) ─────────────────────────────────────────────────────────
def clear_incoming(con: sqlite3.Connection) -> None:
    """이번 fetch 가 세울 임시 테이블을 비운다(스키마는 남긴다 — 추론된 컬럼을 잃지 않기 위해)."""
    for api_id in TRS:
        table = INCOMING_PREFIX + api_id
        if _table_exists(con, table):
            con.execute(f'DELETE FROM "{table}"')
    con.commit()


def fetch_tr(spec: TrSpec, tickers: Sequence[str], *, date: str, prev_date: str, db_path: str,
             client: ModuleType, lock: threading.Lock, dry_run: bool) -> TrFetch:
    """TR 하나를 유니버스 전체에 대해 종목당 1콜씩 돈다. 4 TR 이 각자 이 함수를 스레드로 돌린다."""
    incoming = INCOMING_PREFIX + spec.api_id
    cols: list[str] = list(spec.cols) if spec.cols else []
    ensured: tuple[str, ...] = ()
    gap_sec = 1.0 / RATE_PER_SEC
    stamp = _now_utc()
    n_calls = n_rows = n_nodata = n_error = 0
    token_failed = False
    holdings_d: dict[str, str] = {}
    holdings_prev: dict[str, str] = {}
    errors: list[str] = []
    con = None if dry_run else sqlite3.connect(db_path, timeout=60)
    try:
        for ticker in tickers:
            started = time.time()
            out = call_tr(client, spec, ticker, date)
            n_calls += 1
            if out.status is CallStatus.TOKEN:
                token_failed = True
                errors.append(f"token code={out.code} ticker={ticker} msg={out.detail}")
                break
            if out.status is CallStatus.ERROR:
                n_error += 1
                if len(errors) < 5:
                    errors.append(out.detail)
                print(f"[kw_daily] 콜 실패 — date={date} {out.detail}", file=sys.stderr)
            elif out.status is CallStatus.NODATA:
                n_nodata += 1
            elif out.rows:
                if spec.api_id == GATE_TR:
                    for row in out.rows:
                        row_dt = str(row.get("dt", ""))
                        if row_dt == date:
                            holdings_d[ticker] = str(row.get("poss_stkcnt", ""))
                        elif row_dt == prev_date:
                            holdings_prev[ticker] = str(row.get("poss_stkcnt", ""))
                cols += [k for k in out.rows[0] if k not in cols]
                if con is None:
                    n_rows += len(out.rows)
                else:
                    with lock:
                        if tuple(cols) != ensured:
                            ensure_table(con, incoming, cols, extra=("fetched_at",))
                            ensured = tuple(cols)
                        n_rows += insert_rows(con, incoming, cols, ticker, out.rows,
                                              spec.api_id, stamp, {"fetched_at": stamp})
                        con.commit()
            time.sleep(max(0.0, gap_sec - (time.time() - started)))
    finally:
        if con is not None:
            con.close()
    return TrFetch(spec.api_id, n_calls, n_rows, n_nodata, n_error, token_failed,
                   "; ".join(errors), holdings_d, holdings_prev)


def stale_gate(db_path: str, prev_date: str, holdings_d: Mapping[str, str],
               holdings_prev: Mapping[str, str]) -> StaleGate:
    """오염 게이트 (b). 비교 기준은 원장 `dt=D-1`, 원장에 그 날이 없으면 같은 응답의 `D-1` 행.

    (첫 실행·갭 직후엔 원장에 D-1 이 없다. 그 경우에도 판정을 포기하지 않도록 응답 자체의 D-1 을 쓴다 —
    "D 가 D-1 의 복사본인가" 라는 질문은 어느 쪽 기준으로도 같은 답을 준다. 어느 기준을 썼는지는
    `basis` 에 남는다.)
    """
    prev: dict[str, str] = {}
    con = sqlite3.connect(db_path)
    try:
        if _table_exists(con, TRS[GATE_TR].table):
            prev = {str(t): str(p) for t, p in con.execute(
                f'SELECT ticker, poss_stkcnt FROM "{TRS[GATE_TR].table}" WHERE dt=?', (prev_date,))}
    finally:
        con.close()
    basis = "ledger" if prev else ""
    if not prev:
        prev = dict(holdings_prev)
        basis = "incoming" if prev else ""
    common = [t for t in holdings_d if t in prev]
    n_stale = sum(1 for t in common if holdings_d[t] == prev[t])
    return StaleGate(len(common), n_stale, basis if common else "none")


def fetch(tickers: Sequence[str], *, date: str, prev_date: str, db_path: str,
          client: ModuleType, dry_run: bool = False) -> FetchResult:
    """유니버스 × 4 TR 을 종목당 1콜씩 받아 `_kw_incoming_<tr>` 에 세우고 오염 게이트를 판정한다."""
    if not dry_run:
        con = sqlite3.connect(db_path, timeout=60)
        try:
            clear_incoming(con)
        finally:
            con.close()
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=len(TRS)) as pool:
        futures = [pool.submit(fetch_tr, spec, tickers, date=date, prev_date=prev_date,
                               db_path=db_path, client=client, lock=lock, dry_run=dry_run)
                   for spec in TRS.values()]
        stats = [f.result() for f in futures]       # 예외는 여기서 올라온다(조용한 스킵 금지)
    for st in stats:
        print(f"[kw_daily] fetch tr={st.api_id} date={date} 콜={st.n_calls} 행={st.n_rows} "
              f"nodata={st.n_nodata} error={st.n_error}")
    gate_stat = next(s for s in stats if s.api_id == GATE_TR)
    gate = stale_gate(db_path, prev_date, gate_stat.holdings_d, gate_stat.holdings_prev)
    n_calls = sum(s.n_calls for s in stats)
    n_rows = sum(s.n_rows for s in stats)
    detail = (f"universe={len(tickers)} calls={n_calls} rows={n_rows} "
              f"stale={gate.n_stale}/{gate.n_compared} pct={gate.pct} basis={gate.basis} "
              f"errors={sum(s.n_error for s in stats)}")
    bad = [s for s in stats if s.token_failed]
    if bad:
        return FetchResult(FetchStatus.TOKEN_FAILED, date, len(tickers), n_calls, n_rows, gate,
                           detail + " | " + "; ".join(s.detail for s in bad))
    if not gate.passed:
        return FetchResult(FetchStatus.GATE_FAILED, date, len(tickers), n_calls, n_rows, gate,
                           detail + f" | stale_pct={gate.pct} > {STALE_PCT_MAX} "
                                    f"(정상일 실측 9.7% · 오염일 99.0%)")
    return FetchResult(FetchStatus.OK, date, len(tickers), n_calls, n_rows, gate, detail)


# ── merge (08:10 체인) ────────────────────────────────────────────────────────
def krx_quotes(krx_db: str, date: str) -> dict[str, Quote]:
    """KRX 원장의 `bas_dd_req=date` 종가·거래량(유가 ∪ 코스닥). 테이블·행이 없으면 빈 dict."""
    out: dict[str, Quote] = {}
    con = sqlite3.connect(krx_db)
    try:
        for table in ("krx_stk_bydd_trd", "krx_ksq_bydd_trd"):
            if not _table_exists(con, table):
                continue
            for isu, close, vol in con.execute(
                    f'SELECT ISU_CD, TDD_CLSPRC, ACC_TRDVOL FROM "{table}" WHERE bas_dd_req=?',
                    (date,)):
                out[str(isu)] = Quote(_to_int(close), _to_int(vol))
    finally:
        con.close()
    return out


def incoming_quotes(con: sqlite3.Connection, date: str) -> dict[str, Quote]:
    """incoming ka10008 의 `dt=date` 종가·거래량. 가격의 +/- 는 방향 표시자라 abs 로 본다."""
    table = INCOMING_PREFIX + GATE_TR
    if not _table_exists(con, table):
        return {}
    out: dict[str, Quote] = {}
    for ticker, close, vol in con.execute(
            f'SELECT ticker, close_pric, trde_qty FROM "{table}" WHERE dt=?', (date,)):
        parsed = _to_int(close)
        out[str(ticker)] = Quote(None if parsed is None else abs(parsed), _to_int(vol))
    return out


def cross_source(krx: Mapping[str, Quote], kiwoom: Mapping[str, Quote]) -> CrossCheck:
    """게이트 (c). 실측 20260820 은 2,602종목 전건 일치이므로 100% 를 요구한다(findings A §6-2 D)."""
    matched = sorted(set(krx) & set(kiwoom))
    same_close = same_vol = 0
    samples: list[str] = []
    for ticker in matched:
        k, w = krx[ticker], kiwoom[ticker]
        ok_close = k.close_krw is not None and k.close_krw == w.close_krw
        ok_vol = k.volume is not None and k.volume == w.volume
        same_close += int(ok_close)
        same_vol += int(ok_vol)
        if not (ok_close and ok_vol) and len(samples) < 5:
            samples.append(f"{ticker}(krx {k.close_krw}/{k.volume} vs kw {w.close_krw}/{w.volume})")
    return CrossCheck(len(matched), same_close, same_vol, tuple(samples))


def merge_tr(con: sqlite3.Connection, spec: TrSpec, date: str, dry_run: bool) -> TrMerge:
    """incoming 의 `dt <= date` 행만 본 테이블로 옮긴다. `dt > date`(당일 개장 전 행)는 버린다."""
    incoming = INCOMING_PREFIX + spec.api_id
    if not _table_exists(con, incoming):
        return TrMerge(spec.api_id, 0, 0)
    cols = [c for c in _columns(con, incoming) if c not in _META_COLS]
    row_future = con.execute(f'SELECT COUNT(*) FROM "{incoming}" WHERE dt > ?', (date,)).fetchone()
    row_take = con.execute(f'SELECT COUNT(*) FROM "{incoming}" WHERE dt <= ?', (date,)).fetchone()
    n_future = 0 if row_future is None else int(row_future[0])
    n_take = 0 if row_take is None else int(row_take[0])
    if dry_run or n_take == 0:
        return TrMerge(spec.api_id, n_take, n_future)
    ensure_table(con, spec.table, cols)
    names = ",".join(f'"{c}"' for c in ["ticker", *cols, "src_api", "collected_at"])
    con.execute(f'INSERT OR REPLACE INTO "{spec.table}" ({names}) '
                f'SELECT {names} FROM "{incoming}" WHERE dt <= ?', (date,))
    con.commit()
    return TrMerge(spec.api_id, n_take, n_future)


def merge(*, date: str, db_path: str, krx_db: str, dry_run: bool = False) -> MergeResult:
    """KRX 대조를 통과했을 때만 incoming 을 원장에 머지한다. KRX 가 아직 없으면 대기(rc 3)."""
    krx = krx_quotes(krx_db, date)
    con = sqlite3.connect(db_path, timeout=60)
    try:
        kiwoom = incoming_quotes(con, date)
        if not kiwoom:
            return MergeResult(MergeStatus.NO_INCOMING, date, CrossCheck(0, 0, 0), (),
                               f"incoming {INCOMING_PREFIX}{GATE_TR} 에 dt={date} 행이 없다 — "
                               f"--fetch 가 아직 돌지 않았거나 게이트에서 막혔다 db={db_path}")
        if not krx:
            return MergeResult(MergeStatus.KRX_PENDING, date, CrossCheck(0, 0, 0), (),
                               f"KRX 원장에 bas_dd_req={date} 가 없다 — T+1 08:00 공표 전이거나 "
                               f"pending. 머지하지 않고 대기 krx_db={krx_db} kiwoom_rows={len(kiwoom)}")
        cross = cross_source(krx, kiwoom)
        head = (f"date={date} matched={cross.n_matched} same_close={cross.n_same_close} "
                f"same_vol={cross.n_same_vol} krx_rows={len(krx)} kw_rows={len(kiwoom)}")
        if not cross.passed:
            return MergeResult(MergeStatus.CROSS_SOURCE_FAILED, date, cross, (),
                               head + " | 전건 일치가 아니다(실측 기대 100%) "
                                      f"불일치예시={list(cross.samples)}")
        merged = tuple(merge_tr(con, spec, date, dry_run) for spec in TRS.values())
    finally:
        con.close()
    rows = sum(m.n_merged for m in merged)
    future = sum(m.n_dropped_future for m in merged)
    return MergeResult(MergeStatus.OK, date, cross, merged,
                       head + f" | merged={rows} dropped_future={future} "
                              f"per_tr={[(m.api_id, m.n_merged) for m in merged]}")


# ── CLI ───────────────────────────────────────────────────────────────────────
_FETCH_RC = {FetchStatus.OK: 0, FetchStatus.GATE_FAILED: 2, FetchStatus.TOKEN_FAILED: 2,
             FetchStatus.TOO_EARLY: 3}
_MERGE_RC = {MergeStatus.OK: 0, MergeStatus.NO_INCOMING: 2, MergeStatus.CROSS_SOURCE_FAILED: 2,
             MergeStatus.KRX_PENDING: 3, MergeStatus.TOO_EARLY: 3}
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _base() -> str:
    """프로젝트 루트. `QL_HOME` 이 있으면 그것(서버·테스트가 이걸로 트리를 갈아끼운다)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(here))


def _parse_date(value: str) -> dt.date:
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"--date must be YYYYMMDD: got {value!r}")
    return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _too_early(not_before: str | None) -> str | None:
    """`--not-before HH:MM`(KST) 미달이면 사유 문자열, 아니면 None."""
    if not not_before:
        return None
    if not _HHMM_RE.fullmatch(not_before):
        raise ValueError(f"--not-before must be HH:MM (KST): got {not_before!r}")
    now = dt.datetime.now(KST).strftime("%H:%M")
    if now < not_before:
        return f"now={now} KST < not_before={not_before}"
    return None


def _requested_universe(con: sqlite3.Connection, base: str,
                        dry_run: bool) -> universe.RequestedUniverse:
    """요청 유니버스. dry-run 은 상태 파일 사본으로 돌려 유예 카운터를 전진시키지 않는다."""
    state = os.path.join(base, "data", "daily", "universe_kw.json")
    seed = os.path.join(base, "data", "jsonl", "tickers.txt")
    if not dry_run:
        return universe.requested(con, state_path=state, seed_path=seed, grace_days=GRACE_DAYS)
    with tempfile.TemporaryDirectory(prefix="kw_daily_dry_") as tmp:
        shadow = os.path.join(tmp, "universe_kw.json")
        if os.path.exists(state):
            shutil.copyfile(state, shadow)
        return universe.requested(con, state_path=shadow, seed_path=seed, grace_days=GRACE_DAYS)


def _run_fetch(*, date: str, prev_date: str, db_path: str, run_db: str, base: str,
               limit: int, dry_run: bool, not_before: str | None) -> int:
    early = _too_early(not_before)
    if early is not None:
        print(f"[kw_daily] fetch date={date} 실행 하한 미달 — {early}", file=sys.stderr)
        if not dry_run:
            rid = runlog.start(run_db, date=date, source=FETCH_SOURCE)
            runlog.finish(run_db, rid, status=FetchStatus.TOO_EARLY.value, detail=early)
        return _FETCH_RC[FetchStatus.TOO_EARLY]
    con = sqlite3.connect(db_path)
    try:
        req = _requested_universe(con, base, dry_run)
    finally:
        con.close()
    tickers = req.tickers[:limit] if limit > 0 else req.tickers
    print(f"[kw_daily] fetch date={date} prev={prev_date} asof={req.asof} "
          f"universe={len(req.tickers)} 요청={len(tickers)} seeded_only={len(req.seeded_only)} "
          f"dropped={len(req.dropped)} dry_run={dry_run}")
    rid = None if dry_run else runlog.start(run_db, date=date, source=FETCH_SOURCE)
    result = fetch(tickers, date=date, prev_date=prev_date, db_path=db_path,
                   client=_kiwoom_module(), dry_run=dry_run)
    print(f"[kw_daily] fetch status={result.status.value} {result.detail}")
    if rid is not None:
        runlog.finish(run_db, rid, status=result.status.value, n_calls=result.n_calls,
                      n_rows=result.n_rows, detail=result.detail)
    return _FETCH_RC[result.status]


def _run_merge(*, date: str, db_path: str, krx_db: str, run_db: str, dry_run: bool,
               not_before: str | None) -> int:
    early = _too_early(not_before)
    if early is not None:
        print(f"[kw_daily] merge date={date} 실행 하한 미달 — {early}", file=sys.stderr)
        if not dry_run:
            rid = runlog.start(run_db, date=date, source=MERGE_SOURCE)
            runlog.finish(run_db, rid, status=MergeStatus.TOO_EARLY.value, detail=early)
        return _MERGE_RC[MergeStatus.TOO_EARLY]
    rid = None if dry_run else runlog.start(run_db, date=date, source=MERGE_SOURCE)
    result = merge(date=date, db_path=db_path, krx_db=krx_db, dry_run=dry_run)
    print(f"[kw_daily] merge status={result.status.value} {result.detail}")
    if rid is not None:
        runlog.finish(run_db, rid, status=result.status.value, n_rows=result.n_rows,
                      detail=result.detail)
    return _MERGE_RC[result.status]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="daily.kw_daily",
        description="키움 일일 증분 — --fetch(06:00, 콜+오염게이트) / --merge(08:10, KRX 대조+머지)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fetch", action="store_true", help="콜 + _kw_incoming_<tr> 적재 + 오염 게이트")
    mode.add_argument("--merge", action="store_true", help="KRX 크로스소스 대조 후 원장 머지")
    p.add_argument("--date", default=None, help="대상 거래일 YYYYMMDD (기본: 캘린더상 직전 거래일)")
    p.add_argument("--dry-run", action="store_true",
                   help="콜은 하되 원장·incoming·daily_run.db·유니버스 상태에 쓰지 않는다")
    p.add_argument("--limit", type=int, default=0, help="요청 종목 수 제한(fetch 전용)")
    p.add_argument("--not-before", default=None, help="KST HH:MM 이전이면 rc 3")
    p.add_argument("--db", default=None, help="kiwoom.db 경로 (기본 data/raw/kiwoom.db)")
    p.add_argument("--krx-db", default=None, help="krx.db 경로 (기본 data/raw/krx.db)")
    a = p.parse_args(argv)

    base = _base()
    cal = trading_calendar.load(os.path.join(base, "data", "calendar", "kis_holidays.json"))
    if cal.source != "kis_cache":
        print(f"[kw_daily] 휴장 캐시 사용 불가 — 영업일 가정: {cal.detail}", file=sys.stderr)
    date = a.date or cal.prev_trading_day(dt.datetime.now(KST).date()).strftime("%Y%m%d")
    prev_date = cal.prev_trading_day(_parse_date(date)).strftime("%Y%m%d")
    db_path = a.db or os.path.join(base, "data", "raw", "kiwoom.db")
    krx_db = a.krx_db or os.path.join(base, "data", "raw", "krx.db")
    run_db = os.path.join(base, "data", "raw", "daily_run.db")

    if a.fetch:
        return _run_fetch(date=date, prev_date=prev_date, db_path=db_path, run_db=run_db,
                          base=base, limit=a.limit, dry_run=a.dry_run, not_before=a.not_before)
    return _run_merge(date=date, db_path=db_path, krx_db=krx_db, run_db=run_db,
                      dry_run=a.dry_run, not_before=a.not_before)


if __name__ == "__main__":
    sys.exit(main())
