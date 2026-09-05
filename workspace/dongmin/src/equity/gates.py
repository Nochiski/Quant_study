"""게이트 EG0~EG9 의 일반형 (EQUITY_GATES v1.0 §1·§7).

폐기형(FAIL = 버전 폐기): EG0·EG1·EG2·EG3·EG4·EG5·EG6·EG8·EG9. 행 격리형: EG7.
실행 조건이 안 되는 게이트는 SKIP(사유)으로 남기고 폐기하지 않는다 — stage 규약 그대로.
결과 타입은 원장 의존이 없는 `stage.gates` 의 것을 그대로 쓴다.

실행 순서는 GATES §7-1 이 고정한다: EG0 → EG7 → EG1 → EG2 → EG3 → (테이블 특화) → EG4 → EG5.
**앞 게이트가 FAIL 하면 뒤는 실행하지 않고 `skip(upstream_failed)`** — 조인층은 EG0 실패
상태에서 EG1 을 돌리면 오해를 부르는 숫자가 나온다.

테이블 특화 술어(EG3 나머지·EG6·EG8·EG9·EG10~)는 `EquityTable.extra_gates` 훅으로 붙인다.
훅은 `(ctx) -> GateResult` 이고, 이름은 `fn.gate_name` 속성(없으면 `fn.__name__`)에서 읽는다.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
from stage.gates import GateResult, GateStatus
from stage.manifest import BuildRecord

from . import inputs as inputs_mod
from .baseline import Baseline
from .model import EquityTable

DEFAULT_THRESHOLDS: dict[str, float] = {
    "EG7": 0.001,     # 격리 비율 상한 — stage DEFAULT_THRESHOLDS["G7"] 초기값 계승 (GATES §1 EG7)
}

# GATES §0-2 의 폐쇄 어휘 + 프레임이 쓰는 2개(upstream_failed·no_previous_build·inputs_changed).
SKIP_REASONS: tuple[str, ...] = (
    "no_baseline", "no_fixtures", "no_cross_source", "no_multi_version", "declaration_table",
    "not_grid", "no_coverage", "not_built", "dimension_table", "upstream_failed",
    "no_previous_build", "inputs_changed",
)


class SkipGate(Exception):
    """게이트 안에서만 도는 내부 예외 — 상수 미등재 등 '판정 불가' 를 SKIP 으로 나른다."""

    def __init__(self, reason: str, metrics: dict[str, object] | None = None) -> None:
        if reason not in SKIP_REASONS:
            raise ValueError(f"skip reason outside vocabulary: got={reason!r} "
                             f"allowed={list(SKIP_REASONS)}")
        super().__init__(reason)
        self.reason = reason
        self.metrics: dict[str, object] = metrics or {}


@dataclass
class EquityGateContext:
    """게이트가 보는 전부. 산출은 tmp parquet 뷰, 입력은 `_pinned/` 뷰다."""

    con: duckdb.DuckDBPyConnection
    rule: EquityTable
    out_view: str                                 # 채택 행 (tmp parquet 기준)
    reject_view: str | None                       # 격리 행. 없으면 None
    pinned: dict[str, inputs_mod.PinnedBuild]     # stage 테이블 → 고정 입력
    n_out: int
    n_reject: int
    reject_by_reason: dict[str, int]
    inputs: dict[str, str]                        # BuildRecord.inputs 와 같은 dict
    partition_hashes: dict[str, str]              # 파티션 라벨 → content_hash (이번 빌드)
    baseline: Baseline
    previous: BuildRecord | None = None           # 직전 커밋 빌드 (EG5a)
    fixtures: list[dict[str, object]] | None = None
    thresholds: dict[str, float] = field(default_factory=dict)


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[object, ...]:
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: {sql[:200]}")
    return row


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(str(_one(con, sql)[0]))


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def require_const(ctx: EquityGateContext, metric: str,
                  metrics: dict[str, object] | None = None) -> float:
    """baseline 상수. 미등재면 SkipGate('no_baseline') — 코드에 숫자를 못 쓰게 하는 지점이다."""
    v = ctx.baseline.get(ctx.rule.name, metric)
    if v is None:
        raise SkipGate("no_baseline", {"missing_metric": f"{ctx.rule.name}.{metric}",
                                       **(metrics or {})})
    return float(str(v))


def threshold(ctx: EquityGateContext, gate: str) -> float:
    """격리 비율 임계. baseline `threshold_<gate>` → CLI override → 코드 기본값 순."""
    v = ctx.baseline.get(ctx.rule.name, f"threshold_{gate}")
    if gate in ctx.thresholds:
        return float(ctx.thresholds[gate])
    if v is not None:
        return float(str(v))
    return DEFAULT_THRESHOLDS[gate]


def _actual_columns(ctx: EquityGateContext, view: str) -> list[str]:
    return [str(r[0]) for r in ctx.con.execute(f"DESCRIBE {_q(view)}").fetchall()]


def eg0_inputs(ctx: EquityGateContext) -> GateResult:
    """EG0 — 입력 고정(P01) · stage 선언 컬럼 실재(P02) · 입력 게이트 fail 0(P03)
    · 산출 스키마 양방향·순서 대조(P04) · 파티션 축 대조(P06)."""
    unpinned: list[str] = []
    for table, build_id in sorted(ctx.inputs.items()):
        pb = ctx.pinned.get(table)
        if pb is None or pb.build_id != build_id or not pb.root.exists():
            unpinned.append(table)
    missing_columns: list[str] = []
    for table in sorted(ctx.pinned):
        declared = list(ctx.rule.declared_columns(table))
        if declared:
            missing_columns += [f"{table}.{c}"
                                for c in inputs_mod.declared_columns_missing(
                                    ctx.con, table, declared)]
    input_gate_fail: list[str] = []
    for table, pb in sorted(ctx.pinned.items()):
        gates_meta = pb.meta.get("gates")
        if isinstance(gates_meta, list):
            for g in gates_meta:
                if isinstance(g, dict) and g.get("status") == "fail":
                    input_gate_fail.append(f"{table}.{g.get('name')}")
    actual = [c for c in _actual_columns(ctx, ctx.out_view) if c != "year"]
    declared_out = list(ctx.rule.columns)
    schema_mismatch = actual != declared_out
    has_year = "year" in _actual_columns(ctx, ctx.out_view)
    axis_ok = has_year == (ctx.rule.partition_class != "whole")
    metrics: dict[str, object] = {
        "unpinned": unpinned, "missing_columns": missing_columns,
        "input_gate_fail": input_gate_fail, "declared_columns": declared_out,
        "actual_columns": actual, "partition_class": ctx.rule.partition_class,
        "has_year_axis": has_year}
    bad = bool(unpinned or missing_columns or input_gate_fail or schema_mismatch or not axis_ok)
    if not bad:
        return GateResult("EG0", GateStatus.PASS, "입력 고정·선언 대조", metrics)
    why: list[str] = []
    if unpinned:
        why.append(f"unpinned inputs {unpinned}")
    if missing_columns:
        why.append(f"stage columns missing {missing_columns}")
    if input_gate_fail:
        why.append(f"input stage gate fail {input_gate_fail}")
    if schema_mismatch:
        why.append(f"out schema expected {declared_out} got {actual}")
    if not axis_ok:
        why.append(f"partition axis {ctx.rule.partition_class} but has_year={has_year}")
    return GateResult("EG0", GateStatus.FAIL, "; ".join(why), metrics)


def eg7_range(ctx: EquityGateContext) -> GateResult:
    """EG7 — 격리형. 위반 행은 이미 `_reject/<reason>/` 로 갔고 여기서는 비율만 본다."""
    thr = threshold(ctx, "EG7")
    total = ctx.n_out + ctx.n_reject
    ratio = (ctx.n_reject / total) if total else 0.0
    metrics: dict[str, object] = {"n_reject": ctx.n_reject, "n_out": ctx.n_out,
                                  "reject_ratio": ratio, "threshold": thr,
                                  "reject_by_reason": dict(ctx.reject_by_reason)}
    ok = ratio <= thr
    return GateResult("EG7", GateStatus.PASS if ok else GateStatus.FAIL,
                      "격리 비율 이내" if ok else
                      f"reject ratio {ratio:.6f} > threshold {thr} "
                      f"(n_reject={ctx.n_reject} n_out={ctx.n_out})", metrics)


def eg1_equation(ctx: EquityGateContext) -> GateResult:
    """EG1 — `count(out) = <선언 우변> − Σ n_reject`. 등식이 없는 테이블은 착수 금지(FAIL)."""
    if not ctx.rule.eg1_lhs_sql.strip() or not ctx.rule.eg1_rhs_sql.strip():
        return GateResult("EG1", GateStatus.FAIL,
                          f"등식 미선언 — 표에 등식이 없는 테이블은 착수 금지: "
                          f"table={ctx.rule.name}", {"lhs_sql": ctx.rule.eg1_lhs_sql,
                                                     "rhs_sql": ctx.rule.eg1_rhs_sql})
    lhs = int(str(_one(ctx.con, ctx.rule.eg1_lhs_sql)[0]))
    rhs = int(str(_one(ctx.con, ctx.rule.eg1_rhs_sql)[0]))
    expect = rhs - ctx.n_reject
    metrics: dict[str, object] = {"lhs": lhs, "rhs": rhs, "n_reject": ctx.n_reject,
                                  "expect": expect, "delta": lhs - expect,
                                  "lhs_sql": ctx.rule.eg1_lhs_sql,
                                  "rhs_sql": ctx.rule.eg1_rhs_sql}
    ok = lhs == expect
    return GateResult("EG1", GateStatus.PASS if ok else GateStatus.FAIL,
                      "격자 등식 성립" if ok else
                      f"row equation broken: lhs={lhs} expected={expect} "
                      f"(rhs={rhs} - reject={ctx.n_reject}) delta={lhs - expect}", metrics)


def eg2_pit(ctx: EquityGateContext) -> GateResult:
    """EG2 — available_date NOT NULL(P01) · ≥ 내용일(P02) · basis 어휘 폐쇄(P03)."""
    rule = ctx.rule
    if not rule.is_fact:
        return GateResult("EG2", GateStatus.SKIP, "dimension_table",
                          {"available_rule": rule.available_rule})
    absent = [c for c in ("available_date", "available_basis") if c not in rule.columns]
    if absent:
        return GateResult("EG2", GateStatus.FAIL,
                          f"팩트 테이블에 PIT 컬럼이 없다: table={rule.name} missing={absent}",
                          {"missing_columns": absent})
    v = ctx.out_view
    n_null = _count(ctx.con, f"SELECT count(*) FROM {_q(v)} WHERE available_date IS NULL "
                             "AND available_basis IS DISTINCT FROM 'unknown'")
    vocab = ", ".join(f"'{b}'" for b in rule.basis_vocab)
    n_vocab = _count(ctx.con, f"SELECT count(*) FROM {_q(v)} WHERE available_basis IS NULL "
                              f"OR available_basis NOT IN ({vocab})")
    n_early = 0
    if rule.content_date_column is not None:
        n_early = _count(ctx.con, f"SELECT count(*) FROM {_q(v)} WHERE available_date < "
                                  f"{_q(rule.content_date_column)}")
    metrics: dict[str, object] = {"n_available_null": n_null, "n_basis_outside_vocab": n_vocab,
                                  "n_available_before_content": n_early,
                                  "basis_vocab": list(rule.basis_vocab),
                                  "content_date_column": rule.content_date_column}
    ok = (n_null + n_vocab + n_early) == 0
    return GateResult("EG2", GateStatus.PASS if ok else GateStatus.FAIL,
                      "PIT 불변식 성립" if ok else
                      f"available_date NULL={n_null} basis_outside_vocab={n_vocab} "
                      f"available<content={n_early}", metrics)


def eg3_keys(ctx: EquityGateContext) -> GateResult:
    """EG3 — PK 유일(P01) + 격리 사유 어휘 폐쇄. 테이블 특화 불변식은 extra_gates 로."""
    grain = ", ".join(_q(g) for g in ctx.rule.grain)
    n_dup = _count(ctx.con, f"SELECT count(*) FROM (SELECT {grain}, count(*) AS c "
                            f"FROM {_q(ctx.out_view)} GROUP BY {grain} HAVING c > 1)")
    undeclared = sorted(r for r in ctx.reject_by_reason if r not in ctx.rule.reject_reasons)
    metrics: dict[str, object] = {"n_duplicate_keys": n_dup, "grain": list(ctx.rule.grain),
                                 "undeclared_reject_reasons": undeclared,
                                 "declared_reject_reasons": list(ctx.rule.reject_reasons)}
    ok = n_dup == 0 and not undeclared
    why: list[str] = []
    if n_dup:
        why.append(f"duplicate key groups={n_dup} grain={list(ctx.rule.grain)}")
    if undeclared:
        why.append(f"reject_reason outside declared vocabulary: {undeclared} "
                   f"declared={list(ctx.rule.reject_reasons)}")
    return GateResult("EG3", GateStatus.PASS if ok else GateStatus.FAIL,
                      "키·어휘 불변식 성립" if ok else "; ".join(why), metrics)


_FIXTURE_KEYS = ("key", "column", "expect", "source")


def eg4_fixtures(ctx: EquityGateContext) -> GateResult:
    """EG4 — 골든 픽스처 전건 일치. **픽스처가 없으면 FAIL**(stage 는 SKIP 이었다).

    비교는 stage `g4_fixtures` 규약 그대로 양변을 VARCHAR 로 캐스팅한다.
    JSON `null` 은 SQL NULL 기대다.
    """
    if not ctx.fixtures:
        return GateResult("EG4", GateStatus.FAIL,
                          f"골든 픽스처 없음 — equity 는 픽스처 부재가 실패다: "
                          f"table={ctx.rule.name}", {"n_fixtures": 0, "n_mismatch": 0})
    n_mismatch = 0
    detail: list[str] = []
    for fx in ctx.fixtures:
        absent = [k for k in _FIXTURE_KEYS if k not in fx]
        if absent:
            raise ValueError(f"fixture entry missing keys: table={ctx.rule.name} "
                             f"missing={absent} entry={fx}")
        key = fx["key"]
        if not isinstance(key, dict):
            raise ValueError(f"fixture key must be a dict: table={ctx.rule.name} entry={fx}")
        where = " AND ".join(f"CAST({_q(str(k))} AS VARCHAR) = '{v}'" for k, v in key.items())
        col = str(fx["column"])
        row = ctx.con.execute(f"SELECT CAST({_q(col)} AS VARCHAR) FROM {_q(ctx.out_view)} "
                              f"WHERE {where}").fetchone()
        got = None if row is None else row[0]
        expect = fx["expect"]
        ok_one = (got is None and expect is None) or (
            got is not None and expect is not None and str(got) == str(expect))
        if not ok_one:
            n_mismatch += 1
            detail.append(f"{key}.{col}: expected {expect!r} got {got!r}")
    ok = n_mismatch == 0
    return GateResult("EG4", GateStatus.PASS if ok else GateStatus.FAIL,
                      "골든 픽스처 일치" if ok else "; ".join(detail[:10]),
                      {"n_fixtures": len(ctx.fixtures), "n_mismatch": n_mismatch})


def _previous_partition_hashes(prev: BuildRecord) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in prev.partitions:
        path = str(p.get("path", ""))
        h = p.get("content_hash")
        if h is None:
            continue
        label = path.split("/", 1)[1] if "/" in path else "whole"
        out[label] = str(h)
    return out


def eg5a_reproducibility(ctx: EquityGateContext) -> GateResult:
    """EG5a — `inputs` 불변이면 파티션 `content_hash` 전량 동일 (§2-5 함정 1의 회귀 게이트)."""
    prev = ctx.previous
    if prev is None:
        return GateResult("EG5a", GateStatus.SKIP, "no_previous_build",
                          {"partition_hashes": dict(ctx.partition_hashes)})
    if dict(prev.inputs) != dict(ctx.inputs):
        return GateResult("EG5a", GateStatus.SKIP, "inputs_changed",
                          {"previous_inputs": dict(prev.inputs), "inputs": dict(ctx.inputs),
                           "partition_hashes": dict(ctx.partition_hashes)})
    prev_hashes = _previous_partition_hashes(prev)
    if not prev_hashes:
        return GateResult("EG5a", GateStatus.SKIP, "no_previous_build",
                          {"previous_build": prev.build_id,
                           "partition_hashes": dict(ctx.partition_hashes)})
    diff: dict[str, tuple[str | None, str | None]] = {
        k: (prev_hashes.get(k), v) for k, v in ctx.partition_hashes.items()
        if prev_hashes.get(k) != v}
    diff.update({k: (v, None) for k, v in prev_hashes.items()
                 if k not in ctx.partition_hashes})
    metrics: dict[str, object] = {"previous_build": prev.build_id,
                                  "partition_hashes": dict(ctx.partition_hashes),
                                  "previous_partition_hashes": prev_hashes,
                                  "n_changed_partitions": len(diff)}
    ok = not diff
    return GateResult("EG5a", GateStatus.PASS if ok else GateStatus.FAIL,
                      "같은 inputs — 파티션 해시 전량 동일" if ok else
                      f"content_hash changed with unchanged inputs: {sorted(diff)} "
                      f"previous_build={prev.build_id}", metrics)


def _gate_name(fn: Callable[[EquityGateContext], GateResult]) -> str:
    name = getattr(fn, "gate_name", None)
    return str(name) if name else getattr(fn, "__name__", "EG?")


def run_all(ctx: EquityGateContext) -> list[GateResult]:
    """GATES §7-1 순서. 첫 FAIL 이후는 전부 `skip(upstream_failed)`."""
    steps: list[tuple[str, Callable[[EquityGateContext], GateResult]]] = [
        ("EG0", eg0_inputs), ("EG7", eg7_range), ("EG1", eg1_equation), ("EG2", eg2_pit),
        ("EG3", eg3_keys)]
    steps += [(_gate_name(fn), fn) for fn in ctx.rule.extra_gates]
    steps += [("EG4", eg4_fixtures), ("EG5a", eg5a_reproducibility)]
    results: list[GateResult] = []
    failed = False
    for name, fn in steps:
        if failed:
            results.append(GateResult(name, GateStatus.SKIP, "upstream_failed", {}))
            continue
        try:
            r = fn(ctx)
        except SkipGate as s:                 # 상수 미등재 — 측정치는 남긴다 (GATES §7-3)
            r = GateResult(name, GateStatus.SKIP, s.reason, s.metrics)
        results.append(r)
        if r.status is GateStatus.FAIL:
            failed = True
    return results


def load_fixtures(rule_name: str, equity_root: Path,
                  fixtures_path: Path | None = None) -> list[dict[str, object]] | None:
    """골든 픽스처는 코드와 함께 산다 — `src/equity/fixtures/` 정본,
    `<equity_root>/fixtures/` 폴백."""
    candidates = [fixtures_path] if fixtures_path is not None else [
        Path(__file__).parent / "fixtures" / f"{rule_name}.json",
        equity_root / "fixtures" / f"{rule_name}.json"]
    for p in candidates:
        if p is not None and p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError(f"fixture file must hold a list: path={p} "
                                 f"got={type(raw).__name__}")
            return raw
    return None
