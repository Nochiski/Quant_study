"""stage 빌더 — 스냅샷 원장 → duckdb 변환 → 연도 파티션 parquet → 게이트 → MANIFEST 교체.

한 테이블의 흐름 (§2·§3·§5):
  ATTACH(스냅샷, READ_ONLY) → 원장 UNION 뷰 → 캐스팅·결측 판정·정규화 → 격리(reject)·접기(dedup)
  → tmp 에 parquet → 게이트 G0~G9 → 통과: v=<build_id> 이동 + MANIFEST.json 교체
                                   → 실패: tmp 폐기 + _failed/<build_id>.json
"""
from __future__ import annotations

import json
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import duckdb

from . import gates, manifest
from .rules import KIND_DATE_YMD8, KIND_NUMERIC, RULES_VERSION, ColumnRule, TableRule
from .snapshot import Snapshot

_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r" {2,}")


class BuildStatus(Enum):
    OK = "ok"
    GATE_FAILED = "gate_failed"


@dataclass(frozen=True)
class BuildResult:
    status: BuildStatus
    table: str
    build_id: str
    snapshot_id: str
    n_rows: int
    n_src: int
    n_dedup: int
    n_reject: int
    content_hash: str
    gates: list[gates.GateResult]
    elapsed_s: float
    out_dir: Path | None            # OK 일 때 v=<build_id>
    failed_report: Path | None      # GATE_FAILED 일 때 _failed/<build_id>.json

    @property
    def ok(self) -> bool:
        return self.status is BuildStatus.OK


def normalize_text(s: str) -> str:
    """§5 문자열 정규화 (D7 순서 고정): NFKC → 개행·탭 제거 → 연속공백 축약 → strip → 태그 제거."""
    t = unicodedata.normalize("NFKC", s)
    t = t.replace("\r", "").replace("\n", "").replace("\t", "")
    t = _SPACES_RE.sub(" ", t).strip()
    return _TAG_RE.sub("", t).strip()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"count query returned no row: {sql[:200]}")
    return int(str(row[0]))


def _signed(expr: str, policy: str) -> str:
    if policy == "abs":
        return f"ltrim({expr}, '+-')"
    if policy == "strip_plus":
        return f"ltrim({expr}, '+')"
    return expr


def _cast_expr(c: ColumnRule, raw: str) -> str:
    """원장 VARCHAR → stage 타입. 실패는 NULL (miss_kind 가 cast_failed 로 기록)."""
    if c.kind == KIND_DATE_YMD8:
        return f"TRY_CAST(try_strptime(nullif({raw}, ''), '%Y%m%d') AS DATE)"
    if c.kind == KIND_NUMERIC:
        num = _signed(f"replace({raw}, ',', '')", c.sign)
        val = f"TRY_CAST({num} AS {c.decimal_type})"
        if c.zero_is_missing:
            return f"CASE WHEN {raw} = '0' THEN NULL ELSE {val} END"
        return val
    return raw


def _miss_kind_expr(c: ColumnRule, raw: str, staged: str) -> str:
    zero = f"WHEN {raw} = '0' THEN 'ledger_zero' " if c.zero_is_missing else ""
    return (f"CASE WHEN {raw} IS NULL THEN 'ledger_null' WHEN {raw} = '' THEN 'ledger_blank' "
            f"WHEN {raw} = '-' THEN 'ledger_dash' {zero}"
            f"WHEN {staged} IS NULL THEN 'cast_failed' END")


def _source_columns(con: duckdb.DuckDBPyConnection, db: str, table: str) -> list[str]:
    rows = con.execute("SELECT column_name FROM duckdb_columns() WHERE database_name = ? "
                       "AND table_name = ? ORDER BY column_index", [db, table]).fetchall()
    if not rows:
        raise ValueError(f"source table not found in attached ledger: {db}.{table}")
    return [r[0] for r in rows]


def _load_norm_maps(con: duckdb.DuckDBPyConnection, rule: TableRule, src_view: str) -> None:
    """정규화 대상 텍스트 컬럼의 distinct 값만 파이썬으로 정규화해 임시 매핑표로 올린다."""
    for c in rule.columns:
        if not c.normalize_text:
            continue
        tbl = _q("norm__" + c.src)
        vals = [r[0] for r in con.execute(
            f"SELECT DISTINCT {_q(c.src)} FROM {src_view} WHERE {_q(c.src)} IS NOT NULL"
        ).fetchall()]
        con.execute(f"CREATE OR REPLACE TEMP TABLE {tbl} (raw VARCHAR, norm VARCHAR)")
        if vals:
            con.executemany(f"INSERT INTO {tbl} VALUES (?, ?)",
                            [(v, normalize_text(v)) for v in vals])


