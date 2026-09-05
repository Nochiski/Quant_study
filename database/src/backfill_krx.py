"""KRX 날짜축 백필. 1콜 = 그날 전종목.

설계 근거:
  · KRX 명세엔 단위 표기가 없다 → 원장은 응답 필드명 그대로 두고 단위는 registry 가 관리한다.
  · api.krx() 의 raise_for_status() 는 429 에서 예외를 던져 진행상황을 통째로 잃는다.
    여기서는 직접 requests 를 써서 상태코드를 판정한다.
  · 휴장일은 빈 OutBlock_1 로 온다(에러 아님). 가격 1콜로 판정한 뒤 나머지 엔드포인트를 건너뛴다.
  · 재개: (endpoint, bas_dd) 단위로 ingest_log 에 기록하고 ok/holiday 는 다시 안 친다.
"""
import os, sys, json, time, sqlite3, argparse
from datetime import date, timedelta
import requests

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api as A

KRX  = "https://data-dbg.krx.co.kr/svc/apis"
DB   = f"{BASE}/data/raw/krx.db"
RATE = 3.0                 # 초당 제한 미확인 → 보수적으로. 429 나오면 서킷이 낮춘다
BACKOFF, MAX_RETRY = 1.0, 4

# 순서가 곧 우선순위다. 가격(첫 항목)으로 영업일을 판정한다.
# PK 는 우리가 붙이는 bas_dd_req(요청 날짜) + 소스의 종목/지수 식별자.
# 종목기본(*_isu_base_info) 응답에는 BAS_DD 가 없으므로 응답 필드를 날짜 축으로 쓸 수 없다.
EPS = [
 ("sto/stk_bydd_trd",      "krx_stk_bydd_trd",      "ISU_CD"),
 ("sto/ksq_bydd_trd",      "krx_ksq_bydd_trd",      "ISU_CD"),
 ("sto/stk_isu_base_info", "krx_stk_isu_base_info", "ISU_CD"),
 ("sto/ksq_isu_base_info", "krx_ksq_isu_base_info", "ISU_CD"),
 ("idx/kospi_dd_trd",      "krx_kospi_dd_trd",      "IDX_NM"),
 ("idx/kosdaq_dd_trd",     "krx_kosdaq_dd_trd",     "IDX_NM"),
 ("etp/etf_bydd_trd",      "krx_etf_bydd_trd",      "ISU_CD"),
]
SESS = requests.Session()
SESS.headers.update({"AUTH_KEY": A._K["KRX_API_KEY"]})
STOP = False


def call(path, bas_dd):
    """(rows, verdict) — verdict: ok / holiday / rate / error"""
    for _ in range(MAX_RETRY):
        try:
            r = SESS.get(f"{KRX}/{path}", params={"basDd": bas_dd}, timeout=90)
            if r.status_code in (429, 503):
                time.sleep(BACKOFF); continue
            if r.status_code == 401:
                return None, "fatal"
            if r.status_code != 200:
                time.sleep(0.5); continue
            rows = r.json().get("OutBlock_1")
            if not rows:
                return [], "holiday"      # 빈 응답 = 휴장·미래일자. 에러가 아니다
            return rows, "ok"
        except Exception:
            time.sleep(0.5)
    return None, "rate"


def bdays(start, end):
    d, out = date.fromisoformat(start), date.fromisoformat(end)
    while d <= out:
        if d.weekday() < 5: yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2010-01-04")
    ap.add_argument("--to",   dest="to",  default="2026-08-20")
    ap.add_argument("--limit", type=int, default=0, help="날짜 수 제한(스모크용)")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS ingest_log(
      endpoint TEXT NOT NULL, bas_dd TEXT NOT NULL, n_rows INTEGER NOT NULL,
      status TEXT NOT NULL, note TEXT, collected_at TEXT NOT NULL,
      PRIMARY KEY (endpoint, bas_dd))""")
    con.commit()

    done = {(r[0], r[1]) for r in con.execute(
        "SELECT endpoint, bas_dd FROM ingest_log WHERE status IN ('ok','holiday')")}
    days = list(bdays(a.frm, a.to))
    if a.limit: days = days[-a.limit:]
    print(f"  대상 {len(days):,}일 × {len(EPS)}엔드포인트 = 최대 {len(days)*len(EPS):,}콜")
    print(f"  이미 완료 {len(done):,}건")

    gap, calls, t0 = 1.0/RATE, 0, time.time()
    stat = {"ok":0, "holiday":0, "rate":0, "error":0}
    for i, d in enumerate(days):
        for path, tbl, key in EPS:
            if (path, d) in done: continue
            s = time.time()
            rows, v = call(path, d); calls += 1
            stat[v if v in stat else "error"] += 1
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            if v == "fatal":
                print(f"  [{path}] 401 Unauthorized — 권한 없음. 이 엔드포인트 건너뜀"); break
            if v == "ok":
                cols = list(rows[0].keys())
                ddl = ", ".join(f'"{c}" TEXT' for c in cols)
                con.execute(f'CREATE TABLE IF NOT EXISTS {tbl} ({ddl}, bas_dd_req TEXT, collected_at TEXT, '
                            f'PRIMARY KEY ("bas_dd_req", "{key}"))')
                con.executemany(
                    f'INSERT OR REPLACE INTO {tbl} VALUES ({",".join("?"*(len(cols)+2))})',
                    [[r.get(c) for c in cols] + [d, now] for r in rows])
            con.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?)",
                        (path, d, len(rows) if rows else 0, v, None, now))
            con.commit()
            # 가격(첫 엔드포인트)이 휴장이면 그날 나머지는 안 친다 — 6콜 절약
            if path == EPS[0][0] and v == "holiday":
                for p2, _, _ in EPS[1:]:
                    con.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?)",
                                (p2, d, 0, "holiday", "skipped by price-probe", now))
                con.commit(); break
            time.sleep(max(0, gap - (time.time() - s)))
        if (i+1) % 20 == 0:
            el = time.time() - t0
            open(f"{BASE}/logs/progress_krx.txt","w").write(
                f"{time.strftime('%H:%M:%S')} {i+1}/{len(days)}일 {calls:,}콜 "
                f"{calls/el:.2f}/s {stat}\n")
            print(f"  {i+1}/{len(days)}일  {calls:,}콜  {calls/el:.2f}/s  {stat}")
    con.close()
    el = time.time() - t0
    print(f"\n완료 {el:.0f}초 · {calls:,}콜 · {calls/max(el,1):.2f}콜/s")
    print(f"  {stat}")

if __name__ == "__main__":
    main()
