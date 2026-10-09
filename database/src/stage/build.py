"""stage 빌더 — 스냅샷 원장 → duckdb 변환 → 파티션 parquet → 게이트 → MANIFEST 교체.

한 테이블의 흐름 (§2·§3·§5):
  ATTACH(스냅샷, READ_ONLY) → 원장 UNION 뷰 → 캐스팅·결측 판정·정규화·파생 → 격리·접기
  → tmp 에 parquet → 게이트 G0~G9 → 통과: v=<build_id> 이동 + MANIFEST.json 교체
                                   → 실패: tmp 폐기 + _failed/<build_id>.json
파티션 클래스: date_axis·receipt_axis = `year=YYYY/` 하이브 디렉토리, whole = `part0.parquet` 하나.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb

from . import doc_prepass, fold, gates, manifest, parsers
from .model import (
    DATE_FORMATS,
    KIND_BOOL,
    KIND_NUMERIC,
    KIND_TEXT,
    RULES_VERSION,
    BlobSource,
    ColumnRule,
    TableRule,
)
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
    reused_from: str | None = None  # 아침 재사용 판(reuse.py)이면 하드링크한 저녁 판 build_id

    @property
    def ok(self) -> bool:
        return self.status is BuildStatus.OK


def normalize_text(s: str, strip_tags: bool = False) -> str:
    """§5 문자열 정규화(D7 순서): NFKC, 개행·탭 제거, 공백 축약, strip, 옵트인 태그 제거.

    태그 제거는 옵트인 — DART 서술 컬럼의 `<주1>` 각주는 내용이다(5단계 리뷰 DEFECT-E2).
    """
    t = unicodedata.normalize("NFKC", s)
    t = t.replace("\r", "").replace("\n", "").replace("\t", "")
    t = _SPACES_RE.sub(" ", t).strip()
    return _TAG_RE.sub("", t).strip() if strip_tags else t


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"count query returned no row: {sql[:200]}")
    return int(str(row[0]))


def _max_date(con: duckdb.DuckDBPyConnection, view: str, column: str) -> str | None:
    """`view.column` 의 최댓값을 ISO 날짜 문자열로. 열이 없거나 전 행 NULL 이면 None (C6 입력)."""
    cols = {str(d[0]) for d in con.execute(f"SELECT * FROM {view} LIMIT 0").description or ()}
    if column not in cols:
        return None
    row = con.execute(f"SELECT max({_q(column)}) FROM {view}").fetchone()
    v = None if row is None else row[0]
    return None if v is None else str(v)[:10]


def _signed(expr: str, policy: str) -> str:
    if policy == "abs":
        return f"ltrim({expr}, '+-')"
    if policy == "strip_plus":
        return f"ltrim({expr}, '+')"
    return expr


def _zero_pred(raw: str) -> str:
    """§5 '0' 결측 마커 — 원문 리터럴 '0'·'0.00'·'+0'·'00000000' 전부 (KIS 실측, 5단계 리뷰 K1)."""
    return f"regexp_matches({raw}, '^[+-]?0+(\\.0+)?$')"


def _cast_expr(c: ColumnRule, raw: str) -> str:
    """원장 VARCHAR → stage 타입. 실패는 NULL (miss_kind 가 cast_failed 로 기록)."""
    if c.kind in DATE_FORMATS:
        val = f"TRY_CAST(try_strptime(nullif({raw}, ''), '{DATE_FORMATS[c.kind]}') AS DATE)"
        if c.zero_is_missing:
            return f"CASE WHEN {_zero_pred(raw)} THEN NULL ELSE {val} END"
        return val
    if c.kind == KIND_BOOL:
        return f"TRY_CAST({raw} AS BOOLEAN)"
    if c.kind == KIND_NUMERIC:
        num = _signed(f"replace({raw}, ',', '')", c.sign)
        if c.unit_scale is not None:     # 캐스트 전에 곱한다 (§1 허용 변환 '단위 스케일')
            num = f"(TRY_CAST({num} AS DECIMAL(38,{c.scale})) * {c.unit_scale})"
        val = f"TRY_CAST({num} AS {c.decimal_type})"
        if c.zero_is_missing:
            return f"CASE WHEN {_zero_pred(raw)} THEN NULL ELSE {val} END"
        return val
    return raw


def _miss_kind_expr(c: ColumnRule, raw: str, staged: str) -> str:
    zero = f"WHEN {_zero_pred(raw)} THEN 'ledger_zero' " if c.zero_is_missing else ""
    # 비키 날짜: 캐스트됐지만 범위 밖이라 NULL 이 된 셀 = out_of_range (G7 행 격리형)
    oor = (f"WHEN {_cast_expr(c, raw)} IS NOT NULL AND {staged} IS NULL THEN 'out_of_range' "
           if c.kind in DATE_FORMATS and not c.key else "")
    return (f"CASE WHEN {raw} IS NULL THEN 'ledger_null' WHEN {raw} = '' THEN 'ledger_blank' "
            f"WHEN {raw} = '-' THEN 'ledger_dash' {zero}{oor}"
            f"WHEN {staged} IS NULL THEN 'cast_failed' END")


def _source_columns(con: duckdb.DuckDBPyConnection, db: str, table: str) -> list[str]:
    rows = con.execute("SELECT column_name FROM duckdb_columns() WHERE database_name = ? "
                       "AND table_name = ? ORDER BY column_index", [db, table]).fetchall()
    if not rows:
        raise ValueError(f"source table not found in attached ledger: {db}.{table}")
    return [r[0] for r in rows]


def _union_sql(con: duckdb.DuckDBPyConnection, rule: TableRule) -> tuple[list[str], str]:
    """원장 UNION 뷰 SQL. 컬럼 집합은 소스 순서의 합집합이고, 소스에 없는 컬럼은 NULL 패딩
    (elestock ∪ elestock_v1 — v1 에는 row_hash·dup_seq·req_corp_code 가 없다). 규칙 컬럼의
    실재는 G0 이 소스마다 검사한다."""
    per_src = {(s.db, s.table): _source_columns(con, s.db, s.table) for s in rule.sources}
    src_cols: list[str] = []
    for cols in per_src.values():
        src_cols.extend(c for c in cols if c not in src_cols)
    selects = []
    for s in rule.sources:
        have = set(per_src[(s.db, s.table)])
        sel = ", ".join(f"CAST({_q(c)} AS VARCHAR) AS {_q(c)}" if c in have
                        else f"CAST(NULL AS VARCHAR) AS {_q(c)}" for c in src_cols)
        selects.append(f"SELECT {sel}, '{s.src_tag}' AS _src FROM {_q(s.db)}.{_q(s.table)}")
    return src_cols, " UNION ALL ".join(selects)


def _load_norm_maps(con: duckdb.DuckDBPyConnection, rule: TableRule, src_view: str,
                    tmp_dir: Path) -> None:
    """정규화 대상 텍스트 컬럼의 distinct 값만 파이썬으로 정규화해 임시 매핑표로 올린다.

    적재는 JSON Lines → `read_json` (executemany 는 행마다 statement — §11 교훈 ⑥. disclosure
    report_nm 은 distinct 값이 수십만이다).
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for c in rule.columns:
        if not c.normalize_text:
            continue
        tbl = _q("norm__" + c.src)
        vals = [r[0] for r in con.execute(
            f"SELECT DISTINCT {_q(c.src)} FROM {src_view} WHERE {_q(c.src)} IS NOT NULL"
        ).fetchall()]
        con.execute(f"CREATE OR REPLACE TEMP TABLE {tbl} (raw VARCHAR, norm VARCHAR)")
        if not vals:
            continue
        jsonl = tmp_dir / f"norm__{c.src}.jsonl"
        with open(jsonl, "w", encoding="utf-8") as f:
            for v in vals:
                f.write(json.dumps({"raw": v, "norm": normalize_text(v, c.strip_tags)},
                                   ensure_ascii=False))
                f.write("\n")
        con.execute(f"INSERT INTO {tbl} SELECT raw, norm FROM read_json('{jsonl}', "
                    f"format='newline_delimited', columns={{'raw': 'VARCHAR', 'norm': 'VARCHAR'}})")


