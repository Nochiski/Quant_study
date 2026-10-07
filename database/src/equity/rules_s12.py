"""S12 재무 PIT 슬라이스 — `fin_std` (DESIGN v1.2 §4-4 · GATES v1.0 §3-⑫ · WORKFLOW §3-4).

grain (`corp_code`, `period_end`, `report_code`, `fs_div`, `vintage_kind`) · receipt_axis ·
원천 `stg_fin`(long) + `stg_doc_meta`(기간 정본) + `stg_disclosure`(접수일) + S11
`disclosure_version`(무매칭 비대칭 게이트) + `corp`(`fiscal_month` 검산·후보 규칙).

**계정 대응표는 `src/fin_map.py` 가 정본**이다 — 여기서 import 해서 쓰고 베끼지 않는다. `.sql` 의
`_acct` VALUES 블록은 `acct_values_sql()` 이 만든 문자열을 **그대로** 담고 tests 가 대조한다
(S05 종류 어휘 대응표와 같은 규약). fin_map 21 계정에 DESIGN §4-4 의 추가 3
(`depreciation`·`borrowings`·`interest_expense`)을 더해 **24 계정**이고, `gross_profit` 은
fin_map 에 이미 있으므로 새 계정이 아니라 FIELD_MAP §3 의 판정(미지원 → 지원)만 바뀐다.

**기간 정본**: `period_end` = `stg_doc_meta.period_to`(main 멤버), `report_code` = `doc_acode`.
`doc_acode` 는 1분기와 3분기를 **둘 다 11013** 으로 적으므로 `period_from → period_to` 개월 수로
가른다(3개월 → 11013 · 9개월 → 11014). 그 판에 문서가 없으면 같은 정정 사슬(`disclosure_version`
링크)의 최신 문서로 잇고(e1.25.0), 사슬에도 없으면 후보 규칙(`corp.fiscal_month` 말일을
보고서 종류만큼 당긴 두 후보 × `bsns_year`·`bsns_year+1`, 접수일까지 0~`period_end_lag_max_days`
일)으로 채우고 `period_end_basis='inferred'`; 후보가 0 개거나 2 개면 `period_unresolved` 격리.

**기간 어휘**: 손익(IS·CIS)의 `thstrm_amount` 는 분기 보고서에서 **당분기 3개월**, 사업보고서에서
12개월이다(DEFECT-C02, STAGE_SPEC §2-7). 4분기 값은 원장에 없으므로 `<계정>_q4_derived` =
사업보고서 − Σ(1Q·2Q·3Q) 로 만들고 **셋 중 하나라도 없으면 NULL**(부분합 금지). 현금흐름은
연초누계라 `_ytd` 를 그대로 싣고 `_q` = 자기 누계 − 직전 보고서 누계(1분기는 누계 = 분기).
두 파생 블록은 각각 `q4_derived_available_date`·`q4_derived_n_rows`,
`cf_q_available_date`·`cf_q_n_rows` 를 동반한다(DESIGN §3 파생 컬럼 규약).

**판본**: 4A 는 DART API 가 주는 최신 판본만 있으므로 `vintage_kind='api_restated'` 한 종류이고
`restated_unknown=true` 다. 원본·정정 판본은 4C(S14, 문서층 P2 `stg_fin_asreported`) 몫이다.
API 는 정정이 있으면 정정본의 `rcept_no` 를 돌려주므로 `available_date` = 그 접수일이고
`rcept_dt − period_end` 가 1년을 넘는 행이 실제로 있다(절단본 최대 445일). 단 원본부터 그 판까지의
정정이 모두 재무를 건드리지 않았으면 수치가 원본과 같으므로 공개일은 원본 접수일이다
(09-30 — 그 행은 `available_date < rcept_dt`. 서버 실측 2016~ 늦게 찍힌 4,657행 중 재무표 무관 정정 2,581).
재무 정정 판정은 `disclosure_version.corr_has_fin_item` 한 곳이다 — 항목 이름 키워드 외에
정정 사유가 재작성·재감사 류이거나 항목 표가 빈 정정도 재무 정정(TRUE·NULL)이다(C-11, N-25 Q1).
일일 수집은 같은 (corp, bsns_year, reprt_code, fs_div) 에 원본 뒤에 정정을 덧붙이므로 그룹의
판본은 **최신 접수(max `rcept_no`)** 하나다(G-21, N-25 Q2 — 백필 구간과 같은 '최신 판본 값 +
그 판의 공개일'). 공개일의 날짜 원천은 stage 공개일 `stg_disclosure.available_date`(원천 rcept_dt
와 접수번호 날짜 중 늦은 쪽 — J-41 재제출본은 `available_date > rcept_dt`)이고 `rcept_dt` 는
원천 그대로 기간 판정 축이다.

**매출 기준 두 축**: `revenue_basis` 는 그 행의 매출이 어느 규칙에서 나왔는지를,
`revenue_basis_prev` 는 **직전 회계연도 같은 보고서**(`bsns_year` − 1 · 같은 `report_code`·
`fs_div`)가 어느 규칙이었는지를 남긴다.
한 법인 안에서 기준이 바뀌면 매출 시계열이 끊겨 가짜 성장률이 난다 — 삼성카드 2024
`standard` 4.38조 → 2025 `banking_gross` 3.84조(−12%) · 메리츠금융지주 46.6조 → 14.1조(−70%).
equity 는 **두 라벨을 싣기만 한다**. 무엇을 버릴지는 팩터층이 정한다(WORKFLOW §0-2).

**capex 기준 한 축**(DQ-8, 2026-09-26): 유형자산 취득을 집계 한 줄로 적지 않고 자산별로 나눠 적는
회사가 있어(FY2025 연간 결측 311사 중 211사) tier `d_ppe_parts` 가 그 합을 만들고 `capex_basis` 가
출처를 남긴다. 같은 회사가 둘 다 적는 경우는 0건이고 집계 줄이 있으면 `min(tier)` 가 a/b/c 에서
끝내므로 이중계상이 구조적으로 불가능하다. `capex_basis_prev` 는 두지 않는다.
후속 F-A1·F-A3 로 두 축이 더 붙었다: tier `e_ppe_combined`(유형자산+투자부동산 **합산 줄** 7사 →
`ppe_incl_invprop`, 정의가 달라 섞어 쓰면 안 된다는 표시)와 **0 규칙**(현금흐름표가 있는데 유형자산
취득 줄이 아예 없으면 `capex_ytd = 0` · `none_in_cf` — 현금흐름표는 현금흐름을 다 적으므로 줄이
없다는 것은 안 샀다는 뜻이다). 어휘는 `CAPEX_BASIS_VOCAB` 5종이다.
F-A4 로 자산별 합은 `agg='sum_abs'`(크기의 합 — 한 표 안에서 부호가 섞인다)이고 이름 목록은 자산
종류 × 접미어 48종으로 펼쳤으며, `pick` 은 공백 없는 원문을 먼저 본다.

**값이 없는 사유**(T-H, 2026-09-28): 두 basis 어휘의 `unavailable` 을 각각 둘로 갈랐다 —
`capex_basis` 는 `no_cf_statement`(현금흐름표를 읽은 적이 없다 = F-A3 0 규칙의 분모 밖)와
`unmapped`(표는 있는데 대응표가 못 잡았다), `revenue_basis` 는 `no_is_statement`(IS·CIS 행 없음)와
`unmapped` 다. 고칠 대상이 원천인지 대응표인지를 행이 스스로 말한다. 같은 사정으로
**EG8_fin_std** 가 그룹(`fs_div` × 템플릿 × `report_code`)마다 **예상 대상 대비** 유효 비율을
기록하고 직전 판과 견준다 — DQ-6·DQ-8 이 전 게이트를 통과한 이유는 분모가 「만들어진 행」이었기
때문이다.

격리 4종(EG7): `non_krw`(`is_krw` 아님 — 원 단위 축 밖) · `period_unresolved` ·
`rcept_lag_out_of_range` · `duplicate_vintage`(서로 다른 (bsns_year, reprt_code) 가 같은 grain 으로
접힘 — 어느 쪽이 옳은지 규칙이 못 고르므로 둘 다 격리한다).
"""
from __future__ import annotations

import re
from pathlib import Path

from fin_map import CAPEX_COMBINED, CAPEX_FALLBACK, FIN_MAP, REVENUE_FALLBACK
from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext, require_const
from .model import EquityTable, FieldProfile, register

SQL_DIR = Path(__file__).parent / "sql"
SQL_PATH = SQL_DIR / "fin_std.sql"

VINTAGE_KIND = "api_restated"
VINTAGE_KIND_VOCAB: tuple[str, ...] = ("api_restated", "original", "corrected")   # 4C 확장분 포함
PERIOD_END_BASIS_VOCAB: tuple[str, ...] = ("document", "inferred")
# **값이 없는 사유**(T-H, 2026-09-28). 예전에는 둘 다 `unavailable` 한 낱말이라 「볼 표가
# 없었다」 와 「표는 있는데 대응표가 못 잡았다」 가 구별되지 않았다 — DQ-6(금융 템플릿 순이익
# 전건 NULL)·DQ-8(capex 311사 NULL)이 게이트를 다 통과한 통로가 그 자리다. 고칠 대상이
# 원천(표가 없다)인지 대응표(못 잡았다)인지를 행이 스스로 말해야 한다.
REVENUE_BASIS_NULL_REASONS: tuple[str, ...] = ("no_is_statement", "unmapped")
CAPEX_BASIS_NULL_REASONS: tuple[str, ...] = ("no_cf_statement", "unmapped")
REVENUE_BASIS_VOCAB: tuple[str, ...] = ("standard", "banking_gross", "insurance_gross",
                                        "consensus", *REVENUE_BASIS_NULL_REASONS)
