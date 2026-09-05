"""dart_fin_raw 를 전체 필드로 재생성. 원장은 받은 필드를 그대로 둔다 —
앞서 일부만 골라 담아 rcept_no(접수일=available_at 의 근거)와 account_id(IFRS 태그)를 잃었다."""
import sys, json, sqlite3, time
sys.path.insert(0, "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/xcheck")
import api

E2E = "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/e2e"
S = json.load(open(f"{E2E}/sample.json"))
NOW = time.strftime("%Y-%m-%dT%H:%M:%S")
COLS = ["rcept_no","reprt_code","bsns_year","corp_code","sj_div","sj_nm","account_id","account_nm",
        "account_detail","thstrm_nm","thstrm_amount","frmtrm_nm","frmtrm_amount",
        "bfefrmtrm_nm","bfefrmtrm_amount","ord","currency"]

con = sqlite3.connect(f"{E2E}/dart.db")
cc_of = dict(con.execute("SELECT stock_code, corp_code FROM dart_corp_code WHERE stock_code<>''"))
con.execute("DROP TABLE IF EXISTS dart_fin_raw")
con.execute(f"""CREATE TABLE dart_fin_raw(
  ticker TEXT NOT NULL, {", ".join(f'"{c}" TEXT' for c in COLS)},
  fs_div_used TEXT NOT NULL, window TEXT, collected_at TEXT NOT NULL,
  PRIMARY KEY (ticker, bsns_year, reprt_code, sj_div, account_id, ord))""")

calls = 0
print(f"{'종목':<14} {'FY2017':>18} {'FY2025':>18}")
for x in S["survived"]:
    tk, cc, out = x["ticker"], cc_of.get(x["ticker"]), []
    for y, w in [("2017","B"), ("2025","C")]:
        got = None
        for fs in ("CFS", "OFS"):          # 013 이어도 다음 fs_div 로 계속한다
            j = api.dart("fnlttSinglAcntAll.json", corp_code=cc, bsns_year=y,
                         reprt_code="11011", fs_div=fs); calls += 1
            lst = j.get("list") or []
            if j.get("status") == "000" and lst:
                con.executemany(
                    f"INSERT OR REPLACE INTO dart_fin_raw VALUES ({','.join('?'*(len(COLS)+4))})",
                    [[tk] + [r.get(c) for c in COLS] + [fs, w, NOW] for r in lst])
                con.commit(); got = (fs, len(lst)); break
        out.append(f"{got[0]}/{got[1]}행" if got else "013/무자료")
    print(f"{x['name'][:12]:<14} {out[0]:>18} {out[1]:>18}")

print(f"\n{calls}콜")
n, nc, no = con.execute("SELECT COUNT(*), SUM(fs_div_used='CFS'), SUM(fs_div_used='OFS') FROM dart_fin_raw").fetchone()
print(f"dart_fin_raw {n:,}행  (CFS {nc:,} / OFS {no:,})")
con.close()