def _is_partitioned(rule: TableRule) -> bool:
    return rule.partition_class != "whole"


def _stage_sql(rule: TableRule, src_view: str, src_cols: list[str],
               year_lo: int, year_hi: int, content_lo: int, content_hi: int) -> str:
    sel: list[str] = []
    joins: list[str] = []
    for c in rule.columns:
        raw = f"s.{_q(c.src)}"
        if c.kind == KIND_TEXT and c.normalize_text:
            alias = f"nm_{c.src}"
            joins.append(f"LEFT JOIN {_q('norm__' + c.src)} {alias} ON {alias}.raw = {raw}")
            sel.append(f"coalesce({alias}.norm, {raw}) AS {_q(c.name)}")
        elif c.kind in DATE_FORMATS and not c.key:
            # 내용일 축 범위 [1990, 현재+40] 밖은 NULL 로 격리 (행은 유지 — G7 행 격리형)
            x = _cast_expr(c, raw)
            sel.append(f"CASE WHEN year({x}) BETWEEN {content_lo} AND {content_hi} THEN {x} END "
                       f"AS {_q(c.name)}")
        else:
            sel.append(f"{_cast_expr(c, raw)} AS {_q(c.name)}")
        if c.nonempty_flag:
            sel.append(f"({raw} IS NOT NULL AND {raw} <> '') AS {_q(c.nonempty_flag)}")
    for e in rule.extras:
        sel.append(f"{e.sql} AS {_q(e.name)}")
    if rule.observed_src:
        obs = f"CAST(TRY_CAST(s.{_q(rule.observed_src)} AS TIMESTAMP) + INTERVAL 9 HOUR AS DATE)"
        obs_raw = f", s.{_q(rule.observed_src)} AS s_observed_raw"
        order_obs = ", s_observed_raw"
    else:
        obs, obs_raw, order_obs = "CAST(NULL AS DATE)", "", ""
    if rule.payload_columns is not None:
        payload = list(rule.payload_columns)
    else:
        payload = [c for c in src_cols if c not in rule.payload_exclude
                   and not c.startswith("req_") and c not in ("row_hash", "dup_seq")]
    payload_hash = ("hash(concat_ws(chr(31), "
                    + ", ".join(f"coalesce(s.{_q(c)}, '')" for c in payload) + "))")
    key_cols = rule.key_columns
    key_null = " OR ".join(f"{_q(c.name)} IS NULL" for c in key_cols) or "FALSE"
    key_missing = " OR ".join(f"{_q(c.name)} = ''" for c in key_cols if not c.blank_is_value
                              if c.kind == KIND_TEXT) or "FALSE"
    required_null = " OR ".join(f"{_q(c.name)} IS NULL" for c in rule.columns
                                if c.required and not c.key) or "FALSE"
    if _is_partitioned(rule):
        if rule.partition_expr is None or rule.partition_src is None:
            raise ValueError(f"partitioned table needs partition_expr/partition_src: {rule.name}")
        part = rule.partition_expr.replace(rule.partition_src, "s." + _q(rule.partition_src))
        part_sel = f", {part} AS year"
        out_of_range = (f"WHEN TRY_CAST(year AS INTEGER) IS NULL "
                        f"OR TRY_CAST(year AS INTEGER) NOT BETWEEN {year_lo} AND {year_hi} "
                        f"THEN 'out_of_range'")
    else:
        part_sel, out_of_range = "", ""
    mk_cols = rule.castable_columns
    mk_struct = ", ".join(f"{_q(c.name)} := {_q('mk__' + c.name)}" for c in mk_cols)
    # 캐스팅 대상 컬럼이 0개인 테이블(ws_coverage 등)은 빈 struct_pack() 이 duckdb 오류 —
    # 스키마 통일을 위해 자리표시 필드 하나의 NULL STRUCT 를 낸다.
    mk_expr = (f"struct_pack({mk_struct})" if mk_struct
               else 'CAST(NULL AS STRUCT("_none" VARCHAR))')
    fail_list = ", ".join(f"CASE WHEN {_q('mk__' + c.name)} = 'cast_failed' THEN ['{c.name}'] "
                          f"ELSE []::VARCHAR[] END" for c in mk_cols)
    mk_sel = ", ".join(
        _miss_kind_expr(c, f"c.{_q('raw__' + c.src)}", f"c.{_q(c.name)}")
        + f" AS {_q('mk__' + c.name)}" for c in mk_cols)
    # 원장 컬럼은 raw__ 접두사로 분리 — duckdb 식별자는 대소문자 무시(LIST_SHRS = list_shrs)
    raw_sel = ", ".join(f"s.{_q(c)} AS {_q('raw__' + c)}" for c in src_cols)
    nk = ", ".join(_q(k) for k in rule.natural_key)
    # 같은 날 판본 접기(2026-09-11): 판본 축은 observed_date(KST 날짜)라 같은 키가 하루에 페이로드가 다른
    # 관측을 두 번 가지면(예: DART 06:47 재스윕 + 18:05 저녁 스윕, 같은 날 정정) G6 이 표를 폐기했다.
    # 그날의 판 = **그날의 마지막 관측**(collected_at 최댓값)으로 접고 나머지는 n_dedup_same_day 로 센다.
    # 콜·유닛 로그(versioned=False)와 관측일이 없는 표는 접지 않는다 — 같은 키가 하루 여러 번이 정상이다.
    if rule.observed_src and getattr(rule, "versioned", True):
        rn_day_expr = (f"CASE WHEN r.rn = 1 THEN row_number() OVER ("
                       f"PARTITION BY {nk}, observed_date, reject_reason, (r.rn = 1) "
                       f"ORDER BY s_observed_raw DESC, payload_hash) ELSE 0 END")
    else:
        rn_day_expr = "1"
    return f"""
WITH cast_ AS (
  SELECT {raw_sel}, s._src, {", ".join(sel)},
         {obs} AS observed_date{part_sel},
         {payload_hash} AS payload_hash{obs_raw}
  FROM {src_view} s {" ".join(joins)}
), mk AS (
  SELECT c.*, {mk_sel} FROM cast_ c
), flagged AS (
  SELECT m.*,
         flatten([{fail_list}]) AS _cast_fail_cols,
         {mk_expr} AS miss_kind,
         CASE WHEN {key_null} THEN 'key_cast_failed'
              WHEN {key_missing} THEN 'key_missing'
              WHEN {required_null} THEN 'required_null'
              {out_of_range}
         END AS reject_reason
  FROM mk m
), ranked AS (
  SELECT f.*,
         row_number() OVER (PARTITION BY {nk}, payload_hash, reject_reason
                            ORDER BY observed_date{order_obs}) AS rn,
         count(*) OVER (PARTITION BY {nk}, payload_hash, reject_reason) AS observed_n
  FROM flagged f
)
SELECT r.*,
       {rn_day_expr} AS rn_day
FROM ranked r"""


