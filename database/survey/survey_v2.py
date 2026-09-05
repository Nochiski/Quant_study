"""survey v2 — 원장 전 테이블·전 컬럼 **전수 어휘 측정** (숫자 캐스트 금지).

STAGE_DESIGN v2.2 §5·§10 선행 조건 2. v1(`survey_ledger.py`)의 rowid stride 표본 + float 캐스트가
(p,s)를 과소 추정한 것(whol_loan_gvrt 표본 −292~+333 vs 전수 −594.76~+1120.92)을 대체한다.

원칙:
- 조사 대상은 `targets.py` 목록이 아니라 각 DB 의 `sqlite_master` 다 — 미조사 테이블이 구조적으로
  생기지 않는다. 끝나면 `pragma_table_info` 와 대조해 미조사 테이블·컬럼 0 을 스스로 판정하고,
  아니면 종료 코드 1.
- 값을 숫자로 바꾸지 않는다. 문자열 함수와 정규식만 쓴다 — 자릿수·소수자리·콤마·부호·비숫자·
  결측 마커·날짜 모양.
- 엔진은 duckdb sqlite_scanner(READ_ONLY ATTACH). 테이블당 1쿼리, 전 컬럼 동시 집계.

실행: `python survey/survey_v2.py [--raw-dir DIR] [--out DIR] [--db krx,kis] [--table t1,t2]`
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from targets import DBS  # noqa: E402, I001  # reason: reuse targets.DBS SoT after sys.path insert (v1 pattern)

# ── 어휘 패턴 (전부 문자열 정규식 — 캐스트 없음) ──────────────────────────────
NUMERIC_RE = r"^[+-]?[0-9]*\.?[0-9]*$"          # 콤마 제거 후 숫자 모양
SCALE_RE = r"^[+-]?[0-9]*\.([0-9]+)$"           # 소수부 (숫자 모양에 한정 — 점표기 날짜 배제)
INT_RE = r"^[+-]?([0-9]*)"                      # 정수부
ZERO_RE = r"^[+]?0+(\.0+)?$"                    # '0' · '0.00' 류 (KIS 결측 표현 후보)
YMD8_RE = r"^(19|20)[0-9]{6}$"
ISO_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
DOT_RE = r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$"
KOR_RE = r"^[0-9]{4}년 [0-9]{2}월 [0-9]{2}일$"
TOP_PATTERNS = 8
PATTERN_MAXLEN = 24


@dataclass(frozen=True)
class ColumnStats:
    """컬럼 하나의 전수 어휘 통계. 값은 전부 카운트/길이 — 수치 해석 없음."""

    n_rows: int
    n_null: int
    n_blank: int          # ''
    n_dash: int           # '-'
    n_zero: int           # ZERO_RE
    n_comma: int          # 콤마 포함
    n_neg: int            # 콤마 제거 후 '-' 시작 ('-' 단독 제외)
    n_plus: int           # '+' 시작
    n_nonnum: int         # NUMERIC_RE 불일치 (''·'-' 제외)
    max_int_digits: int
    max_scale: int
    max_len: int
    n_ymd8: int
    n_iso: int
    n_dot: int
    n_kor: int
    patterns: dict[str, int] = field(default_factory=dict)

    @property
    def n_present(self) -> int:
        return self.n_rows - self.n_null - self.n_blank - self.n_dash


@dataclass(frozen=True)
class TableProfile:
    db: str
    table: str
    n_rows: int
    columns: dict[str, ColumnStats]
    elapsed_s: float


@dataclass(frozen=True)
class PsSpec:
    """rules.py 에 넣을 컬럼 선언 재료. kind 가 numeric 일 때만 precision/scale 이 있다."""

    kind: str  # numeric | date_yyyymmdd | date_iso | date_dot | date_korean | text | empty
    precision: int | None
    scale: int | None
    has_sign: bool
    has_comma: bool
    n_present: int
    n_nonnum: int


class CoverageStatus(Enum):
    OK = "ok"
    MISSING = "missing"


@dataclass(frozen=True)
class CoverageResult:
    status: CoverageStatus
    missing_tables: list[str]
    missing_columns: list[str]

    @property
    def ok(self) -> bool:
        return self.status is CoverageStatus.OK


class RunStatus(Enum):
    OK = "ok"
    INCOMPLETE = "incomplete"   # 커버리지 미달 — 산출물은 썼지만 (p,s) 확정에 쓰면 안 된다


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    n_tables: int
    coverage: CoverageResult
    ps: dict[str, dict[str, PsSpec]]     # "db.table" → col → PsSpec
    elapsed_s: float
    out_dir: Path

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.OK


# ── 원장 메타 (sqlite3 표준 라이브러리 — 커버리지의 정본) ──────────────────────
def list_ledger_tables(sqlite_path: Path) -> list[str]:
    """`sqlite_master` 의 사용자 테이블 (sqlite_ 내부 테이블 제외), 이름순."""
    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def list_columns(sqlite_path: Path, table: str) -> list[str]:
    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        rows = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    finally:
        con.close()
    if not rows:
        raise ValueError(f"table has no columns or does not exist: db={sqlite_path} table={table}")
    return [r[1] for r in rows]


# ── duckdb 측정 ───────────────────────────────────────────────────────────────
def attach_sqlite(con: duckdb.DuckDBPyConnection, alias: str, sqlite_path: Path) -> None:
    con.execute("LOAD sqlite")
    con.execute(f"ATTACH '{sqlite_path}' AS \"{alias}\" (TYPE sqlite, READ_ONLY)")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _column_aggregates(col: str) -> str:
    v = f"CAST({_q(col)} AS VARCHAR)"
    s = f"replace({v}, ',', '')"
    shape = (
        f"left(regexp_replace(regexp_replace(regexp_replace({v}, '[0-9]', '9', 'g'),"
        f" '[A-Za-z]', 'A', 'g'), '[가-힣]', '가', 'g'), {PATTERN_MAXLEN})"
    )
    return f"""
      count(*) FILTER (WHERE {v} IS NULL) AS {_q(col + "__n_null")},
      count(*) FILTER (WHERE {v} = '') AS {_q(col + "__n_blank")},
      count(*) FILTER (WHERE {v} = '-') AS {_q(col + "__n_dash")},
      count(*) FILTER (WHERE regexp_matches({v}, '{ZERO_RE}')) AS {_q(col + "__n_zero")},
      count(*) FILTER (WHERE {v} LIKE '%,%') AS {_q(col + "__n_comma")},
      count(*) FILTER (WHERE {s} LIKE '-%' AND {v} <> '-') AS {_q(col + "__n_neg")},
      count(*) FILTER (WHERE {s} LIKE '+%') AS {_q(col + "__n_plus")},
      count(*) FILTER (WHERE NOT regexp_matches({s}, '{NUMERIC_RE}')
                       AND {v} NOT IN ('', '-')) AS {_q(col + "__n_nonnum")},
      coalesce(max(length(regexp_extract({s}, '{INT_RE}', 1))), 0)
        AS {_q(col + "__max_int_digits")},
      coalesce(max(length(regexp_extract({s}, '{SCALE_RE}', 1))), 0) AS {_q(col + "__max_scale")},
      coalesce(max(length({v})), 0) AS {_q(col + "__max_len")},
      count(*) FILTER (WHERE regexp_matches({v}, '{YMD8_RE}')) AS {_q(col + "__n_ymd8")},
      count(*) FILTER (WHERE regexp_matches({v}, '{ISO_RE}')) AS {_q(col + "__n_iso")},
      count(*) FILTER (WHERE regexp_matches({v}, '{DOT_RE}')) AS {_q(col + "__n_dot")},
      count(*) FILTER (WHERE regexp_matches({v}, '{KOR_RE}')) AS {_q(col + "__n_kor")},
      histogram({shape}) FILTER (WHERE {v} IS NOT NULL AND {v} <> '') AS {_q(col + "__patterns")}"""


def build_profile_sql(alias: str, table: str, columns: list[str]) -> str:
    if not columns:
        raise ValueError(f"no columns to profile: alias={alias} table={table}")
    body = ",".join(_column_aggregates(c) for c in columns)
    return f"SELECT count(*) AS n_rows, {body} FROM {_q(alias)}.{_q(table)}"


def _top_patterns(hist: dict[str, int] | None) -> dict[str, int]:
    if not hist:
        return {}
    ranked = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_PATTERNS]
    return dict(ranked)


def profile_table(
    con: duckdb.DuckDBPyConnection, alias: str, table: str, columns: list[str]
) -> TableProfile:
    """테이블 1개를 단일 쿼리로 전수 측정한다."""
    t0 = time.time()
    cur = con.execute(build_profile_sql(alias, table, columns))
    row = cur.fetchone()
    if row is None or cur.description is None:
        raise RuntimeError(f"profile query returned nothing: alias={alias} table={table}")
    values = dict(zip([d[0] for d in cur.description], row, strict=True))
    n_rows = int(values["n_rows"])
    stats: dict[str, ColumnStats] = {}
    for c in columns:
        def g(k: str, c: str = c) -> int:
            return int(values[f"{c}__{k}"])
        stats[c] = ColumnStats(
            n_rows=n_rows, n_null=g("n_null"), n_blank=g("n_blank"), n_dash=g("n_dash"),
            n_zero=g("n_zero"), n_comma=g("n_comma"), n_neg=g("n_neg"), n_plus=g("n_plus"),
            n_nonnum=g("n_nonnum"), max_int_digits=g("max_int_digits"), max_scale=g("max_scale"),
            max_len=g("max_len"), n_ymd8=g("n_ymd8"), n_iso=g("n_iso"), n_dot=g("n_dot"),
            n_kor=g("n_kor"), patterns=_top_patterns(values[f"{c}__patterns"]),
        )
    return TableProfile(db=alias, table=table, n_rows=n_rows, columns=stats,
                        elapsed_s=round(time.time() - t0, 2))


# ── (p,s) 유도 ─────────────────────────────────────────────────────────────────
def derive_ps(s: ColumnStats) -> PsSpec:
    """어휘 통계 → 컬럼 종류와 Decimal (p,s).

    존재값 전건이 날짜 모양이면 날짜, 비숫자 0 이면 numeric, 그 외 text.
    """
    present = s.n_present
    has_sign = s.n_neg > 0 or s.n_plus > 0
    if present <= 0:
        kind = "empty"
    elif s.n_ymd8 == present:
        kind = "date_yyyymmdd"
    elif s.n_iso == present:
        kind = "date_iso"
    elif s.n_dot == present:
        kind = "date_dot"
    elif s.n_kor == present:
        kind = "date_korean"
    elif s.n_nonnum == 0 and (s.max_int_digits > 0 or s.max_scale > 0):
        kind = "numeric"
    else:
        kind = "text"
    if kind == "numeric":
        return PsSpec(kind, s.max_int_digits + s.max_scale, s.max_scale, has_sign, s.n_comma > 0,
                      present, s.n_nonnum)
    return PsSpec(kind, None, None, has_sign, s.n_comma > 0, present, s.n_nonnum)


# ── 커버리지 ───────────────────────────────────────────────────────────────────
def check_coverage(
    expected: dict[str, dict[str, list[str]]], surveyed: dict[str, dict[str, list[str]]]
) -> CoverageResult:
    """expected = 원장 실물(sqlite_master + pragma), surveyed = 측정된 것. 차집합이 0 이어야 OK."""
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for db, tables in expected.items():
        for table, cols in tables.items():
            got = surveyed.get(db, {}).get(table)
            if got is None:
                missing_tables.append(f"{db}.{table}")
                continue
            missing_columns.extend(f"{db}.{table}.{c}" for c in cols if c not in set(got))
    complete = not missing_tables and not missing_columns
    status = CoverageStatus.OK if complete else CoverageStatus.MISSING
    return CoverageResult(status, sorted(missing_tables), sorted(missing_columns))


# ── 오케스트레이션 ─────────────────────────────────────────────────────────────
def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)


def survey_ledgers(
    raw: dict[str, Path], out_dir: Path, only_tables: set[str] | None = None
) -> RunResult:
    """DB 별 전 테이블 측정 → JSON 저장 → 커버리지 판정. only_tables 는 디버그용(커버리지 미달)."""
    t0 = time.time()
    expected: dict[str, dict[str, list[str]]] = {}
    surveyed: dict[str, dict[str, list[str]]] = {}
    ps_all: dict[str, dict[str, PsSpec]] = {}
    n_tables = 0
    for db, path in raw.items():
        tables = list_ledger_tables(path)
        expected[db] = {t: list_columns(path, t) for t in tables}
        con = duckdb.connect()
        try:
            con.execute("SET memory_limit='4GB'")
            con.execute("SET threads=3")
            attach_sqlite(con, db, path)
            for t in tables:
                if only_tables is not None and t not in only_tables:
                    continue
                cols = expected[db][t]
                prof = profile_table(con, db, t, cols)
                ps = {c: derive_ps(s) for c, s in prof.columns.items()}
                ps_all[f"{db}.{t}"] = ps
                surveyed.setdefault(db, {})[t] = cols
                n_tables += 1
                rec = asdict(prof)
                rec["ps"] = {c: asdict(p) for c, p in ps.items()}
                rec["method"] = {"scan": "full", "cast": "none", "engine": "duckdb sqlite_scanner"}
                _write_json(out_dir / f"{db}.{t}.json", rec)
                print(f"  ✓ {db}.{t}  {prof.n_rows:,}행 · {len(cols)}컬럼 · {prof.elapsed_s}s",
                      flush=True)
        finally:
            con.close()
    coverage = check_coverage(expected, surveyed)
    _write_json(out_dir / "ps_table.json",
                {k: {c: asdict(p) for c, p in v.items()} for k, v in ps_all.items()})
    _write_json(out_dir / "coverage.json",
                {"status": coverage.status.value, "missing_tables": coverage.missing_tables,
                 "missing_columns": coverage.missing_columns})
    status = RunStatus.OK if coverage.ok else RunStatus.INCOMPLETE
    return RunResult(status, n_tables, coverage, ps_all, round(time.time() - t0, 1), out_dir)


def _default_raw() -> dict[str, Path]:
    return {k: Path(v) for k, v in DBS.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", type=Path, help="원장 디렉토리 (기본: targets.DBS 경로)")
    ap.add_argument("--out", type=Path, help="산출 디렉토리 (기본: $QL_HOME/survey_out/v2)")
    ap.add_argument("--db", help="쉼표 구분 DB 이름 필터 (예: krx,kis)")
    ap.add_argument("--table", help="쉼표 구분 테이블 필터 (디버그 — 커버리지 미달로 종료 코드 1)")
    a = ap.parse_args(argv)

    raw = _default_raw()
    if a.raw_dir is not None:
        raw = {k: a.raw_dir / p.name for k, p in raw.items()}
    if a.db:
        keep = set(a.db.split(","))
        raw = {k: v for k, v in raw.items() if k in keep}
    base = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = a.out or Path(base) / "survey_out" / "v2"
    only = set(a.table.split(",")) if a.table else None

    print(f"════ survey v2 시작 — DB {len(raw)}개 → {out}", flush=True)
    r = survey_ledgers(raw, out, only)
    cov = r.coverage
    print(f"════ 종료 {r.elapsed_s}s · 테이블 {r.n_tables} · 커버리지 {cov.status.value}"
          f" (미조사 테이블 {len(cov.missing_tables)} · 컬럼 {len(cov.missing_columns)})",
          flush=True)
    for m in r.coverage.missing_tables[:20]:
        print(f"  ✖ 미조사 테이블 {m}")
    for m in r.coverage.missing_columns[:20]:
        print(f"  ✖ 미조사 컬럼 {m}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
