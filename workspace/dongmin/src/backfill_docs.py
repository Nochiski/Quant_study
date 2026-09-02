#!/usr/bin/env python3
"""공시 원문(ZIP) 수집 — 원장 문서고.

document.xml 은 이름과 달리 ZIP 바이너리를 준다(실측 선두 b'PK\\x03\\x04').
원장 원칙에 따라 받은 바이트를 그대로 data/raw/documents/{yyyy}/{rcept_no}.zip 에
저장하고 내용은 해석하지 않는다. 수신 무결성(zip_ok)만 판정한다 — KIS 의
verdict='empty', DART 의 status='013' 과 동급인 "제대로 받았는가" 기록이다.
섹션·표 파싱과 인덱스는 stage(data/build/doc/) 몫이다 (2026-09-01 설계 합의).

우선순위 (중단돼도 가치 높은 것부터 확보되도록):
  ① 주식분할·병합 결정   조정계수의 정답지 — KRX 일별 마스터 역산의 검산축
  ② 사업보고서           가동률·직원수·주석의 연간 축
  ③ 반기·분기보고서       분기 해상도

키는 k3 → k2 순서다. backfill_dart 계열(k2 → k3)과 반대로 두어 동시 실행 시
경합을 늦춘다. kael 은 프로덕션 키라 쓰지 않는다. 예산 창은 KST 자정 리셋이며
(2026-08-27/28 실측: 020 이 그날 누적 정확히 40,000에서), 전 키 소진 시 자정까지
기다렸다가 이어간다 — 크론 없이 이 프로세스 하나로 며칠짜리 계획을 완주한다.
"""
import argparse
import hashlib
import io
import os
import re
import sqlite3
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api  # noqa: E402

DB    = os.path.join(BASE, "data", "raw", "dart.db")
DOCS  = os.path.join(BASE, "data", "raw", "documents")
EP    = "document.xml"
URL   = "https://opendart.fss.or.kr/api/document.xml?crtfc_key={key}&rcept_no={rno}"
PACE  = 0.3               # 응답이 평균 0.5MB 라 조회 API 보다 한 박자 느리게
QUOTA_LIMIT = 40_000      # 평일 실측 한도. 주말 완화는 미검증이라 기대하지 않는다
RETRY_MAX, RETRY_BASE = 3, 5.0

# 사업보고서 LIKE 매칭이 "사업보고서제출기한연장신고서" 를 물지 않게 막는다.
NOT_REPORT = "report_nm NOT LIKE '%연장신고%'"
PRIORITIES = {
    1: ("분할·병합", "(report_nm LIKE '%주식분할결정%' OR report_nm LIKE '%주식병합결정%') "
                   "AND stock_code <> ''"),
    2: ("사업보고서", f"report_nm LIKE '%사업보고서%' AND {NOT_REPORT} AND stock_code <> ''"),
    3: ("반기·분기", "(report_nm LIKE '%반기보고서%' OR report_nm LIKE '%분기보고서%') "
                   f"AND {NOT_REPORT} AND stock_code <> ''"),
}

DDL = """CREATE TABLE IF NOT EXISTS doc_store (
  rcept_no    TEXT PRIMARY KEY,
  bytes       INTEGER NOT NULL,
  sha256      TEXT NOT NULL,
  n_files     INTEGER,
  zip_ok      INTEGER NOT NULL,
  http_status TEXT,
  fetched_at  TEXT NOT NULL)"""


def now_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def kst_cut(con: sqlite3.Connection) -> str:
    """오늘 KST 자정의 UTC 표기 — dart_call_log.ts 와 같은 축."""
    return con.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%S','now','+9 hours','start of day','-9 hours')"
    ).fetchone()[0]


def used_today(con: sqlite3.Connection, kid: str) -> int:
    """키별 오늘 소진. 콜 로그 파생이라 stage 계열 등 다른 프로세스 몫도 함께 잡힌다."""
    return con.execute("SELECT COUNT(*) FROM dart_call_log WHERE ts > ? AND key_id = ?",
                       (kst_cut(con), kid)).fetchone()[0]


def sleep_to_kst_midnight() -> None:
    kst = datetime.utcnow() + timedelta(hours=9)
    nxt = (kst + timedelta(days=1)).replace(hour=0, minute=2, second=0, microsecond=0)
    sec = (nxt - kst).total_seconds()
    print(f"  · 전 키 소진 — KST 자정까지 {sec/3600:.1f}h 대기", flush=True)
    time.sleep(sec)