def _output_columns(rule: TableRule) -> list[str]:
    cols = list(rule.natural_key)
    for c in rule.columns:
        if c.name not in cols:
            cols.append(c.name)
        if c.nonempty_flag:
            cols.append(c.nonempty_flag)
    cols.extend(e.name for e in rule.extras)
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


def _current_build_glob(stage_root: Path, table: str) -> str:
    """참조 stage 테이블의 현재 빌드 parquet glob. 없으면 빌드 순서 오류 — 예외."""
    m = manifest.load(stage_root / table / "MANIFEST.json")
    if m.current_build is None:
        raise FileNotFoundError(f"lookup table has no committed build — build it first: {table} "
                                f"(stage_root={stage_root})")
    return str(stage_root / table / f"v={m.current_build}" / "**" / "*.parquet")


def _recorded_metrics(con: duckdb.DuckDBPyConnection, rule: TableRule, view: str
                      ) -> dict[str, object]:
    """판정하지 않는 기록형 지표 — G3 metrics 에 실린다. 원천 품질이 움직이면 여기서 보인다."""
    a = rule.available
    if a.kind != "greatest_ymd8" or a.column is None or a.fallback_column is None:
        return {}
    col, pfx = _q(a.column), _ymd8_prefix_sql(a.fallback_column)
    # E08: 접두보다 과거인 행은 available 을 **뒤로 밀었고**(look-ahead 차단), 미래인 행은 그대로다.
    row = con.execute(f"SELECT count(*) FILTER (WHERE {col} < {pfx}), "
                      f"count(*) FILTER (WHERE {col} > {pfx}) FROM {view}").fetchone()
    if row is None:
        return {}
    return {"n_rcept_dt_before_no_prefix": int(str(row[0])),
            "n_rcept_dt_after_no_prefix": int(str(row[1]))}


def _ymd8_prefix_sql(column: str, alias: str | None = None) -> str:
    """텍스트 컬럼 앞 8자리를 DATE 로. 읽히지 않으면 NULL (E08 — 접수번호 접두 = 접수일)."""
    ref = _q(column) if alias is None else f"{alias}.{_q(column)}"
    return f"TRY_CAST(try_strptime(substr({ref}, 1, 8), '%Y%m%d') AS DATE)"


