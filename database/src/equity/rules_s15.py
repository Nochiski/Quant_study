"""S15 지분·감사 슬라이스 — `holder_daily` · `ownership_snapshot` · `audit_opinion`.

DESIGN v1.2 §4-5 · GATES v1.0 §2(18·19·22행) · §3-⑮⑯⑰ · WORKFLOW §3-1 S15.

세 테이블 모두 **receipt_axis** 이고 PIT 축은 `available_date = rcept_dt`(derived) 하나다.
입력은 stage 원천 + 1단계 `corp`(법인 대조, 기록형)뿐이라 가격·캘린더 축에 의존하지 않는다.

**초안과 다르게 한 것 3가지**(근거·실측은 GATES §9 `S15 구현 정정` 표):

1. `ownership_snapshot` grain 에 **`stock_knd` 를 넣었다**. DESIGN §4-5 초안 grain
   (corp, bsns_year, reprt_code, `nm`) 은 유일하지 않다 — 한 주주가 보통주·우선주를 나눠
   신고하면 같은 이름으로 2행이 온다(절단본 비집계 826행이 이름 축으로는 733개, 충돌 93행).
   `stock_knd` 는 stage 자연키에도 있고(rules_dart.py) 넣으면 826 = 826 으로 유일해진다.
   EG1 우변(GATES §3-⑯)도 같은 축으로 고쳤다.
2. `audit_opinion` EG1 우변을 `count(*) FROM stg_audit` 에서 **grain distinct** 로 고쳤다.
   초안(GATES §3-⑰)은 1:1(93,037)을 전제하는데 실제로는 그렇지 않다 — 절단본 291행이
   grain 226개로 접힌다(연결/별도 감사보고서가 구조 컬럼 없이 자유 텍스트로만 갈리고, 응답이
   같은 행을 그대로 두 번 주는 경우도 있다). grain 을 지키면 우변도 distinct 여야 한다.
3. 세 테이블 모두 `n_source_rows`(그 grain 으로 접힌 stage 행 수) 컬럼을 뒀다. stage 3테이블이
   전부 `key_unique=False` 라 접힘이 조용히 일어날 수 있는데, 접힌 수를 행에 남기지 않으면
   소비 측이 "1행 = 1신고" 로 읽는다. 게이트는 `n_grain_folded` 를 기록형으로 감시한다.

**어휘·범위 상수는 baseline 이 정본**(`baseline_seed_s15.json`) — `.sql` 숫자 리터럴 금지
규약(tests/test_equity_build.py::test_sql파일에_상수_하드코딩_없음)을 지킨다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, register

SQL_DIR = Path(__file__).parent / "sql"
HOLDER_SQL = SQL_DIR / "holder_daily.sql"
OWNERSHIP_SQL = SQL_DIR / "ownership_snapshot.sql"
AUDIT_SQL = SQL_DIR / "audit_opinion.sql"

# ── 폐쇄 어휘 ────────────────────────────────────────────────────────────────
SRC_VOCAB: tuple[str, ...] = ("elestock", "majorstock")
"""`holder_daily.src` — 두 지분 공시 제도. `.sql` 의 리터럴이 이것을 그대로 옮긴다."""

AGGREGATE_LABELS: tuple[str, ...] = ("합계", "계", "총계", "소계")
"""stage `_row_kind` 가 집계행으로 표시하는 이름 어휘(rules_dart.py). `ownership_snapshot` 은
`row_kind` 로 거르지만 게이트는 **이 어휘로 독립 재판정**해 누수를 본다(항진명제 회피)."""

OPINION_CLASS_VOCAB: tuple[str, ...] = ("적정", "한정", "의견거절", "부적정", "other")
"""stage `adt_opinion_class` 어휘. `adt_opinion` 이 NULL 이면 class 도 NULL 이다."""

OPINION_CLASS_ORDER: tuple[str, ...] = ("부적정", "의견거절", "한정", "적정")
"""재분류 우선순위. **부적정을 먼저** 본다 — `LIKE '%적정%'` 을 먼저 쓰면 부적정이 적정으로
뒤집힌다(STAGE_SPEC §2-13 이 명시한 함정). 게이트가 원문에서 이 순서로 다시 분류해 stage 의
`adt_opinion_class` 와 대조한다(기록형)."""

# ── 격리 어휘 ────────────────────────────────────────────────────────────────
HOLDER_REJECTS: tuple[str, ...] = ("rcept_dt_missing",)
OWNERSHIP_REJECTS: tuple[str, ...] = ("rcept_dt_missing", "pct_out_of_range")
AUDIT_REJECTS: tuple[str, ...] = ("rcept_dt_missing",)

# ── EG1 우변 — stage 뷰만으로 세는 독립 산출 (GATES §3-⑮⑯⑰) ─────────────────
# `stg_holder_*` 는 append_only · key_unique=False 라 **자연키 distinct** 로 센다. 행으로 세면
# 재수집 판본만큼 우변이 부풀어 산출(grain 축)과 어긋난다(S11 서버 1차 delta −26 과 같은 함정).
HOLDER_EG1_RHS = (
    "SELECT (SELECT count(*) FROM (SELECT DISTINCT rcept_no, repror FROM stg_holder_elestock)) "
    "+ (SELECT count(*) FROM (SELECT DISTINCT rcept_no, repror FROM stg_holder_majorstock))")
OWNERSHIP_EG1_RHS = (
    "SELECT count(*) FROM (SELECT DISTINCT corp_code, bsns_year, reprt_code, nm, stock_knd "
    "FROM stg_hyslr WHERE row_kind IS DISTINCT FROM 'aggregate')")
AUDIT_EG1_RHS = (
    "SELECT count(*) FROM (SELECT DISTINCT corp_code, bsns_year, reprt_code, bsns_year_label "
    "FROM stg_audit)")
EG1_LHS = 'SELECT count(*) FROM "out_pq"'


# ── 게이트 도우미 ────────────────────────────────────────────────────────────
def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def _val(ctx: EquityGateContext, sql: str) -> object:
    row = ctx.con.execute(sql).fetchone()
    return None if row is None or row[0] is None else row[0]


def _f(ctx: EquityGateContext, sql: str) -> float | None:
    v = _val(ctx, sql)
    return None if v is None else float(str(v))


def _s(ctx: EquityGateContext, sql: str) -> str | None:
    v = _val(ctx, sql)
    return None if v is None else str(v)


def _counts(ctx: EquityGateContext, column: str) -> dict[str, int]:
    return {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f'SELECT "{column}", count(*) FROM "{ctx.out_view}" GROUP BY 1 ORDER BY 1').fetchall()}


def _lit(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _const(ctx: EquityGateContext, metric: str) -> float | None:
    """기록형 metric 이 쓰는 baseline 상수. 미등재면 None — 게이트를 skip 시키지 않는다.

    임계형(`require_const`)과 다르다: 여기서 skip 하면 같은 게이트가 든 폐기형 검사까지
    사라진다(GATES §0-3 은 임계에만 skip 을 요구한다)."""
    v = ctx.baseline.get(ctx.rule.name, metric)
    return None if v is None else float(str(v))


def _corp_unmatched(ctx: EquityGateContext) -> int:
    """산출 `corp_code` 가 1단계 `corp` 에 없는 행 수(기록형). 있으면 종목 축으로 못 잇는다."""
    return _n(ctx, f'SELECT count(*) FROM "{ctx.out_view}" o WHERE NOT EXISTS '
                   "(SELECT 1 FROM corp c WHERE c.corp_code = o.corp_code)")


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            ok_detail: str) -> GateResult:
    bad = {k: n for k, n in checks.items() if n}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, ok_detail, merged)
    return GateResult(name, GateStatus.FAIL,
                      "; ".join(f"{k}={n}" for k, n in sorted(bad.items())), merged)


# ── holder_daily ─────────────────────────────────────────────────────────────
def eg3_holder_daily(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — `src` 어휘 폐쇄 · 원천 왕복 · 원천 전용 컬럼 누수 · PIT 축 항등.

    **원천 왕복**(`n_src_roundtrip_miss`)이 이 게이트의 축이다: 산출 행마다 그 `src` 가 가리키는
    stage 테이블에 같은 (rcept_no, repror) 가 실제로 있는지 본다 — 산출이 만들지 않은 축이라
    항진명제가 아니다(GATES §5-C 규약). UNION ALL 의 두 갈래가 뒤바뀌면 여기서 잡힌다.
    """
    v = ctx.out_view
    ele = ("EXISTS (SELECT 1 FROM stg_holder_elestock e WHERE e.rcept_no = o.rcept_no "
           "AND e.repror IS NOT DISTINCT FROM o.repror)")
    mjr = ("EXISTS (SELECT 1 FROM stg_holder_majorstock m WHERE m.rcept_no = o.rcept_no "
           "AND m.repror IS NOT DISTINCT FROM o.repror)")
    pct_max = _const(ctx, "pct_max")
    checks = {
        "n_src_outside_vocab": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE src IS NULL '
                                       f"OR src NOT IN ({_lit(SRC_VOCAB)})"),
        "n_src_roundtrip_miss": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE NOT '
                 f"(CASE WHEN o.src = 'elestock' THEN {ele} ELSE {mjr} END)"),
        # 원천 전용 컬럼이 반대편 행에 새면 UNION 의 컬럼 정렬이 어긋난 것이다
        "n_exclusive_column_leak": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE '
                 "(src = 'elestock' AND (report_tp IS NOT NULL OR report_resn IS NOT NULL "
                 "OR ctr_qty_shr IS NOT NULL OR ctr_rate_pct IS NOT NULL)) "
                 "OR (src = 'majorstock' AND (ofcps IS NOT NULL OR exec_rgist IS NOT NULL "
                 "OR main_shrholdr IS NOT NULL))"),
        "n_available_ne_rcept_dt": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                           "WHERE available_date IS DISTINCT FROM rcept_dt"),
        # 지분 "전" = 후 − 증감. 증감이 있는데 전 값이 없으면 산출식이 깨진 것이다
        "n_prev_null_with_change": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE (qty_shr IS NOT NULL '
                 "AND qty_change_shr IS NOT NULL AND qty_prev_shr IS NULL) "
                 "OR (rate_pct IS NOT NULL AND rate_change_pct IS NOT NULL "
                 "AND rate_prev_pct IS NULL)"),
    }
    metrics: dict[str, object] = {
        "n_by_src": _counts(ctx, "src"),
        "n_stage_rows_elestock": _n(ctx, "SELECT count(*) FROM stg_holder_elestock"),
        "n_stage_rows_majorstock": _n(ctx, "SELECT count(*) FROM stg_holder_majorstock"),
        "n_stage_dup_natural_key": _n(
            ctx, "SELECT (SELECT count(*) - count(DISTINCT (rcept_no, repror)) "
                 "FROM stg_holder_elestock) + (SELECT count(*) - "
                 "count(DISTINCT (rcept_no, repror)) FROM stg_holder_majorstock)"),
        "n_source_rows_gt1": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE n_source_rows > 1'),
        "n_corp_unmatched": _corp_unmatched(ctx),
        "n_repror_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE repror IS NULL'),
        "n_qty_prev_negative": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE qty_prev_shr < 0'),
        "rate_pct_min": _f(ctx, f'SELECT min(rate_pct) FROM "{v}"'),
        "rate_pct_max": _f(ctx, f'SELECT max(rate_pct) FROM "{v}"'),
        # 롤링 2년 창(elestock 은 재수집 불가, DART_DESIGN P3e)의 실제 경계 — FX-4B-005 근거
        "rcept_dt_min": _s(ctx, f'SELECT CAST(min(rcept_dt) AS VARCHAR) FROM "{v}"'),
        "rcept_dt_max": _s(ctx, f'SELECT CAST(max(rcept_dt) AS VARCHAR) FROM "{v}"'),
        "pct_max": pct_max,
        "n_rate_above_pct_max": (None if pct_max is None else
                                 _n(ctx, f'SELECT count(*) FROM "{v}" '
                                         f"WHERE rate_pct > {pct_max} OR rate_pct < 0")),
        "reject_by_reason": dict(ctx.reject_by_reason),
    }
    return _result("EG3_holder_daily", checks, metrics, "어휘·원천 왕복·PIT 축 항등 성립")


