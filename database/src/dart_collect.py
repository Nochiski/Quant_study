"""DART 3구간 수집. 채점표의 DART 행을 실측으로 채운다.
status=013(무자료)은 실패가 아니라 '원래 없음'이다 — ok 로 기록하지 않는다."""
import sys, json, sqlite3, zipfile, io, time
import xml.etree.ElementTree as ET
sys.path.insert(0, "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/xcheck")
import api

E2E = "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/e2e"
DB, CALLS = f"{E2E}/dart.db", [0]
S = json.load(open(f"{E2E}/sample.json"))
NOW = time.strftime("%Y-%m-%dT%H:%M:%S")
WIN = {"A": ("20100104","20100115","2009"), "B": ("20180402","20180413","2017"), "C": ("20260807","20260820","2025")}

con = sqlite3.connect(DB); con.execute("PRAGMA journal_mode=WAL")
con.executescript("""
CREATE TABLE IF NOT EXISTS dart_corp_code(corp_code TEXT PRIMARY KEY, corp_name TEXT, stock_code TEXT, modify_date TEXT);
CREATE TABLE IF NOT EXISTS dart_fin_raw(corp_code TEXT, ticker TEXT, bsns_year TEXT, reprt_code TEXT,
  fs_div_used TEXT, sj_div TEXT, account_nm TEXT, thstrm_amount TEXT, frmtrm_amount TEXT,
  currency TEXT, collected_at TEXT);
CREATE TABLE IF NOT EXISTS dart_disclosure(rcept_no TEXT PRIMARY KEY, corp_code TEXT, corp_name TEXT,
  stock_code TEXT, report_nm TEXT, rcept_dt TEXT, flr_nm TEXT, window TEXT, collected_at TEXT);
CREATE TABLE IF NOT EXISTS dart_call_log(endpoint TEXT, corp_code TEXT, ticker TEXT, bsns_year TEXT,
  reprt_code TEXT, fs_div TEXT, window TEXT, status TEXT, message TEXT, n_rows INTEGER, collected_at TEXT);
""")

def log(**k):
    con.execute("INSERT INTO dart_call_log VALUES (:endpoint,:corp_code,:ticker,:bsns_year,:reprt_code,:fs_div,:window,:status,:message,:n_rows,:collected_at)",
        {**dict(endpoint=None,corp_code=None,ticker=None,bsns_year=None,reprt_code=None,fs_div=None,window=None,status=None,message=None,n_rows=0,collected_at=NOW), **k})

# ── 1) 기업코드 매핑 (1콜) ──────────────────────────────────────
z = api.dart("corpCode.xml"); CALLS[0] += 1
root = ET.parse(io.BytesIO(zipfile.ZipFile(io.BytesIO(z)).read("CORPCODE.xml"))).getroot()
rows = [(e.findtext("corp_code"), e.findtext("corp_name"), (e.findtext("stock_code") or "").strip(), e.findtext("modify_date"))
        for e in root.iter("list")]
con.executemany("INSERT OR REPLACE INTO dart_corp_code VALUES (?,?,?,?)", rows)
con.commit()
log(endpoint="corpCode.xml", status="ok", n_rows=len(rows))
print(f"기업코드 {len(rows):,}건")

byticker = {r[2]: r[0] for r in rows if r[2]}
byname   = {r[1]: r[0] for r in rows}
def corp_of(x):
    return byticker.get(x["ticker"]) or byname.get(x["name"])

allsym = S["survived"] + S["delisted"] + S["new"]
mapped = {x["ticker"]: corp_of(x) for x in allsym}
miss = [k for k, v in mapped.items() if not v]
print(f"매핑 {len(allsym)-len(miss)}/{len(allsym)}" + (f"  미매핑 {miss}" if miss else ""))

# ── 2) 연간재무 — 생존 10종목 × 3연도, CFS→OFS 폴백 ────────────
FIN_COLS = ("sj_div","account_nm","thstrm_amount","frmtrm_amount","currency")
def fetch_fin(tk, cc, year, reprt, win):
    for fs in ("CFS", "OFS"):
        j = api.dart("fnlttSinglAcntAll.json", corp_code=cc, bsns_year=year, reprt_code=reprt, fs_div=fs)
        CALLS[0] += 1
        st, msg, lst = j.get("status"), j.get("message"), j.get("list") or []
        log(endpoint="fnlttSinglAcntAll", corp_code=cc, ticker=tk, bsns_year=year, reprt_code=reprt,
            fs_div=fs, window=win, status=st, message=msg, n_rows=len(lst))
        if st == "000" and lst:
            con.executemany("INSERT INTO dart_fin_raw VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(cc, tk, year, reprt, fs, *[r.get(c) for c in FIN_COLS], NOW) for r in lst])
            con.commit()
            return st, fs, len(lst)
        # 013(무자료)이어도 다음 fs_div 로 계속한다.
        # 연결재무제표가 없는 회사(종속회사 미보유)는 CFS 가 013, OFS 가 정상이다 — 실측 확인.
    return st, None, 0

print("\n연간재무 (reprt_code=11011)")
print(f"  {'종목':<14} {'FY2009':>16} {'FY2017':>16} {'FY2025':>16}")
for x in S["survived"]:
    cc = mapped[x["ticker"]]
    out = []
    for w in ("A","B","C"):
        st, fs, n = fetch_fin(x["ticker"], cc, WIN[w][2], "11011", w)
        out.append(f"{st}/{fs or '-'}/{n}" if st else "err")
    print(f"  {x['name'][:12]:<14} {out[0]:>16} {out[1]:>16} {out[2]:>16}")

# ── 3) 분기재무 하한 프로브 (FY2014 vs FY2016) ──────────────────
print("\n분기재무 하한 프로브 (삼성전자 1분기 11013)")
for y in ("2014", "2016"):
    st, fs, n = fetch_fin("005930", mapped["005930"], y, "11013", "probe")
    print(f"  FY{y}: status={st} fs={fs} rows={n}")

# ── 4) 공시 목록 3구간 ─────────────────────────────────────────
print("\n공시 목록")
for w, (s, e, _) in WIN.items():
    page, total, got = 1, None, 0
    while page <= 3:
        j = api.dart("list.json", bgn_de=s, end_de=e, page_no=page, page_count=100); CALLS[0] += 1
        st, lst = j.get("status"), j.get("list") or []
        if total is None: total = j.get("total_count", 0)
        log(endpoint="list.json", window=w, status=st, message=j.get("message"), n_rows=len(lst))
        if st != "000" or not lst: break
        con.executemany("INSERT OR REPLACE INTO dart_disclosure VALUES (?,?,?,?,?,?,?,?,?)",
            [(r["rcept_no"], r["corp_code"], r["corp_name"], r.get("stock_code",""), r["report_nm"],
              r["rcept_dt"], r.get("flr_nm",""), w, NOW) for r in lst])
        con.commit(); got += len(lst)
        if len(lst) < 100: break
        page += 1
    print(f"  {w} {s}~{e}: total={total:,} 수집={got}" + ("  ← 3페이지 상한으로 절단" if got < (total or 0) else ""))

con.close()
print(f"\n총 {CALLS[0]}콜")