def _not_before_ymd8_sql(date_expr: str, text_column: str) -> str:
    """날짜와 텍스트 컬럼 앞 8자리(접수번호 = DART 규약상 접수일) 중 **늦은 쪽**.

    원천 날짜가 접수번호보다 과거면 그 판본은 접수번호 날짜부터 DART 에 있었으므로 원천 날짜를
    쓰면 look-ahead 다(E08 · J-41 박셀바이오 재제출본 최대 −654일). 미래 오타는 보수적이라 둔다.
    접두가 안 읽히면 날짜 그대로, 날짜가 NULL 이면 NULL 그대로다 — `greatest` 는 NULL 을 건너뛰어
    참조표 미스를 접두 날짜로 채워 버리므로 쓰지 않는다(§6 — rcept_no[:8] 폴백 폐기).
    텍스트 열은 원천 행 별칭 `a`(stage_ok 의 `FROM stage_all a`)로 한정한다 — 룩업 조인 `lk` 와
    이름이 겹쳐도 모호해지지 않게.
    """
    pfx = _ymd8_prefix_sql(text_column, alias="a")
    return f"CASE WHEN {pfx} > {date_expr} THEN {pfx} ELSE {date_expr} END"


def _available_sql(rule: TableRule, con: duckdb.DuckDBPyConnection,
                   stage_root: Path) -> tuple[str, str]:
    """(SELECT 절 조각, JOIN 절 조각). available_date·available_basis 두 컬럼을 낸다."""
    a = rule.available
    if a.kind == "column":
        col = _q(str(a.column))
        if a.fallback_column is None:
            return f"{col} AS available_date, '{a.basis}' AS available_basis", ""
        fb = _q(a.fallback_column)      # §6 v3 revision: collected_date NULL 행 → base_date/default
        basis = f"CASE WHEN {col} IS NULL THEN 'default' ELSE '{a.basis}' END"
        return f"COALESCE({col}, {fb}) AS available_date, {basis} AS available_basis", ""
    if a.kind == "greatest_ymd8":
        # 날짜 컬럼과 "앞 8자리가 YYYYMMDD 인 텍스트 컬럼" 중 **늦은 쪽**. 원천 날짜 오타가
        # available 을 앞당기는(=look-ahead) 방향을 막는다 (E08 — rules_dart.STG_DISCLOSURE).
        # 접두가 날짜로 안 읽히면 날짜 컬럼을 그대로 쓴다.
        if a.column is None or a.fallback_column is None:
            raise ValueError(f"greatest_ymd8 needs column and fallback_column: table={rule.name}")
        return ((f"{_not_before_ymd8_sql(f'a.{_q(a.column)}', a.fallback_column)} "
                 "AS available_date, "
                 f"'{a.basis}' AS available_basis"), "")
    if a.kind == "lookup":
        if not (a.table and a.local_key and a.lookup_key and a.lookup_value):
            raise ValueError(f"incomplete lookup rule: table={rule.name} available={a}")
        glob = _current_build_glob(stage_root, a.table)
        con.execute(f"CREATE OR REPLACE TEMP VIEW lk AS SELECT {_q(a.lookup_key)} AS k, "
                    f"{_q(a.lookup_value)} AS v FROM read_parquet('{glob}')")
        # J-41(N-26 4.2): 참조표 값(원천 rcept_dt)에도 stg_disclosure 와 같은 보정을 건다.
        v = ("lk.v" if a.fallback_column is None
             else _not_before_ymd8_sql("lk.v", a.fallback_column))
        sel = (f"{v} AS available_date, "
               "CASE WHEN lk.v IS NULL THEN 'unknown' ELSE 'derived' END AS available_basis")
        return sel, f"LEFT JOIN lk ON lk.k = a.{_q(a.local_key)}"
    return "CAST(NULL AS DATE) AS available_date, CAST(NULL AS VARCHAR) AS available_basis", ""


# B-01(플랜 2026-10-06 Task 3) — 회사 단위로 나눠 푸는 파서. 한 번에 풀면 파서가 전 행
# dict 를 쥐어 빌드 메모리가 수집일마다 는다(10-03 서버 stg_fin_wise 557만 행 · 최대 RSS
# 12.8GiB(13,375,880KiB), RAM 15.4GiB). `parsers._parse_fin` 은 (cmp_cd, fetched_date, ep,
# pkey, seq) 로 전역 정렬하므로, 회사 오름차순 묶음을 차례로 풀어 이어 쓰면 JSONL 이 한 번에
# 푼 것과 바이트까지 같다. 산출물 동일에는 필요 이상의 충분조건이다 — parquet 는 자연키
# ORDER BY 로 쓰고 content_hash 는 행 순서와 무관한 bit_xor 다.
CHUNKED_PARSERS: frozenset[str] = frozenset({"parse_fin_wise", "parse_fin_wise_q"})
# 묶음 크기 근거(10-06 측정): 파서 출력 1행 ≈ 2.2KB. 회사 1곳의 행은 수집일마다 stg_fin_wise
# ≈ 257행(557만 행 ÷ 867곳 ÷ 25일) · stg_fin_wise_q ≈ 740행(128만 행 ÷ 867곳 ÷ 2일)이다.
# 묶음 메모리는 전체 회사 수와 무관하고 수집일에 비례한다 — 1곳 기준 수집일마다 fin_wise
# ≈ 0.57MB · _q ≈ 1.6MB(20곳 묶음이면 _q 가 수집일마다 ≈ 32MB 씩, 250일이면 ≈ 8GB). 묶음마다
# 색인 조회 한 번이라 1·20·100곳 소요가 같아(로컬 실측) 가장 작은 1곳으로 둔다.
FIN_CHUNK_COMPANIES = 1


