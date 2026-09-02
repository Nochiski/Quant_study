"""키움 원장 DB 적재. 받은 필드명을 그대로 컬럼으로 둔다 — 이것이 원장의 정의다.
가공(단위 환산·패딩 판정·소스 대조)은 통합 DB 단계에서 하고 여기서는 하지 않는다."""
import json, sqlite3, os, time

BASE = "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad"
RAW = f"{BASE}/ratetest/raw"
DB  = f"{BASE}/final/kiwoom.db"
COLLECTED = "2026-08-21T15:47:00"   # sustain.py 실행 시각
REQ_START, REQ_END = "20250101", "20260820"

SPEC = {
 "ka10014": dict(
   tbl="ka10014_short_selling",
   cols=["dt","close_pric","pred_pre_sig","pred_pre","flu_rt","trde_qty",
         "shrts_qty","ovr_shrts_qty","trde_wght","shrts_trde_prica","shrts_avg_pric"],
   cap=372),
 "ka20068": dict(
   tbl="ka20068_lending_balance",
   cols=["dt","dbrt_trde_cntrcnt","dbrt_trde_rpy","dbrt_trde_irds","rmnd","remn_amt"],
   cap=100),
}

con = sqlite3.connect(DB)
con.execute("PRAGMA journal_mode=WAL")

for api, s in SPEC.items():
    cols = s["cols"]
    ddl = ",\n  ".join(f'"{c}" TEXT' for c in cols)
    con.execute(f"DROP TABLE IF EXISTS {s['tbl']}")
    con.execute(f"""CREATE TABLE {s['tbl']} (
  ticker TEXT NOT NULL,
  {ddl},
  src_api TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  PRIMARY KEY (ticker, dt)
)""")

# 종목별 수집 결과. 캡에 걸렸는지를 여기 남긴다 — rt_cd=0 은 완결성을 보증하지 않는다
con.execute("DROP TABLE IF EXISTS ingest_shard")
con.execute("""CREATE TABLE ingest_shard (
  src_api TEXT NOT NULL, ticker TEXT NOT NULL,
  req_start TEXT NOT NULL, req_end TEXT NOT NULL,
  n_rows INTEGER NOT NULL, cap INTEGER NOT NULL,
  status TEXT NOT NULL,             -- ok / truncated / empty
  first_dt TEXT, last_dt TEXT,
  collected_at TEXT NOT NULL,
  next_cursor TEXT,
  PRIMARY KEY (src_api, ticker, req_start, req_end)
)""")

for api, s in SPEC.items():
    cols, tbl, cap = s["cols"], s["tbl"], s["cap"]
    ph = ",".join("?" * (len(cols) + 3))
    rows, shards, t0 = [], [], time.time()
    for line in open(f"{RAW}/{api}.jsonl"):
        r = json.loads(line); tk, d = r["tk"], r["d"]
        for x in d:
            rows.append([tk] + [x.get(c) for c in cols] + [api, COLLECTED])
        n = len(d)
        st = "empty" if n == 0 else ("truncated" if n >= cap else "ok")
        dts = [x.get("dt") for x in d if x.get("dt")]
        shards.append((api, tk, REQ_START, REQ_END, n, cap, st,
                       min(dts) if dts else None, max(dts) if dts else None, COLLECTED, None))
    con.executemany(f"INSERT OR REPLACE INTO {tbl} VALUES ({ph})", rows)
    con.executemany("INSERT OR REPLACE INTO ingest_shard VALUES (?,?,?,?,?,?,?,?,?,?,?)", shards)
    con.commit()
    print(f"{tbl}: {len(rows):,}행 / {len(shards):,}종목  ({time.time()-t0:.1f}초)")

print()
for api, tk, st, n in con.execute("""
  SELECT src_api, COUNT(*), status, SUM(n_rows) FROM ingest_shard GROUP BY src_api, status"""):
    print(f"  {api}  {st:<10} {tk:>5}종목  {n:>9,}행")
con.close()
print(f"\n→ {DB}  ({os.path.getsize(DB)/1048576:.1f} MB)")
