"""corp_code(DART) → ticker(KRX·키움) 브리지. 통합층이 가격·수급과 만나는 지점.

DART 는 기업 단위(corp_code)이고 시장 데이터는 종목 단위(ticker)다.
둘을 잇지 못하면 재무는 백테스트에서 쓸 수 없다.

우선주 처리:
  DART 는 우선주에 별도 corp_code 를 주지 않는다. 우선주의 재무는 본주 것과 같다 —
  같은 회사이므로 그게 회계적으로 맞다. KRX 티커 규칙상 우선주는 본주 앞 5자리를
  공유하므로 substr(ticker,1,5)||'0' 로 본주에 도달한다(실측 113/113).

  단 시가총액·주가는 우선주가 따로 움직인다. 그래서 재무만 공유하고
  가격 축 지표(PBR·PER)는 각 티커의 시총으로 계산해야 한다.
"""
import os, sqlite3, argparse

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DEFAULT   = f"{BASE}/data/raw/dart.db"
KRX_DEFAULT   = f"{BASE}/data/raw/krx.db"
PANEL_DEFAULT = f"{BASE}/data/build/equity_fin.db"

DDL = """
DROP TABLE IF EXISTS corp_ticker;
CREATE TABLE corp_ticker (
  ticker     TEXT NOT NULL,          -- KRX 6자리 단축코드
  corp_code  TEXT NOT NULL,          -- DART 8자리
  corp_name  TEXT,
  share_kind TEXT,                   -- 보통주 / 구형우선주 / 신형우선주 / 종류주권
  is_common  INTEGER NOT NULL,       -- 1 = 보통주(직접 매핑) · 0 = 우선주(브리지)
  bridge     TEXT NOT NULL,          -- direct | prefix5
  first_dd   TEXT,                   -- 원장에 처음 나타난 거래일
  last_dd    TEXT,                   -- 마지막. 오늘이 아니면 폐지된 것이다
  PRIMARY KEY (ticker)
);
CREATE INDEX ix_ct_corp ON corp_ticker(corp_code);
"""


def build(raw: str, krx: str, panel: str) -> None:
    raw, krx, panel = map(os.path.abspath, (raw, krx, panel))
    for p, nm in ((raw, "원장"), (krx, "KRX 원장"), (panel, "통합층")):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{nm} 이 없다 — path={p}")

    con = sqlite3.connect(f"file:{panel}", uri=True)
    con.executescript(DDL)
    con.execute("ATTACH DATABASE ? AS d", (f"file:{raw}?mode=ro",))
    con.execute("ATTACH DATABASE ? AS k", (f"file:{krx}?mode=ro",))

    # KRX 전기간 종목. 최신 스냅샷만 쓰면 폐지 909종목이 빠지고,
    # 그게 곧 생존편향(실측 26.75%)이다. 종목별 마지막 관측을 대표로 삼는다.
    con.execute("""
      CREATE TEMP VIEW krx_master AS
      WITH u AS (
        SELECT ISU_SRT_CD tk, ISU_ABBRV nm, KIND_STKCERT_TP_NM kind, bas_dd_req dd
        FROM k.krx_stk_isu_base_info
        UNION ALL
        SELECT ISU_SRT_CD, ISU_ABBRV, KIND_STKCERT_TP_NM, bas_dd_req
        FROM k.krx_ksq_isu_base_info)
      SELECT tk, nm, kind, MIN(dd) first_dd, MAX(dd) last_dd
      FROM u GROUP BY tk HAVING dd = MAX(dd)
    """)

    # 1) 직접 매핑 — 보통주
    con.execute("""
      INSERT OR IGNORE INTO corp_ticker
      SELECT x.tk, m.corp_code, m.corp_name, x.kind, 1, 'direct', x.first_dd, x.last_dd
      FROM krx_master x JOIN d.dart_corp_map m ON m.stock_code = x.tk
    """)
    # 2) 브리지 — 우선주는 본주 corp_code 를 공유한다
    con.execute("""
      INSERT OR IGNORE INTO corp_ticker
      SELECT x.tk, m.corp_code, m.corp_name, x.kind, 0, 'prefix5', x.first_dd, x.last_dd
      FROM krx_master x
      JOIN d.dart_corp_map m ON m.stock_code = SUBSTR(x.tk, 1, 5) || '0'
      WHERE NOT EXISTS (SELECT 1 FROM corp_ticker c WHERE c.ticker = x.tk)
    """)
    con.commit()

    tot = con.execute("SELECT COUNT(*) FROM krx_master").fetchone()[0]
    got = con.execute("SELECT COUNT(*) FROM corp_ticker").fetchone()[0]
    print(f"  KRX 종목 {tot:,} → 매핑 {got:,}  ({got/tot*100:.1f}%)")
    for b, n, k in con.execute(
            "SELECT bridge, COUNT(*), COUNT(DISTINCT corp_code) FROM corp_ticker GROUP BY 1"):
        print(f"    {b:<9} {n:>5,} 종목 · {k:,} 기업")
    live = con.execute(
        "SELECT COUNT(*) FROM corp_ticker WHERE last_dd = "
        "(SELECT MAX(last_dd) FROM corp_ticker)").fetchone()[0]
    print(f"    상장 유지 {live:,} · 폐지 {got-live:,}  (폐지분이 곧 생존편향 제거의 근거다)")
    miss = con.execute(
        "SELECT kind, COUNT(*) FROM krx_master x "
        "WHERE NOT EXISTS (SELECT 1 FROM corp_ticker c WHERE c.ticker = x.tk) "
        "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    if miss:
        print("  미매핑: " + " · ".join(f"{k or '(불명)'} {n}" for k, n in miss))
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=RAW_DEFAULT)
    ap.add_argument("--krx", default=KRX_DEFAULT)
    ap.add_argument("--panel", default=PANEL_DEFAULT)
    a = ap.parse_args()
    print(f"브리지 생성: {a.panel}")
    build(a.raw, a.krx, a.panel)


if __name__ == "__main__":
    main()