# capex 산출 기준(DQ-8 · 2026-09-26 F-A1·F-A3 · 2026-09-28 T-H) — `standard` = 집계 한 줄 ·
# `ppe_parts` = 자산별 줄의 합 · `ppe_incl_invprop` = 유형자산+투자부동산 합산 줄(정의가 다르다) ·
# `none_in_cf` = 현금흐름표는 있는데 유형자산 취득 줄이 없어 0 으로 읽은 것 ·
# `no_cf_statement` = 현금흐름표를 읽은 적이 없다(영업·투자 소계 둘 다 없음 = 0 규칙의 분모 밖) ·
# `unmapped` = 표는 있는데 대응표가 못 잡았다(값이 모호해 NULL 이 된 것 포함).
CAPEX_BASIS_VOCAB: tuple[str, ...] = ("standard", "ppe_parts", "ppe_incl_invprop",
                                      "none_in_cf", *CAPEX_BASIS_NULL_REASONS)
REPORT_CODE_VOCAB: tuple[str, ...] = ("11011", "11012", "11013", "11014")
FS_DIV_VOCAB: tuple[str, ...] = ("CFS", "OFS")
REJECT_REASONS: tuple[str, ...] = ("non_krw", "period_unresolved", "rcept_lag_out_of_range",
                                   "duplicate_vintage")

# ── 계정 (fin_map 21 + DESIGN §4-4 추가 3 = 24) ────────────────────────────────
# `family` 가 기간 어휘를 정한다: flow = 손익(3개월/12개월, q4 파생 대상) · stock = 재무상태표
# 시점값 · cf = 현금흐름 연초누계(`_ytd`, `_q` 파생 대상).
FLOW_ACCOUNTS: tuple[str, ...] = ("revenue", "cost_of_sales", "gross_profit", "op_profit",
                                  "pretax_income", "net_income", "net_income_owners",
                                  "eps_basic", "depreciation", "interest_expense")
STOCK_ACCOUNTS: tuple[str, ...] = ("total_asset", "total_liab", "total_equity", "equity_owners",
                                   "cash", "inventories", "current_assets", "current_liab",
                                   "lease_liab", "borrowings")
CF_ACCOUNTS: tuple[str, ...] = ("cf_operating_ytd", "cf_investing_ytd", "cf_financing_ytd",
                                "capex_ytd")
ACCOUNTS: tuple[str, ...] = FLOW_ACCOUNTS + STOCK_ACCOUNTS + CF_ACCOUNTS

# DESIGN §4-4 추가 3 — `account_id` 실재를 절단본에서 확인하고 넣었다(§10 P30).
#   depreciation      CIS `ifrs-full_DepreciationAndAmortisationExpense` 9행 ·
#                     `dart_DepreciationExpense` 6행(2차 태그). 현금흐름표의
#                     `AdjustmentsForDepreciation*` 는 **쓰지 않는다** — 기간 어휘가 누계라
#                     손익 3개월 값과 섞으면 안 된다.
#   borrowings        BS `ifrs-full_Borrowings` 9행. 단기·장기·유동성장기를 합산하지 않는다 —
#                     `CurrentPortionOfLongtermBorrowings` 가 `LongtermBorrowings` 와 겹쳐
#                     이중계상된다(fin_map 규칙 1: 완전일치만). 커버율이 낮은 것은 사실이고
#                     EG3_fin_std 의 계정 커버율이 그 값을 남긴다.
#   interest_expense  CIS `ifrs-full_InterestExpense` 12행. `FinanceCosts`(금융비용 137행)는
#                     이자비용보다 넓은 개념이라 대체 태그로 쓰지 않는다.
EXTRA_ACCOUNTS: dict[str, dict[str, object]] = {
    "depreciation": {"concept": ["DepreciationAndAmortisationExpense"],
                     "concept_alt": ["DepreciationExpense"],
                     "nm": ["감가상각비 및 상각비", "감가상각비"], "sj": ["IS", "CIS"],
                     "agg": "pick"},
    "borrowings": {"concept": ["Borrowings"], "nm": ["차입부채"], "sj": ["BS"], "agg": "pick"},
    "interest_expense": {"concept": ["InterestExpense"], "nm": ["이자비용"], "sj": ["IS", "CIS"],
                         "agg": "pick"},
}

_FAMILY: dict[str, str] = ({m: "flow" for m in FLOW_ACCOUNTS}
                           | {m: "stock" for m in STOCK_ACCOUNTS}
                           | {m: "cf" for m in CF_ACCOUNTS})

# 파생 컬럼 이름 — 손익은 `<계정>_q4_derived`, 현금흐름은 `_ytd` → `_q`
Q4_COLUMNS: tuple[str, ...] = tuple(f"{m}_q4_derived" for m in FLOW_ACCOUNTS)
CF_Q_COLUMNS: tuple[str, ...] = tuple(m.removesuffix("_ytd") + "_q" for m in CF_ACCOUNTS)
# 현금흐름 `_q` 의 직전 보고서 (1분기는 누계 자체가 분기값이라 직전이 없다)
CF_PRIOR_REPORT: dict[str, str] = {"11012": "11013", "11014": "11012", "11011": "11014"}


def _spec(metric: str) -> dict[str, object]:
    return dict(EXTRA_ACCOUNTS[metric] if metric in EXTRA_ACCOUNTS else FIN_MAP[metric])


# 탐색 순서(tier). **정렬 가능한 라벨**이라 `min(tier)` 이 우선순위를 고른다 — 숫자로 두면
# `.sql` 이 조정 상수처럼 보이는 리터럴을 갖게 된다(tests/test_equity_build.py 규약).
TIER_CONCEPT = "a_concept"
TIER_CONCEPT_ALT = "b_concept_alt"
TIER_NM = "c_nm"
TIER_REVENUE_FALLBACK: tuple[str, ...] = ("d_insurance_gross", "e_banking_gross")
TIER_CAPEX_PARTS = "d_ppe_parts"
TIER_CAPEX_COMBINED = "e_ppe_combined"

# `basis` 를 싣는 계정 — 산출 뒤 출처를 알 수 없어 라벨 컬럼이 따로 나가는 둘이다
# (`revenue_basis`·`capex_basis`). 나머지 계정은 tier a/b/c 에서 basis 가 NULL 이다.
_BASIS_METRICS: tuple[str, ...] = ("revenue", "capex_ytd")


_WS = re.compile(r"\s+")


def norm_nm(tokens: list[str]) -> list[str]:
    """계정명 토큰을 **공백 뗀 판**으로 바꾼다(F-A2, 중복은 접는다).

    DART 계정명의 공백은 회사마다 임의다(2026-09-26 실측: CF 90,456행에 공백이 있고
    `영업활동으로 인한 순현금흐름`·`유형자산 및 투자부동산의 취득` 처럼 위치가 갈린다).
    `.sql` 이 `account_nm` 을 같은 규칙으로 정규화해 대조하므로 토큰도 여기서 한 번만
    정규화한다 — fin_map 목록은 읽을 수 있는 원문으로 남는다. 완전일치 규칙(fin_map 규칙 1)은
    그대로다: 공백만 무시하고 부분일치는 여전히 안 쓴다.
    """
    out: list[str] = []
    for t in tokens:
        n = _WS.sub("", t)
        if n not in out:
            out.append(n)
    return out


def acct_rows() -> tuple[tuple[object, ...], ...]:
    """`_acct` 행 — (metric, tier, kind, tokens, sjs, agg, require, basis, family).

    tier 는 fin_map 의 탐색 순서다: `a_concept` → `b_concept_alt`(1차 태그가 한 건도 없을 때만)
    → `c_nm`(표준태그 미사용 행 폴백). 매출은 `d_insurance_gross` · `e_banking_gross` 가 더
    붙고 `require` 태그가 있어야 발동한다(fin_map.REVENUE_FALLBACK, 보험이 먼저).
    capex 는 `d_ppe_parts`(fin_map.CAPEX_FALLBACK — 같은 tier 라벨에 kind 두 줄) 와
    `e_ppe_combined`(fin_map.CAPEX_COMBINED — 유형자산+투자부동산 합산 줄)가 더 붙고 둘 다
    `require` 가 없다. 이름(`nm`·`nm_nonstd`) 토큰은 `norm_nm()` 으로 공백을 뗀 판이다.
    `agg` 어휘는 `pick`·`sum`·`sum_abs` 셋이고 `sum_abs`(크기의 합)는 자산별 합 전용이다.
    """
    rows: list[tuple[object, ...]] = []
    for metric in ACCOUNTS:
        spec = _spec(metric)
        sjs = list(spec["sj"])                      # type: ignore[arg-type]
        agg = str(spec["agg"])
        fam = _FAMILY[metric]
        basis = "standard" if metric in _BASIS_METRICS else None
        for tier, key, kind in ((TIER_CONCEPT, "concept", "concept"),
                                (TIER_CONCEPT_ALT, "concept_alt", "concept"),
                                (TIER_NM, "nm", "nm")):
            tokens = list(spec.get(key) or [])      # type: ignore[arg-type]
            if tokens:
                rows.append((metric, tier, kind,
                             tokens if kind == "concept" else norm_nm(tokens),
                             sjs, agg, None, basis, fam))
    for i, fb in enumerate(REVENUE_FALLBACK):
        rows.append(("revenue", TIER_REVENUE_FALLBACK[i], "concept", list(fb["concept"]),
                     list(fb["sj"]), "sum", str(fb["require"][0]), str(fb["basis"]), "flow"))
    # capex 자산별 합(DQ-8) — 두 줄이 **같은 tier 라벨**이라야 표준 태그 줄과 비표준 이름 줄이
    # 함께 더해진다. 집계 줄(tier a/b/c)이 하나라도 있으면 `best_tier` 의 min(tier) 이 거기서
    # 끝내므로 이중계상이 나지 않는다. `nm_nonstd` 는 표준계정코드 미사용 행만 보는 kind 다 —
    # 표준 태그 줄을 이름으로 한 번 더 세지 않기 위한 것이다(fin_map.CAPEX_FALLBACK 주석).
    # `agg` 는 `sum_abs` 다(F-A4) — 같은 표 안에서 표준 태그 줄은 +, 비표준 줄은 − 로 적는 회사가
    # 있어 그냥 더하면 취득이 상계된다(fin_map.CAPEX_FALLBACK 주석, 00402989 FY2016).
    for key, kind in (("concept", "concept"), ("nm", "nm_nonstd")):
        tokens = list(CAPEX_FALLBACK[key])
        rows.append(("capex_ytd", TIER_CAPEX_PARTS, kind,
                     tokens if kind == "concept" else norm_nm(tokens),
                     list(CAPEX_FALLBACK["sj"]), str(CAPEX_FALLBACK["agg"]), None,
                     str(CAPEX_FALLBACK["basis"]), _FAMILY["capex_ytd"]))
    # 유형자산+투자부동산 합산 줄(F-A1) — 자산별 합보다 **뒤**(tier e)다. 자산별 줄이 있으면
    # 그 합이 PPE 정의에 정확히 맞으므로 먼저 쓰고, 합산 줄은 정의가 다르다는 표시
    # (`ppe_incl_invprop`)를 달고 마지막에 받는다. 한 줄이라 `pick` 이다.
    rows.append(("capex_ytd", TIER_CAPEX_COMBINED, "nm_nonstd",
                 norm_nm(list(CAPEX_COMBINED["nm"])), list(CAPEX_COMBINED["sj"]), "pick", None,
                 str(CAPEX_COMBINED["basis"]), _FAMILY["capex_ytd"]))
    return tuple(rows)


