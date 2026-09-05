"""equity 빌더 — 고정 stage parquet → `.sql` 1개 → 파티션 parquet → 게이트 → MANIFEST 교체.

한 테이블의 흐름 (DESIGN §2 · GATES §7):
  `_pinned/` 고정 → stage TEMP VIEW → `_const` 상수 주입 → `sql/<table>.sql` 실행
  → `reject_reason` 으로 `out_ok`/`out_rej` 분리 → tmp 에 parquet → 게이트 EG0~EG5
  → 통과: `v=<build_id>` 이동 + MANIFEST.json 교체 / 실패: tmp 폐기 + `_failed/<build_id>.json`
파티션 클래스: whole = `part0.parquet` 하나, date_axis·receipt_axis = `year=YYYY/` 하이브.
격리 행은 `_reject/reject_reason=<r>/part_*.parquet`(duckdb `PARTITION_BY` 하이브 표기).

`content_hash` 는 **반드시 tmp 경로에서** 뜬다 — 최종 경로에서 뜨면 `hive_partitioning` 이
`v=<build_id>` 까지 컬럼으로 뽑아 build_id 가 해시에 섞이고 EG5a 재현성이 영원히 깨진다.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import duckdb
from stage import manifest

from . import gates, inputs
from .baseline import Baseline
from .model import RULES_VERSION, EquityTable


class BuildStatus(Enum):
    OK = "ok"
    GATE_FAILED = "gate_failed"


@dataclass(frozen=True)
class BuildResult:
    status: BuildStatus
    table: str
    build_id: str
    inputs: dict[str, str]
    n_rows: int
    n_reject: int
    content_hash: str
    partitions: list[dict[str, object]]
    gates: list[gates.GateResult]
    elapsed_s: float
    out_dir: Path | None            # OK 일 때 v=<build_id>
    failed_report: Path | None      # GATE_FAILED 일 때 _failed/<build_id>.json

    @property
    def ok(self) -> bool:
        return self.status is BuildStatus.OK


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"count query returned no row: {sql[:200]}")
    return int(str(row[0]))


def _content_hash(con: duckdb.DuckDBPyConnection, glob: str) -> str:
    """stage `build.py:_content_hash` 를 글자 그대로 복제. 행 struct 문자열화라 컬럼 순서 의존."""
    row = con.execute(f"SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) "
                      f"FROM read_parquet('{glob}', hive_partitioning=true) t").fetchone()
    if row is None:
        raise RuntimeError(f"content hash query returned no row: {glob}")
    n, h = row
    return f"{int(str(n))}:{'0' if h is None else format(int(str(h)), 'x')}"


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _lit(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def make_consts(con: duckdb.DuckDBPyConnection, rule: EquityTable, baseline: Baseline) -> None:
    """`rule.consts` 를 baseline 에서 꺼내 1행 wide 임시 테이블 `_const` 로 올린다.

    `.sql` 본문은 상수를 `CROSS JOIN _const` 로만 본다 — 숫자 리터럴 하드코딩 금지
    (EQUITY_WORKFLOW §1)를 기계적으로 검사할 수 있게 하는 유일한 통로다.

    키는 자기 테이블의 metric 이름이거나 `<table>.<metric>`(다른 테이블에 등재된 상수를 복제 없이
    읽는다 — S06 `adj_factor` 가 `corp_event.near_dup_window_days` 를 읽는 식). 컬럼명은
    metric 부분.
    """
    keyed = [(k, *_split_const_key(rule.name, k)) for k in rule.consts]
    missing = [k for k, t, m in keyed if baseline.get(t, m) is None]
    if missing:
        raise KeyError(f"baseline constant not found — table={rule.name} keys={missing} "
                       f"path={baseline.path} (등재는 사람 승인: GATES §7-3)")
    sel = ", ".join(f"{_lit(baseline.get(t, m))} AS {_q(m)}" for _, t, m in keyed)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _const AS SELECT {sel or 'NULL AS _none'}")


def _split_const_key(table: str, key: str) -> tuple[str, str]:
    """`metric` → (자기 테이블, metric) · `other.metric` → (other, metric)."""
    owner, _, metric = key.rpartition(".")
    return (owner or table, metric)


def _previous_record(table_root: Path) -> manifest.BuildRecord | None:
    m = manifest.load(table_root / "MANIFEST.json")
    if m.current_build is None:
        return None
    return next((b for b in m.builds if b.build_id == m.current_build), None)


def build_table(rule: EquityTable, stage_root: Path, equity_root: Path, baseline: Baseline, *,
                keep: int = manifest.KEEP_DEFAULT, memory_limit: str = "6GB", threads: int = 3,
                temp_dir: Path | None = None, build_id: str | None = None,
                fixtures_path: Path | None = None,
                gate_thresholds: dict[str, float] | None = None) -> BuildResult:
    """테이블 1개를 고정 stage 입력에서 빌드한다. 결과는 status 로, 예외는 버그·환경 오류에만."""
    if rule.build_by_year:
        raise NotImplementedError(
            f"build_by_year loop is not implemented yet (T7 범위) — table={rule.name}. "
            "전 구간을 한 번에 물면 duckdb 가 스필한다: 연도 루프를 붙이기 전에는 빌드하지 않는다")
    t0 = time.time()
    bid = build_id or datetime.now(UTC).strftime("b_%Y%m%dT%H%M%S_%fZ")
    table_root = equity_root / rule.name
    tmp_root = equity_root / "_tmp" / bid
    tmp_table = tmp_root / rule.name
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_table.mkdir(parents=True)
    spill = temp_dir or (equity_root / "_tmp" / "spill")
    spill.mkdir(parents=True, exist_ok=True)
    partitioned = rule.partition_class != "whole"

    con = duckdb.connect()
    try:
        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET threads = {int(threads)}")
        con.execute(f"SET temp_directory = '{spill}'")
        pinned = {t: inputs.pin(stage_root, equity_root, t) for t in rule.inputs}
        inputs.create_views(con, pinned,
                            {t: list(rule.declared_columns(t)) for t in rule.inputs})
        make_consts(con, rule, baseline)

        sql_text = rule.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
        con.execute(f"CREATE OR REPLACE TEMP TABLE {_q('out')} AS {sql_text}")
        sel = ", ".join(_q(c) for c in rule.columns)
        year_sel = f", {rule.partition_key_expr} AS year" if partitioned else ""
        con.execute(f"CREATE OR REPLACE TEMP VIEW out_ok AS SELECT {sel}{year_sel} "
                    f"FROM {_q('out')} WHERE reject_reason IS NULL")
        con.execute(f"CREATE OR REPLACE TEMP VIEW out_rej AS SELECT * FROM {_q('out')} "
                    "WHERE reject_reason IS NOT NULL")
        n_ok = _count(con, "SELECT count(*) FROM out_ok")
        n_reject = _count(con, "SELECT count(*) FROM out_rej")
        reject_by_reason = {str(r[0]): int(str(r[1])) for r in con.execute(
            "SELECT reject_reason, count(*) FROM out_rej GROUP BY 1 ORDER BY 1").fetchall()}

        order_by = ", ".join(_q(g) for g in rule.grain)
        if partitioned:
            glob = str(tmp_table / "year=*" / "*.parquet")
            if n_ok:
                con.execute(f"COPY (SELECT * FROM out_ok ORDER BY {order_by}) TO '{tmp_table}' "
                            "(FORMAT PARQUET, PARTITION_BY (year), OVERWRITE_OR_IGNORE, "
                            "FILENAME_PATTERN 'part')")
        else:
            glob = str(tmp_table / "*.parquet")
            if n_ok:
                con.execute(f"COPY (SELECT * FROM out_ok ORDER BY {order_by}) "
                            f"TO '{tmp_table / 'part0.parquet'}' (FORMAT PARQUET)")
        if n_reject:
            con.execute(f"COPY (SELECT * FROM out_rej) TO '{tmp_table / '_reject'}' "
                        "(FORMAT PARQUET, PARTITION_BY (reject_reason), OVERWRITE_OR_IGNORE, "
                        "FILENAME_PATTERN 'part')")

        if n_ok:
            con.execute("CREATE OR REPLACE TEMP VIEW out_pq AS SELECT * FROM "
                        f"read_parquet('{glob}', hive_partitioning=true)")
            content_hash = _content_hash(con, glob)
        else:   # 전 행 reject — PARTITION_BY COPY 는 파일을 안 만들어 read_parquet 이 죽는다
            con.execute("CREATE OR REPLACE TEMP VIEW out_pq AS SELECT * FROM out_ok")
            content_hash = "0:empty"

        if partitioned and n_ok:
            years = con.execute("SELECT year, count(*) FROM out_pq GROUP BY 1 ORDER BY 1"
                                ).fetchall()
            part_dirs = [(f"year={y}", int(str(n)), tmp_table / f"year={y}") for y, n in years]
        else:
            part_dirs = [("whole", n_ok, tmp_table)]
        partition_hashes = {label: (_content_hash(con, str(pdir / "*.parquet")) if n else "0:empty")
                            for label, n, pdir in part_dirs}

        pinned_ids = {t: pb.build_id for t, pb in pinned.items()}
        ctx = gates.EquityGateContext(
            con=con, rule=rule, out_view="out_pq", reject_view="out_rej", pinned=pinned,
            n_out=n_ok, n_reject=n_reject, reject_by_reason=reject_by_reason, inputs=pinned_ids,
            partition_hashes=partition_hashes, baseline=baseline,
            previous=_previous_record(table_root),
            fixtures=gates.load_fixtures(rule.name, equity_root, fixtures_path),
            thresholds=dict(gate_thresholds or {}))
        results = gates.run_all(ctx)
        gate_dicts = [g.as_dict() for g in results]
        failed = [g for g in results if g.status is gates.GateStatus.FAIL]
        n_src = next((int(str(g.metrics.get("rhs", 0))) for g in results if g.name == "EG1"
                      and "rhs" in g.metrics), 0)

        if failed:
            report = equity_root / "_failed" / f"{bid}.json"
            _write_json(report, {
                "table": rule.name, "build_id": bid, "snapshot_id": "", "inputs": pinned_ids,
                "n_src": n_src, "n_rows": n_ok, "n_reject": n_reject,
                "n_reject_by_reason": reject_by_reason,
                "first_failed_gate": failed[0].name, "gates": gate_dicts})
            shutil.rmtree(tmp_root, ignore_errors=True)
            return BuildResult(BuildStatus.GATE_FAILED, rule.name, bid, pinned_ids, n_ok,
                               n_reject, content_hash, [], results, round(time.time() - t0, 1),
                               None, report)

        elapsed = round(time.time() - t0, 1)
        partitions: list[dict[str, object]] = []
        for label, n, pdir in part_dirs:
            meta: dict[str, object] = {
                "table": rule.name, "build_id": bid, "partition": label, "n_rows": n,
                "n_src": n_src, "n_dedup": 0, "n_reject": n_reject,
                "n_reject_by_reason": reject_by_reason, "inputs": pinned_ids, "snapshot_id": "",
                "rules_version": RULES_VERSION, "content_hash": content_hash,
                "partition_content_hash": partition_hashes[label],
                "lag_known_inputs": {t: pb.meta.get("lag_known") for t, pb in pinned.items()},
                "coverage_from": None, "memory_limit": memory_limit, "threads": int(threads),
                "elapsed_s": elapsed, "gates": gate_dicts}
            _write_json(pdir / "_meta.json", meta)
            partitions.append({"path": f"v={bid}" + ("" if label == "whole" else f"/{label}"),
                               "n_rows": n, "content_hash": partition_hashes[label]})
    finally:
        con.close()

    table_root.mkdir(parents=True, exist_ok=True)
    final_dir = table_root / f"v={bid}"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.move(str(tmp_table), str(final_dir))
    shutil.rmtree(tmp_root, ignore_errors=True)
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=bid, snapshot_id="", rules_version=RULES_VERSION,
        built_at_utc=datetime.now(UTC).isoformat(timespec="seconds"), n_rows=n_ok,
        content_hash=content_hash, partitions=partitions, gates=gate_dicts,
        inputs=pinned_ids), keep=keep)
    return BuildResult(BuildStatus.OK, rule.name, bid, pinned_ids, n_ok, n_reject, content_hash,
                       partitions, results, elapsed, final_dir, None)
