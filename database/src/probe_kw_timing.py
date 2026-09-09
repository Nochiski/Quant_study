"""키움 시계열 4 TR · KIS 신용잔고의 "전날(T-1) 데이터 확정 시각" 프로브. 플랜 P0 Task 0.7.

매시 1회(크론 `5 * * * *`) 종목 하나(005930)를 각 소스에 1콜씩 쏘고 응답을 `data/evidence/kw_timing.db`
에 남긴다. 판독 대상은 두 가지다 — ① 06:00 KST 시점에 T-1 행이 이미 있는가 ② 그 행의 값이 08:00 KRX
공표 이후에도 바뀌지 않는가. `probe_krx_timing.py`(KRX T+1 08:00 을 밝힌 프로브)와 같은 방식이다.
3거래일 치가 쌓이면 `--report` 로 시각별 표를 뽑는다. 하루 콜 수 = 5 소스 × 24 = 120.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

BASE = os.environ.get("QL_HOME", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(BASE, "data", "evidence", "kw_timing.db")
CAL = os.path.join(BASE, "data", "calendar", "kis_holidays.json")
TICKER = "005930"
KST = dt.timezone(dt.timedelta(hours=9))

# (src, url, body 생성기, 응답 리스트 키(None 이면 첫 list 값), 날짜 컬럼)
KW_SPECS: list[tuple[str, str, str, str | None, str]] = [
    ("ka10008", "/api/dostk/frgnistt", "none", "stk_frgnr", "dt"),
    ("ka10060", "/api/dostk/chart", "dt", None, "dt"),
    ("ka10014", "/api/dostk/shsa", "range", "shrts", "dt"),
    ("ka20068", "/api/dostk/slb", "range_all", None, "dt"),
]


def _holidays() -> set[str]:
    try:
        with open(CAL, encoding="utf-8") as f:
            return {str(x) for x in json.load(f)["holidays"]}
    except (OSError, KeyError, ValueError):
        return set()


def prev_trading_day(today: dt.date, holidays: set[str]) -> dt.date:
    """오늘 직전 거래일 — 주말과 휴장 캐시를 건너뛴다(캐시 없으면 주말만)."""
    d = today - dt.timedelta(days=1)
    while d.weekday() >= 5 or d.strftime("%Y%m%d") in holidays:
        d -= dt.timedelta(days=1)
    return d


def _kw_body(kind: str, tk: str, start: str, end: str) -> dict[str, str]:
    if kind == "none":
        return {"stk_cd": tk}
    if kind == "dt":
        return {"dt": end, "stk_cd": tk, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1000"}
    if kind == "range":          # ka10014 (backfill_kw.py:41 과 동일)
        return {"stk_cd": tk, "tm_tp": "1", "strt_dt": start, "end_dt": end}
    if kind == "range_all":      # ka20068 (backfill_kw.py:47 과 동일)
        return {"stk_cd": tk, "strt_dt": start, "end_dt": end, "all_tp": "0"}
    raise ValueError(f"unknown body kind: {kind}")


def _rows(payload: dict[str, object], key: str | None) -> list[dict[str, object]]:
    if key is not None:
        v = payload.get(key)
        return list(v) if isinstance(v, list) else []
    for v in payload.values():
        if isinstance(v, list):
            return list(v)
    return []


def _ensure_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS probe (
        ts_kst TEXT NOT NULL, src TEXT NOT NULL, target_dt TEXT NOT NULL,
        n_rows INTEGER NOT NULL, has_target INTEGER NOT NULL, max_dt TEXT,
        row_json TEXT, error TEXT)""")
    return con


def run_once() -> int:
    import api  # .env 를 import 시점에 읽으므로 --report 경로에서는 로드하지 않는다

    now = dt.datetime.now(KST)
    today = now.date()
    target = prev_trading_day(today, _holidays()).strftime("%Y%m%d")
    start = (today - dt.timedelta(days=14)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    con = _ensure_db()
    ts = now.strftime("%Y-%m-%dT%H:%M:%S")
    n_err = 0
    for src, url, kind, key, dcol in KW_SPECS:
        try:
            payload, _headers = api.kiwoom(src, url, _kw_body(kind, TICKER, start, end))
            rows = _rows(payload, key)
            dts = [str(r.get(dcol, "")) for r in rows]
            hit = next((r for r in rows if str(r.get(dcol, "")) == target), None)
            con.execute("INSERT INTO probe VALUES (?,?,?,?,?,?,?,NULL)",
                        (ts, src, target, len(rows), int(hit is not None),
                         max(dts) if dts else None, json.dumps(hit, ensure_ascii=False) if hit else None))
        except Exception as e:  # noqa: BLE001  # reason: 프로브는 한 소스가 죽어도 나머지를 기록해야 한다
            n_err += 1
            con.execute("INSERT INTO probe VALUES (?,?,?,0,0,NULL,NULL,?)",
                        (ts, src, target, f"{type(e).__name__}: {e}"[:300]))
    # KIS 신용잔고 — T+2 확정. target 은 T-2 거래일(전전 거래일).
    t2 = prev_trading_day(dt.date(int(target[:4]), int(target[4:6]), int(target[6:8])), _holidays()).strftime("%Y%m%d")
    try:
        r = api.kis("/uapi/domestic-stock/v1/quotations/daily-credit-balance", "FHPST04760000",
                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20476",
                     "FID_INPUT_ISCD": TICKER, "FID_INPUT_DATE_1": end})
        rows = r.get("output") if isinstance(r, dict) else None
        rows = list(rows) if isinstance(rows, list) else []
        dts = [str(x.get("deal_date", "")) for x in rows]
        hit = next((x for x in rows if str(x.get("deal_date", "")) == t2), None)
        con.execute("INSERT INTO probe VALUES (?,?,?,?,?,?,?,NULL)",
                    (ts, "kis_credit", t2, len(rows), int(hit is not None),
                     max(dts) if dts else None, json.dumps(hit, ensure_ascii=False) if hit else None))
    except Exception as e:  # noqa: BLE001  # reason: 위와 같다
        n_err += 1
        con.execute("INSERT INTO probe VALUES (?,?,?,0,0,NULL,NULL,?)",
                    (ts, "kis_credit", t2, f"{type(e).__name__}: {e}"[:300]))
    con.commit()
    con.close()
    print(f"probe {ts} target={target} kis_target={t2} errors={n_err}")
    return 1 if n_err else 0


def report() -> None:
    """소스별·시각별: T-1 행 존재 여부와 행 값이 처음 관측값에서 바뀐 시각."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ("SELECT src, target_dt, substr(ts_kst,12,5) AS hh, has_target, row_json, error "
         "FROM probe ORDER BY src, target_dt, ts_kst")
    first: dict[tuple[str, str], str | None] = {}
    for src, target, hh, has, row, err in con.execute(q):
        k = (src, target)
        if k not in first:
            first[k] = row
            print(f"\n== {src} target={target}")
        changed = "" if row == first[k] else "  ← 값 변경"
        print(f"  {hh}  has={has}  {('ERR ' + err) if err else ''}{changed}")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="쌓인 프로브를 시각별 표로 출력")
    a = ap.parse_args()
    if a.report:
        report()
        sys.exit(0)
    sys.exit(run_once())
