"""사람이 읽는 층. 통합층 위에 뷰 넷을 얹는다.

원장을 바꾸지 않는다. 원장 전량은 엑셀에 안 들어가지만(3,478사 × 11년 × 183행 ≈ 700만 행)
통합층은 들어간다(연간 38,258행 = 엑셀 0.04시트). 그래서 사람이 보는 대상은 통합층이다.

  v_fin          억원 단위 · 한글 컬럼. 종목 하나를 훑어볼 때
  v_fin_item     wide 를 표시용 long 으로 되돌린 것. 재무제표 배열 순서
  v_fin_cover    커버리지 대시보드. 무엇이 얼마나 찼나
  v_fin_diag     특정 항목이 왜 비었는지 추적
"""
import os, sys, sqlite3, argparse

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
from fin_map import CORE

PANEL_DEFAULT = f"{BASE}/data/build/equity_fin.db"

RC = ("CASE reprt_code WHEN '11011' THEN '사업' WHEN '11012' THEN '반기' "
      "WHEN '11013' THEN '1분기' WHEN '11014' THEN '3분기' ELSE reprt_code END")

# 표시 순서 = 재무제표 배열 순서. seq 가 그 순서를 담는다.
ITEMS = [
    (10, "매출액", "revenue", "억원"), (11, "매출원가", "cost_of_sales", "억원"),
    (12, "매출총이익", "gross_profit", "억원"), (13, "영업이익", "op_profit", "억원"),
    (14, "법인세차감전이익", "pretax_income", "억원"), (15, "당기순이익", "net_income", "억원"),
    (16, "지배주주순이익", "net_income_owners", "억원"),
    (17, "기본주당이익", "eps_basic", "원"),
    (30, "자산총계", "total_asset", "억원"), (31, "유동자산", "current_assets", "억원"),
    (32, "현금및현금성자산", "cash", "억원"), (33, "재고자산", "inventories", "억원"),
    (40, "부채총계", "total_liab", "억원"), (41, "유동부채", "current_liab", "억원"),
    (42, "리스부채", "lease_liab", "억원"),
    (50, "자본총계", "total_equity", "억원"),
    (51, "지배주주지분", "equity_owners", "억원"),
    (60, "영업활동현금흐름", "cf_operating", "억원"),
    (61, "투자활동현금흐름", "cf_investing", "억원"),
    (62, "재무활동현금흐름", "cf_financing", "억원"),
    (63, "유형자산취득(CAPEX)", "capex", "억원"),
]


def build(panel_path: str) -> None:
    panel_path = os.path.abspath(panel_path)
    if not os.path.exists(panel_path):
        raise FileNotFoundError(f"통합층이 없다 — panel={panel_path}. build_panel.py 를 먼저 실행하라")
    con = sqlite3.connect(panel_path)

    # ── 1. v_fin — 억원 단위 한글 컬럼 ────────────────────────
    body = ",\n  ".join(
        f'{col} / 100000000 AS "{ko}"' if unit == "억원" else f'{col} AS "{ko}"'
        for _, ko, col, unit in sorted(ITEMS))
    con.executescript(f"""
    DROP VIEW IF EXISTS v_fin;
    CREATE VIEW v_fin AS
    SELECT corp_code AS 기업, bsns_year AS 사업연도, {RC} AS 보고서,
           fs_div AS 연결구분, term_nm AS 기수, rcept_no AS 접수번호,
      {body}
    FROM financials WHERE term_rank = 0;
    """)

    # ── 2. v_fin_item — 표시용 long ───────────────────────────
    #    unit 컬럼이 있어야 EPS 에 /1e8 이 안 걸린다. 없으면 6,605원이 0 이 된다.
    union = "\n  UNION ALL ".join(
        f"SELECT corp_code, bsns_year, reprt_code, fs_div, term_rank, "
        f"{seq} AS seq, '{ko}' AS 항목, '{unit}' AS 단위, "
        f"{col} / 100000000 AS 값 FROM financials" if unit == "억원" else
        f"SELECT corp_code, bsns_year, reprt_code, fs_div, term_rank, "
        f"{seq} AS seq, '{ko}' AS 항목, '{unit}' AS 단위, "
        f"{col} AS 값 FROM financials"
        for seq, ko, col, unit in sorted(ITEMS))
    con.executescript(f"""
    DROP VIEW IF EXISTS v_fin_item;
    CREATE VIEW v_fin_item AS {union};
    """)

    # ── 3. v_fin_cover — 커버리지 ─────────────────────────────
    cover = ",\n  ".join(
        f"SUM({c} IS NOT NULL) AS \"{ko}\"" for _, ko, c, _ in sorted(ITEMS) if c in CORE)
    con.executescript(f"""
    DROP VIEW IF EXISTS v_fin_cover;
    CREATE VIEW v_fin_cover AS
    SELECT bsns_year AS 사업연도, {RC} AS 보고서, fs_div AS 연결구분,
           COUNT(*) AS 조합수,
      {cover}
    FROM financials WHERE term_rank = 0
    GROUP BY bsns_year, reprt_code, fs_div;
    """)

    # ── 4. v_fin_diag — 왜 비었나 ─────────────────────────────
    con.executescript("""
    DROP VIEW IF EXISTS v_fin_diag;
    CREATE VIEW v_fin_diag AS
    SELECT corp_code AS 기업, bsns_year AS 사업연도, reprt_code AS 보고서,
           fs_div AS 연결구분, term_rank AS 기, item AS 항목,
           CASE reason
             WHEN 'empty_term' THEN '그 기(期)에 금액 없음 — 분기 전기 손익은 정상'
             WHEN 'ambiguous'  THEN '값이 다른 후보 2개 이상 — 규칙을 좁혀야 함'
             WHEN 'missing'    THEN '계정 자체가 응답에 없음'
             ELSE reason END AS 사유, detail AS 상세
    FROM fin_reject;
    """)
    con.commit()

    views = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]
    print(f"  뷰 {len(views)}개: {', '.join(views)}")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=PANEL_DEFAULT)
    a = ap.parse_args()
    print(f"뷰 생성: {a.panel}")
    build(a.panel)


if __name__ == "__main__":
    main()