def _list_sql(values: list[str]) -> str:
    return "[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]"


def _lit(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def acct_values_sql() -> str:
    """`.sql` 의 `_acct` VALUES 블록. 이 문자열이 `sql/fin_std.sql` 에 그대로 들어간다."""
    return ",\n".join(
        "        (" + ", ".join((
            _lit(m), _lit(tier), _lit(kind),
            _list_sql(tokens),          # type: ignore[arg-type]
            _list_sql(sjs),             # type: ignore[arg-type]
            _lit(agg), _lit(req), _lit(basis), _lit(fam))) + ")"
        for m, tier, kind, tokens, sjs, agg, req, basis, fam in acct_rows())


# `revenue_basis_prev` 의 조회 축 — 같은 법인·같은 보고서·같은 연결범위의 **직전 회계연도**.
# `.sql` 의 `basis_prev` 를 베끼지 않고 게이트가 되풀이 계산하는 자리라 술어를 한 곳에 둔다
# (`o` = 판정 대상 행 · `p` = 직전 회계연도 후보 행).
PREV_FY_PREDICATE = ("p.corp_code = o.corp_code AND p.report_code = o.report_code "
                     "AND p.fs_div = o.fs_div AND TRY_CAST(p.bsns_year AS INTEGER) "
                     "= TRY_CAST(o.bsns_year AS INTEGER) - 1")


def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def _f(ctx: EquityGateContext, sql: str) -> float | None:
    row = ctx.con.execute(sql).fetchone()
    if row is None or row[0] is None:
        return None
    return float(str(row[0]))


# 산출 행(`o`) 접수번호의 stage 공개일. 재수집 판본이 쌓여도(append_only) 상한·하한으로 읽는다.
_RCEPT_STAGE_AVAIL_MAX = ("(SELECT max(d.available_date) FROM stg_disclosure d "
                          "WHERE d.rcept_no = o.rcept_no)")
_RCEPT_STAGE_AVAIL_MIN = ("(SELECT min(d.available_date) FROM stg_disclosure d "
                          "WHERE d.rcept_no = o.rcept_no)")
# 기간 문서의 증인 접수(rcept_no → w_rcept) — 산출 행의 판 자신, 그리고 그 판에 문서가 없을 때
# `.sql` 이 기간을 빌려 오는 같은 정정 사슬(disclosure_version 링크)의 판(`chain_doc`, G-21 후속).
# 존재 명제로만 본다 — 어느 판의 문서를 골랐는지는 베끼지 않는다.
_DOC_WITNESS = ("(SELECT rcept_no, rcept_no AS w_rcept FROM \"{v}\" UNION "
                "SELECT s.rcept_no, x.rcept_no FROM disclosure_version s JOIN disclosure_version x "
                "ON coalesce(x.orig_rcept_no, x.rcept_no) = coalesce(s.orig_rcept_no, s.rcept_no))")


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _outside_vocab(ctx: EquityGateContext, column: str, values: tuple[str, ...]) -> int:
    return _n(ctx, f'SELECT count(*) FROM "{ctx.out_view}" WHERE "{column}" IS NULL '
                   f'OR "{column}" NOT IN ({_vocab_sql(values)})')


def _counts(ctx: EquityGateContext, column: str) -> dict[str, int]:
    return {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f'SELECT "{column}", count(*) FROM "{ctx.out_view}" GROUP BY 1 ORDER BY 1').fetchall()}


def _coverage(ctx: EquityGateContext, columns: tuple[str, ...]) -> dict[str, float]:
    """계정별 채움 비율(기록형). 커버율이 갑자기 떨어지면 대응표나 원천 서식이 바뀐 것이다."""
    if not ctx.n_out:
        return dict.fromkeys(columns, 0.0)
    sel = ", ".join(f'count("{c}")' for c in columns)
    row = ctx.con.execute(f'SELECT {sel} FROM "{ctx.out_view}"').fetchone()
    if row is None:
        return dict.fromkeys(columns, 0.0)
    return {c: round(int(str(v)) / ctx.n_out, 6) for c, v in zip(columns, row, strict=True)}


_INFERRED_RECENT_DAYS_DEFAULT = 30


def _inferred_recent(ctx: EquityGateContext) -> dict[str, object]:
    """최근 접수 구간에서 `period_end_basis='inferred'` 인 채택 행의 비율 (DEFECT-F01).

    창의 상한은 이 표의 `max(available_date)`(= 최신 접수일)이고 하한은 거기서
    `inferred_recent_days`(기본 30일)를 뺀 날이다 — 날짜를 상수로 박으면 매일 사람이 올려야
    한다(규칙 e1.15.0 의 날짜 파생화와 같은 규약). 임계 `fin_std_inferred_recent_ratio_max` 가
    baseline 에 없으면 `over_max = 0` 으로 두어 **기록형**으로만 남는다 — 상수를 등재하는 순간
    폐기형이 된다. **배포 순서 주의**: 문서층이 뒤처진 상태에서 임계를 켜면 최근 구간이 100%
    `inferred` 라 `fin_std` 가 매일 폐기된다.
    """
    v = ctx.out_view
    days = ctx.baseline.get(ctx.rule.name, "inferred_recent_days")
    n_days = int(str(days)) if days is not None else _INFERRED_RECENT_DAYS_DEFAULT
    ratio_max = ctx.baseline.get(ctx.rule.name, "fin_std_inferred_recent_ratio_max")
    row = ctx.con.execute(
        f'WITH w AS (SELECT max(available_date) - INTERVAL {n_days} DAY AS lo '
        f'FROM "{v}") '
        'SELECT CAST((SELECT CAST(lo AS DATE) FROM w) AS VARCHAR), '
        'count(*) FILTER (WHERE o.available_date > (SELECT lo FROM w)), '
        'count(*) FILTER (WHERE o.available_date > (SELECT lo FROM w) '
        "AND o.period_end_basis = 'inferred') "
        f'FROM "{v}" o').fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} "
                           "metric=inferred_recent")
    recent_from, n_recent, n_inferred = str(row[0]), int(str(row[1])), int(str(row[2]))
    ratio = (n_inferred / n_recent) if n_recent else None
    over = int(ratio_max is not None and ratio is not None
               and ratio > float(str(ratio_max)))
    return {"recent_from": recent_from, "days": n_days, "n_recent": n_recent,
            "n_inferred": n_inferred, "ratio": ratio,
            "ratio_max": None if ratio_max is None else float(str(ratio_max)),
            "over_max": over}


