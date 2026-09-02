"""① DART 를 통합 DB 에 financials 로 반영  ② 전 DB 샘플 CSV 생성.

asof_date 는 결산월(company.json acc_mt)로 정한다. 12월 결산을 가정하면
3월 결산 법인의 사업연도 종료일이 9개월 틀어진다 — 실측으로 확인된 함정이다.
available_at 은 rcept_no 앞 8자리(접수일)+1800. 사업연도 종료일이 아니라 공시 시점이어야 미래참조를 막는다.
"""
import sys, json, sqlite3, csv, os, time, calendar
sys.path.insert(0, "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/xcheck")
import api

E2E = "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/e2e"
OUT = f"{E2E}/samples"; os.makedirs(OUT, exist_ok=True)
NOW = time.strftime("%Y-%m-%dT%H:%M:%S")

# ── ① 결산월 수집 → financials ─────────────────────────────────
d = sqlite3.connect(f"{E2E}/dart.db")
tickers = [r[0] for r in d.execute("SELECT DISTINCT ticker FROM dart_fin_raw")]
cc_of = dict(d.execute("SELECT stock_code, corp_code FROM dart_corp_code WHERE stock_code<>''"))
d.execute("CREATE TABLE IF NOT EXISTS dart_company(corp_code TEXT PRIMARY KEY, ticker TEXT, corp_name TEXT, acc_mt TEXT, est_dt TEXT, collected_at TEXT)")
have = {r[0] for r in d.execute("SELECT ticker FROM dart_company")}
calls = 0
for tk in tickers:
    if tk in have: continue
    j = api.dart("company.json", corp_code=cc_of[tk]); calls += 1
    d.execute("INSERT OR REPLACE INTO dart_company VALUES (?,?,?,?,?,?)",
              (cc_of[tk], tk, j.get("corp_name"), j.get("acc_mt"), j.get("est_dt"), NOW))
    d.commit()
acc = dict(d.execute("SELECT ticker, acc_mt FROM dart_company"))
print(f"결산월 수집 {calls}콜 — 분포 {dict((m, list(acc.values()).count(m)) for m in set(acc.values()))}")

def asof(year, mt):
    y, m = int(year), int(mt or 12)
    return f"{y:04d}{m:02d}{calendar.monthrange(y, m)[1]:02d}"

e = sqlite3.connect(f"{E2E}/equity_test.db")
e.execute("DROP TABLE IF EXISTS financials")
e.execute("""CREATE TABLE financials(
  ticker TEXT NOT NULL, asof_date TEXT NOT NULL, fiscal_year TEXT NOT NULL, acc_mt TEXT,
  reprt_code TEXT NOT NULL, fs_div_used TEXT NOT NULL, sj_div TEXT, sj_nm TEXT,
  account_id TEXT, account_nm TEXT, amount_krw TEXT, currency TEXT,
  rcept_no TEXT, available_at TEXT NOT NULL, src TEXT NOT NULL,
  collected_at TEXT NOT NULL, vintage_seq INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (ticker, asof_date, reprt_code, sj_div, account_id, vintage_seq))""")
rows, rej = [], 0
for r in d.execute("""SELECT ticker,bsns_year,reprt_code,fs_div_used,sj_div,sj_nm,account_id,
                      account_nm,thstrm_amount,currency,rcept_no FROM dart_fin_raw"""):
    tk, yr, rc, fs, sjd, sjn, aid, anm, amt, cur, rcp = r
    if not rcp or len(rcp) < 8: rej += 1; continue
    rows.append((tk, asof(yr, acc.get(tk)), yr, acc.get(tk), rc, fs, sjd, sjn, aid, anm, amt, cur,
                 rcp, rcp[:8] + "1800", "dart:fnlttSinglAcntAll", NOW, 1))
e.executemany(f"INSERT OR REPLACE INTO financials VALUES ({','.join('?'*17)})", rows)
e.commit()
print(f"financials {len(rows):,}행 적재" + (f" (rcept_no 결측 {rej}행 제외)" if rej else ""))
for t, a, m, n in e.execute("SELECT ticker,asof_date,acc_mt,COUNT(*) FROM financials GROUP BY ticker,asof_date ORDER BY acc_mt DESC LIMIT 3"):
    print(f"  {t} asof={a} 결산월={m} {n}행")

# ── ② 전 DB 샘플 CSV ───────────────────────────────────────────
KO = json.load(open(f"{E2E}/colmap.json")) if os.path.exists(f"{E2E}/colmap.json") else {}
DBS = {"krx": f"{E2E}/krx.db", "kiwoom": f"{E2E}/kiwoom.db", "kis": f"{E2E}/kis.db",
       "dart": f"{E2E}/dart.db", "equity_test": f"{E2E}/equity_test.db"}
N = 20
idx = []
for nm, path in DBS.items():
    if not os.path.exists(path) or os.path.getsize(path) == 0: continue
    os.makedirs(f"{OUT}/{nm}", exist_ok=True)
    c = sqlite3.connect(path)
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        cur = c.execute(f"SELECT * FROM {t} LIMIT {N}")
        cols = [x[0] for x in cur.description]
        data = cur.fetchall()
        total = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        with open(f"{OUT}/{nm}/{t}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([f"{col}({KO[col]})" if col in KO else col for col in cols])
            w.writerows(data)
        idx.append((nm, t, total, len(data), len(cols)))
    c.close()

with open(f"{OUT}/00_목록.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["DB", "테이블", "전체행수", "샘플행수", "컬럼수"])
    w.writerows(idx)
print(f"\nCSV {len(idx)}개 → {OUT}/")
for nm in DBS:
    k = [x for x in idx if x[0] == nm]
    if k: print(f"  {nm:<12} {len(k):>2}개 테이블  {sum(x[2] for x in k):>8,}행")
