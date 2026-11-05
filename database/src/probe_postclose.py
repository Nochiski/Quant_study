"""장 마감 직후 원천 프로브 — 키움 REST(KRX 코드)가 당일(T) 정규장 값을 언제 확정해 주는가.

DECISIONS Q-1·N-13(모델 데이터는 정규장이 끝나고 최대한 빨리). **원장·모델에 쓰지 않는다** —
`data/evidence/postclose.db` 에만 남기고, 다음 거래일 아침 `grade` 가 KRX 공식값과 대조해
보고서를 쓴다.
임시 크론(KST, 2026-10-06~10-08 거래일 — `--until` 뒤엔 아무것도 안 한다):

  15:20~16:30 5분마다 minute 고정 3종목 × 거래소 3 — ka10060 T 행(현재가·누적거래량·
                            투자자별 순매수) · ka10086 T 행(종가) · ka10095 한 콜(현재가·종가·
                            기준가·체결시간)
  15:45·16:00·16:20 sweep   후보 100종목(고정 3 + 최신 fi_universe eligible 에서 고르게 97) —
                            ka10060 T 행(KRX) + ka10095 묶음(거래소 3). 소요 시간·실패·유량
                            초과를 운영 수집기와 같은 속도(4.4콜/초)로 잰다
  다음 거래일 09:20 grade    KRX 공식 종가(T)·원장 키움 T 행(21:05 수집)과 대조 →
                            `data/evidence/postclose_<T>.md`·`.json`

판독: ① 가격이 공식 종가와 같아지는 시각과 애프터마켓 체결로 달라지는 시각
② 수급이 정규장 확정값이 되는
시각 ③ 후보 수집 시간(100종목 → 630종목 환산) ④ 거래소(KRX·NXT·통합)별 값 차이.
(1분봉 15:30 봉 시험은 10-06 사용자 결정으로 뺐다.)
요청 본문·재시도 규칙은 운영 수집기(`daily/kw_daily.py`)의 것을 그대로 쓴다 —
잰 값이 운영과 같아야 한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from daily import calendar as trading_calendar
from daily import kw_daily as KW
from daily.kw_daily import FLOW_KEYS, pick_rows

BASE = Path(os.environ.get("QL_HOME", Path(__file__).resolve().parents[1]))
DB = BASE / "data" / "evidence" / "postclose.db"
KST = dt.timezone(dt.timedelta(hours=9))
UNTIL_DEFAULT = "20261008"
# 고정 3종목(10-06 사용자 '3종목으로 줄여'): 삼성전자(코스피 대형·NXT 있음) · 에코프로(코스닥·
# NXT 있음) · 삼화콘덴서(코스피 중형·NXT 없음, 10-02 모델 1위)
TICKERS = ("005930", "086520", "001820")
SWEEP_N = 100           # 후보 조회 종목 수(10-06 사용자 '100종목만해')
BATCH = 100              # ka10095 한 콜 종목 수(시험값 — 응답 행 수로 실제 한도를 본다)
# 키움 REST 거래소 구분 = 종목코드 접미사(공식 가이드): KRX 그대로 · NXT '_NX' · 통합(SOR) '_AL'.
# 분 단위 조회와 묶음 조회는 셋 다(10-06 사용자 요청), 후보 전량 ka10060 은 운영 원천인 KRX 만.
EXCHANGES = (("KRX", ""), ("NXT", "_NX"), ("SOR", "_AL"))
EMPTY = ""
# minute 은 크론이 5분마다 :00 에 띄운다 — 관측 시각을 이 간격으로 내리면 그 실행 회차(칸)다
SLOT_MINUTES = 5
# 칸 안 종목 상태 — 일치 · 불일치 · 관측 없음(그 칸에 견줄 값이 없다)
MATCH, MISMATCH, MISSING = "match", "mismatch", "missing"
UNGRADABLE = "공식 종가 없음 — 채점 불가"
UNDETERMINED = "같은 회차 안 호출 순서 차이 — 선후 판정 불가"
# 가격 구간 두 가지 — (구간 키, 기준 칸 키, 앞끝, 뒤끝). 전환 = 공식 종가가 반영되는 때,
# 이탈 = 16:00 뒤 애프터마켓 체결로 공식 종가에서 벗어나는 때(L-04)
SWITCH = ("switch_window", "first_all_match", "last_mismatch", "all_matched_at")
LEAVE = ("leave_window", "first_mismatch_after_1600", "last_match", "first_mismatch")


def _spec(api_id: str, url: str, rows_key: str,
          body: Callable[[str, str, str], dict[str, str]]) -> KW.TrSpec:
    return KW.TrSpec(api_id=api_id, url=url, table=EMPTY, rows_key=rows_key, cap=0, floor=EMPTY,
                     cols=None, body=body)


SPECS: dict[str, KW.TrSpec] = {
    "ka10060": KW.TRS["ka10060"],
    "ka10086": _spec("ka10086", "/api/dostk/mrkcond", "daly_stkpc",
                     lambda tk, s, e: {"stk_cd": tk, "qry_dt": e, "indc_tp": "0"}),
    "ka10095": _spec("ka10095", "/api/dostk/stkinfo", "atn_stk_infr",
                     lambda tk, s, e: {"stk_cd": tk}),
}


class CountingClient:
    """`api.kiwoom` 을 감싸 유량 초과 응답 수를 센다(`KW.call_tr` 는 재시도만 하고 세지 않는다)."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.rate = inner, 0

    def kiwoom(self, api_id: str, url: str, body: dict[str, str], cont: str | None = None,
               next_key: str | None = None) -> tuple[Any, Any]:
        payload, headers = self.inner.kiwoom(api_id, url, body, cont, next_key)
        if isinstance(payload, dict) and payload.get("return_code") != 0:
            found = KW._CODE_RE.search(str(payload.get("return_msg", "")))
            if payload.get("return_code") == 5 or (found and found.group(1) in KW._RATE_CODES):
                self.rate += 1
        return payload, headers