eg3_holder_daily.gate_name = "EG3_holder_daily"          # type: ignore[attr-defined]


# ── ownership_snapshot ───────────────────────────────────────────────────────
def eg3_ownership_snapshot(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 집계행 누수 독립 재판정 · 격리 규칙 완결 · 접힘 감시 · 집계 대조.

    `n_aggregate_leaked` 는 stage 의 `row_kind` 를 믿지 않고 `AGGREGATE_LABELS` 로 이름을 다시
    본다 — `.sql` 이 쓴 축(`row_kind`)과 다른 축이라 항진명제가 아니다.

    `agg_reconcile_*`(기록형)는 **원장 집계행**(산출 모집단 밖)과의 대조다: 같은 접수·보통주
    안에서 Σ 상세 지분율 이 원장 '계' 행 값과 `agg_rate_tol_pct` 이내인가. 원장이 소수 2자리라
    반올림이 쌓이므로 판정하지 않고 기록만 한다(절단본 76그룹 중 75).
    """
    v = ctx.out_view
    tol = _const(ctx, "agg_rate_tol_pct")
    pct_min, pct_max = _const(ctx, "pct_min"), _const(ctx, "pct_max")
    checks = {
        "n_aggregate_leaked": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                      f"WHERE trim(nm) IN ({_lit(AGGREGATE_LABELS)})"),
        # 격리 규칙이 실제로 걸렸는가 — 산출에 범위 밖 지분율이 남아 있으면 안 된다
        "n_pct_out_of_range_kept": (
            0 if pct_min is None or pct_max is None else
            _n(ctx, f'SELECT count(*) FROM "{v}" WHERE bsis_rate_pct < {pct_min} '
                    f"OR bsis_rate_pct > {pct_max} OR trmend_rate_pct < {pct_min} "
                    f"OR trmend_rate_pct > {pct_max}")),
        "n_stock_knd_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE stock_knd IS NULL'),
        "n_available_before_stlm": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                           "WHERE available_date < stlm_dt"),
    }
    n_detail = _n(ctx, "SELECT count(*) FROM stg_hyslr WHERE row_kind IS DISTINCT FROM 'aggregate'")
    agg = ("SELECT count(*) AS n, count(*) FILTER (WHERE abs(d.s - a.t) <= {tol}) AS n_close FROM "
           f'(SELECT rcept_no, sum(trmend_rate_pct) AS s FROM "{v}" '
           "WHERE stock_knd = '보통주' GROUP BY 1) d JOIN "
           "(SELECT rcept_no, max(trmend_posesn_stock_qota_rt_pct) AS t FROM stg_hyslr "
           "WHERE row_kind = 'aggregate' AND stock_knd = '보통주' GROUP BY 1) a USING (rcept_no)")
    if tol is None:
        n_agg, n_agg_close = None, None
    else:
        row = ctx.con.execute(agg.format(tol=tol)).fetchone()
        n_agg, n_agg_close = (None, None) if row is None else (int(str(row[0])), int(str(row[1])))
    metrics: dict[str, object] = {
        "n_stage_rows": _n(ctx, "SELECT count(*) FROM stg_hyslr"),
        "n_stage_detail_rows": n_detail,
        "n_stage_aggregate_rows": _n(ctx, "SELECT count(*) FROM stg_hyslr "
                                          "WHERE row_kind = 'aggregate'"),
        # 접힌 상세 행 수 — 0 이 아니면 그만큼 지분율이 산출에서 빠졌다
        "n_grain_folded": n_detail - ctx.n_out - ctx.n_reject,
        "n_source_rows_gt1": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE n_source_rows > 1'),
        "n_by_stock_knd": _counts(ctx, "stock_knd"),
        "n_by_reprt_code": _counts(ctx, "reprt_code"),
        "n_relate_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE relate IS NULL'),
        "n_corp_unmatched": _corp_unmatched(ctx),
        "agg_rate_tol_pct": tol,
        "agg_reconcile_n": n_agg,
        "agg_reconcile_n_close": n_agg_close,
        "trmend_rate_pct_max": _f(ctx, f'SELECT max(trmend_rate_pct) FROM "{v}"'),
        "reject_by_reason": dict(ctx.reject_by_reason),
    }
    return _result("EG3_ownership_snapshot", checks, metrics,
                   "집계행 배제·범위·접힘 불변식 성립")


eg3_ownership_snapshot.gate_name = "EG3_ownership_snapshot"    # type: ignore[attr-defined]


# ── audit_opinion ────────────────────────────────────────────────────────────
def _reclass_sql(column: str) -> str:
    """`adt_opinion` 원문에서 등급을 **독립 재분류**하는 CASE(stage 규칙과 같은 우선순위)."""
    norm = f"regexp_replace({column}, '[[:space:]]', '', 'g')"
    arms = " ".join(f"WHEN {norm} LIKE '%{c}%' THEN '{c}'" for c in OPINION_CLASS_ORDER)
    return f"CASE WHEN {column} IS NULL THEN NULL {arms} ELSE 'other' END"


def eg3_audit_opinion(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 의견 등급 어휘 폐쇄 · 원문 독립 재분류 대조 · grain 접힘 감시.

    `n_class_conflict_grain` 이 이 게이트의 핵심이다: grain 이 유일하지 않은 stage 원장을
    접어야 하므로(연결/별도 감사보고서가 구조 컬럼 없이 자유 텍스트로만 갈린다), **접힌 행들의
    의견이 갈리는지**를 stage 축에서 본다. 0 이면 어느 행을 남겨도 의견은 같다(절단본 0).
    0 이 아니면 그만큼 의견이 조용히 하나로 뭉개진 것이다 — 기록형이지만 서버 값이 0 이 아니면
    grain 을 다시 열어야 한다(DESIGN §11).
    """
    v = ctx.out_view
    grain = "corp_code, bsns_year, reprt_code, bsns_year_label"

    def _conflict(column: str) -> int:
        return _n(ctx, f"SELECT count(*) FROM (SELECT count(DISTINCT {column}) AS c "
                       f"FROM stg_audit GROUP BY {grain} HAVING c > 1)")

    checks = {
        "n_class_outside_vocab": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE adt_opinion_class IS NOT NULL '
                 f"AND adt_opinion_class NOT IN ({_lit(OPINION_CLASS_VOCAB)})"),
        # 원문이 없으면 등급도 없다(stage 규칙) — 어긋나면 분류가 원문 밖에서 왔다는 뜻이다
        "n_class_without_opinion": _n(
            ctx, f'SELECT count(*) FROM "{v}" '
                 "WHERE adt_opinion IS NULL AND adt_opinion_class IS NOT NULL"),
        "n_available_before_stlm": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                           "WHERE available_date < stlm_dt"),
        "n_label_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE bsns_year_label IS NULL'),
    }
    n_stage = _n(ctx, "SELECT count(*) FROM stg_audit")
    metrics: dict[str, object] = {
        "n_by_class": _counts(ctx, "adt_opinion_class"),
        "n_stage_rows": n_stage,
        "n_grain_folded": n_stage - ctx.n_out - ctx.n_reject,
        "n_source_rows_gt1": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE n_source_rows > 1'),
        "n_source_rows_max": _n(ctx, f'SELECT coalesce(max(n_source_rows), 0) FROM "{v}"'),
        # 접힌 행들이 실제로 갈리는 축 3종 (stage 축 — 산출이 만들지 않았다)
        "n_class_conflict_grain": _conflict("adt_opinion_class"),
        "n_opinion_conflict_grain": _conflict("adt_opinion"),
        "n_adtor_conflict_grain": _conflict("adtor"),
        # 원문에서 다시 분류해 stage 의 등급과 대조한다(부적정 선매칭 함정의 감시)
        "n_class_recomputed_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE adt_opinion_class IS DISTINCT FROM '
                 f"({_reclass_sql('adt_opinion')})"),
        "n_opinion_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE adt_opinion IS NULL'),
        "n_by_reprt_code": _counts(ctx, "reprt_code"),
        "n_corp_unmatched": _corp_unmatched(ctx),
        "opinion_class_order": list(OPINION_CLASS_ORDER),
        "reject_by_reason": dict(ctx.reject_by_reason),
    }
    return _result("EG3_audit_opinion", checks, metrics, "의견 어휘·재분류 대조·접힘 감시 성립")


