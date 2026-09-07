"""S08 슬라이스 선언 — `flow_daily` 수급 격자 (DESIGN v1.2 §4-3, GATES v1.0 §3 ⑪ · EG3-P06
· EG7-P06 · EG9).

격자 테이블의 첫 구현이다. 세 가지를 코드로 고정한다.
  ① 격자는 `universe_daily`(status ∈ {listed, suspended} ∧ sec_type <> 'etf')다 — 원장이 격자를
     정하지 않는다. 격자 밖 원장 행은 `_reject/pre_calendar`·`_reject/off_grid` 로 나간다
     (폐지 구간 티커 재사용 행이 격자로 새면 생존편향이다 — 절단본 036220 1,411 · 101970 303).
  ② 값 없는 셀은 NULL 이고 `fill_kind`(kind, evidence)가 이유를 든다. 0 으로 채우지 않는다
     (GATES EG9-P04). 판정은 수집 로그 축(`stg_shards_kiwoom`·`stg_units_kis`)이지 산출 축이
     아니다.
  ③ 단위는 stage 가 이미 맞췄다 — 키움 `_flow_krw`·KIS `_mn` 이 둘 다 `unit_scale=1e6` 로
     백만원 → 원 환산을 마쳤다(STAGE_HANDOFF §2). equity 는 곱하지 않고 나르기만 하며,
     EG3_flow_daily 가 원장 재조인으로 그 사실을 검사한다(×1e6 오적용의 방어선).

입력 — stage 4(`stg_flow_daily_kiwoom`·`stg_flow_split_daily` 원장 2 + `stg_shards_kiwoom`·
`stg_units_kis` 로그 2) + equity 2(`universe_daily` 격자 · `trading_calendar` 하한).

테이블 특화 술어(`extra_gates`):
  EG1_ledger      — GATES §3 ⑪ (b) 원장 보존 등식을 **원천별로**: 원장 행수 = 그 원천의 measured
                    행 + 그 원천의 격리 행. `_reject_counts` 는 사유 축뿐이라 원천을 못 가르므로
                    격리 뷰(`out_rej`)의 `src` 로 가른다(격리 뷰가 없는 재판정 문맥에서는 합계
                    등식으로 낮춰 세우고 `reject_split_by_src: false` 를 남긴다).
                    (c) 키움 13주체 컬럼 계승 — 비교축은 stage 선언(`rules_kiwoom` 의
                    `unit_scale=1e6` 컬럼 13)이다.
                    (d) 사유별 격리 건수는 기록형(서버 실측 뒤 baseline 승격 — GATES §7-3).
  EG3_flow_daily  — 어휘 폐쇄(`src`·`fill_kind.kind`·`fill_kind.evidence`) · ticker 폭 ·
                    **단위·값 대조**(measured 행을 원장에 독립 재조인, 13/10 컬럼 전수) ·
                    항등식 EG3-P06(키움 12주체 합 = 0 ± `investor_sum_tol_krw`, `orgn` 제외) ·
                    **격자 정합 양방향**(`n_row_outside_grid`·`n_grid_cell_missing` — EG1 이
                    좌·우변 동반 이동에 눈이 머는 축을 여기서 덮는다) + 기록형 metric(원천별
                    커버율 · 원장 최종일 이후 gap 셀 수 · 두 원천 겹침과 차이 · `orgn` 대 기관
                    7 합 편차 · fill_kind 분포 · evidence_rate).

`orgn` 이 항등식에서 빠지는 이유는 합계 컬럼이어서가 아니라 **기관 7 주체 합과 안 맞기 때문**
이다 — 절단본 20,313 행 중 10,799 행이 다르고 편차 최대 −283,440 백만원(rounding 이 아니다).
DESIGN §4-3 이 `orgn` 을 항등식에서 뺀 것은 이 사실과 정합하고, 편차 분포는 기록형으로 남긴다
(FIELD_MAP `flow.institution_net_buy` 가 '부분' 인 GAP-03 의 실측 근거).
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus
from stage.rules_kiwoom import STG_FLOW_DAILY_KIWOOM

from .gates import EquityGateContext
from .model import FILL_EVIDENCE, FILL_KINDS, EquityTable, FieldProfile, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# 격리 사유 어휘 (EG7-P06). `_reject/<reason>/` 디렉토리 이름이자 EG3 어휘 폐쇄 대상.
REJECT_REASONS: tuple[str, ...] = ("pre_calendar", "off_grid")

# `src` 폐쇄 어휘 (DESIGN §3 — 상보 결합 테이블). not_collected 셀은 원천이 없으므로 NULL 이다.
SRC_VOCAB: tuple[str, ...] = ("kiwoom", "kis")

# 키움 13주체 컬럼 — stage 선언에서 독립 산출한다(GATES §3 ⑪ (c) 의 `_reg_vocab` 대체축).
# `_flow_krw` 만 `unit_scale=1e6` 를 갖는다: close_krw·pred_pre_krw·volume_shr 는 안 갖는다.
KIWOOM_INVESTOR_COLUMNS: tuple[str, ...] = tuple(
    c.name for c in STG_FLOW_DAILY_KIWOOM.columns if c.unit_scale == 1_000_000)

# EG3-P06 항등식 축 — 13주체에서 합계 컬럼 `orgn_krw` 만 뺀 12개.
ORGN_COLUMN = "orgn_krw"
INVESTOR_SUM_COLUMNS: tuple[str, ...] = tuple(c for c in KIWOOM_INVESTOR_COLUMNS
                                              if c != ORGN_COLUMN)
# 기록형 — `orgn` 이 기관 7주체 합과 같은지(같지 않다, 위 docstring). 선언 순서로 잘라 쓴다.
ORGN_MEMBER_COLUMNS: tuple[str, ...] = ("fnnc_invt_krw", "insrnc_krw", "invtrt_krw",
                                        "etc_fnnc_krw", "bank_krw", "penfnd_etc_krw",
                                        "samo_fund_krw")

# KIS 대응표 (DESIGN §4-3, 명세 기반 · 검증축 없음). None = KIS 에 대응 주체가 없어 NULL.
KIS_MAPPING: tuple[tuple[str, str | None], ...] = (
    ("ind_invsr_krw", "prsn_ntby_tr_pbmn_krw"),
    ("frgnr_invsr_krw", "frgn_ntby_tr_pbmn_krw"),
    ("orgn_krw", "orgn_ntby_tr_pbmn_krw"),
    ("fnnc_invt_krw", "scrt_ntby_tr_pbmn_krw"),
    ("insrnc_krw", "insu_ntby_tr_pbmn_krw"),
    ("invtrt_krw", "ivtr_ntby_tr_pbmn_krw"),
    ("etc_fnnc_krw", None),
    ("bank_krw", "bank_ntby_tr_pbmn_krw"),
    ("penfnd_etc_krw", "fund_ntby_tr_pbmn_krw"),
    ("samo_fund_krw", "pe_fund_ntby_tr_pbmn_krw"),
    ("natn_krw", None),
    ("etc_corp_krw", "etc_corp_ntby_tr_pbmn_krw"),
    ("natfor_krw", None),
)

if tuple(c for c, _ in KIS_MAPPING) != KIWOOM_INVESTOR_COLUMNS:      # 선언 오타 방어
    raise ValueError(f"KIS_MAPPING keys must match the kiwoom investor columns in order: "
                     f"kis={[c for c, _ in KIS_MAPPING]} kiwoom={list(KIWOOM_INVESTOR_COLUMNS)}")

_GRID_PREDICATE = "u.status IN ('listed', 'suspended') AND u.sec_type <> 'etf'"
"""격자 술어(DESIGN §4-3). `.sql` 과 게이트가 같은 문자열을 쓰되 게이트는 산출을 재계산하지
않고 `universe_daily` 를 직접 다시 읽는다."""


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _i(v: object) -> int:
    return int(str(v))


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과 (rules_s01·rules_s04 규약)."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def _measured(alias: str) -> str:
    return f"{alias}.fill_kind['kind'] = 'measured'"


def _n_measured_kiwoom(ctx: EquityGateContext, out: str) -> int:
    return _i(_row(ctx, f"SELECT count(*) FROM {out} o WHERE o.src = 'kiwoom' "
                        f"AND {_measured('o')}")[0])


# ── EG1_ledger — GATES §3 ⑪ (b)(c)(d) ────────────────────────────────────────

def eg1_ledger(ctx: EquityGateContext) -> GateResult:
    """(b) 원천별 원장 보존 등식 · (c) 키움 13주체 컬럼 계승 · (d) 사유별 격리 건수(기록형).

    (b) 는 `_reject_counts` 대신 격리 뷰의 `src` 로 원천을 가른다 — 사유 축만으로는 두 원천의
    격리가 섞여 한쪽이 새도 합이 맞을 수 있다(GATES §3 ⑪ 주석의 공백).
    """
    out = _q(ctx.out_view)
    n_kw_src, n_kis_src, n_kw_out, n_kis_out = _row(ctx, f"""
        SELECT (SELECT count(*) FROM stg_flow_daily_kiwoom),
               (SELECT count(*) FROM stg_flow_split_daily),
               (SELECT count(*) FROM {out} o WHERE o.src = 'kiwoom' AND {_measured('o')}),
               (SELECT count(*) FROM {out} o WHERE o.src = 'kis' AND {_measured('o')})""")
    actual = {str(r[0]) for r in ctx.con.execute(f"DESCRIBE {out}").fetchall()}
    missing = [c for c in KIWOOM_INVESTOR_COLUMNS if c not in actual]
    checks = {"n_investor_column_missing": len(missing)}
    metrics: dict[str, object] = {
        "n_ledger_kiwoom": _i(n_kw_src), "n_ledger_kis": _i(n_kis_src),
        "n_measured_kiwoom": _i(n_kw_out), "n_measured_kis": _i(n_kis_out),
        "n_reject_by_reason": dict(ctx.reject_by_reason),          # (d) 기록형
        "investor_columns": list(KIWOOM_INVESTOR_COLUMNS),
        "missing_investor_columns": missing,
    }
    if ctx.reject_view is None:
        # 재판정(`python -m equity gate`)에는 격리 뷰가 없다 — `_meta` 의 합계만 있으므로 원천
        # 축을 못 가른다. 등식을 합계 형태로 낮춰 세우고 그 사실을 metrics 에 남긴다.
        checks["delta_src_total"] = (_i(n_kw_src) + _i(n_kis_src)
                                     - (_i(n_kw_out) + _i(n_kis_out) + ctx.n_reject))
        metrics["reject_split_by_src"] = False
        return _result("EG1_ledger", checks, metrics,
                       "원장 보존 등식(합계) 성립 · 13주체 컬럼 계승")
    rej = _q(ctx.reject_view)
    n_kw_rej, n_kis_rej = _row(ctx, f"""
        SELECT (SELECT count(*) FROM {rej} r WHERE r.src = 'kiwoom'),
               (SELECT count(*) FROM {rej} r WHERE r.src = 'kis')""")
    checks["delta_src_kiwoom"] = _i(n_kw_src) - (_i(n_kw_out) + _i(n_kw_rej))
    checks["delta_src_kis"] = _i(n_kis_src) - (_i(n_kis_out) + _i(n_kis_rej))
    metrics["n_reject_kiwoom"] = _i(n_kw_rej)
    metrics["n_reject_kis"] = _i(n_kis_rej)
    metrics["reject_split_by_src"] = True
    return _result("EG1_ledger", checks, metrics, "원장 보존 등식 성립 · 13주체 컬럼 계승")


eg1_ledger.gate_name = "EG1_ledger"             # type: ignore[attr-defined]


# ── EG3_flow_daily — 어휘 · 단위 · 항등식 ────────────────────────────────────

def _kiwoom_mismatch_sql(out: str) -> str:
    diff = " OR ".join(f"o.{_q(c)} IS DISTINCT FROM k.{_q(c)}" for c in KIWOOM_INVESTOR_COLUMNS)
    return (f"SELECT count(*) FROM {out} o JOIN stg_flow_daily_kiwoom k "
            f"ON k.ticker = o.ticker AND k.date = o.date "
            f"WHERE o.src = 'kiwoom' AND {_measured('o')} AND ({diff})")


def _kis_mismatch_sql(out: str) -> str:
    parts = [f"o.{_q(c)} IS DISTINCT FROM f.{_q(s)}" if s else f"o.{_q(c)} IS NOT NULL"
             for c, s in KIS_MAPPING]
    return (f"SELECT count(*) FROM {out} o JOIN stg_flow_split_daily f "
            f"ON f.ticker = o.ticker AND f.date = o.date "
            f"WHERE o.src = 'kis' AND {_measured('o')} AND ({' OR '.join(parts)})")


def eg3_flow_daily(ctx: EquityGateContext) -> GateResult:
    """EG3 특화 — 어휘 폐쇄 · 값·단위 대조 · EG3-P06 항등식 + 기록형 커버리지.

    폐기 조건은 산출식을 다시 계산하지 않는 축만 건다(DESIGN §1). 값 대조는 산출을 원장에
    **다시 조인**해 보는 것이라 항진명제가 아니다 — SQL 이 컬럼을 바꿔 싣거나 ×1e6 을 덧대면
    여기서만 잡힌다. 커버율·gap·겹침·`orgn` 편차는 사실이지 결함이 아니므로 **기록**한다.
    """
    out = _q(ctx.out_view)
    src_vocab = ", ".join(f"'{s}'" for s in SRC_VOCAB)
    kind_vocab = ", ".join(f"'{k}'" for k in FILL_KINDS)
    ev_vocab = ", ".join(f"'{e}'" for e in FILL_EVIDENCE)
    (n_src_vocab, n_kind_vocab, n_ev_vocab, n_ticker_bad, n_fill_null, n_basis_bad,
     n_off_grid_in_out, n_measured_no_src) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {out} WHERE src IS NOT NULL AND src NOT IN ({src_vocab})),
          (SELECT count(*) FROM {out} WHERE fill_kind['kind'] NOT IN ({kind_vocab})),
          (SELECT count(*) FROM {out} WHERE fill_kind['evidence'] NOT IN ({ev_vocab})),
          (SELECT count(*) FROM {out}
            WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
               OR length(ticker) <> {TICKER_LEN}),
          (SELECT count(*) FROM {out} WHERE fill_kind IS NULL),
          (SELECT count(*) FROM {out} WHERE available_date IS DISTINCT FROM date),
          (SELECT count(*) FROM {out} o WHERE NOT EXISTS (
             SELECT 1 FROM universe_daily u
              WHERE u.date = o.date AND u.ticker = o.ticker AND {_GRID_PREDICATE})),
          (SELECT count(*) FROM {out} o WHERE {_measured('o')} AND o.src IS NULL)""")
    # 격자 → 산출 방향. `n_row_outside_grid` 의 반대이고 둘 다 있어야 EG1 의 사각을 덮는다.
    n_grid_missing = _i(_row(ctx, f"""
        SELECT count(*) FROM universe_daily u WHERE {_GRID_PREDICATE}
          AND NOT EXISTS (SELECT 1 FROM {out} o
                           WHERE o.date = u.date AND o.ticker = u.ticker)""")[0])
    n_kw_mismatch = _i(_row(ctx, _kiwoom_mismatch_sql(out))[0])
    n_kis_mismatch = _i(_row(ctx, _kis_mismatch_sql(out))[0])
    # 측정 행에 원장이 없다 / 미측정 행에 원장이 있다 — 양방향 (조인 방향 오류를 둘 다 잡는다)
    n_measured_no_ledger, n_unmeasured_with_ledger = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {out} o WHERE {_measured('o')}
             AND NOT EXISTS (SELECT 1 FROM stg_flow_daily_kiwoom k
                              WHERE k.ticker = o.ticker AND k.date = o.date)
             AND NOT EXISTS (SELECT 1 FROM stg_flow_split_daily f
                              WHERE f.ticker = o.ticker AND f.date = o.date)),
          (SELECT count(*) FROM {out} o WHERE NOT {_measured('o')}
             AND (EXISTS (SELECT 1 FROM stg_flow_daily_kiwoom k
                           WHERE k.ticker = o.ticker AND k.date = o.date)
               OR EXISTS (SELECT 1 FROM stg_flow_split_daily f
                           WHERE f.ticker = o.ticker AND f.date = o.date)))""")
    # EG3-P06 — 12주체 합. coalesce 로 NULL 을 0 으로 접으면 결측이 항등을 통과한다(GATES §1):
    # 12컬럼 전부 NOT NULL 인 행만 보고 제외 행수를 남긴다.
    sum12 = " + ".join(_q(c) for c in INVESTOR_SUM_COLUMNS)
    notnull12 = " AND ".join(f"{_q(c)} IS NOT NULL" for c in INVESTOR_SUM_COLUMNS)
    n_sum_rows, sum_abs_max, n_sum_nonzero = _row(ctx, f"""
        WITH k AS (SELECT abs({sum12}) AS s FROM {out} o
                    WHERE o.src = 'kiwoom' AND {_measured('o')} AND {notnull12})
        SELECT count(*), max(s), count(*) FILTER (WHERE s <> 0) FROM k""")
    tol = ctx.baseline.get(ctx.rule.name, "investor_sum_tol_krw")
    n_sum_violation: int | None = None
    if tol is not None:
        n_sum_violation = _i(_row(ctx, f"""
            WITH k AS (SELECT abs({sum12}) AS s FROM {out} o
                        WHERE o.src = 'kiwoom' AND {_measured('o')} AND {notnull12})
            SELECT count(*) FROM k WHERE s > {float(str(tol))}""")[0])
    checks = {
        "n_src_outside_vocab": _i(n_src_vocab),
        "n_fill_kind_outside_vocab": _i(n_kind_vocab),
        "n_fill_evidence_outside_vocab": _i(n_ev_vocab),
        "n_fill_kind_null": _i(n_fill_null),
        "n_ticker_bad_width": _i(n_ticker_bad),
        "n_available_date_ne_date": _i(n_basis_bad),
        "n_row_outside_grid": _i(n_off_grid_in_out),
        "n_grid_cell_missing": n_grid_missing,
        "n_measured_without_src": _i(n_measured_no_src),
        "n_kiwoom_value_mismatch": n_kw_mismatch,
        "n_kis_value_mismatch": n_kis_mismatch,
        "n_measured_without_ledger": _i(n_measured_no_ledger),
        "n_unmeasured_with_ledger": _i(n_unmeasured_with_ledger),
    }
    if n_sum_violation is not None:
        checks["n_investor_sum_violation"] = n_sum_violation
    metrics: dict[str, object] = {
        "investor_sum_tol_krw": None if tol is None else float(str(tol)),
        "n_investor_sum_rows": _i(n_sum_rows),
        "n_investor_sum_excluded_null": _n_measured_kiwoom(ctx, out) - _i(n_sum_rows),
        "investor_sum_abs_max_krw": None if sum_abs_max is None else float(str(sum_abs_max)),
        "n_investor_sum_nonzero": _i(n_sum_nonzero),
        **_coverage_metrics(ctx, out),
    }
    return _result("EG3_flow_daily", checks, metrics,
                   "어휘 폐쇄 · 원장 값·단위 일치 · 12주체 항등식 성립")


eg3_flow_daily.gate_name = "EG3_flow_daily"     # type: ignore[attr-defined]


def _coverage_metrics(ctx: EquityGateContext, out: str) -> dict[str, object]:
    """기록형 — 커버율 · gap · 두 원천 겹침 · `orgn` 편차 · fill_kind 분포."""
    kinds = {str(r[0]): _i(r[1]) for r in ctx.con.execute(
        f"SELECT fill_kind['kind'], count(*) FROM {out} GROUP BY 1 ORDER BY 1").fetchall()}
    evidence = {str(r[0]): _i(r[1]) for r in ctx.con.execute(
        f"SELECT fill_kind['evidence'], count(*) FROM {out} GROUP BY 1 ORDER BY 1").fetchall()}
    srcs = {("null" if r[0] is None else str(r[0])): _i(r[1]) for r in ctx.con.execute(
        f"SELECT src, count(*) FROM {out} GROUP BY 1 ORDER BY 1").fetchall()}
    n_cells, n_measured = _row(ctx, f"SELECT count(DISTINCT (date, ticker)), "
                                    f"count(*) FILTER (WHERE fill_kind['kind'] = 'measured') "
                                    f"FROM {out}")
    n_cells, n_measured = _i(n_cells), _i(n_measured)
    n_omitted = kinds.get("src_omitted", 0)
    n_evidenced = evidence.get("shard_done", 0) + evidence.get("unit_ok", 0)
    # 원천별 원장 최종일 이후 격자 셀 = 수집 gap (서버: KIS flow 2026-08-14 종료, DESIGN §10 P16)
    gap: dict[str, object] = {}
    for src, table in (("kiwoom", "stg_flow_daily_kiwoom"), ("kis", "stg_flow_split_daily")):
        last = _row(ctx, f"SELECT max(date) FROM {table}")[0]
        gap[f"{src}_ledger_max_date"] = None if last is None else str(last)
        gap[f"n_grid_cells_after_{src}_max"] = None if last is None else _i(
            _row(ctx, f"SELECT count(DISTINCT (date, ticker)) FROM {out} "
                      f"WHERE date > DATE '{last}'")[0])
    n_overlap, overlap_diff_max = _row(ctx, f"""
        WITH o AS (SELECT date, ticker FROM stg_flow_daily_kiwoom
                   INTERSECT SELECT date, ticker FROM stg_flow_split_daily)
        SELECT count(*),
               (SELECT max(greatest({', '.join(
                   f'abs(coalesce(k.{_q(c)}, 0) - coalesce(f.{_q(s)}, 0))'
                   for c, s in KIS_MAPPING if s)}))
                  FROM o JOIN stg_flow_daily_kiwoom k USING (date, ticker)
                         JOIN stg_flow_split_daily f USING (date, ticker))
        FROM o""")
    n_both_logs = _i(_row(ctx, f"""
        SELECT count(*) FROM {out} o
         WHERE EXISTS (SELECT 1 FROM stg_shards_kiwoom s
                        WHERE s.ticker = o.ticker AND s.src_api = 'ka10060'
                          AND o.date BETWEEN s.req_start AND s.req_end)
           AND EXISTS (SELECT 1 FROM stg_units_kis u
                        WHERE u.ticker = o.ticker AND u.dataset = 'flow'
                          AND o.date BETWEEN u.window_from AND u.window_to)""")[0])
    sum7 = " + ".join(_q(c) for c in ORGN_MEMBER_COLUMNS)
    notnull7 = " AND ".join(f"{_q(c)} IS NOT NULL" for c in (ORGN_COLUMN, *ORGN_MEMBER_COLUMNS))
    n_orgn_rows, n_orgn_ne, orgn_dev_max = _row(ctx, f"""
        WITH d AS (SELECT abs({_q(ORGN_COLUMN)} - ({sum7})) AS dev FROM {out} o
                    WHERE o.src = 'kiwoom' AND {_measured('o')} AND {notnull7})
        SELECT count(*), count(*) FILTER (WHERE dev <> 0), max(dev) FROM d""")
    return {
        "n_grid_cells": n_cells,
        "n_measured": n_measured,
        "coverage_measured": (n_measured / n_cells) if n_cells else None,
        "fill_kind_counts": kinds,
        "fill_evidence_counts": evidence,
        "src_counts": srcs,
        "evidence_rate": (n_evidenced / n_omitted) if n_omitted else None,   # EG9-P02 축
        "n_cells_both_logs": n_both_logs,
        "n_src_overlap": _i(n_overlap),                                      # EG8-P05 축
        "src_overlap_diff_max_krw": (None if overlap_diff_max is None
                                     else float(str(overlap_diff_max))),
        "n_orgn_identity_rows": _i(n_orgn_rows),
        "n_orgn_ne_member_sum": _i(n_orgn_ne),
        "orgn_member_sum_dev_max_krw": (None if orgn_dev_max is None
                                        else float(str(orgn_dev_max))),
        "n_reject_rows": ctx.n_reject,
        **gap,
    }


# ── S19 필드 선언 (DESIGN §4-7 · FIELD_MAP §2 `flow.*`) ──────────────────────
# **랙 1 세션**. 두 원장(`stg_flow_daily_kiwoom`·`stg_flow_split_daily`)이 stage
# `lag_known=false` — 공표 시각 컬럼이 없어 "그날 장중에 알 수 있었는가" 를 잰 적이 없다.
# STAGE_HANDOFF §2 「lag_known=false 는 lag 0 을 적용하면 안 된다」 + FIELD_MAP §1 랙 단위
# 「나머지 전부 1 세션」 을 그대로 따른다. `available_date = date`(basis default)인 것은 팩트 행의
# 축이고, 소비 랙은 이 선언이 낸다(테이블 `available_rule` 이 그렇게 적어 두었다).
# 여기 없는 `flow.foreign_ownership`·`flow.foreign_limit_exhaustion`·`flow.pension_net_buy` 는
# **선언하지 않는다** — 앞 둘은 원천이 `stg_foreign_daily`(ka10008)라 이 테이블에 컬럼이 없고
# (S08-2, DESIGN §4-3 구현 결과 ①), `pension_net_buy` 는 `penfnd_etc_krw` 가 실재하지만 FIELD_MAP
# §2 어휘에 그 field_id 행이 없다. 없는 것을 선언하면 `dataset_profile` 에 '있는데 늘 빈' 행이
# 생겨 S20 준비도가 거짓으로 ready 가 된다.
_FAXIS: tuple[str, str] = ("ticker", "date")
_FLOW_DISCLOSURE = ("원장 날짜 = 매매일. 키움 ka10060·KIS 투자자별 매매동향 모두 공표 시각을 "
                    "주지 않는다(stage lag_known=false) → 익일 지식으로 쓴다")
_FLOW_SRC_NOTE = ("grain 에 `src` 가 들어 한 격자 셀에 원장 행이 둘일 수 있다(상보 결합) — 고르는 "
                  "규칙은 미결이 아니라 확정이다: 키움 우선, 동률은 `src` 사전순으로 한 행"
                  "(FIELD_MAP §2 · S21-3 어댑터 `pick_order`). 값을 섞지 않는다. 절단본 겹침 0, "
                  "서버는 `EG3_flow_daily.n_src_overlap` 이 매 빌드 센다.")

FIELDS: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="flow.foreign_net_buy", columns=("frgnr_invsr_krw",),
        label="외국인 순매수(대금)", unit="KRW", value_type="amount", frequency="session",
        recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False, disclosure_basis=_FLOW_DISCLOSURE,
        evidence="flow_daily.frgnr_invsr_krw ← stg_flow_daily_kiwoom.frgnr_invsr_krw ∪ "
                 "stg_flow_split_daily.frgn_ntby_tr_pbmn_krw. **원 단위** — stage 가 백만원 "
                 "×1e6 환산을 마쳤고(STAGE_HANDOFF §2) equity 도 어댑터도 다시 곱하지 않는다"
                 "(EG3_flow_daily 원장 재조인 단위 대조). 값 없는 셀은 0 이 아니라 NULL 이고 "
                 "이유는 fill_kind 가 나른다. " + _FLOW_SRC_NOTE,
        coverage_axis="grid_session", axis_columns=_FAXIS),
    FieldProfile(
        field_id="flow.institution_net_buy", columns=("orgn_krw",),
        label="기관 순매수(대금)", unit="KRW", value_type="amount", frequency="session",
        recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=True, disclosure_basis=_FLOW_DISCLOSURE,
        evidence="flow_daily.orgn_krw ← 키움 orgn_krw ∪ KIS orgn_ntby_tr_pbmn_krw. "
                 "**미결 조건(GAP-03)**: `orgn` 은 원장의 합계 컬럼인데 그 값이 무엇을 합한 것인지 "
                 "원천이 공표하지 않고, 기관 7주체(fnnc_invt·insrnc·invtrt·etc_fnnc·bank·"
                 "penfnd_etc·samo_fund) 합과 실제로 다르다(절단본 18,581행 중 10,783행 불일치, "
                 "편차 최대 2,834억원 — EG3_flow_daily 기록형이 매 빌드 갱신). 그래서 12주체 "
                 "항등식(EG3-P06)에서도 빠진다. 합계 컬럼을 쓸지 7주체를 다시 합할지는 소비 측이 "
                 "골라야 하고(FACTORS §11-2), 그 선택이 남아 있는 동안 이 필드는 "
                 "partial_support 다. " + _FLOW_SRC_NOTE,
        coverage_axis="grid_session", axis_columns=_FAXIS),
    FieldProfile(
        field_id="flow.retail_net_buy", columns=("ind_invsr_krw",),
        label="개인 순매수(대금)", unit="KRW", value_type="amount", frequency="session",
        recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False, disclosure_basis=_FLOW_DISCLOSURE,
        evidence="flow_daily.ind_invsr_krw ← 키움 ind_invsr_krw ∪ KIS prsn_ntby_tr_pbmn_krw. "
                 "원 단위(stage ×1e6 완료 — 재환산 금지). " + _FLOW_SRC_NOTE,
        coverage_axis="grid_session", axis_columns=_FAXIS),
)


# ── 선언 ─────────────────────────────────────────────────────────────────────

_INVESTOR_TYPES = {c: "DECIMAL(15,0)" for c in KIWOOM_INVESTOR_COLUMNS}

FLOW_DAILY = register(EquityTable(
    name="flow_daily",
    # PK 에 `src` 가 든다(DESIGN §3 상보 결합) → EG1 좌변은 count(DISTINCT (date,ticker))
    # (GATES §5-B4). 정렬 축이기도 하다 — build.py 의 ORDER BY 가 이 순서다.
    grain=("date", "ticker", "src"),
    # 컬럼 순서 = 키·원천 → 키움 원장 13주체 순서 계승 → 결측 어휘 → PIT 2.
    # 타입은 stage 키움 DECIMAL(15,0) 과 KIS DECIMAL(13~14,0) 의 UNION ALL 결과다(EG0 는 이름·
    # 순서만 대조한다 — DESIGN §4-3 이 KIS 를 상보 원천으로 못박아 두 스키마가 함께 결정한다).
    columns={"date": "DATE", "ticker": "VARCHAR", "src": "VARCHAR",
             **_INVESTOR_TYPES,
             "fill_kind": "STRUCT(kind VARCHAR, evidence VARCHAR)",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_flow_daily_kiwoom", "stg_flow_split_daily", "stg_shards_kiwoom",
            "stg_units_kis", "universe_daily", "trading_calendar"),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    available_rule="column:date — 수급(stage lag_known=false), 공표 시각 미제공 → basis default",
    # GATES §3 ⑪ (a) 는 `count(T) − count(grid) = 0` 이고 격리 항이 없다 — 격자 테이블의 격리는
    # **원장 행**이지 격자에서 빠진 산출 행이 아니기 때문이다(그 축의 등식은 (b) = EG1_ledger).
    # 프레임은 `expect = rhs − n_reject` 를 강제하므로 우변 = 격자 + 격자 밖 원장 행수로 두어
    # expect = 격자가 되게 한다. 우변의 두 항은 `universe_daily` 를 직접 다시 읽어 산출을
    # 재계산하지 않는다. 이 형태는 좌·우변이 함께 움직이는 사고(격자 밖 행이 격리되지 않고
    # 산출로 새는 경우)에 눈이 머므로, 그 축은 `EG3_flow_daily` 의 `n_row_outside_grid`(산출 →
    # universe_daily 안티조인)와 `n_grid_cell_missing`(그 반대 방향)이 각각 폐기형으로 잡는다.
    # `out_rej` 를 우변에 넣으면 EG1 하나로 다 잡히지만 `python -m equity gate` 재판정 문맥에는
    # 격리 뷰가 없어 EG1 이 Binder 오류로 죽는다 — 그래서 입력만 읽는 형태를 골랐다.
    # 좌변이 셀 수(count DISTINCT)인 것은 `src` 가 PK 에 들기 때문이다(GATES §5-B4).
    eg1_lhs_sql="SELECT count(DISTINCT (date, ticker)) FROM out_pq",
    eg1_rhs_sql=(
        "SELECT (SELECT count(*) FROM universe_daily u WHERE " + _GRID_PREDICATE + ") + "
        "(SELECT count(*) FROM (SELECT date, ticker FROM stg_flow_daily_kiwoom "
        "UNION ALL SELECT date, ticker FROM stg_flow_split_daily) l "
        "WHERE NOT EXISTS (SELECT 1 FROM universe_daily u "
        "WHERE u.date = l.date AND u.ticker = l.ticker AND " + _GRID_PREDICATE + "))"),
    sql_path=SQL_DIR / "flow_daily.sql",
    input_columns={
        "stg_flow_daily_kiwoom": ("ticker", "date", *KIWOOM_INVESTOR_COLUMNS),
        "stg_flow_split_daily": ("ticker", "date",
                                 *(s for _, s in KIS_MAPPING if s is not None)),
        "stg_shards_kiwoom": ("src_api", "ticker", "req_start", "req_end", "status"),
        "stg_units_kis": ("dataset", "ticker", "status", "window_from", "window_to"),
        "universe_daily": ("date", "ticker", "status", "sec_type"),
        "trading_calendar": ("date",)},
    available_basis=("default",),
    content_date_column="date",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg1_ledger, eg3_flow_daily),
    field_profiles=FIELDS,
))

TABLES: tuple[EquityTable, ...] = (FLOW_DAILY,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s08.json"

__all__ = ["FIELDS", "FLOW_DAILY", "KIS_MAPPING", "KIWOOM_INVESTOR_COLUMNS",
           "REJECT_REASONS", "SRC_VOCAB", "TABLES"]