# ── 순수 함수(테스트 대상) ────────────────────────────────────────────────────
def price(v: object) -> int | None:
    """키움 가격 문자열('-259500' 의 부호는 등락 방향) → 절댓값 정수."""
    try:
        return abs(int(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def norm_code(code: object) -> str:
    """응답 종목코드 → 6자리(앞의 'A'·뒤의 '_NX'·'_AL' 제거)."""
    s = str(code).strip().split("_")[0]
    return s[1:] if len(s) == 7 and s[0] == "A" else s


def split_code(key: str) -> tuple[str, str]:
    """저장 키 '005930_NX' → ('005930', 'NXT'). 접미사가 없으면 KRX."""
    for name, sfx in EXCHANGES:
        if sfx and key.endswith(sfx):
            return key[: -len(sfx)], name
    return key, "KRX"


def latest_date(api_id: str, rows: Sequence[Mapping[str, Any]]) -> str:
    """T 행이 없을 때 진단용 — 응답의 가장 최근 날짜(또는 시각) 표기."""
    key = {"ka10060": "dt", "ka10086": "date"}.get(api_id)
    vals = [str(r.get(key)) for r in rows if key and r.get(key)]
    return max(vals) if vals else "(빈 응답)"


def settle_time(series: Sequence[tuple[str, object]]) -> str | None:
    """(시각, 값) 오름차순 → 값이 마지막 값과 같아진 뒤 끝까지 그대로인 첫 시각. 비면 None."""
    if not series:
        return None
    final = series[-1][1]
    first = series[-1][0]
    for ts, v in reversed(series):
        if v != final:
            break
        first = ts
    return first


def slot_of(hms: str) -> str:
    """관측 시각 'HH:MM:SS' → 칸 'HH:MM' — 5분 격자로 내린 실행 회차(크론이 :00 에 띄운다).
    응답이 늦어 회차가 분 경계를 넘어도(15:45:59 → 15:46:01) 같은 칸에 남는다(L-03)."""
    minute = int(hms[3:5])
    return f"{hms[:3]}{minute - minute % SLOT_MINUTES:02d}"


def close_state(v: int | None, close: int | None) -> str:
    """값을 KRX 공식 종가와 견준 상태. 둘 다 있어야 일치·불일치를 가리고, 하나라도 없으면 견줄
    값이 없어 MISSING 이다 — 공식 종가가 없는 종목의 빈 값(None == None)은 일치가 아니다(L-05)."""
    if v is None or close is None:
        return MISSING
    return MATCH if v == close else MISMATCH


def grade_minute_prices(per: Mapping[str, Sequence[tuple[str, int | None]]],
                        closes: Mapping[str, int | None]) -> dict[str, Any]:
    """5분 간격 가격 한 필드(거래소·TR·필드) 채점 — `per` 는 종목 → [(관측 시각 HH:MM:SS, 값)]
    오름차순.

    칸(실행 회차, `slot_of`)마다 종목 상태를 일치·불일치·관측 없음으로 나눈다(L-03). 관측 없음은
    그 칸에 견줄 값이 없다는 뜻이고(수집 실패·중단으로 행이 없음, T 행·값 없음, 공식 종가 없음)
    불일치로 세지 않는다. 한 칸에 관측이 여럿이면 하나라도 불일치면 불일치다.
    - minutes · {match,mismatch,missing}_by_minute: 칸 수 · 칸별 상태별 종목 수(키 이름의
      minute 은 minute 회차다)
    - first_all_match: 모든 종목이 관측되고 모두 일치한 첫 칸
    - first_mismatch_after_1600: 16:00 이후 실제 불일치가 처음 나온 칸
    - switch_window: 전환 구간(초, L-04) — 첫 전 종목 일치 칸 앞의 마지막 불일치 관측(last_mismatch)
      ~ 그 칸에서 전 종목 일치를 확인한 시각(all_matched_at, 그 칸의 마지막 일치 관측). 공식 종가
      반영은 이 사이에 있다. 앞에 불일치가 없으면(첫 관측부터 일치) last_mismatch 는 None
    - leave_window: 이탈 구간(초, L-04) — 16:00 뒤 첫 불일치 칸 앞(16:00 이전 포함)의 마지막 일치
      관측 ~ 그 칸의 첫 불일치 관측. 애프터마켓 체결로 처음 벗어난 때는 이 사이에 있다. 어느
      종목이 먼저 벗어났는지 모르므로 앞끝은 종목마다 본 마지막 일치 중 가장 이른 시각이고,
      일치를 한 번도 못 본 종목이 있으면 last_match 는 None
    """
    # 이 필드(거래소·TR·필드)에서 값이 한 번도 없던 종목(T 행 없음·빈 값, 예: NXT 미상장)은
    # 분모에서 뺀다(L-01)
    graded = {tk: [(hms, close_state(v, closes.get(tk))) for hms, v in s]
              for tk, s in per.items() if any(v is not None for _, v in s)}
    cells: dict[str, dict[str, str]] = {}            # 칸 → 종목 → 상태(없는 종목은 관측 없음)
    for tk, obs in graded.items():
        for hms, st in obs:
            cell = cells.setdefault(slot_of(hms), {})
            if st != MISSING and cell.get(tk) != MISMATCH:    # 불일치 > 일치 > 관측 없음
                cell[tk] = st
    counts = {slot: {st: sum(1 for tk in graded if cells[slot].get(tk, MISSING) == st)
                     for st in (MATCH, MISMATCH, MISSING)} for slot in sorted(cells)}
    n = len(graded)
    first_all = next((slot for slot, c in counts.items() if c[MATCH] == n), None)
    first_diff = next((slot for slot, c in counts.items() if slot >= "16:00" and c[MISMATCH]),
                      None)
    flat = [(slot_of(hms), hms, st) for obs in graded.values() for hms, st in obs]
    switch = leave = None
    if first_all is not None:
        switch = {"last_mismatch": max((hms for slot, hms, st in flat
                                        if st == MISMATCH and slot < first_all), default=None),
                  "all_matched_at": max(hms for slot, hms, st in flat
                                        if st == MATCH and slot == first_all)}
    if first_diff is not None:
        # 종목마다 그 칸 앞에서 마지막으로 일치를 본 시각 — 못 봤으면 ''(앞끝을 모른다)
        last = [max((hms for hms, st in obs if st == MATCH and slot_of(hms) < first_diff),
                    default="") for obs in graded.values()]
        leave = {"last_match": min(last) or None,
                 "first_mismatch": min(hms for slot, hms, st in flat
                                       if st == MISMATCH and slot == first_diff)}
    return {"n_tickers": n, "minutes": len(counts), "first_all_match": first_all,
            "first_mismatch_after_1600": first_diff,
            "match_by_minute": {slot: c[MATCH] for slot, c in counts.items()},
            "mismatch_by_minute": {slot: c[MISMATCH] for slot, c in counts.items()},
            "missing_by_minute": {slot: c[MISSING] for slot, c in counts.items()},
            "switch_window": switch, "leave_window": leave}


def undetermined_pairs(prices: Mapping[str, Mapping[str, Any]],
                       window: tuple[str, str, str, str]) -> list[tuple[str, str]]:
    """기준 칸은 다른데 구간(`SWITCH` 전환 · `LEAVE` 이탈)이 겹치는 필드 쌍 — 표의 칸 차이가 선후를
    뜻하지 않는다(L-04). 시각이 초 단위로 잘려 있어 닫힌 구간으로 견준다(같은 초끼리는 순서를
    모른다). 기준 칸이 같은 필드끼리는 구간이 늘 겹친다(앞끝은 그 칸 앞, 뒤끝은 그 칸 안)."""
    win, slot, lo, hi = window
    # 필드 → (기준 칸, 앞끝, 뒤끝). 앞끝이 없으면 관측 전부터 — '' 는 어느 시각보다 앞선다
    spans = {key: (v[slot], v[win][lo] or "", v[win][hi]) for key, v in prices.items() if v[win]}
    return [(a, b) for (a, (slot_a, lo_a, hi_a)), (b, (slot_b, lo_b, hi_b))
            in itertools.combinations(spans.items(), 2)
            if slot_a != slot_b and lo_a <= hi_b and lo_b <= hi_a]


# ── 저장 ─────────────────────────────────────────────────────────────────────
def connect(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60)
    con.execute("""CREATE TABLE IF NOT EXISTS obs (
        ts_kst TEXT NOT NULL, run TEXT NOT NULL, api TEXT NOT NULL, ticker TEXT NOT NULL,
        target_dt TEXT NOT NULL, ok INTEGER NOT NULL, row_json TEXT, err TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS runs (
        run TEXT NOT NULL, api TEXT NOT NULL, target_dt TEXT NOT NULL, started_kst TEXT,
        ended_kst TEXT, n_req INTEGER, n_ok INTEGER, n_err INTEGER, n_rate INTEGER, note TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS obs_key ON obs(target_dt, run, api, ticker)")
    return con


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def _stamp(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def collect(con: sqlite3.Connection, client: CountingClient, run: str, api_id: str,
            tickers: Sequence[str], target: str, suffix: str = "") -> dict[str, int | str]:
    """종목(또는 묶음 코드열)마다 1콜 — 운영 수집기와 같은 간격(1/4.4초)으로 쏜다.
    `suffix` = 거래소 접미사. 묶음 응답의 종목코드에 다시 붙여 저장 키를 거래소별로 가른다."""
    spec, gap = SPECS[api_id], 1.0 / KW.RATE_PER_SEC
    started, rate0 = now_kst(), client.rate
    n_ok = n_err = 0
    for tk in tickers:
        t0 = time.time()
        out = KW.call_tr(client, spec, tk, target)
        ts = _stamp(now_kst())
        if out.status is KW.CallStatus.OK or out.status is KW.CallStatus.NODATA:
            n_ok += 1
            rows = pick_rows(api_id, out.rows, target)
            if api_id == "ka10095":
                con.executemany("INSERT INTO obs VALUES (?,?,?,?,?,1,?,NULL)",
                                [(ts, run, api_id, norm_code(r.get("stk_cd", tk)) + suffix, target,
                                  json.dumps(r, ensure_ascii=False)) for r in rows])
            else:
                note = None if rows else f"T 행 없음 — 최신 {latest_date(api_id, out.rows)}"
                con.execute("INSERT INTO obs VALUES (?,?,?,?,?,1,?,?)",
                            (ts, run, api_id, tk, target, json.dumps(rows, ensure_ascii=False),
                             note))
        else:
            n_err += 1
            con.execute("INSERT INTO obs VALUES (?,?,?,?,?,0,NULL,?)",
                        (ts, run, api_id, tk, target, f"{out.status.value} {out.detail}"[:300]))
            if out.status is KW.CallStatus.TOKEN:
                break
        # 콜마다 커밋한다 — 반복 전체를 트랜잭션 하나로 잡으면 같은 DB 에 쓰는 다른 프로세스
        # (minute·sweep)가 그동안 쓰기 잠금에 막힌다(L-02). TOKEN 중단은 아래 반복 끝 커밋이 맡는다
        con.commit()
        time.sleep(max(0.0, gap - (time.time() - t0)))
    con.commit()
    stats: dict[str, int | str] = {
        "run": run, "api": api_id + suffix, "started": _stamp(started), "ended": _stamp(now_kst()),
        "n_req": len(tickers), "n_ok": n_ok, "n_err": n_err, "n_rate": client.rate - rate0}
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run, api_id + suffix, target, stats["started"], stats["ended"], len(tickers), n_ok,
                 n_err,
                 stats["n_rate"], ""))
    con.commit()
    return stats


def spread(tickers: Sequence[str], n: int) -> list[str]:
    """정렬된 목록에서 고르게 n 개(같은 간격) — 매번 같은 종목이 뽑힌다."""
    if n >= len(tickers):
        return list(tickers)
    step = len(tickers) / n
    return [tickers[int(i * step)] for i in range(n)]


def sample(cands: Sequence[str], n: int = SWEEP_N) -> list[str]:
    """고정 종목 + 나머지 후보에서 고르게 — 합계 n 종목."""
    rest = sorted(set(cands) - set(TICKERS))
    return sorted({*TICKERS, *spread(rest, n - len(TICKERS))})


def candidates(base: Path = BASE) -> list[str]:
    """최신 factor_inputs 판의 eligible 종목(장 마감 직후 모델이 받을 범위)."""
    import duckdb
    root = base / "data" / "factor_inputs"
    bid = json.loads((root / "latest_morning.json").read_text(encoding="utf-8"))["build_id"]
    part = root / "fi_universe" / f"v={bid}"
    got = duckdb.sql(f"SELECT ticker FROM read_parquet('{part.as_posix()}/*.parquet', "
                     "hive_partitioning=false) WHERE eligible ORDER BY ticker").fetchall()
    return sorted({str(t) for (t,) in got})


def batches(tickers: Sequence[str], size: int = BATCH) -> list[str]:
    return ["|".join(tickers[i:i + size]) for i in range(0, len(tickers), size)]


# ── 명령 ─────────────────────────────────────────────────────────────────────
def _active(today: dt.date, until: str, cal: Any) -> bool:
    return today.strftime("%Y%m%d") <= until and cal.is_trading_day(today)


def _client() -> CountingClient:
    return CountingClient(KW._kiwoom_module())


def cmd_minute(target: str) -> None:
    con, client = connect(), _client()
    try:
        for _name, sfx in EXCHANGES:
            codes = [t + sfx for t in TICKERS]
            for api_id in ("ka10060", "ka10086"):
                collect(con, client, "minute", api_id, codes, target, sfx)
            collect(con, client, "minute", "ka10095", ["|".join(codes)], target, sfx)
    finally:
        con.close()


def cmd_sweep(target: str) -> None:
    run = f"sweep@{now_kst().strftime('%H%M')}"
    con, client = connect(), _client()
    try:
        tickers = sample(candidates())
        print(json.dumps(collect(con, client, run, "ka10060", tickers, target), ensure_ascii=False))
        for _name, sfx in EXCHANGES:
            stats = collect(con, client, run, "ka10095", batches([t + sfx for t in tickers]),
                            target, sfx)
            print(json.dumps(stats, ensure_ascii=False))
    finally:
        con.close()


# ── 채점 ─────────────────────────────────────────────────────────────────────
def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def krx_official(target: str, krx_db: Path) -> dict[str, tuple[int | None, int | None]]:
    """KRX 공식 (종가, 일 거래량) — 코스피·코스닥."""
    con = _ro(krx_db)
    try:
        out: dict[str, tuple[int | None, int | None]] = {}
        for table in ("krx_stk_bydd_trd", "krx_ksq_bydd_trd"):
            for code, close, vol in con.execute(
                    f"SELECT ISU_CD, TDD_CLSPRC, ACC_TRDVOL FROM {table} WHERE BAS_DD = ?",
                    (target,)):
                out[str(code)] = (price(close), price(vol))
        return out
    finally:
        con.close()


def ledger_flows(target: str, kw_db: Path) -> dict[str, dict[str, str]]:
    """원장 키움 ka10060 T 행(21:05 수집 — 애프터마켓 포함)."""
    if not kw_db.exists():
        return {}
    con = _ro(kw_db)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(ka10060_investor_flows)")]
        if "dt" not in cols:
            return {}
        want = [c for c in ("ticker", "cur_prc", *FLOW_KEYS) if c in cols]
        rows = con.execute(f"SELECT {', '.join(want)} FROM ka10060_investor_flows WHERE dt = ?",
                           (target,)).fetchall()
        return {str(r[0]): dict(zip(want[1:], map(str, r[1:]), strict=True)) for r in rows}
    finally:
        con.close()


def _obs(con: sqlite3.Connection, target: str, run_like: str,
         api_id: str) -> list[tuple[str, str, Any]]:
    got = con.execute("SELECT ts_kst, ticker, row_json FROM obs WHERE target_dt = ? AND run LIKE ? "
                      "AND api = ? AND ok = 1 ORDER BY ts_kst", (target, run_like, api_id))
    return [(ts, tk, json.loads(js) if js else None) for ts, tk, js in got]


def _first(rows: Any) -> dict[str, Any] | None:
    if isinstance(rows, list):
        return rows[0] if rows else None
    return rows if isinstance(rows, dict) else None


def grade(target: str, *, db: Path = DB, krx_db: Path, kw_db: Path) -> dict[str, Any]:
    """T 의 관측을 KRX 공식 종가·원장 수급과 대조한 보고서 — `cmd_grade` 가 json·md 로 쓴다.

    minute 의 시각 축은 관측 분이 아니라 실행 회차(칸, `slot_of` — 5분 격자 'HH:MM')다(L-03).
    기존 키 first_all_match · first_mismatch_after_1600 · minutes · match_by_minute ·
    minute_flows.*.settle_by_ticker · settle_max 의 'HH:MM' 과 개수는 이 회차 기준이다(L-03 수정
    전 보고서는 관측 분 기준). 초 단위는 switch_window · leave_window 에, 칸이 다른데 구간이 겹치는
    짝은 minute_undetermined 에 싣는다(L-04). gradable(공식 종가표가 비지 않음, M-5)과
    no_official_close(공식 종가 없는 고정 종목, I-1)는 md 머리의 판정 근거다."""
    official = krx_official(target, krx_db)
    closes = {tk: close for tk, (close, _vol) in official.items()}
    ledger = ledger_flows(target, kw_db)
    con = _ro(db)
    try:
        rep: dict[str, Any] = {"target": target, "n_official": len(official),
                               "gradable": len(official) > 0}
        # ① 5분 간격 고정 종목 × 거래소(KRX·NXT·SOR) — 가격(ka10060 현재가 · ka10086 종가 ·
        #    ka10095 현재가/종가)을 KRX 공식 종가와, 수급은 각자의 마지막 값과 견준다.
        #    가격은 초 단위 관측 시각을 그대로 넘기고(전환 구간, L-04) 수급은 칸(실행 회차)으로
        #    묶는다(L-03)
        minute: dict[str, dict[str, list[tuple[str, Any]]]] = {}
        for api_id, field in (("ka10060", "cur_prc"), ("ka10086", "close_pric")):
            for ts, key, rows in _obs(con, target, "minute", api_id):
                tk, ex = split_code(key)
                r = _first(rows)
                minute.setdefault(f"{ex}.{api_id}.{field}", {}).setdefault(tk, []).append(
                    (ts[11:19], None if r is None else price(r.get(field))))
                if api_id == "ka10060" and r is not None:
                    minute.setdefault(f"{ex}.ka10060.flows", {}).setdefault(tk, []).append(
                        (slot_of(ts[11:19]), tuple(str(r.get(k)) for k in FLOW_KEYS)))
        for field in ("cur_prc", "close_pric"):
            for ts, key, r in _obs(con, target, "minute", "ka10095"):
                if isinstance(r, dict):
                    tk, ex = split_code(key)
                    minute.setdefault(f"{ex}.ka10095.{field}", {}).setdefault(tk, []).append(
                        (ts[11:19], price(r.get(field))))
        rep["minute_price"] = {key: grade_minute_prices(per, closes)
                               for key, per in minute.items() if not key.endswith("flows")}
        # md 목록과 같은 판정 — 기준 칸이 다른데 구간이 겹쳐 선후를 가릴 수 없는 필드 짝(L-04)
        rep["minute_undetermined"] = {
            name: [[a, b] for a, b in undetermined_pairs(rep["minute_price"], window)]
            for name, window in (("switch", SWITCH), ("leave", LEAVE))}
        # 공식 종가 없는 고정 종목 — 값이 한 번이라도 있어 채점 분모에 드는(L-01) 종목만 본다.
        # KRX 적재는 표(시장)마다 커밋돼 한 표만 비는 날이 있다 — 그 종목이 든 필드는 첫 전 종목
        # 일치·전환 구간을 낼 수 없다(I-1)
        priced = {tk for key, per in minute.items() if not key.endswith("flows")
                  for tk, s in per.items() if any(v is not None for _, v in s)}
        rep["no_official_close"] = sorted(tk for tk in priced if closes.get(tk) is None)
        rep["minute_flows"] = {}
        for ex, _sfx in EXCHANGES:
            flows = minute.get(f"{ex}.ka10060.flows", {})
            if not flows:
                continue
            settle = {tk: settle_time(s) for tk, s in flows.items()}
            rep["minute_flows"][ex] = {
                "settle_by_ticker": settle,
                "settle_max": max((v for v in settle.values() if v), default=None),
                # 원장 키움 T 행은 KRX 코드로 받은 것 — 다른 거래소는 참고로만 견준다
                "same_as_ledger": sum(
                    1 for tk, s in flows.items() if s and tk in ledger
                    and s[-1][1] == tuple(ledger[tk].get(k, "") for k in FLOW_KEYS)),
                "n_with_ledger": sum(1 for tk in flows if tk in ledger)}
        # ② 후보 전량
        sweeps: dict[str, Any] = {}
        for run, api_id, started, ended, n_req, n_ok, n_err, n_rate in con.execute(
                "SELECT run, api, started_kst, ended_kst, n_req, n_ok, n_err, n_rate FROM runs "
                "WHERE target_dt = ? AND run LIKE 'sweep@%' ORDER BY started_kst", (target,)):
            secs = (dt.datetime.fromisoformat(ended) - dt.datetime.fromisoformat(started)).seconds
            entry: dict[str, Any] = {"started": started[11:19], "seconds": secs, "n_req": n_req,
                                     "n_ok": n_ok, "n_err": n_err, "n_rate": n_rate}
            base, ex = split_code(api_id)
            obs = [(ts, tk, r) for ts, key, r in _obs(con, target, run, base)
                   for tk, kex in (split_code(key),) if kex == ex]
            if base == "ka10060":
                firsts = {tk: _first(rows) for _, tk, rows in obs}
                got = {tk: r for tk, r in firsts.items() if r is not None}
                entry["n_t_rows"] = len(got)
                entry["close_match"] = sum(
                    1 for tk, r in got.items()
                    if close_state(price(r.get("cur_prc")), closes.get(tk)) == MATCH)
                entry["flows"] = {tk: tuple(str(r.get(k)) for k in FLOW_KEYS)
                                  for tk, r in got.items()}
            else:
                rows = {tk: r for _, tk, r in obs if isinstance(r, dict)}
                entry["n_rows"] = len(rows)
                entry["batch_size"] = BATCH
                for field in ("cur_prc", "close_pric", "base_pric"):
                    # 값이 있는 행 수 — n_rows 는 NXT 미상장 빈 행까지 센 원시값이라
                    # 일치 수의 비율은 이쪽으로 나눈다
                    entry[f"{field}_n_value"] = sum(
                        1 for r in rows.values() if price(r.get(field)) is not None)
                    entry[f"{field}_match_close"] = sum(
                        1 for tk, r in rows.items()
                        if close_state(price(r.get(field)), closes.get(tk)) == MATCH)
            sweeps[f"{run}/{base}/{ex}"] = entry
        flow_runs = [k for k in sweeps if k.endswith("/ka10060/KRX")]
        for a, b in zip(flow_runs, flow_runs[1:], strict=False):
            fa, fb = sweeps[a]["flows"], sweeps[b]["flows"]
            common = set(fa) & set(fb)
            sweeps[f"{a} == {b}"] = {"n": len(common),
                                     "flows_equal": sum(1 for tk in common if fa[tk] == fb[tk])}
        if flow_runs:
            last = sweeps[flow_runs[-1]]["flows"]
            common = [tk for tk in last if tk in ledger]
            sweeps["last_sweep == ledger(21:05)"] = {
                "n": len(common),
                "flows_equal": sum(1 for tk in common
                                   if last[tk] == tuple(ledger[tk].get(k, "") for k in FLOW_KEYS))}
        for k in flow_runs:
            sweeps[k].pop("flows", None)
        rep["sweeps"] = sweeps
        return rep
    finally:
        con.close()


def _window(v: Mapping[str, Any], window: tuple[str, str, str, str]) -> str:
    """구간(`SWITCH` · `LEAVE`) 표기 — 앞끝이 없으면 '관측 전'부터."""
    win, _slot, lo, hi = window
    w = v[win]
    return "None" if w is None else f"{w[lo] or '관측 전'} ~ {w[hi]}"


def report_md(rep: Mapping[str, Any]) -> str:
    prices = rep["minute_price"]
    gradable = rep["gradable"]            # 공식 종가표가 비면 가격 칸은 가리지 않는다(M-5)
    lines = [f"# 장 마감 직후 프로브 채점 — {rep['target']}", "",
             (f"KRX 공식 종가 {rep['n_official']}종목과 대조. " if gradable
              else f"KRX 공식 종가 0종목 — **{UNGRADABLE}**. ")
             + "원장·모델에 쓰지 않은 시험 기록이다.", ""]
    if rep["no_official_close"]:
        lines += [f"공식 종가 없는 고정 종목: {' · '.join(rep['no_official_close'])} — 이 종목이 "
                  "든 필드는 첫 전 종목 일치·전환 구간을 낼 수 없고 이탈 구간 앞끝도 '관측 전'이 "
                  "된다(이탈 짝 목록이 늘 수 있다 — KRX 적재 확인 뒤 "
                  f"`grade --date {rep['target']}` 재실행)", ""]
    lines += ["## 5분 간격 고정 종목 — 가격", "",
              "칸은 실행 회차(관측 시각을 5분 격자로 내림)다. 칸마다 종목을 일치·불일치·"
              "관측 없음(견줄 값이 없음 — 수집 실패·중단, 값 없음, 그 종목 공식 종가 없음)으로 "
              "나누고 관측 없음은 불일치로 세지 않는다. 첫 전 종목 일치 = 모든 종목이 관측되고 "
              "모두 일치한 첫 칸. 구간은 초 단위다 — 전환 구간 = 첫 전 종목 일치 칸 앞의 마지막 "
              "불일치 관측 ~ 그 칸에서 전 종목 일치를 확인한 시각(그 칸의 마지막 일치 관측), "
              "이탈 구간 = 16:00 뒤 첫 불일치 칸 앞의 마지막 일치 관측(종목마다 본 것 중 가장 "
              "이른 시각 — '관측 전'이면 일치를 한 번도 못 본 종목이 있다) ~ 그 칸의 첫 불일치 "
              "관측.", "",
              "같은 거래소·TR 의 두 필드는 한 응답에서 나와 끝점만 맞닿아도 '선후 판정 불가'로 "
              "표시될 수 있다(보수적 — 시각이 초 단위라 같은 초의 다른 호출과 구별하지 "
              "않는다).", "",
              "| 필드 | 종목 수 | 첫 전 종목 일치 | 16:00 뒤 첫 불일치 | 전환 구간 | 이탈 구간 |",
              "|---|---|---|---|---|---|"]
    for key, v in prices.items():
        cells = ((v["first_all_match"], v["first_mismatch_after_1600"], _window(v, SWITCH),
                  _window(v, LEAVE)) if gradable else ("채점 불가",) * 4)
        lines.append(f"| {key} | {v['n_tickers']} | {' | '.join(map(str, cells))} |")
    if gradable:
        for name, window, title, col in (
                ("switch", SWITCH, "전환 구간이 겹치는 짝 — 공식 종가 반영", "첫 전 종목 일치"),
                ("leave", LEAVE, "이탈 구간이 겹치는 짝 — 16:00 뒤 애프터마켓",
                 "16:00 뒤 첫 불일치")):
            lines += ["", f"### {title}", "",
                      f"{col} 칸이 같은 필드끼리는 늘 겹친다. 칸이 다른데 겹치는 짝"
                      "(표의 칸 차이가 선후가 아니다):"]
            lines += [f"- {a}({prices[a][window[1]]}, {_window(prices[a], window)}) ↔ "
                      f"{b}({prices[b][window[1]]}, {_window(prices[b], window)}) — "
                      f"{UNDETERMINED}" for a, b in rep["minute_undetermined"][name]] or ["- 없음"]
    lines += ["", "## 5분 간격 고정 종목 — 수급(거래소별)", ""]
    for ex, mf in rep["minute_flows"].items():
        lines += [f"- {ex}: 정규장 확정(이후 끝까지 같은 값)이 가장 늦은 종목의 회차 "
                  f"**{mf['settle_max']}** · 마지막 회차 값 = 원장 21:05(KRX) 값 "
                  f"{mf['same_as_ledger']}/{mf['n_with_ledger']}",
                  f"  - 종목별: {mf['settle_by_ticker']}"]
    lines += ["", "## 후보 100종목", "", "```json",
              json.dumps(rep["sweeps"], ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(lines)


def cmd_grade(target: str) -> None:
    raw = BASE / "data" / "raw"
    rep = grade(target, krx_db=raw / "krx.db", kw_db=raw / "kiwoom.db")
    out = BASE / "data" / "evidence"
    (out / f"postclose_{target}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
    (out / f"postclose_{target}.md").write_text(report_md(rep), encoding="utf-8")
    print(f"채점 {target} → {out}/postclose_{target}.md")


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="probe_postclose")
    p.add_argument("cmd", choices=("minute", "sweep", "grade"))
    p.add_argument("--until", default=UNTIL_DEFAULT, help="이 날짜(YYYYMMDD) 뒤엔 아무것도 안 한다")
    p.add_argument("--date", default=None,
                   help="대상 T(YYYYMMDD) — 기본: 오늘, grade 는 직전 거래일")
    a = p.parse_args(argv)
    cal = trading_calendar.load()
    today = now_kst().date()
    if a.cmd == "grade":
        target = a.date or cal.prev_trading_day(today).strftime("%Y%m%d")
        if target > a.until or not (DB.exists()):
            print(f"grade 건너뜀 — target={target} until={a.until} db={DB.exists()}")
            return 0
        cmd_grade(target)
        return 0
    if a.date is None and not _active(today, a.until, cal):
        print(f"{a.cmd} 건너뜀 — {today} 거래일 아님 또는 until {a.until} 지남")
        return 0
    target = a.date or today.strftime("%Y%m%d")
    {"minute": cmd_minute, "sweep": cmd_sweep}[a.cmd](target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
