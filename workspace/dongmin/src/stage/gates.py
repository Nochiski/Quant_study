"""게이트 G0~G9 (STAGE_DESIGN v2.2 §9).

폐기형(FAIL = 버전 폐기): G0·G1·G3·G4·G5·G6·G8·G9. 행 격리형: G2·G7 (비율 임계 초과 시에만 FAIL).
실행 조건이 안 되는 게이트는 SKIP(사유) 로 기록하고 폐기하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import duckdb

from .model import DATE_FORMATS, CrossCheck, TableRule

DEFAULT_THRESHOLDS: dict[str, float] = {
    "G2": 0.0,        # cast_failed 행 비율 상한 (survey v2: 숫자 컬럼 비숫자 0 → 0)
    "G7": 0.001,      # out_of_range 격리 비율 상한 (survey v2 후 확정, 기본 0.1%)
    "G9_close": 1.0,  # 종가 교차 일치율 하한 (SPEC 100.0000%)
}
YEAR_RANGE_OBSERVED = (1999, 1)   # 관측일 축 [1999(DART 최초 공시), 현재+1] — 키/파티션, reject
YEAR_RANGE_CONTENT = (1956, 40)   # 내용일 축 [1956(KRX 개장), 현재+40] — 비키 날짜, 위반 = 셀 격리
                                  # 하한 1990 은 상장일 19750611 을 격리했다 (5단계 리뷰 DEFECT-A)


class GateStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class GateResult:
    name: str
    status: GateStatus
    detail: str
    metrics: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "status": self.status.value, "detail": self.detail,
                "metrics": dict(self.metrics)}


@dataclass
class GateContext:
    con: duckdb.DuckDBPyConnection
    rule: TableRule
    src_view: str                 # 원장 UNION 뷰 (원문 VARCHAR + _src)
    stage_view: str               # 채택 행 (parquet 기준)
    reject_view: str              # 격리 행
    n_src: int
    n_dedup: int
    n_reject: int
    n_stage: int
    thresholds: dict[str, float]
    fixtures: list[dict[str, object]] | None
    baseline: dict[str, object] | None
    previous_g1: dict[str, object] | None   # 직전 빌드의 G1 metrics (G5 기준)
    cross_alias: str | None                 # G9 원장 alias (미부착이면 None)
    current_year: int
    lookup_miss: int | None = None          # available lookup 미스 행수 (참조표 없는 테이블은 None)
    parse_metrics: dict[str, object] | None = None   # blob 파서 계상 (blob 테이블 아니면 None)


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[object, ...]:
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: {sql[:200]}")
    return row


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(str(_one(con, sql)[0]))


def g0_declaration(ctx: GateContext) -> GateResult:
    """rules ↔ 원장 실물: 컬럼 실재 + expected_len 길이 분포."""
    missing: list[str] = []
    for s in ctx.rule.sources:
        cols = {r[0] for r in ctx.con.execute(
            "SELECT column_name FROM duckdb_columns() WHERE database_name = ? AND table_name = ?",
            [s.db, s.table]).fetchall()}
        if ctx.rule.blob_source is not None:
            need = set(ctx.rule.blob_source.required_columns)   # 파서 입력 컬럼이 원장 실물 계약
        else:
            need = {c.src for c in ctx.rule.columns}
            if ctx.rule.observed_src:
                need.add(ctx.rule.observed_src)
        missing += [f"{s.db}.{s.table}.{c}" for c in sorted(need - cols)]
    n_len = 0
    for c in ctx.rule.columns:
        if c.expected_len is not None:
            n_len += _count(ctx.con, f'SELECT count(*) FROM {ctx.src_view} '
                                       f'WHERE length("{c.src}") <> {c.expected_len}')
    ok = not missing and n_len == 0
    metrics: dict[str, object] = {"missing_columns": missing, "n_len_mismatch": n_len}
    if ctx.lookup_miss is not None:
        metrics["rcept_map_miss"] = ctx.lookup_miss   # 0 초과 = 경고(설계 §4) — 실패 아님
    return GateResult("G0", GateStatus.PASS if ok else GateStatus.FAIL,
                      "선언 대조" if ok else f"missing={missing} len_mismatch={n_len}", metrics)


def g1_row_equation(ctx: GateContext) -> GateResult:
    expect = ctx.n_src * ctx.rule.fanout - ctx.n_dedup - ctx.n_reject
    ok = ctx.n_stage == expect
    return GateResult("G1", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"stage={ctx.n_stage} expected={expect}",
                      {"n_src": ctx.n_src, "fanout": ctx.rule.fanout, "n_dedup": ctx.n_dedup,
                       "n_reject": ctx.n_reject, "n_stage": ctx.n_stage})


def g2_cast_loss(ctx: GateContext) -> GateResult:
    n_partial = _count(ctx.con, f"SELECT count(*) FROM {ctx.stage_view} "
                                  f"WHERE _src_flag = 'partial'")
    ratio = n_partial / ctx.n_stage if ctx.n_stage else 0.0
    lim = ctx.thresholds["G2"]
    ok = ratio <= lim
    return GateResult("G2", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"cast_failed rows={n_partial} ratio={ratio:.6f} limit={lim}",
                      {"n_partial": n_partial, "ratio": ratio, "limit": lim})


def g3_invariants(ctx: GateContext) -> GateResult:
    metrics: dict[str, object] = {}
    for inv in ctx.rule.invariants:
        n = _count(ctx.con, f"SELECT count(*) FROM {ctx.stage_view} "
                              f"WHERE {inv.violation_sql}")
        metrics[f"{inv.key}_violations"] = n
    if ctx.rule.key_unique:
        keys = ", ".join(f'"{k}"' for k in ctx.rule.natural_key)
        metrics["key_uniqueness_violations"] = _count(
            ctx.con, f"SELECT count(*) FROM (SELECT {keys}, count(*) c FROM {ctx.stage_view} "
                     f"GROUP BY ALL HAVING c > 1)")
    bad = {k: v for k, v in metrics.items() if int(str(v)) > 0}
    return GateResult("G3", GateStatus.FAIL if bad else GateStatus.PASS,
                      f"violations={bad}" if bad else "불변식 전부 성립", metrics)


def g4_fixtures(ctx: GateContext) -> GateResult:
    """골든 픽스처. unit_scale 선언 컬럼은 픽스처 없음 = 실패(§9 — ×1e6 오적용의 유일 방어)."""
    covered = {str(fx["column"]) for fx in ctx.fixtures or ()}
    uncovered = [c.name for c in ctx.rule.columns if c.unit_scale is not None
                 and c.name not in covered]
    if uncovered:
        return GateResult("G4", GateStatus.FAIL,
                          f"unit_scale column without fixture: {uncovered}",
                          {"n_fixtures": len(ctx.fixtures or ()), "n_mismatch": 0,
                           "unit_scale_uncovered": uncovered})
    if not ctx.fixtures:
        return GateResult("G4", GateStatus.SKIP, "no_fixtures", {})
    n_mismatch = 0
    detail: list[str] = []
    for fx in ctx.fixtures:
        key = fx["key"]
        if not isinstance(key, dict):
            raise ValueError(f"fixture key must be a dict: {fx}")
        where = " AND ".join(f'CAST("{k}" AS VARCHAR) = \'{v}\'' for k, v in key.items())
        col = str(fx["column"])
        row = ctx.con.execute(f'SELECT CAST("{col}" AS VARCHAR) FROM {ctx.stage_view} '
                              f"WHERE {where}").fetchone()
        got = None if row is None else row[0]
        expect = fx["expect"]        # JSON null = SQL NULL 기대, 그 외는 문자열 비교
        ok_one = (got is None and expect is None) or (
            got is not None and expect is not None and str(got) == str(expect))
        if not ok_one:
            n_mismatch += 1
            detail.append(f"{key}.{col}: expected {expect!r} got {got!r}")
    ok = n_mismatch == 0
    return GateResult("G4", GateStatus.PASS if ok else GateStatus.FAIL,
                      "골든 픽스처 일치" if ok else "; ".join(detail[:10]),
                      {"n_fixtures": len(ctx.fixtures), "n_mismatch": n_mismatch})


def g5_regression_delta(ctx: GateContext) -> GateResult:
    prev = ctx.previous_g1
    if prev is None:
        return GateResult("G5", GateStatus.SKIP, "no_baseline", {})
    d_src = ctx.n_src - int(str(prev["n_src"]))
    d_dedup = ctx.n_dedup - int(str(prev["n_dedup"]))
    d_reject = ctx.n_reject - int(str(prev["n_reject"]))
    d_stage = ctx.n_stage - int(str(prev["n_stage"]))
    expect = d_src * ctx.rule.fanout - d_dedup - d_reject
    ok = d_stage == expect
    return GateResult("G5", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"Δstage={d_stage} expected={expect}",
                      {"d_src": d_src, "d_dedup": d_dedup, "d_reject": d_reject,
                       "d_stage": d_stage})


def g6_version_keep(ctx: GateContext) -> GateResult:
    if ctx.rule.write_mode != "append_only":
        return GateResult("G6", GateStatus.SKIP, f"write_mode={ctx.rule.write_mode}", {})
    if not ctx.rule.versioned:          # 콜·유닛 로그 — 같은 키가 하루에 여러 번이 정상 (D2)
        return GateResult("G6", GateStatus.SKIP, "unversioned", {})
    keys = ", ".join(f'"{k}"' for k in ctx.rule.natural_key)
    n = _count(ctx.con, f"SELECT count(*) FROM (SELECT {keys}, observed_date, count(*) c "
                          f"FROM {ctx.stage_view} GROUP BY ALL HAVING c > 1)")
    return GateResult("G6", GateStatus.PASS if n == 0 else GateStatus.FAIL,
                      f"(natural_key, observed_date) duplicates={n}", {"n_dup": n})


def g7_range(ctx: GateContext) -> GateResult:
    """행 격리형. 관측일 축 위반 = reject 행, 내용일 축 위반 = NULL 셀. 합산 비율로 임계 비교."""
    n_rows = _count(ctx.con, f"SELECT count(*) FROM {ctx.reject_view} "
                             f"WHERE reject_reason = 'out_of_range'")
    date_cols = [c.name for c in ctx.rule.columns if c.kind in DATE_FORMATS and not c.key]
    n_cells = 0
    if date_cols:
        terms = " + ".join(f"count(*) FILTER (WHERE miss_kind.\"{c}\" = 'out_of_range')"
                           for c in date_cols)
        n_cells = _count(ctx.con, f"SELECT {terms} FROM {ctx.stage_view}")
    n = n_rows + n_cells
    ratio = n / ctx.n_src if ctx.n_src else 0.0
    lim = ctx.thresholds["G7"]
    ok = ratio <= lim
    lo, hi = YEAR_RANGE_OBSERVED[0], ctx.current_year + YEAR_RANGE_OBSERVED[1]
    clo, chi = YEAR_RANGE_CONTENT[0], ctx.current_year + YEAR_RANGE_CONTENT[1]
    return GateResult("G7", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"out_of_range rows={n_rows} cells={n_cells} ratio={ratio:.6f} limit={lim} "
                      f"observed∈[{lo},{hi}] content∈[{clo},{chi}]",
                      {"n_out_of_range": n, "n_out_of_range_rows": n_rows,
                       "n_out_of_range_cells": n_cells, "ratio": ratio, "limit": lim})


def g8_parse_equation(ctx: GateContext) -> GateResult:
    """blob 테이블: 파서 계상 등식 — 실패 0 · 5001≡5002 · 방출 행수 = 원장 행수(n_src)."""
    pm = ctx.parse_metrics
    if pm is None:
        return GateResult("G8", GateStatus.SKIP, "not_blob", {})
    emitted = int(str(pm.get("n_rows_emitted", 0)))
    reasons: list[str] = []
    if int(str(pm.get("n_parse_failed", 0))) > 0:
        reasons.append(f"parse_failed={pm['n_parse_failed']}")
    if int(str(pm.get("n_value_mismatch", 0))) > 0:
        reasons.append(f"value_mismatch={pm['n_value_mismatch']}")
    if emitted != ctx.n_src:
        reasons.append(f"emitted {emitted} != n_src {ctx.n_src}")
    ok = not reasons
    return GateResult("G8", GateStatus.PASS if ok else GateStatus.FAIL,
                      "파싱 등식 성립" if ok else "; ".join(reasons), dict(pm))


def g9_cross_source(ctx: GateContext) -> GateResult:
    cc: CrossCheck | None = ctx.rule.cross_check
    if cc is None:
        return GateResult("G9", GateStatus.SKIP, "no_cross_check", {})
    if ctx.cross_alias is None:
        return GateResult("G9", GateStatus.SKIP, f"ledger_unavailable:{cc.db}", {})
    joined, close_ok, vol_ok = _one(ctx.con, f"""
        SELECT count(*),
               count(*) FILTER (WHERE {cc.close_match_sql}),
               count(*) FILTER (WHERE {cc.volume_match_sql})
        FROM "{ctx.cross_alias}"."{cc.table}" o JOIN {ctx.stage_view} s ON {cc.join_sql}""")
    joined_i = int(str(joined))
    close_r = int(str(close_ok)) / joined_i if joined_i else 0.0
    vol_r = int(str(vol_ok)) / joined_i if joined_i else 0.0
    metrics: dict[str, object] = {"close_joined": joined_i, "close_match_ratio": close_r,
                                  "volume_match_ratio": vol_r}
    reasons: list[str] = []
    if joined_i == 0:
        reasons.append("joined=0")
    if close_r < ctx.thresholds["G9_close"]:
        reasons.append(f"close_match_ratio {close_r:.6f} < {ctx.thresholds['G9_close']}")
    if ctx.baseline is not None:
        for k in ("close_match_ratio", "volume_match_ratio"):
            base = ctx.baseline.get(k)
            if isinstance(base, int | float) and float(str(metrics[k])) < float(base) - 1e-9:
                reasons.append(f"{k} {metrics[k]} < baseline {base}")
    ok = not reasons
    return GateResult("G9", GateStatus.PASS if ok else GateStatus.FAIL,
                      "교차 소스 회귀 유지" if ok else "; ".join(reasons), metrics)


def run_all(ctx: GateContext) -> list[GateResult]:
    return [g0_declaration(ctx), g1_row_equation(ctx), g2_cast_loss(ctx), g3_invariants(ctx),
            g4_fixtures(ctx), g5_regression_delta(ctx), g6_version_keep(ctx), g7_range(ctx),
            g8_parse_equation(ctx), g9_cross_source(ctx)]