def classify(blob: bytes) -> tuple[str, int]:
    """(status, n_files). ZIP 이면 열어서 파일 수까지 — zip_ok 판정의 실체다."""
    if blob[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                return "000", len(zf.namelist())
        except zipfile.BadZipFile:
            return "badzip", 0
    # 오류 바디는 XML/JSON 로 status 코드가 온다 — 020(한도) 판별에 필요하다
    head = blob[:500].decode("utf-8", errors="replace")
    m = re.search(r'["<]status[">]*[>:"\s]*(\d{3})', head)
    return (m.group(1) if m else "notzip"), 0


def save_zip(rno: str, blob: bytes) -> str:
    """연도 샤딩 + 원자적 쓰기(.tmp → rename). 중단돼도 반쪽짜리 파일이 남지 않는다."""
    d = os.path.join(DOCS, rno[:4])
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{rno}.zip")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)
    return path


def fetch(key: str, rno: str) -> tuple[str, bytes]:
    """(status, blob). 서버측 일시 오류만 백오프 재시도한다."""
    for i in range(RETRY_MAX):
        try:
            with urllib.request.urlopen(URL.format(key=key, rno=rno), timeout=120) as r:
                return "http", r.read()
        except Exception as e:  # noqa: BLE001  # reason: 원인 무관하게 재시도 후 exc 로 기록
            if i == RETRY_MAX - 1:
                return f"exc/{type(e).__name__}", b""
            time.sleep(RETRY_BASE * (2 ** i))
    return "exc", b""


def build_plan(con: sqlite3.Connection, priorities: list[int]) -> list[tuple[int, str]]:
    """(우선순위, rcept_no) 리스트. zip_ok=1 로 이미 받은 것은 뺀다."""
    done = {r[0] for r in con.execute("SELECT rcept_no FROM doc_store WHERE zip_ok = 1")}
    plan, seen = [], set()
    for p in priorities:
        label, cond = PRIORITIES[p]
        rows = con.execute(
            f"SELECT DISTINCT rcept_no FROM dart_disclosure WHERE {cond} "
            f"ORDER BY rcept_dt DESC").fetchall()
        fresh = [r[0] for r in rows if r[0] not in done and r[0] not in seen]
        seen.update(fresh)
        plan.extend((p, rno) for rno in fresh)
        print(f"  ② P{p} {label:<6} 대상 {len(rows):,} · 수집할 것 {len(fresh):,}", flush=True)
    return plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", default="1,2,3", help="수집할 우선순위 (쉼표구분)")
    ap.add_argument("--max-calls", type=int, default=0, help="이번 실행의 콜 상한 (0=무제한)")
    a = ap.parse_args()

    con = sqlite3.connect(DB, timeout=60)   # stage 계열과 동시 실행 대비 busy 대기
    con.execute("PRAGMA busy_timeout=60000")
    con.execute(DDL)
    con.commit()

    keys = [(kid, k) for kid, k in api.dart_keys() if kid != "kael"]
    keys.sort(key=lambda t: t[0] != "k3")   # k3 먼저 — backfill_dart(k2 우선)와 경합 회피
    if not keys:
        print("  ✖ 사용할 키가 없다 (kael 제외 후 0개)"); return
    print(f"  ① 키 순서 {[k for k, _ in keys]} · 한도 {QUOTA_LIMIT:,}/일 (KST 자정 리셋)", flush=True)

    plan = build_plan(con, [int(p) for p in a.priority.split(",") if p.strip()])
    if not plan:
        print("  ③ 수집할 것이 없다 — 완료 상태"); return

    calls = saved = nbytes = 0
    folded: set[str] = set()
    usage = {kid: used_today(con, kid) for kid, _ in keys}
    t0 = time.time()

    for i, (p, rno) in enumerate(plan, 1):
        if a.max_calls and calls >= a.max_calls:
            print(f"  · --max-calls {a.max_calls} 도달 — 중단 (재실행 시 이어받는다)", flush=True)
            break
        # 키 선택 — 사전 계상으로 한도 앞에서 비켜서고, 020 이 오면 그 키를 접은 뒤
        # "같은 항목"을 다음 키로 다시 시도한다 (건너뛰면 그 문서가 이번 런에서 빠진다)
        while True:
            pick = next(((kid, k) for kid, k in keys
                         if kid not in folded and usage[kid] < QUOTA_LIMIT), None)
            if pick is None:
                sleep_to_kst_midnight()
                folded.clear()
                usage = {kid: used_today(con, kid) for kid, _ in keys}
                continue
            kid, key = pick
            st, blob = fetch(key, rno)
            if st == "http":
                st, n_files = classify(blob)
            else:
                n_files = 0
            con.execute("INSERT INTO dart_call_log VALUES (?,?,?,?,?,?,?,?,?)",
                        (EP, "", rno[:4], "", rno, st, n_files, now_utc(), kid))
            calls += 1
            usage[kid] += 1
            if st == "020":
                print(f"  ✖ {kid} 020 (오늘 {usage[kid]:,}콜째) — 이 키를 접는다", flush=True)
                folded.add(kid)
                con.commit()
                continue
            break

        if st == "000":
            save_zip(rno, blob)
            con.execute("INSERT OR REPLACE INTO doc_store VALUES (?,?,?,?,?,?,?)",
                        (rno, len(blob), hashlib.sha256(blob).hexdigest(),
                         n_files, 1, st, now_utc()))
            saved += 1
            nbytes += len(blob)
        else:
            # 실패도 doc_store 에 남긴다(zip_ok=0) — 재실행 시 재시도 대상이 되고,
            # "안 받은 것"과 "받았는데 깨진 것"이 로그에서 갈린다
            con.execute("INSERT OR REPLACE INTO doc_store VALUES (?,?,?,?,?,?,?)",
                        (rno, len(blob), "", 0, 0, st, now_utc()))
        con.commit()

        if i % 200 == 0:
            el = time.time() - t0
            print(f"  {i:>7,}/{len(plan):,}  저장 {saved:,} · {nbytes/1048576:,.0f}MB "
                  f"· {calls/el*60:.0f}콜/분 · 키 " +
                  " ".join(f"{k}:{usage[k]:,}" for k, _ in keys), flush=True)
        time.sleep(PACE)

    print(f"\n  ④ 종료 — 콜 {calls:,} · 저장 {saved:,} · {nbytes/1048576:,.1f}MB "
          f"· {(time.time()-t0)/60:.0f}분", flush=True)
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
