"""KRX 가 당일 데이터를 언제부터 주는지 관측. 매시 1회, 당일·전일 2콜."""
import os, sys, sqlite3, time
from datetime import datetime, timedelta
BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api

DB = f"{BASE}/data/raw/krx_timing.db"
con = sqlite3.connect(DB)
con.execute("""CREATE TABLE IF NOT EXISTS probe(
  ts_kst TEXT NOT NULL, target_dd TEXT NOT NULL, offset_d INTEGER NOT NULL,
  n_rows INTEGER NOT NULL, PRIMARY KEY (ts_kst, target_dd))""")
kst = datetime.utcnow() + timedelta(hours=9)
for off in (0, 1):                      # 당일 · 전일
    d = kst - timedelta(days=off)
    if d.weekday() >= 5: continue       # 주말은 애초에 데이터가 없다
    dd = d.strftime("%Y%m%d")
    try:    n = len(api.krx("sto/stk_bydd_trd", dd))
    except Exception: n = -1
    con.execute("INSERT OR REPLACE INTO probe VALUES (?,?,?,?)",
                (kst.strftime("%Y-%m-%d %H:%M"), dd, off, n))
    time.sleep(0.4)
con.commit(); con.close()
