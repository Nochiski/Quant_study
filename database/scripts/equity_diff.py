"""equity 표 두 판(build)의 **전 컬럼 diff** — 규칙 변경이 무엇을 바꿨는지 숫자로 못박는다 (T-G).

플랜 `docs/plans/2026-09-26-layer-fixes.md` §11. 지난 사흘 동안 오케스트레이터가 손으로 짜던
ad-hoc DuckDB 스크립트(두 판을 grain 으로 FULL OUTER JOIN → 컬럼별 값→NULL·NULL→값·값→다른 값)를
저장소 도구로 굳힌 것이다. 그 손작업이 실제 회귀를 세 번 잡았다(e1.19.0 의 `ppe_parts` 부호 상계
53행 · `cf_operating_ytd` 2행 값→NULL 퇴행 · 이름 정규화가 만든 모호).

사용 (로컬):
  uv run --project backend python database/scripts/equity_diff.py \
      --root <equity 루트> --table fin_std --before <bid> --after <bid> \
      --expect database/docs/equity_diff/expect_fa4_example.json --gate
사용 (서버, `$QL_HOME` 에서 — expect 파일은 docs/ 가 배포되지 않으므로 scp 로 올린다):
  PYTHONPATH=src .venv/bin/python scripts/equity_diff.py --table fin_std --gate
  PYTHONPATH=src .venv/bin/python scripts/equity_diff.py --table fin_std \
      --determinism <build_a> <build_b>

판정 축(`--gate`):
  · **미설명** 값→NULL > 0        — 있던 값이 사라지는 것은 규칙 개선이 아니라 회귀다
  · **미설명** 행 삭제 > 0        — PIT 표에서 행이 사라지면 과거 백테스트가 조용히 달라진다
  · **미설명** available_date 변경 > 0 — 공개시점 축이 움직이면 look-ahead 판정 자체가 흔들린다
  미설명 값변경·NULL→값은 **보고만** 한다(규칙 개선의 정상 산출이 대부분 이 두 갈래다).
  `--expect` 로 「예상한 변경」을 선언하면 그만큼이 explained 로 빠진다.

새 의존성 없음 — duckdb(이미 서버 venv 에 있다) + 표준 라이브러리. YAML expect 는 PyYAML 이
있을 때만 읽고(로컬 backend venv 에 있다), 없으면 JSON 을 쓰라고 분명히 거절한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb

# ── 규약 상수 ────────────────────────────────────────────────────────────────
KINDS_COLUMN: tuple[str, ...] = ("value_to_null", "null_to_value", "value_changed")
KINDS_ROW: tuple[str, ...] = ("rows_added", "rows_removed")
KINDS: tuple[str, ...] = KINDS_COLUMN + KINDS_ROW
ROW_COLUMN = "*"                       # 행 단위 카운터의 컬럼 자리
DEFAULT_TOL = 1e-9                     # 상대 허용오차(수치 컬럼 전용)
MAX_EXAMPLES = 3
MARK = "_ql_row"                       # 조인 양쪽의 존재 표지
IN_AFTER, IN_BEFORE = "_ql_in_after", "_ql_in_before"
BEFORE_PREFIX, KEY_PREFIX = "before_", "k_"
REJECT_DIR = "_reject"                 # 격리 행은 산출이 아니다 — 비교에서 뺀다
# 운영 메타 — 판마다 반드시 달라지므로 비교하면 전 컬럼이 changed 로 나온다.
META_EXACT = frozenset({"v", "build_id", "snapshot_id", "generated_at", "built_at_utc",
                        "generated_at_utc", "elapsed_s"})
META_SUFFIX = ("_build", "_build_id", "_generated_at", "_at_utc")
_NUMERIC_RE = re.compile(r"^(DECIMAL|DOUBLE|FLOAT|REAL|NUMERIC|[US]?(BIG|HUGE|SMALL|TINY)?INT)")


class UsageError(Exception):
    """사용법·데이터 위생 오류 — rc 1."""


@dataclass(frozen=True)
class ExpectEntry:
    """예상 변경 선언 1건. `where` 는 after 행 컬럼 위의 SQL 술어(before 값은 `before_<컬럼>`)."""

    column: str
    kind: str
    where: str | None
    note: str

    def as_dict(self) -> dict[str, object]:
        return {"column": self.column, "kind": self.kind, "where": self.where, "note": self.note}


# ── 선언 읽기 (grain) ────────────────────────────────────────────────────────
def declared_table(table: str, *, required: bool):
    """`EquityTable` 선언. import 가 안 되면 required 일 때만 거절하고 아니면 None."""
    src = Path(__file__).resolve().parents[1] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        import importlib
        import pkgutil

        import equity
        from equity.model import RULES
        # `rules_s*` 는 import 부작용으로 RULES 에 등록한다 — 목록을 여기 박지 않고 훑는다.
        for mod in pkgutil.iter_modules(equity.__path__):
            if mod.name.startswith("rules_"):
                importlib.import_module(f"equity.{mod.name}")
    except ImportError as e:                                  # duckdb·stage 없는 환경
        if required:
            raise UsageError(f"equity 선언을 import 할 수 없다 — --key 로 grain 을 직접 "
                             f"넘긴다: {e}") from e
        return None
    if table not in RULES:
        if required:
            raise UsageError(f"선언에 없는 표다 — table={table} known={sorted(RULES)}")
        return None
    return RULES[table]


def declared_grain(table: str) -> tuple[str, ...]:
    """표의 grain(PK)을 규칙 모듈 선언에서 읽는다. `--key` 가 없을 때의 정본이다."""
    return tuple(declared_table(table, required=True).grain)


# ── MANIFEST · 디스크 ────────────────────────────────────────────────────────
def load_manifest(table_root: Path) -> dict:
    """MANIFEST.json 원문. `stage.manifest` 를 통하지 않는다 — 이 스크립트는 읽기 전용이다."""
    path = table_root / "MANIFEST.json"
    if not path.exists():
        raise UsageError(f"MANIFEST 가 없다 — {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UsageError(f"MANIFEST 를 읽지 못했다 — {path}: {e}") from e


def build_files(table_root: Path, build_id: str) -> list[Path]:
    """`v=<build_id>/**/*.parquet` 실물 목록. `_reject/` 는 뺀다."""
    vdir = table_root / f"v={build_id}"
    if not vdir.is_dir():
        raise UsageError(f"디스크에 없는 판이다(GC 되었을 수 있다) — {vdir}")
    files = [p for p in sorted(vdir.rglob("*.parquet"))
             if REJECT_DIR not in p.relative_to(vdir).parts]
    if not files:
        raise UsageError(f"판에 parquet 이 없다 — {vdir}")
    return files


def hive_keys(files: list[Path], table_root: Path) -> set[str]:
    """경로에서 온 하이브 컬럼 이름(`v`·`year` …). 선언 컬럼이 아니면 비교에서 뺀다."""
    out: set[str] = set()
    for f in files:
        for seg in f.relative_to(table_root).parts[:-1]:
            if "=" in seg:
                out.add(seg.split("=", 1)[0])
    return out


def resolve_builds(mf: dict, table_root: Path, before: str | None, after: str | None
                   ) -> tuple[str, str]:
    """기본값: after = current_build, before = MANIFEST 순서상 그 직전이며 **디스크에 남은** 판."""
    order = [str(b["build_id"]) for b in mf.get("builds", [])]
    after = after or str(mf.get("current_build") or "")
    if not after:
        raise UsageError(f"current_build 가 비었다 — {table_root / 'MANIFEST.json'}")
    if before is None:
        if after not in order:
            raise UsageError(f"after 판이 MANIFEST 에 없어 직전 판을 고를 수 없다 — after={after} "
                             f"manifest={order}")
        prior = [b for b in order[:order.index(after)] if (table_root / f"v={b}").is_dir()]
        if not prior:
            raise UsageError(
                f"비교할 직전 판이 없다 — after={after} manifest={order} "
                f"디스크={sorted(p.name for p in table_root.glob('v=*'))}. "
                "`--before <build_id>` 로 직접 지정한다(keep=3 GC 로 사라졌을 수 있다)")
        before = prior[-1]
    if before == after:
        raise UsageError(f"before 와 after 가 같은 판이다 — {before}")
    return before, after


def record_of(mf: dict, build_id: str) -> dict:
    for b in mf.get("builds", []):
        if str(b["build_id"]) == build_id:
            return b
    raise UsageError(f"MANIFEST 에 없는 판이다 — build={build_id}")


# ── expect 선언 ─────────────────────────────────────────────────────────────
def load_expect(path: Path) -> list[ExpectEntry]:
    """예상 변경 선언 파일(JSON 항상 · YAML 은 PyYAML 이 있을 때)."""
    if not path.exists():
        raise UsageError(f"expect 파일이 없다 — {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as e:                              # 서버 venv 에는 없을 수 있다
            raise UsageError("YAML expect 는 PyYAML 이 필요하다 — 같은 내용을 .json 으로 넘긴다"
                             ) from e
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise UsageError(f"expect YAML 을 읽지 못했다 — {path}: {e}") from e
    else:
        try:
            raw = json.loads(text)
        except ValueError as e:
            raise UsageError(f"expect JSON 을 읽지 못했다 — {path}: {e}") from e
    items = raw.get("expect") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise UsageError(f"expect 는 항목 목록이거나 {{'expect': [...]}} 이어야 한다 — {path}")
    out: list[ExpectEntry] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise UsageError(f"expect[{i}] 가 매핑이 아니다 — {it!r}")
        unknown = set(it) - {"column", "kind", "where", "note"}
        if unknown:
            raise UsageError(f"expect[{i}] 에 모르는 키가 있다 — {sorted(unknown)}")
        kind = str(it.get("kind", ""))
        if kind not in KINDS:
            raise UsageError(f"expect[{i}].kind 어휘 밖 — got={kind!r} allowed={list(KINDS)}")
        column = str(it.get("column") or ROW_COLUMN)
        if kind in KINDS_ROW:
            column = ROW_COLUMN                               # 행 카운터는 컬럼이 없다
        elif column == ROW_COLUMN:
            raise UsageError(f"expect[{i}] 는 column 이 필요하다 — kind={kind}")
        where = it.get("where")
        out.append(ExpectEntry(column, kind, str(where) if where else None,
                               str(it.get("note") or "")))
    return out


def parse_tol(values: list[str] | None) -> tuple[float, dict[str, float]]:
    """`--tol 1e-9`(기본) · `--tol capex_ytd=1e-6`(컬럼별). 둘을 섞어 여러 번 줄 수 있다."""
    default, by_col = DEFAULT_TOL, {}
    for v in values or []:
        if "=" in v:
            col, _, num = v.partition("=")
            try:
                by_col[col] = float(num)
            except ValueError as e:
                raise UsageError(f"--tol 값이 수가 아니다 — {v!r}") from e
        else:
            try:
                default = float(v)
            except ValueError as e:
                raise UsageError(f"--tol 값이 수가 아니다 — {v!r}") from e
    return default, by_col


# ── SQL 조립 ────────────────────────────────────────────────────────────────
def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def read_parquet_sql(files: list[Path]) -> str:
    return (f"read_parquet([{', '.join(_lit(str(f)) for f in files)}], "
            "hive_partitioning=true, union_by_name=true)")


def is_meta_column(name: str) -> bool:
    low = name.lower()
    return low in META_EXACT or low.endswith(META_SUFFIX)


def group_of(column: str) -> str | None:
    """따로 세는 컬럼 묶음 — 공개시점 축과 basis 어휘."""
    if column.endswith("available_date"):
        return "available_date"
    if "basis" in column.split("_"):
        return "basis"
    return None


def column_types(con: duckdb.DuckDBPyConnection, alias: str) -> dict[str, str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM {alias}").fetchall()
    return {str(r[0]): str(r[1]) for r in rows if str(r[0]) != MARK}


def is_numeric(sql_type: str) -> bool:
    return bool(_NUMERIC_RE.match(sql_type.upper()))


def change_cond(column: str, kind: str, *, numeric: bool, tol: float) -> str:
    """조인 뷰 `j` 위의 변경 술어. 양쪽에 다 있는 행만 셀 수 있다(행 추가/삭제와 안 겹친다)."""
    a, b = q(column), q(BEFORE_PREFIX + column)
    both = f"{IN_BEFORE} AND {IN_AFTER}"
    if kind == "value_to_null":
        return f"{both} AND {b} IS NOT NULL AND {a} IS NULL"
    if kind == "null_to_value":
        return f"{both} AND {b} IS NULL AND {a} IS NOT NULL"
    if kind == "value_changed":
        if numeric and tol > 0:
            da, db = f"CAST({a} AS DOUBLE)", f"CAST({b} AS DOUBLE)"
            # 상대 허용오차 — 둘 다 0 이면 greatest 가 0 이라 '차이 > 0' 이 되어 안전하다.
            differs = f"abs({da} - {db}) > {tol} * greatest(abs({da}), abs({db}))"
        else:
            differs = f"{a} <> {b}"
        return f"{both} AND {b} IS NOT NULL AND {a} IS NOT NULL AND ({differs})"
    if kind == "rows_added":
        return f"{IN_AFTER} AND NOT {IN_BEFORE}"
    if kind == "rows_removed":
        return f"{IN_BEFORE} AND NOT {IN_AFTER}"
    raise UsageError(f"모르는 kind — {kind}")


def make_views(con: duckdb.DuckDBPyConnection, before_files: list[Path],
               after_files: list[Path]) -> None:
    for alias, files in (("_before", before_files), ("_after", after_files)):
        con.execute(f"CREATE OR REPLACE TEMP VIEW {alias} AS "
                    f"SELECT *, TRUE AS {MARK} FROM {read_parquet_sql(files)}")


def make_join_view(con: duckdb.DuckDBPyConnection, key: tuple[str, ...],
                   before_cols: list[str], after_cols: list[str]) -> None:
    """`j` — after 컬럼은 제 이름, before 컬럼은 `before_` 접두, 키는 `k_` 접두 coalesce."""
    sel = [f"coalesce(a.{q(k)}, b.{q(k)}) AS {q(KEY_PREFIX + k)}" for k in key]
    sel += [f"a.{q(c)} AS {q(c)}" for c in after_cols]
    sel += [f"b.{q(c)} AS {q(BEFORE_PREFIX + c)}" for c in before_cols]
    sel += [f"a.{MARK} IS NOT NULL AS {IN_AFTER}", f"b.{MARK} IS NOT NULL AS {IN_BEFORE}"]
    on = " AND ".join(f"a.{q(k)} IS NOT DISTINCT FROM b.{q(k)}" for k in key)
    con.execute(f"CREATE OR REPLACE TEMP VIEW j AS SELECT {', '.join(sel)} "
                f"FROM _before b FULL OUTER JOIN _after a ON {on}")


def key_issues(con: duckdb.DuckDBPyConnection, alias: str, key: tuple[str, ...]) -> dict:
    """키 중복·NULL 을 **먼저** 본다 — 중복이 있으면 조인이 팬아웃해 아래 숫자를 못 믿는다."""
    keys = ", ".join(q(k) for k in key)
    null_pred = " OR ".join(f"{q(k)} IS NULL" for k in key)
    dup = con.execute(f"SELECT count(*) FROM (SELECT {keys} FROM {alias} GROUP BY {keys} "
                      "HAVING count(*) > 1)").fetchone()
    nulls = con.execute(f"SELECT count(*) FROM {alias} WHERE {null_pred}").fetchone()
    ex = con.execute(f"SELECT {keys}, count(*) AS n FROM {alias} GROUP BY {keys} "
                     f"HAVING count(*) > 1 ORDER BY n DESC LIMIT {MAX_EXAMPLES}").fetchall()
    return {"duplicate_keys": int(dup[0]) if dup else 0,
            "null_keys": int(nulls[0]) if nulls else 0,
            "duplicate_examples": [dict(zip(key, [jsonable(v) for v in r[:-1]], strict=True))
                                   | {"n": int(r[-1])} for r in ex]}


def jsonable(v: object) -> object:
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def examples(con: duckdb.DuckDBPyConnection, key: tuple[str, ...], column: str, kind: str,
             cond: str, limit: int) -> list[dict]:
    """대표 사례 — 키 + (컬럼 카운터면) 전/후 값."""
    cols = [f"{q(KEY_PREFIX + k)} AS {q(k)}" for k in key]
    if kind in KINDS_COLUMN:
        cols += [f"{q(BEFORE_PREFIX + column)} AS _b", f"{q(column)} AS _a"]
    rows = con.execute(f"SELECT {', '.join(cols)} FROM j WHERE {cond} LIMIT {limit}").fetchall()
    out = []
    for r in rows:
        item: dict[str, object] = {
            "key": dict(zip(key, [jsonable(v) for v in r[:len(key)]], strict=True))}
        if kind in KINDS_COLUMN:
            item["before"], item["after"] = jsonable(r[-2]), jsonable(r[-1])
        out.append(item)
    return out


# ── diff 본체 ───────────────────────────────────────────────────────────────
def run_diff(a: argparse.Namespace) -> tuple[int, dict, str]:
    table_root = a.root / a.table
    mf = load_manifest(table_root)
    before, after = resolve_builds(mf, table_root, a.before, a.after)
    b_files, a_files = build_files(table_root, before), build_files(table_root, after)
    key = tuple(c.strip() for c in a.key.split(",") if c.strip()) if a.key \
        else declared_grain(a.table)
    tol_default, tol_by_col = parse_tol(a.tol)
    expect = load_expect(a.expect) if a.expect else []

    con = duckdb.connect()
    try:
        con.execute(f"SET threads = {int(a.threads)}")
        con.execute(f"SET memory_limit = '{a.memory_limit}'")
        make_views(con, b_files, a_files)
        b_types, a_types = column_types(con, "_before"), column_types(con, "_after")
        hive = hive_keys(a_files, table_root) | hive_keys(b_files, table_root)
        present = set(b_types) | set(a_types)
        # 제외 = 운영 메타 + 선언 컬럼이 아닌 하이브 키(`v` 는 곧 판 id, `year` 는 파티션 축).
        # 선언에 있는 이름(예 `year` 를 진짜 컬럼으로 내는 표)은 하이브 키여도 비교한다.
        rule = declared_table(a.table, required=False)
        rule_cols = set(rule.columns) if rule is not None else set()
        excluded = sorted({c for c in present if is_meta_column(c)}
                          | ((hive & present) - rule_cols))
        keep = [c for c in present if c not in excluded]
        for c in keep:
            if c.startswith((BEFORE_PREFIX, KEY_PREFIX)) or c in (MARK, IN_AFTER, IN_BEFORE):
                raise UsageError(f"컬럼 이름이 diff 내부 접두어와 겹친다 — {c!r} "
                                 f"(예약: {BEFORE_PREFIX}* · {KEY_PREFIX}* · {MARK})")
        missing = [k for k in key if k not in b_types or k not in a_types or k in excluded]
        if missing:
            raise UsageError(f"키 컬럼이 양쪽 판에 다 있지 않다 — key={list(key)} "
                             f"missing={missing} columns={sorted(keep)}")
        after_cols = [c for c in a_types if c in keep]
        before_cols = [c for c in b_types if c in keep]
        compared = [c for c in after_cols if c in set(before_cols)]
        only_after = sorted(set(after_cols) - set(before_cols))
        only_before = sorted(set(before_cols) - set(after_cols))

        rep: dict[str, object] = {
            "table": a.table, "root": str(a.root), "before": before, "after": after,
            "before_record": _rec_brief(record_of(mf, before)),
            "after_record": _rec_brief(record_of(mf, after)),
            "key": list(key), "key_source": "--key" if a.key else "rules 선언",
            "tol_default": tol_default, "tol_by_column": tol_by_col,
            "excluded_columns": excluded, "compared_columns": compared,
            "columns_only_in_after": only_after, "columns_only_in_before": only_before,
        }
        issues = {"before": key_issues(con, "_before", key),
                  "after": key_issues(con, "_after", key)}
        rep["key_issues"] = issues
        bad = [s for s, v in issues.items()
               if v["duplicate_keys"] or v["null_keys"]]
        if bad:
            rep["status"] = "key_error"
            rep["gate"] = {"enabled": bool(a.gate), "rc": 1, "reasons": [
                f"{s}: 중복 키 {issues[s]['duplicate_keys']} · NULL 키 {issues[s]['null_keys']}"
                for s in bad]}
            return 1, rep, render_markdown(rep)

        make_join_view(con, key, before_cols, after_cols)
        counters = _count_all(con, compared, a_types, tol_default, tol_by_col)
        n_join = counters.pop(("", "n_join"))
        _explain(con, counters, expect, compared, a_types, tol_default, tol_by_col)
        n_examples = max(0, int(a.max_examples))
        for (col, kind), c in counters.items():
            cond = change_cond(col, kind, numeric=is_numeric(a_types.get(col, "VARCHAR")),
                               tol=tol_by_col.get(col, tol_default))
            matched_sql = c.pop("_matched_sql", None)
            if c["total"] and n_examples:
                # 미설명이 남으면 그쪽을 먼저 보여 준다 — 사람이 볼 것은 설명 안 되는 변경이다.
                pred = (f"({cond}) AND NOT coalesce({matched_sql}, FALSE)"
                        if matched_sql and 0 < c["unexplained"] < c["total"] else cond)
                c["examples"] = examples(con, key, col, kind, pred, n_examples)

        n_before = _scalar(con, "SELECT count(*) FROM _before")
        n_after = _scalar(con, "SELECT count(*) FROM _after")
        added = counters[(ROW_COLUMN, "rows_added")]
        removed = counters[(ROW_COLUMN, "rows_removed")]
        rep["rows"] = {"before": n_before, "after": n_after,
                       "matched": n_join - added["total"] - removed["total"],
                       "join_rows": n_join, "rows_added": added, "rows_removed": removed}
        rep["columns"] = {c: {k: counters[(c, k)] for k in KINDS_COLUMN} for c in compared}
        rep["groups"] = _groups(compared, counters)
        used = {(e.column, e.kind) for e in expect}
        rep["expect"] = {
            "path": str(a.expect) if a.expect else None,
            "entries": [e.as_dict() for e in expect],
            "entries_without_matching_counter": [
                e.as_dict() for e in expect
                if counters.get((e.column, e.kind), {"total": 0})["total"] == 0],
            "counters_with_entry": sorted(f"{c}:{k}" for c, k in used)}
        rep["gate"] = _gate(rep, counters, enabled=bool(a.gate))
        rep["status"] = "ok"
        rc = int(rep["gate"]["rc"]) if a.gate else 0
        return rc, rep, render_markdown(rep)
    finally:
        con.close()


def _rec_brief(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("build_id", "basis", "rules_version", "built_at_utc",
                                    "n_rows", "content_hash")}


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    return int(row[0]) if row else 0


def _count_all(con: duckdb.DuckDBPyConnection, compared: list[str], a_types: dict[str, str],
               tol_default: float, tol_by_col: dict[str, float]) -> dict:
    """한 번의 집계로 전 컬럼 × 3종 + 행 추가·삭제를 센다(컬럼당 질의를 쏘지 않는다)."""
    specs: list[tuple[str, str]] = [(c, k) for c in compared for k in KINDS_COLUMN]
    specs += [(ROW_COLUMN, k) for k in KINDS_ROW]
    parts = ["count(*) AS n_join"]
    for i, (col, kind) in enumerate(specs):
        cond = change_cond(col, kind, numeric=is_numeric(a_types.get(col, "VARCHAR")),
                           tol=tol_by_col.get(col, tol_default))
        parts.append(f"coalesce(sum(CASE WHEN {cond} THEN 1 ELSE 0 END), 0) AS n{i}")
    row = con.execute(f"SELECT {', '.join(parts)} FROM j").fetchone()
    if row is None:
        raise UsageError("집계가 빈 결과를 냈다 — 판이 비었는가")
    out: dict = {("", "n_join"): int(row[0])}
    for i, spec in enumerate(specs):
        n = int(row[i + 1])
        out[spec] = {"total": n, "explained": 0, "unexplained": n, "examples": []}
    return out


def _explain(con: duckdb.DuckDBPyConnection, counters: dict, expect: list[ExpectEntry],
             compared: list[str], a_types: dict[str, str], tol_default: float,
             tol_by_col: dict[str, float]) -> None:
    """expect 항목이 덮는 행 수를 카운터별로 뺀다. 같은 (컬럼, kind) 의 항목은 OR 로 묶는다."""
    by_counter: dict[tuple[str, str], list[ExpectEntry]] = {}
    for e in expect:
        by_counter.setdefault((e.column, e.kind), []).append(e)
    for ck, entries in by_counter.items():
        c = counters.get(ck)
        if c is None:
            raise UsageError(f"expect 가 없는 카운터를 가리킨다 — column={ck[0]} kind={ck[1]} "
                             f"(비교 컬럼: {sorted(compared)})")
        matched = " OR ".join(f"({e.where})" if e.where else "TRUE" for e in entries)
        c["_matched_sql"] = f"({matched})"
        col, kind = ck
        cond = change_cond(col, kind, numeric=is_numeric(a_types.get(col, "VARCHAR")),
                           tol=tol_by_col.get(col, tol_default))
        # 변경이 0 인 선언도 술어는 검사한다 — 오타 난 where 가 조용히 지나가면 다음 판에서
        # 「설명된 줄 알았던」 변경이 게이트를 그냥 통과한다.
        sql = (f"SELECT count(*) FROM j WHERE ({cond}) AND ({matched})" if c["total"]
               else f"SELECT count(*) FROM (SELECT 1 FROM j WHERE ({matched}) LIMIT 1)")
        try:
            n = _scalar(con, sql)
        except duckdb.Error as e:
            raise UsageError(f"expect where 술어가 실행되지 않는다 — column={col} kind={kind} "
                             f"where={matched}: {e}") from e
        if c["total"]:
            c["explained"], c["unexplained"] = n, c["total"] - n


def _groups(compared: list[str], counters: dict) -> dict:
    out: dict[str, dict] = {}
    for col in compared:
        g = group_of(col)
        if g is None:
            continue
        slot = out.setdefault(g, {"columns": [], "changed": 0, "unexplained": 0,
                                  **{k: 0 for k in KINDS_COLUMN}})
        slot["columns"].append(col)
        for k in KINDS_COLUMN:
            slot[k] += counters[(col, k)]["total"]
            slot["changed"] += counters[(col, k)]["total"]
            slot["unexplained"] += counters[(col, k)]["unexplained"]
    return out


def _gate(rep: dict, counters: dict, *, enabled: bool) -> dict:
    v2n = sum(c["unexplained"] for (col, kind), c in counters.items()
              if kind == "value_to_null")
    removed = counters[(ROW_COLUMN, "rows_removed")]["unexplained"]
    avail = int(rep["groups"].get("available_date", {}).get("unexplained", 0))
    reasons = []
    if v2n:
        reasons.append(f"미설명 값→NULL {v2n:,}")
    if removed:
        reasons.append(f"미설명 행 삭제 {removed:,}")
    if avail:
        reasons.append(f"미설명 available_date 변경 {avail:,}")
    return {"enabled": enabled, "rc": 2 if reasons else 0, "reasons": reasons,
            "unexplained_value_to_null": v2n, "unexplained_rows_removed": removed,
            "unexplained_available_date": avail}


# ── 결정성 ──────────────────────────────────────────────────────────────────
def run_determinism(a: argparse.Namespace) -> tuple[int, dict, str]:
    """같은 입력으로 두 번 지은 판의 파티션 content_hash 만 본다 — EG5a 가 rules_changed 로
    skip 한 빌드에서 결정성을 따로 확인하는 축이다(플랜 §11 T-G)."""
    left, right = a.determinism
    mf = load_manifest(a.root / a.table)
    lr, rr = record_of(mf, left), record_of(mf, right)

    def parts(rec: dict) -> dict[str, str]:
        out = {}
        for p in rec.get("partitions", []):
            path = str(p.get("path", ""))
            out[path.split("/", 1)[1] if "/" in path else "whole"] = str(p.get("content_hash"))
        return out

    lp, rp = parts(lr), parts(rr)
    labels = sorted(set(lp) | set(rp))
    rows = [{"partition": lab, "left": lp.get(lab), "right": rp.get(lab),
             "equal": lp.get(lab) == rp.get(lab) and lab in lp and lab in rp} for lab in labels]
    equal = (all(r["equal"] for r in rows) and lr.get("content_hash") == rr.get("content_hash")
             and set(lp) == set(rp))
    rep = {"table": a.table, "root": str(a.root), "mode": "determinism",
           "left": _rec_brief(lr), "right": _rec_brief(rr), "partitions": rows,
           "content_hash_equal": lr.get("content_hash") == rr.get("content_hash"),
           "rules_version_equal": lr.get("rules_version") == rr.get("rules_version"),
           "inputs_equal": lr.get("inputs") == rr.get("inputs"),
           "equal": equal, "rc": 0 if equal else 2}
    return (0 if equal else 2), rep, render_determinism(rep)


# ── 출력 ────────────────────────────────────────────────────────────────────
def _n(v: object) -> str:
    return f"{int(v):,}" if isinstance(v, (int, float)) else str(v)


def render_markdown(rep: dict) -> str:
    b, a = rep["before_record"], rep["after_record"]
    lines = [f"# equity diff — {rep['table']}  ({rep['before']} → {rep['after']})", ""]
    lines += [f"- root: `{rep['root']}`",
              f"- before: `{rep['before']}` rules={b.get('rules_version')} "
              f"rows={_n(b.get('n_rows') or 0)} built={b.get('built_at_utc')}",
              f"- after: `{rep['after']}` rules={a.get('rules_version')} "
              f"rows={_n(a.get('n_rows') or 0)} built={a.get('built_at_utc')}",
              f"- 키(grain): {', '.join(rep['key'])}  ({rep['key_source']})",
              f"- 허용오차: 기본 {rep['tol_default']:g} 상대 · "
              f"컬럼별 {rep['tol_by_column'] or '없음'}",
              f"- 비교 컬럼 {len(rep['compared_columns'])} · 제외 "
              f"{len(rep['excluded_columns'])}({', '.join(rep['excluded_columns']) or '없음'})"
              f" · after 전용 {rep['columns_only_in_after'] or '없음'}"
              f" · before 전용 {rep['columns_only_in_before'] or '없음'}"]
    ki = rep.get("key_issues") or {}
    for side in ("before", "after"):
        v = ki.get(side) or {}
        if v.get("duplicate_keys") or v.get("null_keys"):
            lines.append(f"- **키 위생 실패({side})**: 중복 키 그룹 {_n(v['duplicate_keys'])} · "
                         f"NULL 키 행 {_n(v['null_keys'])} — 예 {v.get('duplicate_examples')}")
    if rep.get("status") == "key_error":
        lines += ["", "키가 유일하지 않아 FULL OUTER JOIN 이 팬아웃한다 — 컬럼 비교를 하지 않았다.",
                  "grain 선언이 맞는지, 빌드가 EG3-P01(유일성)을 통과했는지 본다."]
        return "\n".join(lines) + "\n"

    r = rep["rows"]
    lines += [f"- 행: before {_n(r['before'])} · after {_n(r['after'])} · 공통 {_n(r['matched'])}"
              f" · 추가 {_n(r['rows_added']['total'])}(미설명 {_n(r['rows_added']['unexplained'])})"
              f" · 삭제 {_n(r['rows_removed']['total'])}"
              f"(미설명 {_n(r['rows_removed']['unexplained'])})", ""]
    lines += ["| 컬럼 | 값→NULL | NULL→값 | 값변경 | 미설명 |", "|---|---:|---:|---:|---:|"]
    quiet = 0
    for col, kinds in rep["columns"].items():
        tot = sum(kinds[k]["total"] for k in KINDS_COLUMN)
        if not tot:
            quiet += 1
            continue
        unexp = sum(kinds[k]["unexplained"] for k in KINDS_COLUMN)
        lines.append(f"| `{col}` | {_n(kinds['value_to_null']['total'])} | "
                     f"{_n(kinds['null_to_value']['total'])} | "
                     f"{_n(kinds['value_changed']['total'])} | {_n(unexp)} |")
    lines.append(f"| _변화 없는 컬럼 {quiet}개_ | 0 | 0 | 0 | 0 |")
    if rep["groups"]:
        lines += ["", "| 묶음 | 컬럼 | 변경 합 | 미설명 |", "|---|---|---:|---:|"]
        for g, v in sorted(rep["groups"].items()):
            lines.append(f"| {g} | {', '.join(f'`{c}`' for c in v['columns'])} | "
                         f"{_n(v['changed'])} | {_n(v['unexplained'])} |")
    ex_lines = []
    for col, kinds in rep["columns"].items():
        for k in KINDS_COLUMN:
            c = kinds[k]
            if c["total"] and c["examples"]:
                ex_lines.append(f"- `{col}` · {k} — {_n(c['total'])}건"
                                f"(설명 {_n(c['explained'])} · 미설명 {_n(c['unexplained'])})")
                ex_lines += [f"    - {e['key']}: {e.get('before')!r} → {e.get('after')!r}"
                             for e in c["examples"]]
    for k in KINDS_ROW:
        c = r[k]
        if c["total"] and c["examples"]:
            ex_lines.append(f"- {k} — {_n(c['total'])}건(설명 {_n(c['explained'])} · "
                            f"미설명 {_n(c['unexplained'])})")
            ex_lines += [f"    - {e['key']}" for e in c["examples"]]
    if ex_lines:
        lines += ["", "## 대표 사례", *ex_lines]
    exp = rep["expect"]
    if exp["entries"]:
        lines += ["", f"## expect `{exp['path']}` — {len(exp['entries'])}건"]
        lines += [f"- `{e['column']}` {e['kind']}"
                  f"{' where ' + e['where'] if e['where'] else ''} — {e['note']}"
                  for e in exp["entries"]]
        if exp["entries_without_matching_counter"]:
            lines.append("- ⚠ 해당 변경이 0 인 선언(낡았을 수 있다): "
                         + ", ".join(f"{e['column']}:{e['kind']}"
                                     for e in exp["entries_without_matching_counter"]))
    g = rep["gate"]
    mode = "--gate" if g["enabled"] else "판정만 — --gate 없음"
    lines += ["", f"## 게이트 rc={g['rc']} ({mode})"]
    lines.append("- " + ("; ".join(g["reasons"]) if g["reasons"]
                         else "미설명 값→NULL · 행 삭제 · available_date 변경 모두 0"))
    return "\n".join(lines) + "\n"


def render_determinism(rep: dict) -> str:
    lines = [f"# equity 결정성 — {rep['table']}  {rep['left']['build_id']} vs "
             f"{rep['right']['build_id']}", "",
             f"- 규칙 판본 동일: {rep['rules_version_equal']} · 입력 동일: {rep['inputs_equal']}",
             f"- 표 content_hash 동일: {rep['content_hash_equal']}", "",
             "| 파티션 | left | right | 같음 |", "|---|---|---|---|"]
    for p in rep["partitions"]:
        lines.append(f"| {p['partition']} | `{p['left']}` | `{p['right']}` | {p['equal']} |")
    lines += ["", f"## 결정성 rc={rep['rc']} — "
                  + ("전 파티션 해시 동일" if rep["equal"] else "해시가 다르다(비결정)")]
    return "\n".join(lines) + "\n"


# ── CLI ─────────────────────────────────────────────────────────────────────
def parser() -> argparse.ArgumentParser:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[1])
    p = argparse.ArgumentParser(prog="equity_diff", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", type=Path, default=base / "data" / "equity",
                   help="equity 루트 (기본 $QL_HOME/data/equity)")
    p.add_argument("--table", required=True, help="표 이름 — 예 fin_std")
    p.add_argument("--before", help="기준 판 build_id (기본: MANIFEST 순서상 직전 · 디스크 존재)")
    p.add_argument("--after", help="새 판 build_id (기본: current_build)")
    p.add_argument("--key", help="grain 재정의 — 쉼표 구분. 기본은 rules 모듈 선언")
    p.add_argument("--tol", action="append",
                   help=f"상대 허용오차. `1e-9`(기본 {DEFAULT_TOL:g}) 또는 `컬럼=1e-6`. 반복 가능")
    p.add_argument("--expect", type=Path, help="예상 변경 선언(JSON, PyYAML 있으면 YAML)")
    p.add_argument("--gate", action="store_true",
                   help="미설명 값→NULL · 행 삭제 · available_date 변경이 있으면 rc 2")
    p.add_argument("--determinism", nargs=2, metavar=("BUILD_A", "BUILD_B"),
                   help="파티션 content_hash 만 비교(같은 입력 두 판) — 다르면 rc 2")
    p.add_argument("--out", type=Path, help="JSON 보고서 경로 "
                                            "(기본 <root 의 부모>/logs/equity_diff/…)")
    p.add_argument("--max-examples", type=int, default=MAX_EXAMPLES,
                   help=f"카운터별 대표 사례 수 (기본 {MAX_EXAMPLES})")
    p.add_argument("--threads", type=int, default=3, help="duckdb 스레드 (서버 4코어 규약)")
    p.add_argument("--memory-limit", default="6GB", help="duckdb 메모리 상한")
    return p


def default_out(a: argparse.Namespace, before: str, after: str) -> Path:
    stem = (f"{a.table}_determinism_{before}_{after}" if a.determinism
            else f"{a.table}_{before}_{after}")
    return a.root.parent / "logs" / "equity_diff" / f"{stem}.json"


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        if a.determinism:
            if a.before or a.after or a.expect or a.gate:
                raise UsageError("--determinism 은 --before/--after/--expect/--gate 와 같이 쓰지 "
                                 "않는다 — 파티션 해시만 보는 축이다")
            rc, rep, text = run_determinism(a)
            before, after = a.determinism
        else:
            rc, rep, text = run_diff(a)
            before, after = str(rep["before"]), str(rep["after"])
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except duckdb.Error as e:
        print(f"error: duckdb — {e}", file=sys.stderr)
        return 1
    out = a.out or default_out(a, before, after)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(text)
    print(f"report: {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
