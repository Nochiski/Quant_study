"""백필 대상 corp_code 목록 생성.

DART corpCode 의 상장사는 3,985 건이지만 그중에는 우리 KRX 원장에 한 번도
나타나지 않은 종목(비상장 전환·해외상장 등)이 섞여 있다. 원장에 실재하는
티커와 교집합을 잡아야 헛콜이 안 나간다.
"""
import io, os, sys, zipfile, sqlite3, argparse
from datetime import datetime
import xml.etree.ElementTree as ET

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api

DB  = f"{BASE}/data/raw/dart.db"
KRX = f"{BASE}/data/raw/krx.db"

def krx_tickers(path):
    """KRX 원장 전기간 티커 → (first_dd, last_dd). 날짜축이라 폐지종목이 그대로 남아 있다."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    span = {}
    for tbl in ("krx_stk_isu_base_info", "krx_ksq_isu_base_info"):
        for tk, lo, hi in con.execute(
                f"SELECT ISU_SRT_CD, MIN(bas_dd_req), MAX(bas_dd_req) "
                f"FROM {tbl} WHERE ISU_SRT_CD IS NOT NULL GROUP BY 1"):
            if tk in span:
                lo = min(lo, span[tk][0]); hi = max(hi, span[tk][1])
            span[tk] = (lo, hi)
    con.close()
    return span

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--krx", default=KRX)
    ap.add_argument("--out", default=f"{BASE}/data/corps.txt")
    a = ap.parse_args()

    z = zipfile.ZipFile(io.BytesIO(api.dart("corpCode.xml")))
    root = ET.parse(io.BytesIO(z.read(z.namelist()[0]))).getroot()
    m = {}
    for e in root.findall(".//list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = (e.findtext("corp_code"), e.findtext("corp_name"))
    span = krx_tickers(a.krx)
    tk = set(span)
    hit  = sorted(tk & set(m))
    miss = sorted(tk - set(m))

    # corp_code 단위 상장구간. 우선주는 본주와 corp_code 를 공유하므로 합집합을 잡는다.
    gate = {}
    for sc in hit:
        cc = m[sc][0]
        lo, hi = span[sc]
        if cc in gate:
            lo = min(lo, gate[cc][0]); hi = max(hi, gate[cc][1])
        gate[cc] = (lo, hi)

    con = sqlite3.connect(DB, timeout=60)
    con.execute("""CREATE TABLE IF NOT EXISTS dart_corp_map(
      stock_code TEXT PRIMARY KEY, corp_code TEXT NOT NULL, corp_name TEXT)""")
    con.executemany("INSERT OR REPLACE INTO dart_corp_map VALUES (?,?,?)",
                    [(s, m[s][0], m[s][1]) for s in hit])
    con.commit(); con.close()

    # 포맷: corp_code<TAB>first_year<TAB>last_year
    #   백필이 이 구간 밖 연도를 안 쏘게 하는 게 목적이다. 게이팅이 없으면
    #   폐지 종목도 올해까지 돌아 corp_year 축이 전건 013 으로 낭비된다.
    #   상장 직전연도 1년 유예 — 상장 첫 해 보고서에 전기 비교값이 실린다.
    FLOOR, CEIL = 2015, datetime.now().year
    with open(a.out, "w") as f:
        for sc in hit:
            cc = m[sc][0]
            lo, hi = gate[cc]
            y0 = max(FLOOR, int(lo[:4]) - 1)
            y1 = min(CEIL, int(hi[:4]))
            f.write(f"{cc}\t{y0}\t{y1}\n")

    print(f"  DART 상장사      {len(m):,}")
    print(f"  KRX 원장 티커    {len(tk):,}")
    print(f"  교집합(대상)     {len(hit):,}  → {a.out}")
    print(f"  DART 에 없는 티커 {len(miss):,}  예: {', '.join(miss[:8])}")
    ny = sum(min(CEIL, int(gate[c][1][:4])) - max(FLOOR, int(gate[c][0][:4]) - 1) + 1
             for c in {m[s][0] for s in hit})
    print(f"  게이팅 corp-year  {ny:,}  (게이팅 없으면 "
          f"{len({m[s][0] for s in hit}) * (CEIL - FLOOR + 1):,})")

if __name__ == "__main__":
    main()
