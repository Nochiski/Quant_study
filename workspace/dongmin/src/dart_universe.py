"""백필 대상 corp_code 목록 생성.

DART corpCode 의 상장사는 3,985 건이지만 그중에는 우리 KRX 원장에 한 번도
나타나지 않은 종목(비상장 전환·해외상장 등)이 섞여 있다. 원장에 실재하는
티커와 교집합을 잡아야 헛콜이 안 나간다.
"""
import io, os, sys, zipfile, sqlite3, argparse
import xml.etree.ElementTree as ET

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api

DB  = f"{BASE}/data/raw/dart.db"
KRX = f"{BASE}/data/raw/krx.db"

def krx_tickers(path):
    """KRX 원장 전기간 티커 합집합. 날짜축이라 폐지종목이 그대로 남아 있다."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    t = set()
    for tbl in ("krx_stk_isu_base_info", "krx_ksq_isu_base_info"):
        t |= {r[0] for r in con.execute(f"SELECT DISTINCT ISU_SRT_CD FROM {tbl}") if r[0]}
    con.close()
    return t

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
    tk = krx_tickers(a.krx)
    hit  = sorted(tk & set(m))
    miss = sorted(tk - set(m))

    con = sqlite3.connect(DB, timeout=60)
    con.execute("""CREATE TABLE IF NOT EXISTS dart_corp_map(
      stock_code TEXT PRIMARY KEY, corp_code TEXT NOT NULL, corp_name TEXT)""")
    con.executemany("INSERT OR REPLACE INTO dart_corp_map VALUES (?,?,?)",
                    [(s, m[s][0], m[s][1]) for s in hit])
    con.commit(); con.close()

    with open(a.out, "w") as f:
        f.write("\n".join(m[s][0] for s in hit) + "\n")

    print(f"  DART 상장사      {len(m):,}")
    print(f"  KRX 원장 티커    {len(tk):,}")
    print(f"  교집합(대상)     {len(hit):,}  → {a.out}")
    print(f"  DART 에 없는 티커 {len(miss):,}  예: {', '.join(miss[:8])}")

if __name__ == "__main__":
    main()