eg3_audit_opinion.gate_name = "EG3_audit_opinion"        # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────
HOLDER_DAILY = register(EquityTable(
    name="holder_daily",
    grain=("rcept_no", "repror", "src"),
    columns={"rcept_no": "VARCHAR", "src": "VARCHAR", "repror": "VARCHAR",
             "corp_code": "VARCHAR", "rcept_dt": "DATE",
             "ofcps": "VARCHAR", "exec_rgist": "VARCHAR", "main_shrholdr": "VARCHAR",
             "report_tp": "VARCHAR", "report_resn": "VARCHAR",
             "qty_shr": "DECIMAL(12,0)", "qty_change_shr": "DECIMAL(11,0)",
             "qty_prev_shr": "DECIMAL(13,0)",
             "rate_pct": "DECIMAL(16,11)", "rate_change_pct": "DECIMAL(17,11)",
             "rate_prev_pct": "DECIMAL(18,11)",
             "ctr_qty_shr": "DECIMAL(11,0)", "ctr_rate_pct": "DECIMAL(7,2)",
             "n_source_rows": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_holder_elestock", "stg_holder_majorstock", "corp"),
    partition_class="receipt_axis",
    partition_key_expr="CAST(substr(rcept_no, 1, 4) AS INTEGER)",
    available_rule="column:rcept_dt — DART 접수일(derived)",
    eg1_lhs_sql=EG1_LHS,
    eg1_rhs_sql=HOLDER_EG1_RHS,
    sql_path=HOLDER_SQL,
    input_columns={
        # `observed_date` 는 재수집 판본을 접는 축이다(first_write_wins) — 산출 컬럼이 아니다.
        "stg_holder_elestock": ("rcept_no", "repror", "rcept_dt", "corp_code",
                                "isu_exctv_ofcps", "isu_exctv_rgist_at", "isu_main_shrholdr",
                                "sp_stock_lmp_cnt_shr", "sp_stock_lmp_irds_cnt_shr",
                                "sp_stock_lmp_rate_pct", "sp_stock_lmp_irds_rate_pct",
                                "observed_date"),
        "stg_holder_majorstock": ("rcept_no", "repror", "rcept_dt", "corp_code", "report_tp",
                                  "report_resn", "stkqy_shr", "stkqy_irds_shr", "stkrt_pct",
                                  "stkrt_irds_pct", "ctr_stkqy_shr", "ctr_stkrt_pct",
                                  "observed_date"),
        # 산출에 쓰지 않는다 — 법인 대조(기록형 `n_corp_unmatched`) 전용이자 S01 선행 고정.
        "corp": ("corp_code",)},
    available_basis=("derived",),
    content_date_column="rcept_dt",
    reject_reasons=HOLDER_REJECTS,
    extra_gates=(eg3_holder_daily,),
))

OWNERSHIP_SNAPSHOT = register(EquityTable(
    name="ownership_snapshot",
    grain=("corp_code", "bsns_year", "reprt_code", "nm", "stock_knd"),
    columns={"corp_code": "VARCHAR", "bsns_year": "VARCHAR", "reprt_code": "VARCHAR",
             "nm": "VARCHAR", "stock_knd": "VARCHAR", "rcept_no": "VARCHAR",
             "relate": "VARCHAR", "stlm_dt": "DATE",
             "bsis_qty_shr": "DECIMAL(16,0)", "bsis_rate_pct": "DECIMAL(7,2)",
             "trmend_qty_shr": "DECIMAL(16,0)", "trmend_rate_pct": "DECIMAL(7,2)",
             "rm": "VARCHAR", "n_source_rows": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_hyslr", "corp"),
    partition_class="receipt_axis",
    partition_key_expr="CAST(substr(rcept_no, 1, 4) AS INTEGER)",
    available_rule="column:rcept_dt — DART 접수일(derived, stage 가 접수번호로 조회)",
    eg1_lhs_sql=EG1_LHS,
    eg1_rhs_sql=OWNERSHIP_EG1_RHS,
    sql_path=OWNERSHIP_SQL,
    input_columns={
        "stg_hyslr": ("corp_code", "bsns_year", "reprt_code", "nm", "stock_knd", "rcept_no",
                      "relate", "stlm_dt", "bsis_posesn_stock_co_shr",
                      "bsis_posesn_stock_qota_rt_pct", "trmend_posesn_stock_co_shr",
                      "trmend_posesn_stock_qota_rt_pct", "rm", "row_kind", "row_hash",
                      "available_date", "observed_date"),
        "corp": ("corp_code",)},
    available_basis=("derived",),
    content_date_column="stlm_dt",
    reject_reasons=OWNERSHIP_REJECTS,
    consts=("pct_min", "pct_max"),
    extra_gates=(eg3_ownership_snapshot,),
))

AUDIT_OPINION = register(EquityTable(
    name="audit_opinion",
    grain=("corp_code", "bsns_year", "reprt_code", "bsns_year_label"),
    columns={"corp_code": "VARCHAR", "bsns_year": "VARCHAR", "reprt_code": "VARCHAR",
             "bsns_year_label": "VARCHAR", "rcept_no": "VARCHAR", "stlm_dt": "DATE",
             "adtor": "VARCHAR", "adt_opinion": "VARCHAR", "adt_opinion_class": "VARCHAR",
             "adt_reprt_spcmnt_matter": "VARCHAR", "emphs_matter": "VARCHAR",
             "core_adt_matter": "VARCHAR", "n_source_rows": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_audit", "corp"),
    partition_class="receipt_axis",
    partition_key_expr="CAST(substr(rcept_no, 1, 4) AS INTEGER)",
    available_rule="column:rcept_dt — DART 접수일(derived, stage 가 접수번호로 조회)",
    eg1_lhs_sql=EG1_LHS,
    eg1_rhs_sql=AUDIT_EG1_RHS,
    sql_path=AUDIT_SQL,
    input_columns={
        "stg_audit": ("corp_code", "bsns_year", "reprt_code", "bsns_year_label", "rcept_no",
                      "stlm_dt", "adtor", "adt_opinion", "adt_opinion_class",
                      "adt_reprt_spcmnt_matter", "emphs_matter", "core_adt_matter",
                      "row_hash", "available_date", "observed_date"),
        "corp": ("corp_code",)},
    available_basis=("derived",),
    content_date_column="stlm_dt",
    reject_reasons=AUDIT_REJECTS,
    extra_gates=(eg3_audit_opinion,),
))

TABLES: tuple[EquityTable, ...] = (HOLDER_DAILY, OWNERSHIP_SNAPSHOT, AUDIT_OPINION)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s15.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["AGGREGATE_LABELS", "AUDIT_EG1_RHS", "AUDIT_OPINION", "AUDIT_REJECTS",
           "BASELINE_SEED", "HOLDER_DAILY", "HOLDER_EG1_RHS", "HOLDER_REJECTS",
           "OPINION_CLASS_ORDER", "OPINION_CLASS_VOCAB", "OWNERSHIP_EG1_RHS",
           "OWNERSHIP_REJECTS", "OWNERSHIP_SNAPSHOT", "SRC_VOCAB", "TABLES"]
