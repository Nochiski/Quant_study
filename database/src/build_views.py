"""사람이 읽는 층. 통합층 위에 뷰 다섯을 얹는다.

원장을 바꾸지 않는다. 원장 전량은 엑셀에 안 들어가지만(3,478사 × 11년 × 183행 ≈ 700만 행)
통합층은 들어간다(연간 38,258행 = 엑셀 0.04시트). 그래서 사람이 보는 대상은 통합층이다.

  v_fin          억원 단위 · 한글 컬럼. 종목 하나를 훑어볼 때
  v_fin_item     wide 를 표시용 long 으로 되돌린 것. 재무제표 배열 순서
  v_fin_cover    커버리지 대시보드. 무엇이 얼마나 찼나
  v_fin_diag     특정 항목이 왜 비었는지 추적
  v_fin_anomaly  DART 원본 오류 탐지 결과 (DEFECT-S01)

term_rank = 0 필터를 남겨 둔다. 상시 파이프라인은 당기만 만들지만, 감사 목적으로
전기 축을 되돌리면(build_stage.py TERMS) 재작성 값이 사람이 보는 층에 섞인다.
그 필터가 그때의 방벽이다.

현금흐름 컬럼 이름의 `(누계)`는 장식이 아니다 — 분기에서 손익은 3개월인데
현금흐름은 연초누계라 한 행 안에 기간이 다른 값이 섞인다(DEFECT-C02).
'손익기간(월)' 컬럼이 손익 쪽 기간을 말한다.
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
    (60, "영업활동현금흐름(누계)", "cf_operating_ytd", "억원"),
    (61, "투자활동현금흐름(누계)", "cf_investing_ytd", "억원"),
    (62, "재무활동현금흐름(누계)", "cf_financing_ytd", "억원"),
    (63, "유형자산취득CAPEX(누계)", "capex_ytd", "억원"),
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
           is_months AS "손익기간(월)", revenue_basis AS 매출근거,
      {body}
    FROM financials WHERE term_rank = 0;
    """)

    # ── 2. v_fin_item — 표시용 long ───────────────────────────
    #    unit 컬럼이 있어야 EPS 에 /1e8 이 안 걸린다. 없으면 6,605원이 0 이 된다.
    union = "\n  UNION ALL ".join(
        f"SELECT corp_code, bsns_year, reprt_code, fs_div, term_rank, "
        f"{seq} AS seq, '{ko}' AS 항목, '{unit}' AS 단위, "
        f"{col} / 100000000 AS 값 FROM financials WHERE term_rank = 0" if unit == "억원" else
        f"SELECT corp_code, bsns_year, reprt_code, fs_div, term_rank, "
        f"{seq} AS seq, '{ko}' AS 항목, '{unit}' AS 단위, "
        f"{col} AS 값 FROM financials WHERE term_rank = 0"
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
             WHEN 'empty_term' THEN '그 기(期)에 금액 없음'
             WHEN 'ambiguous'  THEN '값이 다른 후보 2개 이상 — 규칙을 좁혀야 함'
             WHEN 'no_account' THEN '재무제표는 있는데 그 계정이 원장에 없음'
             WHEN 'no_stmt'    THEN '그 재무제표(sj_div) 자체가 응답에 없음'
             ELSE reason END AS 사유, detail AS 상세
    FROM fin_reject;
    """)

    # ── 5. v_fin_anomaly — DART 원본 오류 (DEFECT-S01) ────────
    #    버리지 않고 표시만 한다. 배제 여부는 소비 측이 이 뷰를 조인해 정한다.
    con.executescript("""
    DROP VIEW IF EXISTS v_fin_anomaly;
    CREATE VIEW v_fin_anomaly AS
    SELECT corp_code AS 기업, bsns_year AS 사업연도, reprt_code AS 보고서,
           fs_div AS 연결구분, term_rank AS 기,
           CASE check_id
             WHEN 'assets_vs_eqliab' THEN '자산총계 ≠ 자본과부채총계 — DART 원본 불일치'
             WHEN 'quarterly_assets_stale' THEN '분기 자산총계가 갱신되지 않음 — DART 원본 stale'
             ELSE check_id END AS 검사, detail AS 상세
    FROM fin_anomaly;
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