def _key_date(rule: TableRule) -> str:
    return next(c.name for c in rule.columns if c.key and c.kind == KIND_DATE_YMD8)


def _stage_sql(rule: TableRule, src_view: str, src_cols: list[str],
               year_lo: int, year_hi: int) -> str:
    sel: list[str] = []
    joins: list[str] = []
    for c in rule.columns:
        raw = f"s.{_q(c.src)}"
        if c.kind not in (KIND_NUMERIC, KIND_DATE_YMD8) and c.normalize_text:
            alias = f"nm_{c.src}"
            joins.append(f"LEFT JOIN {_q('norm__' + c.src)} {alias} ON {alias}.raw = {raw}")
            sel.append(f"coalesce({alias}.norm, {raw}) AS {_q(c.name)}")
        else:
            sel.append(f"{_cast_expr(c, raw)} AS {_q(c.name)}")
        if c.nonempty_flag:
            sel.append(f"({raw} IS NOT NULL AND {raw} <> '') AS {_q(c.nonempty_flag)}")
    if rule.observed_src:
        obs = f"CAST(TRY_CAST(s.{_q(rule.observed_src)} AS TIMESTAMP) + INTERVAL 9 HOUR AS DATE)"
        obs_raw = f", s.{_q(rule.observed_src)} AS s_observed_raw"
        order_obs = ", s_observed_raw"
    else:
        obs, obs_raw, order_obs = "CAST(NULL AS DATE)", "", ""
    payload = [c for c in src_cols if c not in rule.payload_exclude
               and not c.startswith("req_") and c not in ("row_hash", "dup_seq")]
    payload_hash = ("hash(concat_ws(chr(31), "
                    + ", ".join(f"coalesce(s.{_q(c)}, '')" for c in payload) + "))")
    key_date = _key_date(rule)
    key_text = [c.name for c in rule.columns if c.key and c.kind != KIND_DATE_YMD8]
    key_missing = " OR ".join(f"{_q(k)} IS NULL OR {_q(k)} = ''" for k in key_text) or "FALSE"
    mk_cols = rule.numeric_or_date_columns
    mk_struct = ", ".join(f"{_q(c.name)} := {_q('mk__' + c.name)}" for c in mk_cols)
    fail_list = ", ".join(f"CASE WHEN {_q('mk__' + c.name)} = 'cast_failed' THEN ['{c.name}'] "
                          f"ELSE []::VARCHAR[] END" for c in mk_cols)
    mk_sel = ", ".join(
        _miss_kind_expr(c, f"c.{_q('raw__' + c.src)}", f"c.{_q(c.name)}")
        + f" AS {_q('mk__' + c.name)}" for c in mk_cols)
    # 원장 컬럼은 raw__ 접두사로 분리 — duckdb 식별자는 대소문자 무시(LIST_SHRS = list_shrs)
    raw_sel = ", ".join(f"s.{_q(c)} AS {_q('raw__' + c)}" for c in src_cols)
    nk = ", ".join(_q(k) for k in rule.natural_key)
    part = rule.partition_expr.replace(rule.date_axis_src, "s." + _q(rule.date_axis_src))
    return f"""
WITH cast_ AS (
  SELECT {raw_sel}, s._src, {", ".join(sel)},
         {obs} AS observed_date,
         {part} AS year,
         {payload_hash} AS payload_hash{obs_raw}
  FROM {src_view} s {" ".join(joins)}
), mk AS (
  SELECT c.*, {mk_sel} FROM cast_ c
), flagged AS (
  SELECT m.*,
         flatten([{fail_list}]) AS _cast_fail_cols,
         struct_pack({mk_struct}) AS miss_kind,
         CASE WHEN {_q(key_date)} IS NULL THEN 'key_cast_failed'
              WHEN {key_missing} THEN 'key_missing'
              WHEN year({_q(key_date)}) NOT BETWEEN {year_lo} AND {year_hi} THEN 'out_of_range'
         END AS reject_reason
  FROM mk m
)
SELECT f.*,
       row_number() OVER (PARTITION BY {nk}, payload_hash, reject_reason
                          ORDER BY observed_date{order_obs}) AS rn,
       count(*) OVER (PARTITION BY {nk}, payload_hash, reject_reason) AS observed_n
FROM flagged f"""


def _output_columns(rule: TableRule) -> list[str]:
    cols = list(rule.natural_key)
    for c in rule.columns:
        if c.name not in cols:
            cols.append(c.name)
        if c.nonempty_flag:
            cols.append(c.nonempty_flag)
    return cols


