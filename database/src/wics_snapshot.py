"""WICS 구성종목 스냅샷 수집 — wiseindex `GetIndexComponets` 를 (기준일 dt, 섹터 코드 sec_cd) 단위로 원문 보존.

원장은 `data/raw/wiseindex.db`(WICS_PROBE §7-2) — `wisereport.db` 와 소스 도메인이 달라 분리한다.
  wics_raw      (dt, sec_cd, collected_at, http_status, n_rows, body zlib)  append-only, 응답 원문
  wics_call_log (dt, sec_cd, requested_at, status, elapsed_ms, bytes, err)  유닛 로그(결측 근거)
호출 규약: 비공식 API — UA 고정, 1콜/초, 비 200·JSON 파싱 실패가 3회 연속이면 즉시 중단(pykrx IP 차단 사례).
멱등: 같은 (dt, sec_cd) 에 200 원문이 이미 있으면 다시 부르지 않는다(`--force` 로 새 판본 추가).
빈 list(CNT=0)도 저장한다 — "코드 없음/휴장일" 판정 근거. 미수집을 0 으로 적재하지 않는다.
L2 코드로 부르면 행의 SEC_CD 는 L1, L2 라벨은 IDX_CD·IDX_NM_KOR 에 온다(WICS_PROBE §6).

사용: PYTHONPATH=src python -m wics_snapshot --dt 20260918 [--codes all|l1|l2|G4535,…]
휴장일 dt 는 CNT=0 이 온다(09-20 실측) — 직전 거래일로 당겨 주지 않으므로 dt 는 반드시 거래일. [--db …] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable, Sequence

URL = "https://www.wiseindex.com/Index/GetIndexComponets"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
L1: tuple[str, ...] = ("G10", "G15", "G20", "G25", "G30", "G35", "G40", "G45", "G50", "G55")
# L2 28개 — 2026-09-18 스냅샷(09-20 실측)에서 라벨 전부 확정, L1 검산(Σ L2 CNT = L1 CNT) 10/10 성립.
# G2540(미디어)은 2018 GICS 개편으로 G5020(미디어와엔터테인먼트)에 흡수돼 CNT=0 이고, 백필을 하지 않기로 했으므로
# (사용자 09-20) 목록에서 뺀다 — 원장은 2026-09-18 부터 앞으로만 쌓인다.
L2: tuple[str, ...] = (
    "G1010", "G1510", "G2010", "G2020", "G2030", "G2510", "G2520", "G2530", "G2550", "G2560",
    "G3010", "G3020", "G3030", "G3510", "G3520", "G4010", "G4020", "G4030", "G4040", "G4050",
    "G4510", "G4520", "G4530", "G4535", "G4540", "G5010", "G5020", "G5510")
DEFAULT_DB = "data/raw/wiseindex.db"
MAX_CONSECUTIVE_FAIL = 3

Fetch = Callable[[str, str], tuple[int, bytes, float]]   # (dt, sec_cd) -> (http_status, body, elapsed_s)


def fetch_http(dt_: str, sec_cd: str, timeout: float = 20.0) -> tuple[int, bytes, float]:
    q = urllib.parse.urlencode({"ceil_yn": "0", "dt": dt_, "sec_cd": sec_cd})
    req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": UA, "Accept": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read(), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return int(e.code), e.read() or b"", time.monotonic() - t0


def parse(body: bytes) -> dict[str, object]:
    """응답 요약 — CNT(info)·list 길이·L2 라벨(IDX_CD/IDX_NM_KOR, list[0])·L1(SEC_CD). 파싱 실패는 예외."""
    j = json.loads(body.decode("utf-8"))
    rows = j.get("list") or []
    head = rows[0] if rows else {}
    return {"cnt": int((j.get("info") or {}).get("CNT") or 0), "n_list": len(rows),
            "idx_cd": head.get("IDX_CD"), "idx_nm": head.get("IDX_NM_KOR"),
            "sec_cd": head.get("SEC_CD"), "sec_nm": head.get("SEC_NM_KOR"),
            "mkt_val": (j.get("info") or {}).get("MKT_VAL")}


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS wics_raw (dt TEXT NOT NULL, sec_cd TEXT NOT NULL, "
                "collected_at TEXT NOT NULL, http_status INTEGER NOT NULL, n_rows INTEGER, body BLOB NOT NULL, "
                "PRIMARY KEY (dt, sec_cd, collected_at))")
    con.execute("CREATE TABLE IF NOT EXISTS wics_call_log (dt TEXT NOT NULL, sec_cd TEXT NOT NULL, "
                "requested_at TEXT NOT NULL, status TEXT NOT NULL, elapsed_ms INTEGER, bytes INTEGER, err TEXT)")
    con.commit()


def have_ok(con: sqlite3.Connection, dt_: str, sec_cd: str) -> bool:
    row = con.execute("SELECT 1 FROM wics_raw WHERE dt=? AND sec_cd=? AND http_status=200 LIMIT 1",
                      (dt_, sec_cd)).fetchone()
    return row is not None


def resolve_codes(spec: str) -> tuple[str, ...]:
    s = spec.strip().lower()
    if s == "all":
        return L2 + L1
    if s == "l2":
        return L2
    if s == "l1":
        return L1
    return tuple(c.strip().upper() for c in spec.split(",") if c.strip())


def run(dt_: str, codes: Sequence[str], db: str, *, sleep_s: float = 1.0, force: bool = False,
        dry_run: bool = False, fetch: Fetch = fetch_http) -> dict[str, dict[str, object]]:
    """코드마다 1콜. 반환은 코드 → 요약(status·cnt·라벨). 3회 연속 실패면 RuntimeError."""
    out: dict[str, dict[str, object]] = {}
    if dry_run:
        for c in codes:
            out[c] = {"status": "dry_run"}
        return out
    con = sqlite3.connect(db, timeout=60)
    ensure_schema(con)
    consecutive_fail = 0
    try:
        for i, code in enumerate(codes):
            if not force and have_ok(con, dt_, code):
                out[code] = {"status": "skip_exists"}
                continue
            if i:
                time.sleep(sleep_s)
            requested_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
            try:
                status, body, elapsed = fetch(dt_, code)
                err = ""
                summary = parse(body) if status == 200 else {}
                ok = status == 200
            except Exception as e:                                   # JSON 파싱·네트워크 — 실패로 기록
                status, body, elapsed, err, summary, ok = -1, b"", 0.0, f"{type(e).__name__}: {e}"[:300], {}, False
            collected_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
            con.execute("INSERT INTO wics_call_log VALUES (?,?,?,?,?,?,?)",
                        (dt_, code, requested_at, "ok" if ok else "fail", int(elapsed * 1000), len(body), err or None))
            if body:
                con.execute("INSERT OR REPLACE INTO wics_raw VALUES (?,?,?,?,?,?)",
                            (dt_, code, collected_at, status, summary.get("n_list") if ok else None,
                             zlib.compress(body)))
            con.commit()
            out[code] = {"status": "ok" if ok else "fail", "http": status, "err": err, **summary}
            consecutive_fail = 0 if ok else consecutive_fail + 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                raise RuntimeError(f"{MAX_CONSECUTIVE_FAIL}회 연속 실패 — 중단(마지막 {code} http={status} {err})")
    finally:
        con.close()
    return out


def report(dt_: str, out: dict[str, dict[str, object]]) -> str:
    lines = [f"WICS 스냅샷 dt={dt_} 코드 {len(out)}개"]
    l1_cnt: dict[str, int] = {}
    l2_sum: dict[str, int] = {}
    for code, r in out.items():
        st = r.get("status")
        if st == "ok":
            cnt = int(r.get("cnt") or 0)
            lines.append(f"  {code:<6} ok   CNT={cnt:>5} list={r.get('n_list'):>5}  {r.get('idx_cd')} {r.get('idx_nm')}")
            if code in L1:
                l1_cnt[code] = cnt
            else:
                l2_sum[code[:3]] = l2_sum.get(code[:3], 0) + cnt
        else:
            lines.append(f"  {code:<6} {st} {r.get('http', '')} {r.get('err', '')}")
    if l1_cnt and l2_sum:
        lines.append("  L1 검산 (Σ L2 CNT vs L1 CNT):")
        for l1 in L1:
            if l1 in l1_cnt:
                a, b = l2_sum.get(l1, 0), l1_cnt[l1]
                lines.append(f"    {l1} ΣL2={a:>5} L1={b:>5} {'=' if a == b else f'차 {a - b:+d}'}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WICS 구성종목 스냅샷 (wiseindex GetIndexComponets)")
    ap.add_argument("--dt", required=True, help="기준일 YYYYMMDD (거래일)")
    ap.add_argument("--codes", default="all", help="all(L2 28+L1 10) | l2 | l1 | 쉼표 목록")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--sleep", type=float, default=1.0, help="콜 간격 초 (기본 1.0)")
    ap.add_argument("--force", action="store_true", help="이미 있는 (dt, sec_cd) 도 새 판본으로 다시 받는다")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if len(a.dt) != 8 or not a.dt.isdigit():
        print(f"--dt 는 YYYYMMDD: {a.dt!r}", file=sys.stderr)
        return 2
    codes = resolve_codes(a.codes)
    try:
        out = run(a.dt, codes, a.db, sleep_s=a.sleep, force=a.force, dry_run=a.dry_run)
    except RuntimeError as e:
        print(f"!!! {e}", file=sys.stderr)
        return 2
    print(report(a.dt, out))
    n_fail = sum(1 for r in out.values() if r.get("status") == "fail")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