def _load_blob_source(con: duckdb.DuckDBPyConnection, rule: TableRule, tmp_dir: Path,
                      chunk_companies: int = FIN_CHUNK_COMPANIES
                      ) -> tuple[list[str], dict[str, object]]:
    """blob 원장을 파이썬 파서로 언네스트해 `src_all` 임시 테이블(전 컬럼 VARCHAR)로 올린다.

    적재는 JSON Lines 파일 → `read_json` 한 번. duckdb executemany 는 행마다 statement 를 돌려
    34만 행에 수십 분이 걸렸다(S3 서버 실측). JSON null 이 그대로 NULL 이라 ''/NULL 구분도 보존된다.
    `CHUNKED_PARSERS`(ws_raw 모양)는 회사 `chunk_companies` 곳씩 나눠 푼다(B-01).
    """
    bs = rule.blob_source
    if bs is None:
        raise ValueError(f"_load_blob_source called without blob_source: {rule.name}")
    parser = parsers.PARSERS[bs.parser]
    jsonl = tmp_dir / "src_all.jsonl"
    tally = fold.Tally() if bs.fold_consecutive else None      # §1 예외 (f) — 두 길 모두 같은 함수
    if bs.parser in CHUNKED_PARSERS and bs.select_sql is None:
        cols, metrics = _write_jsonl_by_company(con, bs, parser, jsonl, chunk_companies, tally)
    else:
        src_ref = f"{_q(bs.db)}.{_q(bs.table)}"
        if bs.select_sql is not None:                               # ws_raw 모양이 아닌 blob 원장(wics_raw)
            sql = bs.select_sql.replace("{src}", src_ref)
        else:
            eps = ", ".join(f"'{e}'" for e in bs.eps)
            sql = f"SELECT {_blob_columns(bs)} FROM {src_ref} WHERE ep IN ({eps})"
        blobs = [_raw_blob(r) for r in con.execute(sql).fetchall()]
        res = parser(blobs if tally is None else tally.fold(blobs))
        cols, metrics = list(res.columns), res.metrics
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in res.rows:
                f.write(json.dumps({c: r[c] for c in cols}, ensure_ascii=False))
                f.write("\n")
    if tally is not None or rule.morning_reuse:
        metrics.update(_ledger_metrics(con, rule, tally))
    schema = ", ".join(f"{_q(c)}: 'VARCHAR'" for c in cols)
    con.execute(f"CREATE OR REPLACE TEMP TABLE src_all AS SELECT *, '{bs.table}' AS _src "
                f"FROM read_json('{jsonl}', format='newline_delimited', columns={{{schema}}})")
    return cols, metrics


def _blob_columns(bs: BlobSource) -> str:
    """ws_raw 계약 SELECT 열. 접기 표는 원장 sha256 을 7번째로 더 읽는다."""
    return ("cmp_cd, ep, pkey, fetched_date, body, fetched_at"
            + (", sha256" if bs.fold_consecutive else ""))


def _raw_blob(r: tuple[Any, ...]) -> parsers.RawBlob:
    """`_blob_columns`(또는 select_sql 6열) 한 행 → RawBlob."""
    return parsers.RawBlob(str(r[0]), str(r[1]), str(r[2]), str(r[3]),
                           bytes(r[4]) if r[4] is not None else b"", str(r[5]),
                           str(r[6]) if len(r) > 6 else "")


def _attached_path(con: duckdb.DuckDBPyConnection, db: str, table: str) -> Path:
    found = con.execute("SELECT path FROM duckdb_databases() WHERE database_name = ?",
                        [db]).fetchone()
    if found is None or found[0] is None:
        raise FileNotFoundError(f"blob ledger is not attached: db={db} table={table}")
    return Path(found[0])


def _sqlite_ro(path: Path) -> sqlite3.Connection:
    """원장(스냅샷) sqlite 를 읽기 전용으로 연다.

    날 경로를 URI 에 넣으면 `#`·`?` 에서 잘리고 mode=ro 가 떨어져 rwc 로 열린다 — as_uri() 가
    `#`·`?`·`%` 를 퍼센트 인코딩한다. 상대 경로는 ATTACH 와 같이 cwd 기준이다(운영 QL_HOME).
    """
    return sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)


def _ledger_metrics(con: duckdb.DuckDBPyConnection, rule: TableRule,
                    tally: fold.Tally | None) -> dict[str, object]:
    """파서 지표 뒤에 싣는 원장 쪽 지표 — G8 이 판정한다(§9).

    `n_ledger_blobs` 는 접기 함수가 아니라 sqlite 에서 따로 센 `ep IN eps` 원장 행 수다 — 같은 함수로 세면
    G8 등식 `n_ledger_blobs = Σn_blobs + n_skipped_pkey + Σn_folded` 가 항진명제가 된다.
    `input_fingerprint` 는 아침 재사용(7-3) 판정 ⑤ 의 저녁 쪽 기록이다.
    """
    bs = rule.blob_source
    if bs is None:
        raise ValueError(f"_ledger_metrics called without blob_source: {rule.name}")
    lite = _sqlite_ro(_attached_path(con, bs.db, bs.table))
    try:
        out: dict[str, object] = {}
        if tally is not None:
            eps = list(bs.eps)
            row = lite.execute(f"SELECT count(*) FROM {_q(bs.table)} "
                               f"WHERE ep IN ({', '.join('?' * len(eps))})", eps).fetchone()
            out["n_ledger_blobs"] = int(row[0]) if row else 0
            out.update(tally.metrics())
        if rule.morning_reuse:
            out["input_fingerprint"] = fold.input_fingerprint(lite, bs)
        return out
    finally:
        lite.close()


