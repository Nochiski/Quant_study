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
가른다(3개월 → 11013 · 9개월 → 11014). 문서가 없으면 후보 규칙(`corp.fiscal_month` 말일을
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
`rcept_dt − period_end` 가 1년을 넘는 행이 실제로 있다(절단본 최대 445일).

격리 4종(EG7): `non_krw`(`is_krw` 아님 — 원 단위 축 밖) · `period_unresolved` ·
`rcept_lag_out_of_range` · `duplicate_vintage`(서로 다른 (bsns_year, reprt_code) 가 같은 grain 으로
접힘 — 어느 쪽이 옳은지 규칙이 못 고르므로 둘 다 격리한다).
"""
from __future__ import annotations

from pathlib import Path

from fin_map import FIN_MAP, REVENUE_FALLBACK
from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext, require_const
from .model import EquityTable, register

SQL_DIR = Path(__file__).parent / "sql"
SQL_PATH = SQL_DIR / "fin_std.sql"

VINTAGE_KIND = "api_restated"
VINTAGE_KIND_VOCAB: tuple[str, ...] = ("api_restated", "original", "corrected")   # 4C 확장분 포함
PERIOD_END_BASIS_VOCAB: tuple[str, ...] = ("document", "inferred")
REVENUE_BASIS_VOCAB: tuple[str, ...] = ("standard", "banking_gross", "insurance_gross",
                                        "consensus", "unavailable")
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


def acct_rows() -> tuple[tuple[object, ...], ...]:
    """`_acct` 행 — (metric, tier, kind, tokens, sjs, agg, require, basis, family).

    tier 는 fin_map 의 탐색 순서다: `a_concept` → `b_concept_alt`(1차 태그가 한 건도 없을 때만)
    → `c_nm`(표준태그 미사용 행 폴백). 매출은 `d_insurance_gross` · `e_banking_gross` 가 더
    붙고 `require` 태그가 있어야 발동한다(fin_map.REVENUE_FALLBACK, 보험이 먼저).
    """
    rows: list[tuple[object, ...]] = []
    for metric in ACCOUNTS:
        spec = _spec(metric)
        sjs = list(spec["sj"])                      # type: ignore[arg-type]
        agg = str(spec["agg"])
        fam = _FAMILY[metric]
        basis = "standard" if metric == "revenue" else None
        for tier, key, kind in ((TIER_CONCEPT, "concept", "concept"),
                                (TIER_CONCEPT_ALT, "concept_alt", "concept"),
                                (TIER_NM, "nm", "nm")):
            tokens = list(spec.get(key) or [])      # type: ignore[arg-type]
            if tokens:
                rows.append((metric, tier, kind, tokens, sjs, agg, None, basis, fam))
    for i, fb in enumerate(REVENUE_FALLBACK):
        rows.append(("revenue", TIER_REVENUE_FALLBACK[i], "concept", list(fb["concept"]),
                     list(fb["sj"]), "sum", str(fb["require"][0]), str(fb["basis"]), "flow"))
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


def eg3_fin_std(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 어휘 폐쇄 · 기간 판정 재계산 · 파생 부분합 금지 · 계정 커버율(기록형).

    기간 판정 재계산은 `.sql` 의 tie-break 를 베끼지 않는다 — "그 접수의 main 문서 중 **어느 한
    행**이 이 `period_end`(그리고 그 행의 개월 수가 이 `report_code`)를 준다"는 존재 명제로 본다.
    `stg_doc_meta` 가 rcept_no 당 main 을 둘 이상 갖는지는 `n_doc_meta_multi_main` 이 기록한다.
    """
    v = ctx.out_view
    q1 = int(str(ctx.baseline.require(ctx.rule.name, "quarter_months")))
    checks = {
        "n_vintage_outside_vocab": _outside_vocab(ctx, "vintage_kind", (VINTAGE_KIND,)),
        "n_report_code_outside_vocab": _outside_vocab(ctx, "report_code", REPORT_CODE_VOCAB),
        "n_fs_div_outside_vocab": _outside_vocab(ctx, "fs_div", FS_DIV_VOCAB),
        "n_period_basis_outside_vocab": _outside_vocab(ctx, "period_end_basis",
                                                       PERIOD_END_BASIS_VOCAB),
        "n_revenue_basis_outside_vocab": _outside_vocab(ctx, "revenue_basis",
                                                        REVENUE_BASIS_VOCAB),
        "n_currency_not_krw": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE currency <> \'KRW\''),
        "n_restated_unknown_false": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                            "WHERE restated_unknown IS DISTINCT FROM TRUE"),
        # `period_end` = stg_doc_meta.period_to 정본 (document 근거 행)
        "n_period_end_not_document": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE o.period_end_basis = \'document\' '
                 "AND NOT EXISTS (SELECT 1 FROM stg_doc_meta m WHERE m.rcept_no = o.rcept_no "
                 "AND m.member_role = 'main' AND m.period_to = o.period_end "
                 "AND m.period_from IS NOT DISTINCT FROM o.period_start)"),
        # 1Q/3Q 판정 — doc_acode 11013 은 개월 수로만 갈린다
        "n_report_code_month_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE o.period_end_basis = \'document\' '
                 "AND NOT EXISTS (SELECT 1 FROM stg_doc_meta m WHERE m.rcept_no = o.rcept_no "
                 "AND m.member_role = 'main' AND m.period_to = o.period_end "
                 "AND m.period_from = o.period_start AND o.report_code = "
                 "(CASE WHEN m.doc_acode <> '11013' THEN m.doc_acode "
                 f"WHEN date_diff('month', m.period_from, m.period_to) + 1 <= {q1} "
                 "THEN '11013' ELSE '11014' END))"),
        "n_period_end_after_rcept": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                            "WHERE period_end > rcept_dt"),
        "n_available_ne_rcept_dt": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                           "WHERE available_date IS DISTINCT FROM rcept_dt"),
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
        # 매출 대체 근거는 표준 태그가 없을 때만 붙는다
        "n_revenue_basis_conflict": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE (revenue IS NULL) <> '
                 "(revenue_basis = 'unavailable')"),
    }
    metrics: dict[str, object] = {
        # corp.fiscal_month 검산 — **기록형**이다. `corp.fiscal_month` 는 현재값 스냅샷이라
        # 결산월을 바꾼 법인의 과거 사업보고서는 정상적으로 어긋난다(DESIGN §11 "결산월 변경은
        # 문서 period_to 로 해소"). 폐기형으로 두면 그 법인 하나가 서버 빌드를 죽인다.
        "n_fiscal_month_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" o JOIN corp c USING (corp_code) '
                 "WHERE o.report_code = '11011' AND c.fiscal_month IS NOT NULL "
                 "AND month(o.period_end) <> c.fiscal_month"),
        # rcept_no 당 main 문서가 둘 이상이면 `.sql` 의 tie-break 가 실제로 작동한 것이다
        "n_doc_meta_multi_main": _n(
            ctx, "SELECT count(*) FROM (SELECT rcept_no FROM stg_doc_meta "
                 "WHERE member_role = 'main' GROUP BY 1 HAVING count(*) > 1)"),
        "n_by_report_code": _counts(ctx, "report_code"),
        "n_by_fs_div": _counts(ctx, "fs_div"),
        "n_by_period_end_basis": _counts(ctx, "period_end_basis"),
        "n_by_revenue_basis": _counts(ctx, "revenue_basis"),
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

