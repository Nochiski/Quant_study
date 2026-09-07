"""S11 공시 판본 슬라이스 — `disclosure_version`.

DESIGN v1.2 §4-4 · GATES v1.0 §3-⑬ · WORKFLOW §3-4.

grain `rcept_no` · receipt_axis · 입력은 stage 4테이블뿐이라 1단계 산출을 읽지 않는다(4A 는
`stg_doc_meta.period_to`·`stg_doc_correction` 만으로 선다). 산출식은 `sql/disclosure_version.sql`
하나이고 상수는 `baseline_seed_s11.json`.

**정정 링크 흡수**: v1.1 의 별도 테이블 `correction_link` 는 v1.2 에서 이 테이블로 접혔다
(DESIGN §12 "링크 흡수"). 그래서 DOC §8.1 의 E-G6a(링크 성립률)·E-G6b(날짜 일치율 기록형)·
E-G7(`rm` 플래그 도달률)은 `correction_link` 가 아니라 여기서 판정하고, baseline 키도
`disclosure_version.*` 로 옮겼다(GATES §9 기록).

**모집단은 정기보고서 접수 전건**이다 — 정정본도 원본과 같은 자격으로 한 행씩 남는다. 기간 라벨
`(YYYY.MM)` 이 없는 행(서버 586)도 격리하지 않는다(DESIGN §4-4 확정): 격리하면 그 접수가
모집단에서 사라져 뷰가 "정정 없음"으로 읽는 생존편향을 게이트가 만든다(GATES §5-C6 과 같은 함정,
초안 EG7-P05 `no_label` 격리를 폐기한 근거).

**사다리 5단**은 임계가 아니라 **재계산 일치**로 판정한다 — 절단본과 서버의 절대 건수가 다르므로
등식으로 못 박을 수 있는 것은 "산출에서 센 값 = stage 에서 독립으로 센 값"뿐이다. 서버 기준값
(20,579 / 24,285 / 17,600 / 15,225)은 `baseline_seed_s11.json` 의 `_measured` 에 참조로 싣는다.
임계형은 E-G6a·E-G7 둘뿐이고 baseline 미등재면 `skip(no_baseline)` + 기록형이다(WORKFLOW §3-4).

`has_correction` 같은 정적 판정 컬럼은 두지 않는다(DEFECT-E01) — 원본 측 팩트
`first_correction_dt`·`n_corrections` 만 싣고 기준일 판정은 뷰가 한다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext, SkipGate, require_const
from .model import EquityTable, FieldProfile, register

SQL_DIR = Path(__file__).parent / "sql"
SQL_PATH = SQL_DIR / "disclosure_version.sql"

# ── 폐쇄 어휘 (EG3_disclosure_version) ───────────────────────────────────────
KIND_VOCAB: tuple[str, ...] = ("annual", "half", "quarter")
CANDIDATE_STATUS_VOCAB: tuple[str, ...] = ("unique", "none", "multi_resolved",
                                           "multi_unresolved", "n/a")
DATE_CHECK_VOCAB: tuple[str, ...] = ("exact", "off_1d", "off_2_7d", "mismatch", "unparsed",
                                     "no_page", "no_zip", "n/a")
GROUP_KEY_BASIS_VOCAB: tuple[str, ...] = ("label", "no_label")
LINK_BASIS_VOCAB: tuple[str, ...] = ("parsed", "n/a")
REJECT_REASONS: tuple[str, ...] = ("rcept_dt_missing",)
# E-G6b 분모에서 빠지는 값 — 원본 접수일과 대조할 재료 자체가 없는 행 (GATES EG6-P06)
DATE_CHECK_UNMEASURED: tuple[str, ...] = ("unparsed", "no_page", "no_zip", "n/a")
# 링크가 성립한 것으로 세는 후보 판정 (GATES EG6-P05 = DOC §8.1 E-G6a)
LINKED_STATUS: tuple[str, ...] = ("unique", "multi_resolved")

# ── 모집단 어휘 — `.sql` 의 LIKE 리터럴이 이것을 그대로 옮긴다(tests 가 대조) ──────────
# `report_nm` 에서 `[…]` 접두를 벗긴 이름이 이 중 하나로 **시작**하면 정기보고서다.
PERIODIC_PREFIXES: tuple[str, ...] = ("사업보고서", "반기보고서", "분기보고서")
# 위로 시작하더라도 이 조각을 포함하면 정기보고서가 아니다(DESIGN §4-4 제외 4종).
# 절단본 실측: '사업보고서제출기한연장신고서 (2022.12)' 1건이 연장신고로 빠진다.
EXCLUDED_TOKENS: tuple[str, ...] = ("연장신고", "유동화전문회사", "회계법인", "해외증권거래소")
# 정정본 접두. `[첨부추가]` 는 원본 라벨이라 여기 없다(원본 후보 자격을 유지한다).
CORRECTION_PREFIXES: tuple[str, ...] = ("기재정정", "첨부정정")
# `corr_has_fin_item`(기록형) 의 키워드. `.sql` 의 LIKE 리터럴이 이것을 그대로 옮긴다.
# ★ 서버 재측정: DESIGN §4-4 의 참조값 2,238/17,600 은 항목 목록이 명시되지 않은 채 인용된
#   수치라 이 키워드 집합으로 재현되지 않을 수 있다(절단본 39/84).
FIN_ITEM_KEYWORDS: tuple[str, ...] = ("재무제표", "재무상태표", "손익계산서", "현금흐름표",
                                      "자본변동표", "요약재무")

# ── 모집단 SQL 재사용 (sql/disclosure_version.sql 의 periodic CTE 까지) ────────
_POPULATION_MARKER = "-- ==== eg1:"


def _population_prefix() -> str:
    """`.sql` 첫 줄부터 periodic CTE 를 닫는 괄호까지 — 뒤에 `SELECT … FROM periodic` 을 붙인다."""
    text = SQL_PATH.read_text(encoding="utf-8")
    head, sep, _ = text.partition(_POPULATION_MARKER)
    if not sep:
        raise ValueError(f"population marker not found in {SQL_PATH}: expected a line starting "
                         f"with {_POPULATION_MARKER!r} right after the periodic CTE")
    return head


def population_sql(select: str) -> str:
    """periodic 위의 단일 SELECT. 예: population_sql(\"count(*) FROM periodic\")."""
    return f"{_population_prefix()}\nSELECT {select}"


# 우변도 접수번호 축으로 센다 — 모집단 CTE 가 이미 dedup 하므로 count(*) 와 같지만,
# 좌변(count DISTINCT rcept_no)과 같은 축이어야 dedup 이 깨질 때 EG1 이 그것을 잡는다.
EG1_RHS_SQL = population_sql("count(DISTINCT rcept_no) FROM periodic")
EG1_LHS_SQL = 'SELECT count(DISTINCT rcept_no) FROM "out_pq"'

# ── 사다리 5단 — stage 뷰만 읽는 **독립** 산출 (모집단 CTE 를 재사용하지 않는다) ────────
# L1 정기보고서 접수 전건 → L2 정정이 붙은 접수(`rm` 플래그) → L3 정정 접수(문서 자신이 정정)
# → L4 그중 ZIP 정정신고 페이지 있음 → L5 그중 filed_date 파싱 성공.
# L2 와 L3 은 축이 다르다(원본 측 vs 정정 측)이라 대소 관계가 고정되지 않는다 — 서버 실측
# 20,579(L2) < 24,285(L3).
_NM_CLEAN = "regexp_replace(d.report_nm, '^\\[[^\\]]*\\]', '')"
_PERIODIC_PRED = " OR ".join(f"{_NM_CLEAN} LIKE '{p}%'" for p in PERIODIC_PREFIXES)
_EXCLUDED_PRED = " AND ".join(f"{_NM_CLEAN} NOT LIKE '%{t}%'" for t in EXCLUDED_TOKENS)
_PERIODIC_FROM = (f"FROM stg_disclosure d WHERE ({_PERIODIC_PRED}) AND {_EXCLUDED_PRED}")

# `stg_disclosure` 는 접수번호가 중복될 수 있으므로(append_only) 전부 `count(DISTINCT rcept_no)`
# 로 센다 — 행으로 세면 산출(접수번호 축)과 축이 달라 사다리가 항상 어긋난다.
LADDER: tuple[tuple[str, str], ...] = (
    ("n_periodic", f"SELECT count(DISTINCT d.rcept_no) {_PERIODIC_FROM}"),
    ("n_rm_corrected_later",
     f"SELECT count(DISTINCT d.rcept_no) {_PERIODIC_FROM} AND d.rm_corrected_later"),
    ("n_is_correction", f"SELECT count(DISTINCT d.rcept_no) {_PERIODIC_FROM} AND d.is_correction"),
    ("n_correction_zip",
     f"SELECT count(DISTINCT d.rcept_no) {_PERIODIC_FROM} AND d.is_correction AND EXISTS "
     "(SELECT 1 FROM stg_doc_correction c WHERE c.rcept_no = d.rcept_no)"),
    ("n_correction_filed_parsed",
     f"SELECT count(DISTINCT d.rcept_no) {_PERIODIC_FROM} AND d.is_correction AND EXISTS "
     "(SELECT 1 FROM stg_doc_correction c WHERE c.rcept_no = d.rcept_no "
     "AND c.filed_date_status = 'parsed')"),
)
# 모집단에서 접힌 재수집 판본 수(기록형) — 0 이 아니면 stage 가 같은 접수를 여러 번 실은 것이다.
POPULATION_DUP_SQL = (f"SELECT count(*) - count(DISTINCT d.rcept_no) {_PERIODIC_FROM}")
# 산출에서 같은 다섯 단을 다시 센다. L1·L3 은 산출 컬럼만으로, L2·L4·L5 는 stage 축과 조인해서
# 센다 — 뒤 세 단이 검사하는 축은 "산출의 정기보고서·정정 집합이 stage 의 그것과 같은가"다
# (`stg_doc_correction` 조인 자체는 양변이 공유한다). 정정 문서의 **재료 유무**가 `date_check`
# 의 no_zip·no_page·unparsed 갈래와 맞는지는 EG3 의 `n_date_check_material_mismatch` 가 따로 본다.
LADDER_OUT: tuple[tuple[str, str], ...] = (
    ("n_periodic", 'SELECT count(*) FROM "{v}"'),
    ("n_rm_corrected_later",
     'SELECT count(*) FROM "{v}" o WHERE EXISTS (SELECT 1 FROM stg_disclosure d '
     "WHERE d.rcept_no = o.rcept_no AND d.rm_corrected_later)"),
    ("n_is_correction", 'SELECT count(*) FROM "{v}" WHERE is_correction'),
    ("n_correction_zip",
     'SELECT count(*) FROM "{v}" o WHERE o.is_correction AND EXISTS '
     "(SELECT 1 FROM stg_doc_correction c WHERE c.rcept_no = o.rcept_no)"),
    ("n_correction_filed_parsed",
     'SELECT count(*) FROM "{v}" o WHERE o.is_correction AND EXISTS '
     "(SELECT 1 FROM stg_doc_correction c WHERE c.rcept_no = o.rcept_no "
     "AND c.filed_date_status = 'parsed')"),
)


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


def eg3_disclosure_version(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 어휘 폐쇄 · 링크 불변식 · 원본 측 집계 재계산 · 사다리 5단 재계산.

    사다리는 임계가 아니라 **두 축의 일치**로 판정한다: stage 뷰만으로 센 다섯 단과 산출에서
    센 다섯 단이 같아야 한다. 절대 건수(서버 20,579 / 24,285 / 17,600 / 15,225)는 baseline
    `_measured` 참조값이고 게이트가 비교하지 않는다 — 접수는 매일 늘어난다.
    """
    v = ctx.out_view
    ladder_stage = {name: _n(ctx, sql) for name, sql in LADDER}
    ladder_out = {name: _n(ctx, sql.format(v=v)) for name, sql in LADDER_OUT}
    # 격리 행(rcept_dt 결측)은 산출에서 빠지므로 L1 만 격리 수를 되돌려 비교한다.
    ladder_out_l1 = ladder_out["n_periodic"] + ctx.n_reject
    ladder_mismatch = {k: (ladder_stage[k], ladder_out[k])
                       for k in ladder_stage
                       if ladder_stage[k] != (ladder_out_l1 if k == "n_periodic"
                                              else ladder_out[k])}
    checks = {
        "n_kind_outside_vocab": _outside_vocab(ctx, "kind", KIND_VOCAB),
        "n_candidate_status_outside_vocab": _outside_vocab(ctx, "candidate_status",
                                                           CANDIDATE_STATUS_VOCAB),
        "n_date_check_outside_vocab": _outside_vocab(ctx, "date_check", DATE_CHECK_VOCAB),
        "n_group_key_basis_outside_vocab": _outside_vocab(ctx, "group_key_basis",
                                                          GROUP_KEY_BASIS_VOCAB),
        "n_link_basis_outside_vocab": _outside_vocab(ctx, "link_basis", LINK_BASIS_VOCAB),
        "n_ladder_mismatch": len(ladder_mismatch),
        # group_key 조립 재계산 — 라벨 없으면 NULL, 있으면 corp|kind|label
        "n_group_key_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE group_key IS DISTINCT FROM '
                 "(CASE WHEN period_label IS NULL THEN NULL "
                 "ELSE corp_code || '|' || kind || '|' || period_label END) "
                 "OR group_key_basis IS DISTINCT FROM "
                 "(CASE WHEN period_label IS NULL THEN 'no_label' ELSE 'label' END)"),
        # 링크 성립 ⟺ candidate_status ∈ {unique, multi_resolved} ⟺ link_basis='parsed'
        "n_link_status_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE (orig_rcept_no IS NOT NULL) <> '
                 f"(candidate_status IN ({_vocab_sql(LINKED_STATUS)})) "
                 "OR (orig_rcept_no IS NOT NULL) <> (link_basis = 'parsed')"),
        # 정정이 아닌 행은 링크 축 전부가 'n/a'
        "n_noncorrection_linked": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE NOT is_correction AND '
                 "(candidate_status <> 'n/a' OR date_check <> 'n/a' "
                 "OR orig_rcept_no IS NOT NULL OR filed_date IS NOT NULL)"),
        # 원본은 산출 안에 있고, 정정이 아니고, 같은 그룹이고, 접수번호가 앞서야 한다
        "n_link_orig_invalid": _n(
            ctx, f'SELECT count(*) FROM "{v}" c LEFT JOIN "{v}" o '
                 "ON o.rcept_no = c.orig_rcept_no WHERE c.orig_rcept_no IS NOT NULL AND "
                 "(o.rcept_no IS NULL OR o.is_correction OR o.group_key IS DISTINCT FROM "
                 "c.group_key OR o.rcept_no >= c.rcept_no)"),
        # 원본 측 집계 재계산
        "n_corrections_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" o LEFT JOIN (SELECT orig_rcept_no, count(*) AS n, '
                 f'min(rcept_dt) AS d FROM "{v}" WHERE orig_rcept_no IS NOT NULL '
                 "GROUP BY 1) b ON b.orig_rcept_no = o.rcept_no "
                 "WHERE o.n_corrections IS DISTINCT FROM coalesce(b.n, 0) "
                 "OR o.first_correction_dt IS DISTINCT FROM b.d"),
        # date_check='exact' 는 filed_date = 원본 rcept_dt 여야 한다
        "n_date_check_exact_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" c JOIN "{v}" o ON o.rcept_no = c.orig_rcept_no '
                 "WHERE c.date_check = 'exact' AND c.filed_date IS DISTINCT FROM o.rcept_dt"),
        # `date_check` 의 "대조 재료 없음" 갈래(no_zip·no_page·unparsed)는 ZIP 정정신고 페이지가
        # 파싱된 접수와 정확히 여집합이어야 한다 — 산출이 만들지 않은 stage 축으로 다시 판정한다.
        "n_date_check_material_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE o.is_correction AND '
                 "(o.date_check IN ('no_zip', 'no_page', 'unparsed')) <> NOT EXISTS "
                 "(SELECT 1 FROM stg_doc_correction c WHERE c.rcept_no = o.rcept_no "
                 "AND c.page_found AND c.filed_date_status = 'parsed' "
                 "AND c.filed_date IS NOT NULL)"),
        # is_correction(stage 플래그) 와 접두 어휘가 어긋나면 모집단 정의가 흔들린 것이다
        "n_is_correction_prefix_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE is_correction <> '
                 f"(corr_prefix IN ({_vocab_sql(CORRECTION_PREFIXES)}))"),
        "n_available_ne_rcept_dt": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                           "WHERE available_date IS DISTINCT FROM rcept_dt"),
    }
    metrics: dict[str, object] = {
        "ladder_stage": ladder_stage, "ladder_out": ladder_out,
        "ladder_out_n_periodic_with_reject": ladder_out_l1,
        "ladder_mismatch": ladder_mismatch,
        "n_by_kind": _counts(ctx, "kind"),
        "n_by_candidate_status": _counts(ctx, "candidate_status"),
        "n_by_date_check": _counts(ctx, "date_check"),
        "n_by_group_key_basis": _counts(ctx, "group_key_basis"),
        "n_population_dup_rcept": _n(ctx, POPULATION_DUP_SQL),
        "n_no_label": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE period_label IS NULL'),
        "n_linked_originals": _n(ctx, f'SELECT count(DISTINCT orig_rcept_no) FROM "{v}"'),
        "n_corr_has_fin_item": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE corr_has_fin_item'),
        "n_corr_items_null": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                     "WHERE is_correction AND corr_has_fin_item IS NULL"),
        "n_delay_negative": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE delay_days < 0'),
        "delay_days_p50": _f(ctx, "SELECT quantile_cont(delay_days, 0.5) FROM "
                                  f'"{v}" WHERE NOT is_correction'),
        "delay_days_p99": _f(ctx, "SELECT quantile_cont(delay_days, 0.99) FROM "
                                  f'"{v}" WHERE NOT is_correction'),
        "max_prior_corr_count": _n(ctx, f'SELECT coalesce(max(prior_corr_count), 0) FROM "{v}"'),
        "reject_by_reason": dict(ctx.reject_by_reason),
        "fin_item_keywords": list(FIN_ITEM_KEYWORDS),
    }
    bad = {k: n for k, n in checks.items() if n}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult("EG3_disclosure_version", GateStatus.PASS,
                          "어휘·링크 불변식·사다리 재계산 일치", merged)
    return GateResult("EG3_disclosure_version", GateStatus.FAIL,
                      "; ".join(f"{k}={n}" for k, n in sorted(bad.items())), merged)


eg3_disclosure_version.gate_name = "EG3_disclosure_version"    # type: ignore[attr-defined]


def eg6_disclosure_version(ctx: EquityGateContext) -> GateResult:
    """EG6-P05(E-G6a 링크 성립률 ≥ baseline) + EG6-P06(E-G6b 날짜 일치율, 기록형).

    baseline `link_rate_min` 미등재면 `skip(no_baseline)` 이지만 두 비율은 metrics 로 남긴다
    (WORKFLOW §3-4: 사다리 baseline 등재 전에는 임계를 걸지 않는다).
    """
    v = ctx.out_view
    n_corr = _n(ctx, f'SELECT count(*) FROM "{v}" WHERE is_correction')
    n_linked = _n(ctx, f'SELECT count(*) FROM "{v}" WHERE candidate_status IN '
                       f"({_vocab_sql(LINKED_STATUS)})")
    n_date_pop = _n(ctx, f'SELECT count(*) FROM "{v}" WHERE date_check NOT IN '
                         f"({_vocab_sql(DATE_CHECK_UNMEASURED)})")
    n_date_ok = _n(ctx, f'SELECT count(*) FROM "{v}" '
                        "WHERE date_check IN ('exact', 'off_1d')")
    link_rate = (n_linked / n_corr) if n_corr else None
    date_rate = (n_date_ok / n_date_pop) if n_date_pop else None
    metrics: dict[str, object] = {
        "n_correction": n_corr, "n_linked": n_linked, "link_rate": link_rate,
        "n_date_measured": n_date_pop, "n_date_exact_or_1d": n_date_ok,
        "date_exact_rate": date_rate,
        # EG6-P07(그룹 표본 오판율)은 사람이 라벨한 표본이 있어야 재는 값이라 코드가 판정하지
        # 않는다 — baseline `misjudge_rate_max` 는 참조로만 싣는다(GATES §9).
        "misjudge_rate_max": ctx.baseline.get(ctx.rule.name, "misjudge_rate_max"),
    }
    floor = require_const(ctx, "link_rate_min", metrics)
    ok = link_rate is not None and link_rate >= floor
    metrics["link_rate_min"] = floor
    if link_rate is None:
        raise SkipGate("no_coverage", metrics)
    return GateResult("EG6_disclosure_version", GateStatus.PASS if ok else GateStatus.FAIL,
                      "정정 링크 성립률 임계 이상" if ok else
                      f"link rate {link_rate:.6f} < min {floor} "
                      f"(n_linked={n_linked} n_correction={n_corr})", metrics)


eg6_disclosure_version.gate_name = "EG6_disclosure_version"    # type: ignore[attr-defined]


def eg8_disclosure_version(ctx: EquityGateContext) -> GateResult:
    """EG8-P09(E-G7) — stage `rm` 정정 플래그가 붙은 **원본** 중 링크가 실제로 닿은 비율.

    `rm_corrected_later` 는 산출 컬럼이 아니라 **stage 축**이다 — 산출이 만든 링크를 산출이
    만들지 않은 축으로 검사해야 항진명제가 아니다(GATES §5-C 규약).

    분모에서 정정본은 뺀다. 정정이 또 정정되면 DART 는 **정정본에도** `rm` 정정 플래그를 붙이지만
    (절단본 15건), 후보 술어가 정정본을 원본 자격에서 빼므로(DESIGN §4-4) 그 행은 구조적으로
    도달 대상이 아니다 — 정정 체인은 평평하게 접혀 그룹의 모든 정정이 같은 원본을 가리키고,
    체인 순서는 `prior_corr_count` 가 남긴다. 정정본까지 분모에 넣으면 절단본 도달률이
    82/97 = 0.845 로 떨어지는데 그 15건은 규칙이 의도한 결과이지 놓친 링크가 아니다.
    """
    v = ctx.out_view
    # stage 축은 **EXISTS** 로 읽는다 — `stg_disclosure` 는 접수번호가 중복될 수 있어 JOIN 하면
    # 산출 행이 판본 수만큼 부풀고 비율이 흔들린다.
    rm = ("EXISTS (SELECT 1 FROM stg_disclosure d WHERE d.rcept_no = o.rcept_no "
          "AND d.rm_corrected_later)")
    n_rm = _n(ctx, f'SELECT count(*) FROM "{v}" o WHERE {rm} AND NOT o.is_correction')
    n_reached = _n(ctx, f'SELECT count(*) FROM "{v}" o WHERE {rm} AND NOT o.is_correction '
                        "AND o.n_corrections > 0")
    # 기록형 둘: ① 정정본에 붙은 rm(구조적 미도달) ② 링크는 닿았는데 stage 플래그가 없는 원본
    n_rm_on_correction = _n(ctx, f'SELECT count(*) FROM "{v}" o WHERE {rm} AND o.is_correction')
    n_linked_no_rm = _n(ctx, f'SELECT count(*) FROM "{v}" o WHERE NOT {rm} '
                             "AND o.n_corrections > 0")
    rate = (n_reached / n_rm) if n_rm else None
    metrics: dict[str, object] = {"n_rm_corrected_later": n_rm, "n_rm_reached": n_reached,
                                  "reach_rate": rate,
                                  "n_rm_on_correction": n_rm_on_correction,
                                  "n_linked_without_rm": n_linked_no_rm}
    floor = require_const(ctx, "reach_rate_min", metrics)
    metrics["reach_rate_min"] = floor
    if rate is None:
        raise SkipGate("no_coverage", metrics)
    ok = rate >= floor
    return GateResult("EG8_disclosure_version", GateStatus.PASS if ok else GateStatus.FAIL,
                      "rm 정정 플래그 도달률 임계 이상" if ok else
                      f"reach rate {rate:.6f} < min {floor} "
                      f"(n_reached={n_reached} n_rm={n_rm})", metrics)


eg8_disclosure_version.gate_name = "EG8_disclosure_version"    # type: ignore[attr-defined]


# ── S19 필드 선언 (DESIGN §4-7) ──────────────────────────────────────────────
# `delay_days` 는 FACTORS §7 의 파생 팩터 "공시 지연" 재료다(구 GAP-05 "산출이 26테이블에 없다" 를
# 4A 가 닫았다). 레지스트리 42 필드에 대응이 없어 equity 내부 스코프이고, 축이 (rcept_no) 라
# 종목 축으로 쓰려면 소비자가 `corp_ticker` 로 전개한다. 랙 1 세션 — 원천 `stg_disclosure`·
# `stg_doc_*` 가 전부 stage `lag_known=false` 이고 접수 시각(장중·장후)을 모른다.
FIELDS_DISCLOSURE: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="event.disclosure_delay_days", columns=("delay_days",),
        label="정기보고서 법정기한 대비 지연일수", unit="일", value_type="count",
        frequency="report", recommended_lag_sessions=1, recommended_lag_days=1,
        point_in_time=True, requires_confirmation=True,
        disclosure_basis="정기보고서 접수일(rcept_dt) — 지연은 접수 시점에 확정된다",
        evidence="disclosure_version.delay_days = rcept_dt − legal_deadline(사업 90일·반기/분기 "
                 "45일). 사후 확정되는 상폐 라벨보다 먼저 뜨는 신호(FACTORS §7). 음수(기한 전 "
                 "제출)가 정상값이라 소비자가 부호 규약을 정해야 한다.",
        coverage_axis="table_rows", scope="internal"),
)


DISCLOSURE_VERSION = register(EquityTable(
    name="disclosure_version",
    grain=("rcept_no",),
    columns={"rcept_no": "VARCHAR", "corp_code": "VARCHAR", "rcept_dt": "DATE",
             "kind": "VARCHAR", "period_label": "VARCHAR", "group_key": "VARCHAR",
             "group_key_basis": "VARCHAR", "is_correction": "BOOLEAN",
             "corr_prefix": "VARCHAR", "orig_rcept_no": "VARCHAR",
             "candidate_status": "VARCHAR", "date_check": "VARCHAR",
             "prior_corr_count": "BIGINT", "corr_page_found": "BOOLEAN",
             "filed_date": "DATE", "reason_raw": "VARCHAR", "corr_has_fin_item": "BOOLEAN",
             "first_correction_dt": "DATE", "n_corrections": "BIGINT",
             "legal_deadline": "DATE", "delay_days": "BIGINT", "link_basis": "VARCHAR",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_disclosure", "stg_doc_correction", "stg_doc_index", "stg_doc_meta"),
    partition_class="receipt_axis",
    # DESIGN §2 receipt_axis 정의 그대로 — 이 테이블은 rcept_no 가 grain 이라 접수연도가 항상 있다
    # (S05 `corp_event` 는 KRX 파생행에 rcept_no 가 없어 year(announce_date) 로 대신했다).
    partition_key_expr="CAST(substr(rcept_no, 1, 4) AS INTEGER)",
    available_rule="column:rcept_dt — DART 접수일(derived)",
    eg1_lhs_sql=EG1_LHS_SQL,
    eg1_rhs_sql=EG1_RHS_SQL,
    sql_path=SQL_PATH,
    input_columns={
        # `observed_date` 는 재수집 판본을 접는 축이다(first_write_wins) — 산출 컬럼이 아니다.
        "stg_disclosure": ("rcept_no", "rcept_dt", "corp_code", "report_nm", "is_correction",
                           "rm_corrected_later", "observed_date"),
        "stg_doc_correction": ("rcept_no", "page_found", "filed_date", "filed_date_status",
                               "reason_raw", "items"),
        "stg_doc_index": ("rcept_no", "zip_ok", "observed_date"),
        # `stg_doc_meta` 는 이 산출에 쓰이지 않지만 **입력으로 고정**한다 — S12 가 같은 판본을
        # 읽어야 `period_end` 정본과 링크가 어긋나지 않는다(WORKFLOW §3-1 4A 입력 4테이블).
        "stg_doc_meta": ("rcept_no",)},
    available_basis=("derived",),
    content_date_column="rcept_dt",
    reject_reasons=REJECT_REASONS,
    consts=("deadline_days_annual", "deadline_days_interim",
            "date_check_near_days"),
    extra_gates=(eg3_disclosure_version, eg6_disclosure_version, eg8_disclosure_version),
    field_profiles=FIELDS_DISCLOSURE,
))

TABLES: tuple[EquityTable, ...] = (DISCLOSURE_VERSION,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s11.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["BASELINE_SEED", "CANDIDATE_STATUS_VOCAB", "CORRECTION_PREFIXES", "DATE_CHECK_VOCAB",
           "POPULATION_DUP_SQL",
           "DATE_CHECK_UNMEASURED", "DISCLOSURE_VERSION", "EXCLUDED_TOKENS",
           "FIN_ITEM_KEYWORDS", "GROUP_KEY_BASIS_VOCAB", "KIND_VOCAB", "LADDER", "LADDER_OUT",
           "LINKED_STATUS", "LINK_BASIS_VOCAB", "PERIODIC_PREFIXES", "REJECT_REASONS", "TABLES",
           "population_sql"]