def _write_jsonl_by_company(con: duckdb.DuckDBPyConnection, bs: BlobSource,
                            parser: Callable[[Iterable[parsers.RawBlob]], parsers.ParseResult],
                            jsonl: Path, chunk_companies: int, tally: fold.Tally | None = None
                            ) -> tuple[list[str], dict[str, object]]:
    """B-01 — ws_raw 를 회사 `chunk_companies` 곳씩 읽어 파싱하고 그 행을 JSONL 에 바로 쓴 뒤
    버린다.

    - 회사 목록은 파이썬 `sorted` 로 정렬한다 — 파서의 str 정렬과 같아야 이어 쓴 행이 전역
      정렬과 같다.
    - 블롭은 ATTACH 된 sqlite 파일을 sqlite3 읽기 전용으로 직접 읽는다. duckdb sqlite 스캐너는
      WHERE 를 sqlite 로 넘기지 않아(1.5.5 실측 — sqlite 가 받는 질의는 `WHERE ROWID BETWEEN`
      뿐) 묶음마다 표 전체(다른 ep 본문까지)를 훑는다. 직접 읽으면 키 색인을 탄다.
    - pkey 는 SQL 에서 거르지 않는다 — 파서가 건너뛰며 세는 n_skipped_pkey 가 같아야 한다.
    - metrics 는 정수는 더하고 dict(ep 별 n_blobs)는 키별로 더한다. 블롭이 없으면 parser([])
      의 열·0 이다.
    - `tally` 가 있으면(접기 표) 묶음마다 파서 전에 연속 판을 접는다 — 단위가 회사 안이라 묶음 크기 무관.
    """
    if chunk_companies < 1:
        raise ValueError(f"chunk_companies must be >= 1: got {chunk_companies} parser={bs.parser}")
    path = _attached_path(con, bs.db, bs.table)
    empty = parser([])
    cols, metrics = list(empty.columns), empty.metrics
    table = _q(bs.table)
    eps = list(bs.eps)
    ep_in = ", ".join("?" * len(eps))
    lite = _sqlite_ro(path)
    try:
        cmps = sorted(str(r[0]) for r in lite.execute(
            f"SELECT DISTINCT cmp_cd FROM {table} WHERE ep IN ({ep_in})", eps))
        with open(jsonl, "w", encoding="utf-8") as f:
            for i in range(0, len(cmps), chunk_companies):
                part = cmps[i:i + chunk_companies]
                cur = lite.execute(
                    f"SELECT {_blob_columns(bs)} FROM {table} "
                    f"WHERE cmp_cd IN ({', '.join('?' * len(part))}) AND ep IN ({ep_in})",
                    part + eps)
                blobs = [_raw_blob(r) for r in cur]
                res = parser(blobs if tally is None else tally.fold(blobs))
                for r in res.rows:
                    f.write(json.dumps({c: r[c] for c in cols}, ensure_ascii=False))
                    f.write("\n")
                for k, v in res.metrics.items():
                    if k not in metrics:
                        raise RuntimeError(
                            f"parse metric missing from parser([]) — key={k!r} value={v!r} "
                            f"parser={bs.parser} known={sorted(metrics)} "
                            f"cmp_cd={part[0]}..{part[-1]}")
                    acc = metrics[k]
                    if isinstance(v, dict) and isinstance(acc, dict):
                        for kk, n in v.items():
                            acc[kk] = acc.get(kk, 0) + n
                    elif isinstance(v, int) and isinstance(acc, int):
                        metrics[k] = acc + v
                    else:
                        raise TypeError(
                            f"cannot sum parse metric — key={k!r} acc={acc!r} new={v!r} "
                            f"parser={bs.parser} cmp_cd={part[0]}..{part[-1]}")
                del res          # 다음 묶음 전에 놓는다 — 두 묶음의 행이 겹쳐 살지 않게
    finally:
        lite.close()
    return cols, metrics