FIN_STD = register(EquityTable(
    name="fin_std",
    grain=("corp_code", "period_end", "report_code", "fs_div", "vintage_kind"),
    columns={"corp_code": "VARCHAR", "period_end": "DATE", "report_code": "VARCHAR",
             "fs_div": "VARCHAR", "vintage_kind": "VARCHAR",
             "bsns_year": "VARCHAR", "rcept_no": "VARCHAR", "rcept_dt": "DATE",
             "period_start": "DATE", "period_end_basis": "VARCHAR", "currency": "VARCHAR",
             "revenue_basis": "VARCHAR", "restated_unknown": "BOOLEAN",
             **{m: _VALUE_COLUMNS[m] for m in ACCOUNTS},
             **{c: _VALUE_COLUMNS[c] for c in Q4_COLUMNS},
             "q4_derived_n_rows": "BIGINT", "q4_derived_available_date": "DATE",
             **{c: _VALUE_COLUMNS[c] for c in CF_Q_COLUMNS},
             "cf_q_n_rows": "BIGINT", "cf_q_available_date": "DATE",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_fin", "stg_doc_meta", "stg_disclosure", "disclosure_version", "corp"),
    partition_class="receipt_axis",
    partition_key_expr="CAST(substr(rcept_no, 1, 4) AS INTEGER)",
    available_rule="column:rcept_dt — DART 접수일(derived). 파생 컬럼은 구성 행 max 를 동반",
    # GATES §3-⑫ — 표준계정 행이 하나라도 있는 (corp, bsns_year, reprt_code, fs_div) 그룹 수
    eg1_lhs_sql='SELECT count(*) FROM "out_pq"',
    eg1_rhs_sql=("SELECT count(*) FROM (SELECT DISTINCT corp_code, bsns_year, reprt_code, fs_div "
                 "FROM stg_fin WHERE account_std)"),
    sql_path=SQL_PATH,
    input_columns={
        "stg_fin": ("corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_id",
                    "account_nm", "thstrm_amount", "account_std", "is_krw", "currency",
                    "rcept_no"),
        "stg_doc_meta": ("rcept_no", "member_role", "doc_acode", "period_from", "period_to"),
        "stg_disclosure": ("rcept_no", "rcept_dt"),
        # 링크 판본을 같이 고정한다(EG6_fin_std 무매칭 비대칭이 읽는다). `stg_rcept_dt_map` 은
        # 실재하지 않아 접수일 원천은 `stg_disclosure.rcept_dt` 다(GATES §9).
        "disclosure_version": ("rcept_no", "corp_code", "kind", "period_label"),
        "corp": ("corp_code", "fiscal_month")},
    available_basis=("derived",),
    content_date_column="period_end",
    reject_reasons=REJECT_REASONS,
    consts=("period_end_lag_max_days", "rcept_lag_p99_days", "quarter_months",
            "half_months", "three_quarter_months"),
    extra_gates=(eg3_fin_std, eg6_fin_std),
))

TABLES: tuple[EquityTable, ...] = (FIN_STD,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s12.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["ACCOUNTS", "BASELINE_SEED", "CF_ACCOUNTS", "CF_PRIOR_REPORT", "CF_Q_COLUMNS",
           "TIER_CONCEPT", "TIER_CONCEPT_ALT", "TIER_NM", "TIER_REVENUE_FALLBACK",
           "EXTRA_ACCOUNTS", "FIN_STD", "FLOW_ACCOUNTS", "PERIOD_END_BASIS_VOCAB",
           "Q4_COLUMNS", "REJECT_REASONS", "REPORT_CODE_VOCAB", "REVENUE_BASIS_VOCAB",
           "STOCK_ACCOUNTS", "TABLES", "VINTAGE_KIND", "VINTAGE_KIND_VOCAB", "acct_rows",
           "acct_values_sql"]
