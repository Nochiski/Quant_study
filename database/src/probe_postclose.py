"""장 마감 직후 원천 프로브 — 키움 REST(KRX 코드)가 당일(T) 정규장 값을 언제 확정해 주는가.

DECISIONS Q-1·N-13(모델 데이터는 정규장이 끝나고 최대한 빨리). **원장·모델에 쓰지 않는다** —
`data/evidence/postclose.db` 에만 남기고, 다음 거래일 아침 `grade` 가 KRX 공식값과 대조해
보고서를 쓴다.
임시 크론(KST, 2026-10-06~10-08 거래일 — `--until` 뒤엔 아무것도 안 한다):

  15:20~16:30 5분마다 minute 고정 10종목 — ka10060 T 행(현재가·누적거래량·투자자별 순매수) ·
                            ka10086 T 행(종가) · ka10095 10종목 한 콜(현재가·종가·기준가·체결시간)
  15:45·16:00·16:20 sweep   후보 전량(최신 fi_universe eligible) ka10060 T 행 + ka10095 묶음 —
                            소요 시간·실패·유량 초과를 운영 수집기와 같은 속도(4.4콜/초)로 잰다
  16:40·20:30       bars    ka10080 1분봉 — 15:30 봉 종가(16:00 을 놓쳤을 때의 대안).
                            16:40 은 후보 전량(`--all`). 10-06 새벽 시험에서 밤에도
                            10종목 중 9종목이 공식 종가와 같았다
  다음 거래일 09:20 grade    KRX 공식 종가(T)·원장 키움 T 행(21:05 수집)과 대조 →
                            `data/evidence/postclose_<T>.md`·`.json`

판독: ① 가격이 공식 종가와 같아지는 시각과 애프터마켓 체결로 달라지는 시각
② 수급이 정규장 확정값이 되는
시각 ③ 후보 전량 수집 시간 ④ 16:00 뒤에도 공식 종가를 주는 필드(ka10095 close_pric·ka10086·1분봉).
요청 본문·재시도 규칙은 운영 수집기(`daily/kw_daily.py`)의 것을 그대로 쓴다 —
잰 값이 운영과 같아야 한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
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

BASE = Path(os.environ.get("QL_HOME", Path(__file__).resolve().parents[1]))
DB = BASE / "data" / "evidence" / "postclose.db"
KST = dt.timezone(dt.timedelta(hours=9))
UNTIL_DEFAULT = "20261008"
# 대형·중형·소형, 코스피·코스닥을 섞었다(09-14 애프터마켓 프로브 6종목 + 10-02 모델 상위 4종목)
TICKERS = ("005930", "000660", "035720", "021240", "086520", "008290",
           "001820", "092870", "403870", "000500")
FLOW_KEYS = ("ind_invsr", "frgnr_invsr", "orgn", "fnnc_invt", "insrnc", "invtrt", "etc_fnnc",
             "bank", "penfnd_etc", "samo_fund", "natn", "etc_corp", "natfor")
BATCH = 100              # ka10095 한 콜 종목 수(시험값 — 응답 행 수로 실제 한도를 본다)
# 키움 REST 거래소 구분 = 종목코드 접미사(공식 가이드): KRX 그대로 · NXT '_NX' · 통합(SOR) '_AL'.
# 분 단위 조회와 묶음 조회는 셋 다(10-06 사용자 요청), 후보 전량 ka10060 은 운영 원천인 KRX 만.
EXCHANGES = (("KRX", ""), ("NXT", "_NX"), ("SOR", "_AL"))
REGULAR_LAST = "153059"  # 정규장 마지막 체결(종가 단일가) 봉 시각 상한
EMPTY = ""


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
    "ka10080": _spec("ka10080", "/api/dostk/chart", "stk_min_pole_chart_qry",
                     lambda tk, s, e: {"stk_cd": tk, "tic_scope": "1", "upd_stkpc_tp": "0",
                                       "base_dt": e}),
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
    key = {"ka10060": "dt", "ka10086": "date", "ka10080": "cntr_tm"}.get(api_id)
    vals = [str(r.get(key)) for r in rows if key and r.get(key)]
    return max(vals) if vals else "(빈 응답)"


def pick_rows(api_id: str, rows: Sequence[Mapping[str, Any]], target: str) -> list[dict[str, Any]]:
    """저장할 행만 — 일별 TR 은 T 행, 1분봉은 T 의 15:20~15:35 봉과 마지막 봉, 묶음 TR 은 전부."""
    if api_id == "ka10060":
        return [dict(r) for r in rows if str(r.get("dt")) == target]
    if api_id == "ka10086":
        return [dict(r) for r in rows if str(r.get("date")) == target]
    if api_id == "ka10080":
        bars = [dict(r) for r in rows if str(r.get("cntr_tm", "")).startswith(target)]
        keep = [b for b in bars if "152000" <= str(b["cntr_tm"])[8:14] <= "153500"]
        if bars:
            last = max(bars, key=lambda b: str(b["cntr_tm"]))
            if last not in keep:
                keep.append(last)
        return keep
    return [dict(r) for r in rows]


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


def regular_close_bar(bars: Sequence[Mapping[str, Any]]) -> tuple[str, int | None] | None:
    """1분봉 중 정규장 마지막 봉(시각 ≤ 15:30:59)의 (시각, 종가)."""
    regular = [b for b in bars if str(b.get("cntr_tm", ""))[8:14] <= REGULAR_LAST]
    if not regular:
        return None
    b = max(regular, key=lambda x: str(x["cntr_tm"]))
    return str(b["cntr_tm"])[8:14], price(b.get("cur_prc"))


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


def candidates(base: Path = BASE) -> list[str]:
    """최신 factor_inputs 판의 eligible 종목 + 고정 10종목(장 마감 직후 모델이 받을 범위)."""
    import duckdb
    root = base / "data" / "factor_inputs"
    bid = json.loads((root / "latest_morning.json").read_text(encoding="utf-8"))["build_id"]
    part = root / "fi_universe" / f"v={bid}"
    got = duckdb.sql(f"SELECT ticker FROM read_parquet('{part.as_posix()}/*.parquet', "
                     "hive_partitioning=false) WHERE eligible ORDER BY ticker").fetchall()
    return sorted({str(t) for (t,) in got} | set(TICKERS))


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
        tickers = candidates()
        print(json.dumps(collect(con, client, run, "ka10060", tickers, target), ensure_ascii=False))
        for _name, sfx in EXCHANGES:
            stats = collect(con, client, run, "ka10095", batches([t + sfx for t in tickers]),
                            target, sfx)
            print(json.dumps(stats, ensure_ascii=False))
    finally:
        con.close()


def cmd_bars(target: str, every: bool = False) -> None:
    run = f"bars@{now_kst().strftime('%H%M')}"
    con, client = connect(), _client()
    try:
        tickers = candidates() if every else list(TICKERS)
        print(json.dumps(collect(con, client, run, "ka10080", tickers, target), ensure_ascii=False))
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
    official = krx_official(target, krx_db)
    ledger = ledger_flows(target, kw_db)
    con = _ro(db)
    try:
        rep: dict[str, Any] = {"target": target, "n_official": len(official)}
        # ① 분 단위 10종목 × 거래소(KRX·NXT·SOR) — 가격(ka10060 현재가 · ka10086 종가 ·
        #    ka10095 현재가/종가)을 KRX 공식 종가와, 수급은 각자의 마지막 값과 견준다
        minute: dict[str, dict[str, list[tuple[str, Any]]]] = {}
        for api_id, field in (("ka10060", "cur_prc"), ("ka10086", "close_pric")):
            for ts, key, rows in _obs(con, target, "minute", api_id):
                tk, ex = split_code(key)
                r = _first(rows)
                minute.setdefault(f"{ex}.{api_id}.{field}", {}).setdefault(tk, []).append(
                    (ts[11:16], None if r is None else price(r.get(field))))
                if api_id == "ka10060" and r is not None:
                    minute.setdefault(f"{ex}.ka10060.flows", {}).setdefault(tk, []).append(
                        (ts[11:16], tuple(str(r.get(k)) for k in FLOW_KEYS)))
        for field in ("cur_prc", "close_pric"):
            for ts, key, r in _obs(con, target, "minute", "ka10095"):
                if isinstance(r, dict):
                    tk, ex = split_code(key)
                    minute.setdefault(f"{ex}.ka10095.{field}", {}).setdefault(tk, []).append(
                        (ts[11:16], price(r.get(field))))
        price_rep: dict[str, Any] = {}
        for key, per in minute.items():
            if key.endswith("flows"):
                continue
            times = sorted({t for s in per.values() for t, _ in s})
            match = {t: sum(1 for tk, s in per.items() for tt, v in s
                            if tt == t and official.get(tk, (None,))[0] == v) for t in times}
            n = len(per)
            all_ok = [t for t in times if match[t] == n]
            after_close = [t for t in times if t >= "15:31"]
            first_diff = next((t for t in after_close if t >= "16:00" and match[t] < n), None)
            price_rep[key] = {"n_tickers": n, "minutes": len(times),
                              "first_all_match": all_ok[0] if all_ok else None,
                              "first_mismatch_after_1600": first_diff,
                              "match_by_minute": match}
        rep["minute_price"] = price_rep
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
                    if official.get(tk, (None,))[0] == price(r.get("cur_prc")))
                entry["flows"] = {tk: tuple(str(r.get(k)) for k in FLOW_KEYS)
                                  for tk, r in got.items()}
            else:
                rows = {tk: r for _, tk, r in obs if isinstance(r, dict)}
                entry["n_rows"] = len(rows)
                entry["batch_size"] = BATCH
                for field in ("cur_prc", "close_pric", "base_pric"):
                    entry[f"{field}_match_close"] = sum(
                        1 for tk, r in rows.items()
                        if official.get(tk, (None,))[0] == price(r.get(field)))
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
        # ③ 1분봉 15:30 봉
        bars: dict[str, Any] = {}
        for (run,) in con.execute("SELECT DISTINCT run FROM obs WHERE target_dt = ? AND run LIKE "
                                  "'bars@%' ORDER BY run", (target,)):
            got = {tk: regular_close_bar(rows or []) for _, tk, rows in _obs(con, target, run,
                                                                             "ka10080")}
            bars[run] = {"n": len(got),
                         "close_match": sum(1 for tk, b in got.items()
                                            if b and official.get(tk, (None,))[0] == b[1]),
                         "bar_times": sorted({b[0] for b in got.values() if b})}
        rep["bars"] = bars
        return rep
    finally:
        con.close()


def report_md(rep: Mapping[str, Any]) -> str:
    lines = [f"# 장 마감 직후 프로브 채점 — {rep['target']}", "",
             f"KRX 공식 종가 {rep['n_official']}종목과 대조. "
             "원장·모델에 쓰지 않은 시험 기록이다.", "",
             "## 분 단위 10종목 — 가격", "", "| 필드 | 첫 전 종목 일치 | 16:00 뒤 첫 불일치 |",
             "|---|---|---|"]
    for key, v in rep["minute_price"].items():
        lines.append(f"| {key} | {v['first_all_match']} | {v['first_mismatch_after_1600']} |")
    lines += ["", "## 분 단위 10종목 — 수급(거래소별)", ""]
    for ex, mf in rep["minute_flows"].items():
        lines += [f"- {ex}: 정규장 확정(이후 끝까지 같은 값) 가장 늦은 종목 시각 "
                  f"**{mf['settle_max']}** · 마지막 분 값 = 원장 21:05(KRX) 값 "
                  f"{mf['same_as_ledger']}/{mf['n_with_ledger']}",
                  f"  - 종목별: {mf['settle_by_ticker']}"]
    lines += ["", "## 후보 전량", "", "```json",
              json.dumps(rep["sweeps"], ensure_ascii=False, indent=1), "```", "",
              "## 1분봉 15:30 봉(대안)", "", "```json",
              json.dumps(rep["bars"], ensure_ascii=False, indent=1), "```", ""]
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
    p.add_argument("cmd", choices=("minute", "sweep", "bars", "grade"))
    p.add_argument("--until", default=UNTIL_DEFAULT, help="이 날짜(YYYYMMDD) 뒤엔 아무것도 안 한다")
    p.add_argument("--all", action="store_true",
                   help="bars 를 후보 전량에 쏜다(기본은 고정 10종목)")
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
    if a.cmd == "bars":
        cmd_bars(target, a.all)
    else:
        {"minute": cmd_minute, "sweep": cmd_sweep}[a.cmd](target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