def _load_file_source(con: duckdb.DuckDBPyConnection, rule: TableRule, snap: Snapshot,
                      stage_root: Path) -> tuple[list[str], dict[str, object]]:
    """doc_prepass 캐시(JSONL) → `src_all`. 캐시가 없으면 프리패스를 먼저 돌리라는 예외."""
    fs = rule.file_source
    if fs is None:
        raise ValueError(f"_load_file_source called without file_source: {rule.name}")
    cache = stage_root / "_tmp" / "doc" / snap.snapshot_id
    summary_path = cache / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(
            f"doc prepass cache not found — run `python -m stage.doc_prepass --snapshot-id "
            f"{snap.snapshot_id}` first: expected {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "ok":
        raise RuntimeError(f"doc prepass gate_failed for snapshot={snap.snapshot_id}: "
                           f"{summary.get('detail')}")
    # F03 — 캐시는 디렉터리 이름이 아니라 문서 집합으로 본다. 옛 캐시를
    # 새 스냅샷 id 로 복사·하드링크해도 조용히 통과하던 구멍을 막는다.
    want = doc_prepass.input_hash_for(snap.files[rule.sources[0].db].path)
    if summary.get("input_hash") != want:
        raise RuntimeError(
            f"doc prepass cache is for a different document set — snapshot={snap.snapshot_id} "
            f"table={rule.name} cache_input_hash={summary.get('input_hash')} "
            f"snapshot_input_hash={want}: re-run `python -m stage.doc_prepass --snapshot-id "
            f"{snap.snapshot_id} [--base-snapshot <prev>]` (cache {cache})")
    glob = str(cache / fs.table / "year=*.jsonl")
    schema = ", ".join(f"{_q(c)}: 'VARCHAR'" for c in fs.columns)
    con.execute(f"CREATE OR REPLACE TEMP TABLE src_all AS SELECT *, 'doc_zip' AS _src "
                f"FROM read_json('{glob}', format='newline_delimited', columns={{{schema}}}, "
                f"union_by_name=false)")
    emitted = int(summary["tables"].get(fs.table, 0))
    return list(fs.columns), {"n_rows_emitted": emitted, "n_parse_failed": 0,
                              "n_docs": summary.get("n_docs"), "modes": summary.get("modes"),
                              "input_hash": summary.get("input_hash")}     # D5 — _meta.json


def _baseline_entry(rule: TableRule, stage_root: Path, baseline_path: Path | None) -> object:
    """baseline.json 의 그 표 항목(§9) — 없으면 None."""
    bpath = baseline_path or (stage_root / "baseline.json")
    return (json.loads(bpath.read_text(encoding="utf-8")).get(rule.name)
            if bpath.exists() else None)


def _fixtures_file(rule: TableRule, stage_root: Path, fixtures_path: Path | None) -> Path:
    return fixtures_path or (stage_root / "fixtures" / f"{rule.name}.json")


def build_inputs(rule: TableRule, stage_root: Path, fixtures_path: Path | None,
                 baseline_path: Path | None, year: int) -> dict[str, object]:
    """원장·코드 밖에서 산출을 정하는 빌드 입력 — baseline.json 의 그 표 항목·골든 픽스처·연도(G7 범위가
    빌드 시각의 UTC 연도에 묶인다). `_meta.json` 에 싣고, 아침 재사용 판정 ⑥(reuse.py)이 같은 함수로
    다시 계산해 비교한다."""
    fpath = _fixtures_file(rule, stage_root, fixtures_path)
    entry = json.dumps(_baseline_entry(rule, stage_root, baseline_path), sort_keys=True,
                       ensure_ascii=False, default=str)
    return {"baseline_sha256": hashlib.sha256(entry.encode("utf-8")).hexdigest(),
            "fixtures_sha256": (hashlib.sha256(fpath.read_bytes()).hexdigest()
                                if fpath.exists() else None),
            "year_utc": year}


def build_table(rule: TableRule, snap: Snapshot, stage_root: Path, build_id: str | None = None,
                extra_ledgers: dict[str, Path] | None = None, fixtures_path: Path | None = None,
                baseline_path: Path | None = None, gate_thresholds: dict[str, float] | None = None,
                memory_limit: str = "6GB", threads: int = 3,
                code_rev: str | None = None) -> BuildResult:
    """테이블 1개를 스냅샷에서 빌드한다. 결과는 status 로, 예외는 버그·환경 오류에만.

    `code_rev` 는 배포 rev(`$QL_HOME/DEPLOYED.json`) — MANIFEST 에 기록만 한다(아침 재사용 판정 ④ 입력).
    """
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
    # 게이트 임계: 기본 ← baseline.json 의 테이블별 thresholds (§9) ← CLI override
    baseline = _baseline_entry(rule, stage_root, baseline_path)
    base_thr = baseline.get("thresholds", {}) if isinstance(baseline, dict) else {}
    thresholds = {**gates.DEFAULT_THRESHOLDS, **base_thr, **(gate_thresholds or {})}
    now_year = datetime.now(UTC).year
    year_lo, year_hi = gates.YEAR_RANGE_OBSERVED[0], now_year + gates.YEAR_RANGE_OBSERVED[1]
    content_lo, content_hi = gates.YEAR_RANGE_CONTENT[0], now_year + gates.YEAR_RANGE_CONTENT[1]
    partitioned = _is_partitioned(rule)

    con = duckdb.connect()
    try:
        con.execute("LOAD sqlite")
        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET threads = {int(threads)}")
        con.execute(f"SET temp_directory = '{spill}'")
        attached = _attach(con, rule, snap, extra_ledgers or {})
        avail_sel, avail_join = _available_sql(rule, con, stage_root)   # 참조표 부재는 여기서 예외
        parse_metrics: dict[str, object] | None = None
        if rule.file_source is not None:
            src_cols, parse_metrics = _load_file_source(con, rule, snap, stage_root)
        elif rule.blob_source is not None:
            src_cols, parse_metrics = _load_blob_source(con, rule, tmp_root)
        else:
            src_cols, union = _union_sql(con, rule)
            con.execute(f"CREATE OR REPLACE TEMP VIEW src_all AS {union}")
        _load_norm_maps(con, rule, "src_all", tmp_root)
        con.execute("CREATE OR REPLACE TEMP TABLE stage_all AS "
                    + _stage_sql(rule, "src_all", src_cols, year_lo, year_hi,
                                 content_lo, content_hi))
        out_cols = ", ".join(f"a.{_q(c)}" for c in _output_columns(rule))
        year_sel = ", a.year" if partitioned else ""
        con.execute(f"""CREATE OR REPLACE TEMP VIEW stage_ok AS
            SELECT {out_cols}, {avail_sel}, a.observed_date, a.observed_n, a._src,
                   CASE WHEN len(a._cast_fail_cols) > 0 THEN 'partial' ELSE 'ok' END AS _src_flag,
                   a._cast_fail_cols, a.miss_kind{year_sel}
            FROM stage_all a {avail_join}
            WHERE a.rn = 1 AND a.rn_day <= 1 AND a.reject_reason IS NULL""")
        raw_cols = ", ".join(f"{_q('raw__' + c)} AS {_q(c)}" for c in src_cols)
        con.execute(f"""CREATE OR REPLACE TEMP VIEW stage_rej AS
            SELECT {raw_cols}, _src, 'G7' AS reject_gate, reject_reason
            FROM stage_all WHERE rn = 1 AND rn_day <= 1 AND reject_reason IS NOT NULL""")
        n_src = _count(con, "SELECT count(*) FROM src_all")
        n_reject = _count(con, "SELECT count(*) FROM stage_rej")
        n_dedup_same_day = _count(con, "SELECT count(*) FROM stage_all WHERE rn = 1 AND rn_day > 1")
        n_dedup = _count(con, "SELECT count(*) FROM stage_all WHERE rn > 1") + n_dedup_same_day
        lookup_miss = None
        if rule.available.kind == "lookup":
            lookup_miss = _count(con, "SELECT count(*) FROM stage_ok "
                                      "WHERE available_basis = 'unknown'")

        nk = ", ".join(_q(k) for k in rule.natural_key)
        n_ok = _count(con, "SELECT count(*) FROM stage_ok")
        if partitioned:
            glob = str(tmp_table / "year=*" / "*.parquet")
            if n_ok:
                con.execute(f"COPY (SELECT * FROM stage_ok ORDER BY {nk}) TO '{tmp_table}' "
                            "(FORMAT PARQUET, PARTITION_BY (year), OVERWRITE_OR_IGNORE, "
                            "FILENAME_PATTERN 'part')")
        else:
            glob = str(tmp_table / "*.parquet")
            if n_ok:
                con.execute(f"COPY (SELECT * FROM stage_ok ORDER BY {nk}) "
                            f"TO '{tmp_table / 'part0.parquet'}' (FORMAT PARQUET)")
        if n_reject:
            (tmp_table / "_reject").mkdir()
            rej = tmp_table / "_reject" / "part.parquet"
            con.execute(f"COPY (SELECT * FROM stage_rej) TO '{rej}' (FORMAT PARQUET)")
        if n_ok:
            con.execute(f"CREATE OR REPLACE TEMP VIEW stage_pq AS "
                        f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
            content_hash = _content_hash(con, glob)
        else:   # 전 행 reject — PARTITION_BY COPY 는 파일을 안 만들어 read_parquet 이 죽는다
            con.execute("CREATE OR REPLACE TEMP VIEW stage_pq AS SELECT * FROM stage_ok")
            content_hash = "0:empty"
        n_stage = _count(con, "SELECT count(*) FROM stage_pq")
        # C6 신선도 입력 (DEFECT-B01): 이 판이 담은 최신 사실·최신 관측의 날짜. 열이 없거나 전 행
        # NULL(available 비부여 표)이면 None 을 싣고 C6 가 사유와 함께 건너뛴다.
        max_available_date = _max_date(con, "stage_pq", "available_date")
        max_observed_date = _max_date(con, "stage_pq", "observed_date")
        recorded = _recorded_metrics(con, rule, "stage_pq")

        fpath = _fixtures_file(rule, stage_root, fixtures_path)
        fixtures = json.loads(fpath.read_text(encoding="utf-8")) if fpath.exists() else None
        cross_alias = None
        if rule.cross_check and rule.cross_check.db in attached:
            cross_alias = rule.cross_check.db
        ctx = gates.GateContext(
            con=con, rule=rule, src_view="src_all", stage_view="stage_pq", reject_view="stage_rej",
            n_src=n_src, n_dedup=n_dedup, n_dedup_same_day=n_dedup_same_day, n_reject=n_reject, n_stage=n_stage,
            thresholds=thresholds, fixtures=fixtures, baseline=baseline,
            previous_g1=_previous_g1(table_root), cross_alias=cross_alias, current_year=now_year,
            lookup_miss=lookup_miss, parse_metrics=parse_metrics,
            recorded_metrics=recorded)
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

        if partitioned:
            parts = con.execute("SELECT year, count(*) FROM stage_pq GROUP BY 1 ORDER BY 1"
                                ).fetchall()
            part_dirs = [(f"year={y}", int(n), tmp_table / f"year={y}") for y, n in parts]
        else:
            part_dirs = [("", n_stage, tmp_table)]
        src_files = [snap.files[s.db] for s in rule.sources if s.db in snap.files]
        inputs = build_inputs(rule, stage_root, fixtures_path, baseline_path, now_year)
        g7 = next(g for g in results if g.name == "G7")
        partitions: list[dict[str, object]] = []
        for label, n, pdir in part_dirs:
            meta: dict[str, object] = {
                "table": rule.name, "build_id": bid, "partition": label or "whole",
                "n_rows": n, "n_src": n_src, "fanout": rule.fanout, "n_dedup": n_dedup,
                "n_dedup_same_day": n_dedup_same_day,
                "n_reject": n_reject, "n_out_of_range": g7.metrics.get("n_out_of_range"),
                "src_bytes": sum(f.bytes for f in src_files),
                "src_mtime": max((f.src_mtime for f in src_files), default=0.0),
                "snapshot_id": snap.snapshot_id, "rules_version": RULES_VERSION,
                "coverage_from": rule.coverage_from,
                "version_loss_upstream": rule.write_mode != "append_only",
                "observed_date_exempt": rule.observed_src is None, "rcept_map_miss": lookup_miss,
                "lag_known": rule.lag_known, "content_hash": content_hash,
                "doc_input_hash": (parse_metrics or {}).get("input_hash"),   # D5 (문서층만)
                "build_inputs": inputs,                                       # 묶음 7-3 ⑥
                "gates": gate_dicts}
            _write_json(pdir / "_meta.json", meta)
            partitions.append({"path": f"v={bid}" + (f"/{label}" if label else ""), "n_rows": n})
    finally:
        con.close()

    table_root.mkdir(parents=True, exist_ok=True)
    final_dir = table_root / f"v={bid}"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.move(str(tmp_table), str(final_dir))
    shutil.rmtree(tmp_root, ignore_errors=True)
    fingerprint = (parse_metrics or {}).get("input_fingerprint")
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=bid, snapshot_id=snap.snapshot_id, rules_version=RULES_VERSION,
        built_at_utc=datetime.now(UTC).isoformat(timespec="seconds"), n_rows=n_stage,
        content_hash=content_hash, partitions=partitions, gates=gate_dicts,
        max_available_date=max_available_date, max_observed_date=max_observed_date,
        input_fingerprint=fingerprint if isinstance(fingerprint, str) else None,
        code_rev=code_rev))
    return BuildResult(BuildStatus.OK, rule.name, bid, snap.snapshot_id, n_stage, n_src, n_dedup,
                       n_reject, content_hash, results, round(time.time() - t0, 1), final_dir, None)