def _content_hash(con: duckdb.DuckDBPyConnection, glob: str) -> str:
    row = con.execute(f"SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) "
                      f"FROM read_parquet('{glob}', hive_partitioning=true) t").fetchone()
    if row is None:
        raise RuntimeError(f"content hash query returned no row: {glob}")
    n, h = row
    return f"{int(str(n))}:{'0' if h is None else format(int(str(h)), 'x')}"


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _previous_g1(table_root: Path) -> dict[str, object] | None:
    builds = manifest.load(table_root / "MANIFEST.json").builds
    if not builds:
        return None
    for g in builds[-1].gates:
        if g.get("name") == "G1":
            m = g.get("metrics")
            return dict(m) if isinstance(m, dict) else None
    return None


def _attach(con: duckdb.DuckDBPyConnection, rule: TableRule, snap: Snapshot,
            extra: dict[str, Path]) -> set[str]:
    needed = {s.db for s in rule.sources}
    if rule.cross_check:
        needed.add(rule.cross_check.db)
    attached: set[str] = set()
    for db in sorted(needed):
        path = snap.files[db].path if db in snap.files else extra.get(db)
        if path is None:
            continue
        con.execute(f"ATTACH '{path}' AS {_q(db)} (TYPE sqlite, READ_ONLY)")
        attached.add(db)
    for s in rule.sources:
        if s.db not in attached:
            raise FileNotFoundError(f"source ledger not in snapshot/extra_ledgers: db={s.db} "
                                    f"table={s.table} snapshot={snap.snapshot_id}")
    return attached