def eg3_fin_std(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 어휘 폐쇄 · 기간 판정 재계산 · 파생 부분합 금지 · 계정 커버율(기록형).

    기간 판정 재계산은 `.sql` 의 tie-break 를 베끼지 않는다 — "그 접수의 main 문서 중 **어느 한
    행**이 이 `period_end`(그리고 그 행의 개월 수가 이 `report_code`)를 준다"는 존재 명제로 본다.
    `stg_doc_meta` 가 rcept_no 당 main 을 둘 이상 갖는지는 `n_doc_meta_multi_main` 이 기록한다.
    """
    v = ctx.out_view
    wit = _DOC_WITNESS.format(v=v)
    q1 = int(str(ctx.baseline.require(ctx.rule.name, "quarter_months")))
    inferred = _inferred_recent(ctx)
    checks = {
        # DEFECT-F01(09-19 감사) — 문서층(`stg_doc_meta`)이 뒤처지면 신규 재무 그룹이
        # `period_end` 정본을 잃고 `corp.fiscal_month` 추정으로 폴백한다. 결산월이 어긋나거나
        # 없는 법인은 후보가 0개라 `period_unresolved` 로 격리 — **행째로 사라진다**(8/31 이후
        # 그룹 14 -> 10). 기존 게이트는 전부 못 잡는다: EG7 격리 비율 1.28% < 임계 3% ·
        # `n_by_period_end_basis` 는 임계 없음 · `n_period_end_not_document` 는
        # `period_end_basis='document'` 행만 보므로 정의상 0. 상수 미등재면 기록만 한다.
        "n_period_end_inferred_recent_over_max": inferred["over_max"],
        "n_vintage_outside_vocab": _outside_vocab(ctx, "vintage_kind", (VINTAGE_KIND,)),
        "n_report_code_outside_vocab": _outside_vocab(ctx, "report_code", REPORT_CODE_VOCAB),
        "n_fs_div_outside_vocab": _outside_vocab(ctx, "fs_div", FS_DIV_VOCAB),
        "n_period_basis_outside_vocab": _outside_vocab(ctx, "period_end_basis",
                                                       PERIOD_END_BASIS_VOCAB),
        "n_revenue_basis_outside_vocab": _outside_vocab(ctx, "revenue_basis",
                                                        REVENUE_BASIS_VOCAB),
        "n_capex_basis_outside_vocab": _outside_vocab(ctx, "capex_basis",
                                                      CAPEX_BASIS_VOCAB),
        "n_currency_not_krw": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE currency <> \'KRW\''),
        "n_restated_unknown_false": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                            "WHERE restated_unknown IS DISTINCT FROM TRUE"),
        # `period_end` = stg_doc_meta.period_to 정본 (document 근거 행 — 판 자신 또는 같은 사슬)
        "n_period_end_not_document": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE o.period_end_basis = \'document\' '
                 f"AND NOT EXISTS (SELECT 1 FROM {wit} c JOIN stg_doc_meta m "
                 "ON m.rcept_no = c.w_rcept WHERE c.rcept_no = o.rcept_no "
                 "AND m.member_role = 'main' AND m.period_to = o.period_end "
                 "AND m.period_from IS NOT DISTINCT FROM o.period_start)"),
        # 1Q/3Q 판정 — doc_acode 11013 은 개월 수로만 갈린다
        "n_report_code_month_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE o.period_end_basis = \'document\' '
                 f"AND NOT EXISTS (SELECT 1 FROM {wit} c JOIN stg_doc_meta m "
                 "ON m.rcept_no = c.w_rcept WHERE c.rcept_no = o.rcept_no "
                 "AND m.member_role = 'main' AND m.period_to = o.period_end "
                 "AND m.period_from = o.period_start AND o.report_code = "
                 "(CASE WHEN m.doc_acode <> '11013' THEN m.doc_acode "
                 f"WHEN date_diff('month', m.period_from, m.period_to) + 1 <= {q1} "
                 "THEN '11013' ELSE '11014' END))"),
        "n_period_end_after_rcept": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                            "WHERE period_end > rcept_dt"),
        # 공개일 = 판 접수번호의 stage 공개일(`stg_disclosure.available_date` — 원천 rcept_dt 와
        # 접수번호 날짜 중 늦은 쪽, E08·J-41). 예외는 원본 공시일을 승계한 정정본(09-30) 하나이고
        # 그 행은 공개일이 더 이르다 — 늦거나 NULL 이 갈리면 규칙이 어긋난 것이다.
        # 이름은 옛 정의(rcept_dt 와 비교)를 그대로 둔다(게이트 출력 계약). 비교 축은 e1.25.0 부터
        # stage 공개일이다 — J-41 재제출본은 `available_date > rcept_dt` 가 정상이다.
        "n_available_ne_rcept_dt": _n(ctx, f'SELECT count(*) FROM "{v}" o '
                                           "WHERE (o.available_date IS NULL) <> "
                                           "(o.rcept_dt IS NULL) "
                                           f"OR o.available_date > {_RCEPT_STAGE_AVAIL_MAX}"),
        # 접수번호의 stage 공개일보다 이른 행(= 승계 행)은 `.sql` 의 `redate` 를 베끼지 않고
        # disclosure_version 에서 다시 증언받는다: 이 판이 정정이고, 연결된 원본의 stage 공개일이
        # 공개일과 같고, 첫 장 원본 제출일이 확인됐고, 원본부터 이 판까지 재무 정정이 없다. 원천
        # rcept_dt 로 되돌아간 재제출본(J-41 회귀)도 증언이 없어 여기서 걸린다.
        "n_orig_filing_unwitnessed": _n(
            ctx, f'SELECT count(*) FROM "{v}" o '
                 f"WHERE o.available_date < {_RCEPT_STAGE_AVAIL_MIN} "
                 "AND NOT EXISTS (SELECT 1 FROM disclosure_version c "
                 "JOIN stg_disclosure g ON g.rcept_no = c.orig_rcept_no "
                 "WHERE c.rcept_no = o.rcept_no AND c.is_correction "
                 "AND g.available_date = o.available_date "
                 "AND c.date_check IN ('exact', 'off_1d') "
                 "AND NOT EXISTS (SELECT 1 FROM disclosure_version x "
                 "WHERE x.orig_rcept_no = c.orig_rcept_no AND x.is_correction "
                 "AND x.rcept_no <= c.rcept_no AND x.corr_has_fin_item IS NOT FALSE))"),
        # 파생 블록 — 구성 보고서가 넷이 아니면 q4 값이 남아 있으면 안 된다(부분합 금지)
        "n_q4_partial_sum": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE coalesce(q4_derived_n_rows, 0) < 4 AND ('
                 + " OR ".join(f'"{c}" IS NOT NULL' for c in Q4_COLUMNS) + ")"),
        "n_q4_on_non_annual": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                      "WHERE report_code <> '11011' AND "
                                      "(q4_derived_n_rows IS NOT NULL OR "
                                      "q4_derived_available_date IS NOT NULL)"),
        "n_cf_q_partial": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE report_code <> \'11013\' '
                 "AND coalesce(cf_q_n_rows, 0) < 2 AND ("
                 + " OR ".join(f'"{c}" IS NOT NULL' for c in CF_Q_COLUMNS) + ")"),
        # 파생 available_date 는 구성 행의 max 라 자기 행보다 앞설 수 없다(DESIGN §3)
        "n_derived_available_before_row": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE '
                 "q4_derived_available_date < available_date "
                 "OR cf_q_available_date < available_date"),
        # 매출 대체 근거는 표준 태그가 없을 때만 붙는다. 값이 없는 행은 **사유 어휘**여야
        # 한다(T-H) — 값이 있는데 사유가 붙거나 그 반대면 라벨이 거짓말을 한 것이다.
        "n_revenue_basis_conflict": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE (revenue IS NULL) <> '
                 f"(revenue_basis IN ({_vocab_sql(REVENUE_BASIS_NULL_REASONS)}))"),
        # 직전 회계연도 기준 — 어휘는 같고, 직전 해가 없으면 NULL 이다
        "n_revenue_basis_prev_outside_vocab": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE revenue_basis_prev IS NOT NULL '
                 f"AND revenue_basis_prev NOT IN ({_vocab_sql(REVENUE_BASIS_VOCAB)})"),
        # 채워진 값은 산출에 실제로 남은 직전 회계연도 행이 증언해야 한다
        "n_revenue_basis_prev_unwitnessed": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE o.revenue_basis_prev IS NOT NULL '
                 f'AND NOT EXISTS (SELECT 1 FROM "{v}" p WHERE {PREV_FY_PREDICATE} '
                 "AND p.revenue_basis = o.revenue_basis_prev)"),
        # 반대 방향 — 직전 회계연도 기준이 하나로 모이는데 비워 두면 조인이 끊긴 것이다
        "n_revenue_basis_prev_missing": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE o.revenue_basis_prev IS NULL AND ('
                 f'SELECT count(DISTINCT p.revenue_basis) FROM "{v}" p '
                 f"WHERE {PREV_FY_PREDICATE}) = 1"),
    }
    metrics: dict[str, object] = {
        # corp.fiscal_month 검산 — **기록형**이다. `corp.fiscal_month` 는 현재값 스냅샷이라
        # 결산월을 바꾼 법인의 과거 사업보고서는 정상적으로 어긋난다(DESIGN §11 "결산월 변경은
        # 문서 period_to 로 해소"). 폐기형으로 두면 그 법인 하나가 서버 빌드를 죽인다.
        # **기록형**. 원본 공시일을 승계한 정정본 수(09-30) — 정정일 편향을 얼마나 되돌렸나.
        # 축은 원천 `rcept_dt` 그대로다(계산 불변). e1.25.0 의 J-41 행(`available_date >
        # rcept_dt`)은 세지 않고, 승계 행도 정정의 원천 rcept_dt 가 원본 공개일보다 이른 드문
        # 재제출본이면 빠진다 — 판정 축(stage 공개일)의 승계 검사는 `n_orig_filing_unwitnessed` 다.
        "n_available_orig_filing": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                           "WHERE available_date < rcept_dt"),
        "n_fiscal_month_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" o JOIN corp c USING (corp_code) '
                 "WHERE o.report_code = '11011' AND c.fiscal_month IS NOT NULL "
                 "AND month(o.period_end) <> c.fiscal_month"),
        # 재수집 판본 접힌 수(기록형) — 0 이 아니면 stage 가 같은 자연키를 여러 번 실었다.
        "n_stg_fin_dup_natural_key": _n(
            ctx, "SELECT coalesce(sum(c - 1), 0) FROM (SELECT count(*) AS c FROM stg_fin "
                 "GROUP BY corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, "
                 "account_detail, ord HAVING c > 1)"),
        "n_disclosure_dup_rcept": _n(
            ctx, "SELECT coalesce(sum(c - 1), 0) FROM (SELECT count(*) AS c "
                 "FROM stg_disclosure GROUP BY rcept_no HAVING c > 1)"),
        # rcept_no 당 main 문서가 둘 이상이면 `.sql` 의 tie-break 가 실제로 작동한 것이다
        "n_doc_meta_multi_main": _n(
            ctx, "SELECT count(*) FROM (SELECT rcept_no FROM stg_doc_meta "
                 "WHERE member_role = 'main' GROUP BY 1 HAVING count(*) > 1)"),
        "n_by_report_code": _counts(ctx, "report_code"),
        "n_by_fs_div": _counts(ctx, "fs_div"),
        "n_by_period_end_basis": _counts(ctx, "period_end_basis"),
        # 최근 접수 구간의 추정 폴백(DEFECT-F01). 창은 이 표의 `max(available_date)` 에서
        # 유도한다 — 날짜를 상수로 박으면 매일 사람이 올려야 한다(e1.15.0 날짜 파생화 규약).
        "recent_from": inferred["recent_from"],
        "inferred_recent_days": inferred["days"],
        "n_recent_rows": inferred["n_recent"],
        "n_period_end_inferred_recent": inferred["n_inferred"],
        "ratio_period_end_inferred_recent": inferred["ratio"],
        "fin_std_inferred_recent_ratio_max": inferred["ratio_max"],
        "n_by_revenue_basis": _counts(ctx, "revenue_basis"),
        "n_by_revenue_basis_prev": _counts(ctx, "revenue_basis_prev"),
        # **기록형**. `ppe_parts` 행 수가 자산별 합 규칙이 실제로 복구한 크기다(DQ-8 GA2).
        "n_by_capex_basis": _counts(ctx, "capex_basis"),
        # **기록형**. 매출 기준이 해를 넘기며 바뀐 행·법인 수 — 「가짜 성장률」이 날 수 있는
        # 구간의 크기다. 여기서 버리지 않는다(그 판정은 팩터층 몫, WORKFLOW §0-2).
        "n_revenue_basis_changed": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE revenue_basis_prev IS NOT NULL '
                 "AND revenue_basis_prev <> revenue_basis"),
        "n_corp_revenue_basis_changed": _n(
            ctx, f'SELECT count(DISTINCT corp_code) FROM "{v}" '
                 "WHERE revenue_basis_prev IS NOT NULL "
                 "AND revenue_basis_prev <> revenue_basis"),
        "coverage_by_account": _coverage(ctx, ACCOUNTS),
        "coverage_q4_derived": _coverage(ctx, Q4_COLUMNS),
        "coverage_cf_q": _coverage(ctx, CF_Q_COLUMNS),
        "n_q4_complete": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE q4_derived_n_rows = 4'),
        "rcept_lag_p50": _f(ctx, "SELECT quantile_cont(date_diff('day', period_end, rcept_dt), "
                                 f'0.5) FROM "{v}"'),
        "rcept_lag_p99": _f(ctx, "SELECT quantile_cont(date_diff('day', period_end, rcept_dt), "
                                 f'0.99) FROM "{v}"'),
        "rcept_lag_max": _f(ctx, "SELECT max(date_diff('day', period_end, rcept_dt)) "
                                 f'FROM "{v}"'),
        # 모집단 밖으로 빠진 원천 그룹 — 표준계정 행이 하나도 없는 (corp, year, reprt, fs_div)
        "n_group_without_account_std": _n(
            ctx, "SELECT count(*) FROM (SELECT corp_code, bsns_year, reprt_code, fs_div "
                 "FROM stg_fin GROUP BY ALL HAVING NOT bool_or(account_std))"),
        "n_accounts_declared": len(ACCOUNTS),
        "vintage_kind_vocab": list(VINTAGE_KIND_VOCAB),   # 4A 는 첫 값 하나만 낸다
        "reject_by_reason": dict(ctx.reject_by_reason),
    }
    bad = {k: n for k, n in checks.items() if n}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult("EG3_fin_std", GateStatus.PASS,
                          "어휘·기간 판정·파생 불변식 성립", merged)
    return GateResult("EG3_fin_std", GateStatus.FAIL,
                      "; ".join(f"{k}={n}" for k, n in sorted(bad.items())), merged)


eg3_fin_std.gate_name = "EG3_fin_std"       # type: ignore[attr-defined]


# ── EG8_fin_std — 예상 대상 기준 그룹별 커버리지 (T-H 1단계, 2026-09-28) ───────
# 분모가 「만들어진 행」이면 대응 실패가 보이지 않는다. DQ-6(은행·보험 템플릿의 순이익이 전건
# NULL — 계정 코드가 달랐다)도 DQ-8(capex 311사 NULL)도 EG7 격리 비율 3% 를 넘지 않았다:
# 그 행들은 **격리된 것이 아니라 값만 빈 채 채택**됐기 때문이다. 그래서 분모를 그룹의
# **예상 대상**(채택 행 + 격리 행)으로 두고 그룹마다 유효 비율을 기록한 뒤 직전 판과 견준다.
#
# 그룹 축 = `fs_div` × 템플릿 × `report_code`. 템플릿은 `.sql` 의 `req` CTE 와 **같은 술어**를
# 게이트가 `stg_fin` 위에서 되풀이 계산한 것이다(게이트는 `.sql` 을 베끼지 않는다는 규약 —
# EG3 의 기간 판정 재계산과 같다). 요구 태그는 `fin_map.REVENUE_FALLBACK` 의 `require` 가
# 정본이고, 둘 다 가진 법인은 **banking** 으로 센다(템플릿 축은 매출 tier 보다 거친 축이다 —
# tier 는 보험이 먼저지만 여기서 갈라 보려는 것은 계정 서식이고 은행 서식이 더 흔하다).
COVERAGE_METRICS: tuple[str, ...] = ("revenue", "op_profit", "net_income", "total_asset",
                                     "cf_operating_ytd", "capex_ytd")
TEMPLATE_STANDARD = "standard"
_TEMPLATE_BY_BASIS: dict[str, str] = {"banking_gross": "banking",
                                      "insurance_gross": "insurance"}
_TEMPLATE_ORDER: tuple[str, ...] = ("banking", "insurance")      # 앞에서 먼저 판정한다


def _template_tags() -> tuple[tuple[str, str], ...]:
    by_template = {_TEMPLATE_BY_BASIS[str(fb["basis"])]: str(fb["require"][0])
                   for fb in REVENUE_FALLBACK}
    return tuple((t, by_template[t]) for t in _TEMPLATE_ORDER)


TEMPLATE_TAGS: tuple[tuple[str, str], ...] = _template_tags()
TEMPLATE_VOCAB: tuple[str, ...] = (*_TEMPLATE_ORDER, TEMPLATE_STANDARD)
# `account_id` → concept. `.sql` 의 `fin` CTE 와 같은 판이어야 게이트가 같은 그룹을 본다.
CONCEPT_PREFIX_PATTERN = "^(ifrs-full_|ifrs_|dart_)"
_GROUP_SEP = "|"
_REPORT_CODE_UNKNOWN = "unknown"           # 기간 미해소로 격리된 행의 자리
_COV_VIEW = "_eg8_fin_std_coverage"
GATE_NAME_EG8 = "EG8_fin_std"

# 판정 규칙 자체이지 조정 상수가 아니라 baseline 에 등재하지 않는다(`_INFERRED_RECENT_DAYS_
# DEFAULT` 와 같은 취급). 플랜 §11 T-H 가 고정한 숫자다.
_COVERAGE_MIN_EXPECTED = 20      # 기록·판정 대상 그룹의 예상 대상 하한 (작은 그룹은 잡음이다)
_COVERAGE_FAIL_DROP = 0.5        # 폐기형 낙폭
_COVERAGE_WARN_DROP = 0.05       # 기록형 경보 낙폭
_COVERAGE_WARN_MIN_ROWS = 5      # 경보 최소 영향 건수(추정)
_COVERAGE_EXAMPLES = 3           # 경보마다 붙는 대표 사례 수
_COVERAGE_DETAIL_MAX = 5         # FAIL detail 한 줄에 펴는 항목 수(전량은 metrics 에 있다)


def _coverage_source_sql(ctx: EquityGateContext) -> str:
    """채택 행 ∪ 격리 행에 그룹 축 라벨을 붙인 뷰의 SQL."""
    concept = f"regexp_replace(account_id, '{CONCEPT_PREFIX_PATTERN}', '')"
    flags = ",\n           ".join(
        f"coalesce(bool_or({concept} = '{tag}') FILTER (WHERE sj_div IN ('IS', 'CIS')), FALSE)"
        f" AS has_{name}" for name, tag in TEMPLATE_TAGS)
    case = " ".join(f"WHEN t.has_{name} THEN '{name}'" for name, _ in TEMPLATE_TAGS)
    cols = ", ".join(f'o."{m}"' for m in COVERAGE_METRICS)
    parts = [
        f"SELECT o.corp_code, o.bsns_year, o.period_end, o.fs_div,\n"
        f"       coalesce(o.report_code, '{_REPORT_CODE_UNKNOWN}') AS report_code,\n"
        f"       CASE {case} ELSE '{TEMPLATE_STANDARD}' END AS template,\n"
        f"       {adopted} AS adopted, {cols}\n"
        f'FROM "{view}" o\n'
        "LEFT JOIN tg t ON t.corp_code = o.corp_code AND t.bsns_year = o.bsns_year\n"
        " AND t.fs_div = o.fs_div AND t.rcept_no = o.rcept_no"
        for view, adopted in ((ctx.out_view, "TRUE"), (ctx.reject_view, "FALSE"))
        if view is not None]
    # `tg` 의 키는 `.sql` 의 `grp`(그룹당 최신 접수 max(rcept_no), G-21)과 같은 자리다 — 산출에는
    # `reprt_code` 가 없고(1Q·3Q 는 `doc_acode` 로 다시 갈린다) `rcept_no` 가 그 그룹의 이름표다.
    return ("WITH tg AS (\n"
            "    SELECT corp_code, bsns_year, fs_div, max(rcept_no) AS rcept_no,\n"
            f"           {flags}\n"
            "    FROM stg_fin\n"
            "    GROUP BY corp_code, bsns_year, reprt_code, fs_div\n"
            ")\n" + "\nUNION ALL\n".join(parts))


def _coverage_measure(ctx: EquityGateContext) -> tuple[dict[str, dict[str, object]],
                                                       dict[str, object]]:
    """그룹별 (예상 대상, 채택, 계정별 유효 건수·비율) 과 전체 합계."""
    sel = ", ".join(f'count("{m}") FILTER (WHERE adopted)' for m in COVERAGE_METRICS)
    rows = ctx.con.execute(
        f'SELECT fs_div, template, report_code, count(*), count(*) FILTER (WHERE adopted), {sel} '
        f'FROM "{_COV_VIEW}" GROUP BY 1, 2, 3 ORDER BY 1, 2, 3').fetchall()
    by_group: dict[str, dict[str, object]] = {}
    t_expected = t_adopted = 0
    t_valid = dict.fromkeys(COVERAGE_METRICS, 0)
    for r in rows:
        key = _GROUP_SEP.join(str(x) for x in r[:3])
        n_expected, n_adopted = int(str(r[3])), int(str(r[4]))
        valid = {m: int(str(r[5 + i])) for i, m in enumerate(COVERAGE_METRICS)}
        by_group[key] = {
            "n_expected": n_expected, "n_adopted": n_adopted,
            "metrics": {m: {"n_valid": n, "ratio": round(n / n_expected, 6) if n_expected else 0.0}
                        for m, n in valid.items()}}
        t_expected += n_expected
        t_adopted += n_adopted
        for m, n in valid.items():
            t_valid[m] += n
    total: dict[str, object] = {
        "n_expected": t_expected, "n_adopted": t_adopted,
        "metrics": {m: {"n_valid": n, "ratio": round(n / t_expected, 6) if t_expected else 0.0}
                    for m, n in t_valid.items()}}
    return by_group, total


def _coverage_examples(ctx: EquityGateContext, group: str, metric: str) -> list[str]:
    """그 그룹에서 값이 비어 있는 채택 행의 grain 키 — 경보를 사람이 바로 좇을 수 있게."""
    fs_div, template, report_code = group.split(_GROUP_SEP)
    rows = ctx.con.execute(
        "SELECT corp_code || '|' || CAST(period_end AS VARCHAR) || '|' || report_code "
        f'|| \'|\' || fs_div FROM "{_COV_VIEW}" WHERE adopted AND "{metric}" IS NULL '
        "AND fs_div = ? AND template = ? AND report_code = ? "
        f"ORDER BY 1 LIMIT {_COVERAGE_EXAMPLES}", [fs_div, template, report_code]).fetchall()
    return [str(r[0]) for r in rows]


def _previous_gate_metric(ctx: EquityGateContext, gate: str, key: str) -> object | None:
    """직전 커밋 빌드가 남긴 게이트 metric(`BuildRecord.gates`). 없으면 None."""
    prev = ctx.previous
    if prev is None:
        return None
    for g in prev.gates:
        if isinstance(g, dict) and g.get("name") == gate:
            metrics = g.get("metrics")
            if isinstance(metrics, dict):
                return metrics.get(key)
    return None


def _coverage_drops(now: dict[str, dict[str, object]], prev: dict[str, object],
                    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """직전 판 대비 낙폭 — (폐기, 경보). 직전 판에 없던 그룹·계정은 판정 축이 없다."""
    fails: list[dict[str, object]] = []
    warns: list[dict[str, object]] = []
    for group, cur in now.items():
        old = prev.get(group)
        if not isinstance(old, dict):
            continue
        old_metrics = old.get("metrics")
        if not isinstance(old_metrics, dict):
            continue
        n_expected = int(str(cur["n_expected"]))
        cur_metrics = dict(cur["metrics"])          # type: ignore[arg-type]
        for metric in COVERAGE_METRICS:
            before_cell, after_cell = old_metrics.get(metric), cur_metrics.get(metric)
            if not isinstance(before_cell, dict) or before_cell.get("ratio") is None:
                continue
            if not isinstance(after_cell, dict):      # 계정 목록이 판 사이에 바뀐 경우
                continue
            before = float(str(before_cell["ratio"]))
            after = float(str(after_cell["ratio"]))
            drop = before - after
            if drop < _COVERAGE_WARN_DROP:
                continue
            n_affected = int(round(drop * n_expected))
            entry: dict[str, object] = {
                "group": group, "metric": metric,
                "ratio_before": before, "ratio_after": after, "drop": round(drop, 6),
                "n_expected": n_expected,
                "n_expected_before": int(str(old.get("n_expected", 0))),
                "n_valid": int(str(after_cell["n_valid"])),
                "n_affected_est": n_affected, "examples": []}
            if drop >= _COVERAGE_FAIL_DROP or (after == 0.0 and before >= _COVERAGE_FAIL_DROP):
                fails.append(entry)
            elif n_affected >= _COVERAGE_WARN_MIN_ROWS:
                warns.append(entry)
    return fails, warns


def eg8_fin_std_coverage(ctx: EquityGateContext) -> GateResult:
    """EG8_fin_std (T-H 1단계) — 그룹별 **예상 대상 대비 유효 비율**과 직전 판 대비 낙폭.

    분모는 그룹의 예상 대상(채택 + 격리)이고 분자는 채택 행 중 값이 있는 것이다. 격리 행을
    분모에 넣는 이유는 「값이 나와야 했던 대상」이 격리로 사라지는 것도 커버리지 손실이기
    때문이다(DQ-5·DEFECT-F01 이 그 유형). 격리 뷰가 없는 ctx(`reject_view is None`)에서는
    분모가 채택 행뿐이다 — 빌드 경로는 격리 0 건이어도 뷰를 만들므로 실제로는 늘 둘 다다.

    판정은 **직전 판이 있을 때만** 한다: 예상 대상 `_COVERAGE_MIN_EXPECTED` 이상인 그룹에서
    유효 비율이 `_COVERAGE_FAIL_DROP` 이상 떨어지거나, 직전이 그 값 이상이었는데 0 이 되면
    FAIL 이다. 그보다 작은 낙폭은 영향 건수가 `_COVERAGE_WARN_MIN_ROWS` 이상일 때 기록형
    경보다. 직전 판이 없으면(첫 빌드·규칙 판본 교체 직후) 기록만 한다 — 절대 수준에 임계를
    걸면 계정 서식이 원래 다른 그룹(금융 템플릿의 `gross_profit` 등)이 매번 걸린다.

    기록은 `coverage_by_group` 으로 `BuildRecord.gates` 에 실리고 **다음 빌드가 그것을 읽는다**
    (`_previous_gate_metric`). JSON 이 커지지 않게 예상 대상 하한을 넘는 그룹만 남긴다.
    """
    ctx.con.execute(f'CREATE OR REPLACE TEMP VIEW "{_COV_VIEW}" AS {_coverage_source_sql(ctx)}')
    by_group, total = _coverage_measure(ctx)
    recorded = {k: v for k, v in by_group.items()
                if int(str(v["n_expected"])) >= _COVERAGE_MIN_EXPECTED}
    prev = _previous_gate_metric(ctx, GATE_NAME_EG8, "coverage_by_group")
    fails: list[dict[str, object]] = []
    warns: list[dict[str, object]] = []
    if isinstance(prev, dict):
        fails, warns = _coverage_drops(recorded, prev)
        for e in (*fails, *warns):
            e["examples"] = _coverage_examples(ctx, str(e["group"]), str(e["metric"]))
    metrics: dict[str, object] = {
        "coverage_by_group": recorded,
        "coverage_total": total,
        "n_groups": len(by_group),
        "n_groups_recorded": len(recorded),
        "coverage_min_expected": _COVERAGE_MIN_EXPECTED,
        "coverage_fail_drop": _COVERAGE_FAIL_DROP,
        "coverage_warn_drop": _COVERAGE_WARN_DROP,
        "coverage_warn_min_rows": _COVERAGE_WARN_MIN_ROWS,
        "coverage_compared": isinstance(prev, dict),
        "previous_build": None if ctx.previous is None else ctx.previous.build_id,
        "coverage_fail": fails, "coverage_warn": warns,
        "n_coverage_fail": len(fails), "n_coverage_warn": len(warns)}
    if fails:
        # detail 은 사람이 읽는 한 줄이라 앞의 몇 건만 편다 — 전량은 metrics 에 있다.
        head = "; ".join(f"{e['group']}.{e['metric']} {e['ratio_before']} → "
                         f"{e['ratio_after']} (n_expected={e['n_expected']} "
                         f"examples={e['examples']})" for e in fails[:_COVERAGE_DETAIL_MAX])
        more = len(fails) - _COVERAGE_DETAIL_MAX
        return GateResult(GATE_NAME_EG8, GateStatus.FAIL,
                          head + (f" … +{more}" if more > 0 else ""), metrics)
    detail = ("직전 판 대비 커버리지 낙폭 없음" if isinstance(prev, dict)
              else "직전 판 기록 없음 — 커버리지 기록만")
    if warns:
        detail += f" (warn {len(warns)})"
    return GateResult(GATE_NAME_EG8, GateStatus.PASS, detail, metrics)


eg8_fin_std_coverage.gate_name = GATE_NAME_EG8      # type: ignore[attr-defined]


def eg6_fin_std(ctx: EquityGateContext) -> GateResult:
    """EG6-P08 — `fin_std` ⋈ `disclosure_version` 무매칭률의 12월 결산 / 비12월 결산 비대칭.

    비12월 결산 법인의 무매칭률이 12월 결산보다 크게 높으면 `period_end` 후보 규칙이 결산월을
    잘못 잡은 것이다. GATES 초안은 `disclosure_version.bsns_year`·`reprt_code` 로 조인했지만
    v1.2 의 `disclosure_version` 은 그 축을 갖지 않는다(grain 이 `rcept_no`) — **접수번호로**
    직접 조인한다(GATES §9 기록).
    """
    v = ctx.out_view
    sql = (f'SELECT (c.fiscal_month = 12) AS dec_fy, count(*) AS n, '
           "count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM disclosure_version d "
           f'WHERE d.rcept_no = o.rcept_no)) AS n_nonmatch FROM "{v}" o '
           "JOIN corp c USING (corp_code) WHERE c.fiscal_month IS NOT NULL GROUP BY 1")
    rows = {bool(r[0]): (int(str(r[1])), int(str(r[2])))
            for r in ctx.con.execute(sql).fetchall()}
    dec_n, dec_bad = rows.get(True, (0, 0))
    non_n, non_bad = rows.get(False, (0, 0))
    dec_rate = (dec_bad / dec_n) if dec_n else None
    non_rate = (non_bad / non_n) if non_n else None
    gap = abs(non_rate - dec_rate) if (dec_rate is not None and non_rate is not None) else None
    metrics: dict[str, object] = {
        "n_dec_fy": dec_n, "n_dec_fy_nonmatch": dec_bad, "dec_nonmatch_rate": dec_rate,
        "n_nondec_fy": non_n, "n_nondec_fy_nonmatch": non_bad, "nondec_nonmatch_rate": non_rate,
        "nonmatch_rate_gap": gap}
    cap = require_const(ctx, "nonmatch_rate_gap_max", metrics)
    metrics["nonmatch_rate_gap_max"] = cap
    if gap is None:
        return GateResult("EG6_fin_std", GateStatus.SKIP, "no_coverage", metrics)
    ok = gap <= cap
    return GateResult("EG6_fin_std", GateStatus.PASS if ok else GateStatus.FAIL,
                      "결산월별 무매칭률 비대칭 이내" if ok else
                      f"nonmatch rate gap {gap:.6f} > max {cap} "
                      f"(non-dec {non_bad}/{non_n} vs dec {dec_bad}/{dec_n})", metrics)


eg6_fin_std.gate_name = "EG6_fin_std"       # type: ignore[attr-defined]


_VALUE_COLUMNS: dict[str, str] = ({m: "DECIMAL(38,4)" for m in ACCOUNTS}
                                  | {c: "DECIMAL(38,4)" for c in Q4_COLUMNS}
                                  | {c: "DECIMAL(38,4)" for c in CF_Q_COLUMNS})

# ── S19 필드 선언 (DESIGN §4-7 · FIELD_MAP §2 `financial.*` + §3 내부 스코프) ──
# 랙 **1 세션**: `available_date = rcept_dt` 는 접수 *날짜* 이지 시각이 아니고(원천 `stg_fin`·
# `stg_doc_meta`·`stg_disclosure` 가 전부 stage `lag_known=false`), 장 마감 뒤 접수된 보고서를
# 그날 지식으로 쓰면 look-ahead 다. 단위는 전부 원(KRW) — stage 가 이미 환산했다(§4-4).
# `frequency='report'` 이고 기간 어휘는 `report_code` 가 정한다(11011 12개월 · 11012~14 3개월 손익,
# 현금흐름은 전부 연초누계 — DEFECT-C02).
FIN_LAG_SESSIONS = 1
_FIN_DISCLOSURE = "정기보고서 접수일(rcept_dt) — 접수 시각 미제공이라 1 세션 뒤부터 쓴다"


def _fin(field_id: str, column: str, label: str, unit: str, value_type: str, evidence: str,
         *, scope: str = "field_map", requires_confirmation: bool = False) -> FieldProfile:
    """`fin_std` 계정 한 줄. 랙·PIT·빈도·커버 축은 이 테이블 전체가 같다."""
    return FieldProfile(
        field_id=field_id, columns=(column,), label=label, unit=unit, value_type=value_type,
        frequency="report", recommended_lag_sessions=FIN_LAG_SESSIONS, recommended_lag_days=1,
        point_in_time=True, requires_confirmation=requires_confirmation,
        disclosure_basis=_FIN_DISCLOSURE, evidence=evidence, coverage_axis="table_rows",
        scope=scope)


FIELDS_FIN: tuple[FieldProfile, ...] = (
    # FIELD_MAP §2 의 8 (레지스트리가 요구하는 것)
    _fin("financial.book_equity", "total_equity", "자본총계", "KRW", "amount",
         "fin_std.total_equity ← 표준계정 자본총계. 지배주주지분은 financial.equity_owners."),
    _fin("financial.net_income", "net_income", "당기순이익", "KRW", "amount",
         "분기는 3개월·사업보고서는 12개월 값이다(report_code 가 기간을 정한다). TTM 합성은 "
         "팩터층이고 fin_std 에 ttm 컬럼은 없다. 4분기 파생은 net_income_q4_derived."),
    _fin("financial.revenue", "revenue", "매출액", "KRW", "amount",
         "금융업은 ifrs-full_Revenue 가 성립하지 않아 행마다 financial.revenue_basis 가 산출 "
         "규칙을 남긴다. **소비 규약** — ① 횡단면은 revenue_basis='standard' 끼리만 견주고 "
         "은행·보험 합산분(banking_gross·insurance_gross)은 업종 안에서만 쓴다. "
         "② financial.revenue_basis_prev 와 다르면 성장률은 결측으로 버린다. 값은 그대로 "
         "내보내고 임계는 소비자가 정한다(WORKFLOW §0-2). 근거는 서버 현판 93,986행 실측 — "
         "기준 분포 standard 92,861행/2,951법인 · unavailable 718/173 · banking_gross 269/39 · "
         "insurance_gross 138/13 (T-H 이전 판의 실측 — 그 뒤로 unavailable 718 은 "
         "no_is_statement·unmapped 둘로 갈려 나간다). 오늘 상장 보통주 2,308 중 합산식으로 "
         "매출을 내는 27종목이 "
         "시총 330조(5.6%)이고(BLOCKED_FACTORS §5-1), standard 와 합산식을 섞어 쓴 법인이 20 · "
         "직전 회계연도 대비 기준이 바뀐 행이 367(136법인)이다. 그중 합산식이 끼어든 것은 "
         "18법인 46건 — 삼성카드 2024 standard 4.38조 → 2025 banking_gross 3.84조(가짜 −12%) · "
         "메리츠금융지주 46.6조 → 14.1조(−70%) · 한국금융지주 21.2조 → 5.9조(−72%) · "
         "한화생명 2023 standard 0 → 2024 insurance_gross 24.6조(0으로 나누기)."),
    _fin("financial.total_assets", "total_asset", "자산총계", "KRW", "amount",
         "fin_std.total_asset ← 표준계정 자산총계."),
    _fin("financial.operating_income", "op_profit", "영업이익", "KRW", "amount",
         "영업이익 아래로는 DART 와 컨센서스가 완전히 일치한다(FACTORS §8 실측)."),
    _fin("financial.operating_cash_flow", "cf_operating_ytd", "영업활동현금흐름(연초누계)",
         "KRW", "amount",
         "**연초누계 축**이다(DEFECT-C02: 현금흐름은 분기보고서도 누계). 분기 차분 축은 별개 "
         "필드 financial.cf_operating_q 로 갈랐다 — S19 가 두 축을 필드로 분리해 FIELD_MAP §2 의 "
         "'소비 측이 축을 골라야 한다' 조건을 닫았다."),
    _fin("financial.total_liabilities", "total_liab", "부채총계", "KRW", "amount",
         "fin_std.total_liab ← 표준계정 부채총계."),
    _fin("financial.gross_profit", "gross_profit", "매출총이익", "KRW", "amount",
         "fin_map.FIN_MAP['gross_profit'](concept GrossProfit·nm 매출총이익) 실재 — 24계정에 "
         "실었다(DESIGN §10 P30). 금융업은 매출총이익 개념이 없어 결측이 정상이다."),
    # 매출 기준 두 축 — 계정이 아니라 **라벨**이다. `financial.revenue` 의 소비 규약이 이 둘을
    # 읽으라고 말하므로 선언하지 않으면 규약이 지킬 수 없는 약속이 된다(값만 내고 근거를 안 내는
    # 상태). 어휘는 REVENUE_BASIS_VOCAB 로 닫혀 있고 임계·판정은 넣지 않는다.
    _fin("financial.revenue_basis", "revenue_basis", "매출 산출 기준", "", "category",
         "fin_std.revenue_basis ∈ {standard, banking_gross, insurance_gross, consensus, "
         "no_is_statement, unmapped}. 매출이 NULL 인 행은 반드시 뒤의 두 **사유** 중 "
         "하나이고(EG3 n_revenue_basis_conflict), 합산식은 require 태그가 있을 때만 "
         "발동한다(보험 → 은행 순). no_is_statement = 손익계산서(IS·CIS) 행이 아예 없다 · "
         "unmapped = 표는 있는데 대응표가 못 잡았다(값이 갈려 모호한 것 포함) — 고칠 대상이 "
         "원천인지 대응표인지를 가르는 축이다(2026-09-28 T-H, 그 전에는 둘 다 unavailable). "
         "PSR·영업이익률을 한 순위표에 섞을 수 있는지는 이 라벨을 보고 소비자가 정한다.",
         scope="internal"),
    _fin("financial.revenue_basis_prev", "revenue_basis_prev", "직전 회계연도 매출 산출 기준",
         "", "category",
         "같은 법인·같은 report_code·같은 fs_div 의 bsns_year − 1 행이 쓴 기준. 직전 해가 "
         "없거나 그 해 기준이 갈리면 NULL 이다. revenue_basis 와 다르면 매출 시계열이 끊긴 "
         "것이므로 성장률(G01)을 결측 처리하라 — equity 는 판정하지 않고 두 라벨만 싣는다. "
         "EG3 의 n_revenue_basis_changed·n_corp_revenue_basis_changed 가 끊긴 구간의 크기를 "
         "기록한다.", scope="internal"),
    # capex 기준(DQ-8, 2026-09-26) — 같은 사정의 라벨이다. 자산별 줄을 합한 뒤에는 값만 보고는
    # 출처를 알 수 없으므로 산출 규칙을 행에 싣는다. 어휘는 CAPEX_BASIS_VOCAB 로 닫혀 있다.
    _fin("financial.capex_basis", "capex_basis", "유형자산 취득 산출 기준", "", "category",
         "fin_std.capex_basis ∈ {standard, ppe_parts, ppe_incl_invprop, none_in_cf, "
         "no_cf_statement, unmapped}. standard = 집계 한 줄"
         "(ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities 또는 "
         "'유형자산의 취득') · ppe_parts = 자산별 줄(dart_PurchaseOf* 9종 + 표준계정코드 미사용 "
         "이름 48종)의 **크기 합**(부호가 섞여 sum_abs, 언제나 양수). 09-26 실측에서 같은 회사가 "
         "둘 다 적는 경우는 0건이라 합이 곧 집계이고 "
         "(FY2025 결측 311사 중 211사 복구), 사용권자산·무형자산은 집계 줄 정의(PPE) "
         "밖이라 제외한다. ppe_incl_invprop = 유형자산과 투자부동산을 한 줄로 적은 회사(7사) — "
         "정의가 다르므로 standard·ppe_parts 와 **섞어 횡단면을 세우면 안 된다**. "
         "none_in_cf = 현금흐름표는 있는데 유형자산 취득 줄이 없어(리스·무형만 있거나 집계 줄이 "
         "값 공란) 0 으로 읽은 것 — 값은 0 이고 결측이 아니다. 값이 NULL 인 행의 **사유**는 "
         "둘로 갈린다(2026-09-28 T-H, 그 전에는 둘 다 unavailable): no_cf_statement = 영업·투자 "
         "소계가 둘 다 없어 현금흐름표를 읽은 적이 없다(0 규칙의 분모 밖이라 「안 샀다」 와 "
         "섞으면 안 된다) · unmapped = 표는 있는데 대응표가 못 잡았다 — 고칠 대상이 원천인지 "
         "대응표인지를 가른다. FCF(Q05) 를 시계열로 쓸 때 기준이 바뀌는 구간은 이 라벨로 "
         "가른다.",
         scope="internal"),
    # equity 내부 스코프 — FIELD_MAP §3 이 "dataset_profile(S19)이 노출 여부를 정한다" 한 16계정
    _fin("financial.cost_of_sales", "cost_of_sales", "매출원가", "KRW", "amount",
         "equity 내부 스코프. 매출총이익 = 매출 − 매출원가 검산 축.", scope="internal"),
    _fin("financial.pretax_income", "pretax_income", "법인세비용차감전순이익", "KRW", "amount",
         "equity 내부 스코프.", scope="internal"),
    _fin("financial.net_income_owners", "net_income_owners", "지배주주순이익", "KRW", "amount",
         "equity 내부 스코프. 연결 기준 ROE 분자를 지배주주로 볼 때 쓴다.", scope="internal"),
    _fin("financial.eps_basic", "eps_basic", "기본주당순이익", "KRW", "amount",
         "equity 내부 스코프. **주식분할 미조정**(원장 그대로 — 삼성전자 2018 1분기 85,435 vs "
         "사업보고서 6,461)이라 시계열로 쓰려면 adj_factor 가 필요하다(FIELD_MAP §3).",
         scope="internal", requires_confirmation=True),
    _fin("financial.equity_owners", "equity_owners", "지배주주지분", "KRW", "amount",
         "equity 내부 스코프.", scope="internal"),
    _fin("financial.cash", "cash", "현금및현금성자산", "KRW", "amount",
         "equity 내부 스코프. FACTORS 정본 V07(순현금비율)·Q08(NOA)의 재료.", scope="internal"),
    _fin("financial.inventories", "inventories", "재고자산", "KRW", "amount",
         "equity 내부 스코프.", scope="internal"),
    _fin("financial.current_assets", "current_assets", "유동자산", "KRW", "amount",
         "equity 내부 스코프.", scope="internal"),
    _fin("financial.current_liabilities", "current_liab", "유동부채", "KRW", "amount",
         "equity 내부 스코프.", scope="internal"),
    _fin("financial.lease_liabilities", "lease_liab", "리스부채", "KRW", "amount",
         "equity 내부 스코프.", scope="internal"),
    _fin("financial.borrowings", "borrowings", "차입금", "KRW", "amount",
         "equity 내부 스코프. GAP-02 의 3계정 중 하나 — V05(EV/EBITDA)·V07·Q08 이 여기에 매달려 "
         "있고 실재 여부는 S19 커버율이 판정한다(GATES §6 EG10).", scope="internal"),
    _fin("financial.depreciation", "depreciation", "감가상각비", "KRW", "amount",
         "equity 내부 스코프. GAP-02 3계정 중 하나 — V05(EBITDA 분모)의 재료.", scope="internal"),
    _fin("financial.interest_expense", "interest_expense", "이자비용", "KRW", "amount",
         "equity 내부 스코프. GAP-02 3계정 중 하나 — Q07(이자보상배율)의 재료.", scope="internal"),
    _fin("financial.cf_operating_q", "cf_operating_q", "영업활동현금흐름(분기 차분)", "KRW",
         "amount",
         "equity 내부 스코프. 직전 보고서 누계와의 차라 직전 판본이 없으면 NULL 이고 "
         "cf_q_n_rows 가 구성 수를 남긴다(FIELD_MAP §2).",
         scope="internal", requires_confirmation=True),
    _fin("financial.cf_investing", "cf_investing_ytd", "투자활동현금흐름(연초누계)", "KRW",
         "amount", "equity 내부 스코프.", scope="internal"),
    _fin("financial.cf_financing", "cf_financing_ytd", "재무활동현금흐름(연초누계)", "KRW",
         "amount", "equity 내부 스코프.", scope="internal"),
    _fin("financial.capex", "capex_ytd", "유형자산 취득(연초누계)", "KRW", "amount",
         "equity 내부 스코프. FACTORS 정본 Q05(FCF 수익률)의 재료.", scope="internal"),
)


FIN_STD = register(EquityTable(
    name="fin_std",
    grain=("corp_code", "period_end", "report_code", "fs_div", "vintage_kind"),
    columns={"corp_code": "VARCHAR", "period_end": "DATE", "report_code": "VARCHAR",
             "fs_div": "VARCHAR", "vintage_kind": "VARCHAR",
             "bsns_year": "VARCHAR", "rcept_no": "VARCHAR", "rcept_dt": "DATE",
             "period_start": "DATE", "period_end_basis": "VARCHAR", "currency": "VARCHAR",
             "revenue_basis": "VARCHAR", "revenue_basis_prev": "VARCHAR",
             "capex_basis": "VARCHAR",
             "restated_unknown": "BOOLEAN",
             **{m: _VALUE_COLUMNS[m] for m in ACCOUNTS},
             **{c: _VALUE_COLUMNS[c] for c in Q4_COLUMNS},
             "q4_derived_n_rows": "BIGINT", "q4_derived_available_date": "DATE",
             **{c: _VALUE_COLUMNS[c] for c in CF_Q_COLUMNS},
             "cf_q_n_rows": "BIGINT", "cf_q_available_date": "DATE",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_fin", "stg_doc_meta", "stg_disclosure", "disclosure_version", "corp"),
    partition_class="receipt_axis",
    partition_key_expr="CAST(substr(rcept_no, 1, 4) AS INTEGER)",
    available_rule=("stg_disclosure.available_date — 판 접수번호의 stage 공개일(derived, 원천"
                    " rcept_dt 와 접수번호 날짜 중 늦은 쪽). 재무를 안 건드린 정정본은 원본 공개일"
                    "(available_date < rcept_dt 로 드러난다). 파생 컬럼은 구성 행 max 를 동반"),
    # GATES §3-⑫ — 표준계정 행이 하나라도 있는 (corp, bsns_year, reprt_code, fs_div) 그룹 수
    eg1_lhs_sql='SELECT count(*) FROM "out_pq"',
    eg1_rhs_sql=("SELECT count(*) FROM (SELECT DISTINCT corp_code, bsns_year, reprt_code, fs_div "
                 "FROM stg_fin WHERE account_std)"),
    sql_path=SQL_PATH,
    input_columns={
        # `account_detail`·`ord`·`observed_date` 는 재수집 판본을 접는 축이다(자연키 8열 +
        # first_write_wins) — 산출 컬럼이 아니다. `account_nm_norm` 은 stage 가 적는 공백 제거
        # 계정명(T-E)이고 `nm_exact` 우선순위가 원문 `account_nm` 과의 동일성으로 갈린다.
        "stg_fin": ("corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_id",
                    "account_detail", "ord", "account_nm", "account_nm_norm", "thstrm_amount",
                    "account_std", "is_krw", "currency", "rcept_no", "observed_date"),
        "stg_doc_meta": ("rcept_no", "member_role", "doc_acode", "period_from", "period_to"),
        # `available_date` = stage 보정 공개일(J-41) — 공개일 축. `rcept_dt` 는 기간 판정 축.
        "stg_disclosure": ("rcept_no", "rcept_dt", "available_date", "observed_date"),
        # 링크 판본을 같이 고정한다(EG6_fin_std 무매칭 비대칭이 읽는다). `stg_rcept_dt_map` 은
        # 실재하지 않아 접수일 원천은 `stg_disclosure.rcept_dt` 다(GATES §9).
        # 정정본의 원본 공시일 승계(09-30)는 정정 여부·원본 링크·재무표 정정 여부·첫 장 날짜 확인을 읽는다.
        "disclosure_version": ("rcept_no", "corp_code", "kind", "period_label", "rcept_dt",
                               "is_correction", "orig_rcept_no", "corr_has_fin_item",
                               "date_check"),
        "corp": ("corp_code", "fiscal_month")},
    available_basis=("derived",),
    content_date_column="period_end",
    reject_reasons=REJECT_REASONS,
    consts=("period_end_lag_max_days", "rcept_lag_p99_days", "quarter_months",
            "half_months", "three_quarter_months"),
    extra_gates=(eg3_fin_std, eg8_fin_std_coverage, eg6_fin_std),
    field_profiles=FIELDS_FIN,
))

TABLES: tuple[EquityTable, ...] = (FIN_STD,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s12.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["ACCOUNTS", "BASELINE_SEED", "CAPEX_BASIS_NULL_REASONS", "CAPEX_BASIS_VOCAB",
           "CF_ACCOUNTS", "CF_PRIOR_REPORT",
           "CF_Q_COLUMNS", "CONCEPT_PREFIX_PATTERN", "COVERAGE_METRICS", "GATE_NAME_EG8",
           "TEMPLATE_STANDARD", "TEMPLATE_TAGS", "TEMPLATE_VOCAB",
           "TIER_CAPEX_COMBINED", "TIER_CAPEX_PARTS", "TIER_CONCEPT", "TIER_CONCEPT_ALT",
           "TIER_NM",
           "TIER_REVENUE_FALLBACK",
           "EXTRA_ACCOUNTS", "FIN_STD", "FLOW_ACCOUNTS", "PERIOD_END_BASIS_VOCAB",
           "PREV_FY_PREDICATE",
           "Q4_COLUMNS", "REJECT_REASONS", "REPORT_CODE_VOCAB", "REVENUE_BASIS_NULL_REASONS",
           "REVENUE_BASIS_VOCAB",
           "STOCK_ACCOUNTS", "TABLES", "VINTAGE_KIND", "VINTAGE_KIND_VOCAB", "acct_rows",
           "acct_values_sql", "norm_nm"]