def build_table(rule: TableRule, snap: Snapshot, stage_root: Path, build_id: str | None = None,
                extra_ledgers: dict[str, Path] | None = None, fixtures_path: Path | None = None,
                baseline_path: Path | None = None, gate_thresholds: dict[str, float] | None = None,
                memory_limit: str = "6GB", threads: int = 3) -> BuildResult:
    """테이블 1개를 스냅샷에서 빌드한다. 결과는 status 로, 예외는 버그·환경 오류에만."""
    t0 = time.time()
    bid = build_id or datetime.now(UTC).strftime("b_%Y%m%dT%H%M%S_%fZ")
    table_root = stage_root / rule.name
    tmp_root = stage_root / "_tmp" / bid
    tmp_table = tmp_root / rule.name
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_table.mkdir(parents=True)
    spill = stage_root / "_tmp" / "spill"
    spill.mkdir(parents=True, exist_ok=True)
    thresholds = {**gates.DEFAULT_THRESHOLDS, **(gate_thresholds or {})}
    now_year = datetime.now(UTC).year
    year_lo, year_hi = gates.YEAR_RANGE_OBSERVED[0], now_year + gates.YEAR_RANGE_OBSERVED[1]
    key_date = _key_date(rule)

    con = duckdb.connect()
    try:
        con.execute("LOAD sqlite")
        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET threads = {int(threads)}")
        con.execute(f"SET temp_directory = '{spill}'")
        attached = _attach(con, rule, snap, extra_ledgers or {})
        src_cols = _source_columns(con, rule.sources[0].db, rule.sources[0].table)
        union = " UNION ALL ".join(
            "SELECT " + ", ".join(f"CAST({_q(c)} AS VARCHAR) AS {_q(c)}" for c in src_cols)
            + f", '{s.src_tag}' AS _src FROM {_q(s.db)}.{_q(s.table)}" for s in rule.sources)
        con.execute(f"CREATE OR REPLACE TEMP VIEW src_all AS {union}")
        _load_norm_maps(con, rule, "src_all")
        con.execute("CREATE OR REPLACE TEMP TABLE stage_all AS "
                    + _stage_sql(rule, "src_all", src_cols, year_lo, year_hi))
        out_cols = ", ".join(_q(c) for c in _output_columns(rule))
        con.execute(f"""CREATE OR REPLACE TEMP VIEW stage_ok AS
            SELECT {out_cols}, {_q(key_date)} AS available_date, 'default' AS available_basis,
                   observed_date, observed_n, _src,
                   CASE WHEN len(_cast_fail_cols) > 0 THEN 'partial' ELSE 'ok' END AS _src_flag,
                   _cast_fail_cols, miss_kind, year
            FROM stage_all WHERE rn = 1 AND reject_reason IS NULL""")
        raw_cols = ", ".join(f"{_q('raw__' + c)} AS {_q(c)}" for c in src_cols)
        con.execute(f"""CREATE OR REPLACE TEMP VIEW stage_rej AS
            SELECT {raw_cols}, _src, 'G7' AS reject_gate, reject_reason
            FROM stage_all WHERE rn = 1 AND reject_reason IS NOT NULL""")
        n_src = _count(con, "SELECT count(*) FROM src_all")
        n_reject = _count(con, "SELECT count(*) FROM stage_rej")
        n_dedup = _count(con, "SELECT count(*) FROM stage_all WHERE rn > 1")

        nk = ", ".join(_q(k) for k in rule.natural_key)
        con.execute(f"COPY (SELECT * FROM stage_ok ORDER BY {nk}) TO '{tmp_table}' "
                    "(FORMAT PARQUET, PARTITION_BY (year), OVERWRITE_OR_IGNORE, "
                    "FILENAME_PATTERN 'part')")
        if n_reject:
            (tmp_table / "_reject").mkdir()
            rej = tmp_table / "_reject" / "part.parquet"
            con.execute(f"COPY (SELECT * FROM stage_rej) TO '{rej}' (FORMAT PARQUET)")
        glob = str(tmp_table / "year=*" / "*.parquet")
        con.execute(f"CREATE OR REPLACE TEMP VIEW stage_pq AS "
                    f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
        n_stage = _count(con, "SELECT count(*) FROM stage_pq")
        content_hash = _content_hash(con, glob)

        fpath = fixtures_path or (stage_root / "fixtures" / f"{rule.name}.json")
        fixtures = json.loads(fpath.read_text(encoding="utf-8")) if fpath.exists() else None
        bpath = baseline_path or (stage_root / "baseline.json")
        baseline = (json.loads(bpath.read_text(encoding="utf-8")).get(rule.name)
                    if bpath.exists() else None)
        cross_alias = None
        if rule.cross_check and rule.cross_check.db in attached:
            cross_alias = rule.cross_check.db
        ctx = gates.GateContext(
            con=con, rule=rule, src_view="src_all", stage_view="stage_pq", reject_view="stage_rej",
            n_src=n_src, n_dedup=n_dedup, n_reject=n_reject, n_stage=n_stage,
            thresholds=thresholds, fixtures=fixtures, baseline=baseline,
            previous_g1=_previous_g1(table_root), cross_alias=cross_alias, current_year=now_year)
        results = gates.run_all(ctx)
        gate_dicts = [g.as_dict() for g in results]
        failed = [g for g in results if g.status is gates.GateStatus.FAIL]

        if failed:
            report = stage_root / "_failed" / f"{bid}.json"
            _write_json(report, {"table": rule.name, "build_id": bid,
                                 "snapshot_id": snap.snapshot_id, "n_src": n_src,
                                 "n_stage": n_stage, "n_reject": n_reject, "gates": gate_dicts})
            shutil.rmtree(tmp_root, ignore_errors=True)
            return BuildResult(BuildStatus.GATE_FAILED, rule.name, bid, snap.snapshot_id, n_stage,
                               n_src, n_dedup, n_reject, content_hash, results,
                               round(time.time() - t0, 1), None, report)

        parts = con.execute("SELECT year, count(*) FROM stage_pq GROUP BY 1 ORDER BY 1").fetchall()
        src_files = [snap.files[s.db] for s in rule.sources if s.db in snap.files]
        g7 = next(g for g in results if g.name == "G7")
        partitions: list[dict[str, object]] = []
        for year, n in parts:
            meta: dict[str, object] = {
                "table": rule.name, "build_id": bid, "partition": f"year={year}",
                "n_rows": int(n), "n_src": n_src, "fanout": rule.fanout, "n_dedup": n_dedup,
                "n_reject": n_reject, "n_out_of_range": g7.metrics.get("n_out_of_range"),
                "src_bytes": sum(f.bytes for f in src_files),
                "src_mtime": max((f.src_mtime for f in src_files), default=0.0),
                "snapshot_id": snap.snapshot_id, "rules_version": RULES_VERSION,
                "coverage_from": None, "version_loss_upstream": rule.write_mode != "append_only",
                "observed_date_exempt": rule.observed_src is None, "rcept_map_miss": None,
                "lag_known": rule.lag_known, "content_hash": content_hash, "gates": gate_dicts}
            _write_json(tmp_table / f"year={year}" / "_meta.json", meta)
            partitions.append({"path": f"v={bid}/year={year}", "n_rows": int(n)})
    finally:
        con.close()

    table_root.mkdir(parents=True, exist_ok=True)
    final_dir = table_root / f"v={bid}"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.move(str(tmp_table), str(final_dir))
    shutil.rmtree(tmp_root, ignore_errors=True)
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=bid, snapshot_id=snap.snapshot_id, rules_version=RULES_VERSION,
        built_at_utc=datetime.now(UTC).isoformat(timespec="seconds"), n_rows=n_stage,
        content_hash=content_hash, partitions=partitions, gates=gate_dicts))
    return BuildResult(BuildStatus.OK, rule.name, bid, snap.snapshot_id, n_stage, n_src, n_dedup,
                       n_reject, content_hash, results, round(time.time() - t0, 1), final_dir, None)
