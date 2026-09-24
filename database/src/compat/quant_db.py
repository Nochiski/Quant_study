"""equity/stage 판 → v3 `quant.db` upsert (플랜 `2026-09-24-v3-merge.md` §5 T1.2 3).

읽기는 duckdb `read_parquet` 뿐이다 — `equity.duckdb` 카탈로그를 열지 않는다(카탈로그는 매크로
전용이고 그림자 실행은 표 직독이면 충분하다). 판 선택은 MANIFEST `current_build` 정본
(`equity.inputs.resolve`) — `v=*` 맨 glob 금지(STAGE_HANDOFF §1).

쓰기는 표 단위 한 트랜잭션(`BEGIN … INSERT OR REPLACE … COMMIT`)이다. 부분 기록이 읽히면
안 되기 때문(플랜 §2-1). 표 하나라도 0행이면 `CompatEmptyError` — 조용한 실패 금지(V2-7).

M1~M3 대상은 별도 파일 `data/compat/quant.db`, M4 부터 v3 파일 제자리(결정 D-2)이므로 sqlite
PRAGMA 는 v3 `backend/db/connection.py:9-17` 과 같은 값을 쓴다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from equity import inputs

from .mappings import BY_TABLE, EQUITY, EVENING_SKIPPED_SQL, MAPPINGS, STAGE, TableMapping

SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "v3_schema.sql"

# v3 모멘텀이 보는 가격 범위(240 행 ≈ 1년)에 여유를 둔 기본 창. `--full` 은 2년.
DEFAULT_WINDOW_DAYS = 550
FULL_WINDOW_DAYS = 730
# 증분 기본값 — 최근 10세션을 확실히 덮는 달력일. 연휴를 포함해도 10세션이 들어온다.
INCREMENTAL_DAYS = 14
# 스냅샷 표(`stocks`)가 as_of 이하 최신 세션 행을 찾을 때 훑는 창. 최장 연휴보다 넉넉하다.
SNAPSHOT_LOOKBACK_DAYS = 30
# 한 번에 sqlite 로 넘기는 행 수. duckdb 결과를 통째로 파이썬 객체로 올리지 않기 위한 값이다.
FETCH_CHUNK = 50_000
# duckdb 자원 — `equity.catalog` 와 같은 한도(DEFECT-C04: 서버 4코어를 v3 체인과 나눠 쓴다).
DUCKDB_THREADS = 3
DUCKDB_MEMORY_LIMIT = "8GB"

BASES = ("evening", "morning")
META_TABLE = "_compat_meta"
META_DDL = f"""
CREATE TABLE IF NOT EXISTS {META_TABLE} (
    exported_at    TEXT PRIMARY KEY,
    date           TEXT NOT NULL,
    basis          TEXT NOT NULL,
    equity_builds  TEXT NOT NULL,
    stage_builds   TEXT NOT NULL,
    tables         TEXT NOT NULL,
    window         TEXT NOT NULL,
    consensus_asof TEXT NOT NULL,
    -- GAP-1/D-8: `price_daily.basis='evening'` 이라 daily_prices 에서 제외한 행 수.
    -- daily_prices 를 안 내보낸 실행에서는 NULL.
    n_evening_rows_skipped INTEGER
)
"""


class CompatError(Exception):
    """호환 계층 공통 예외."""


class CompatEmptyError(CompatError):
    """표의 upsert 결과가 0행 — 조용히 성공으로 기록하지 않는다(플랜 §2-1)."""


class CompatSchemaError(CompatError):
    """대상 sqlite 의 기존 스키마가 v3 선언과 다르다 — 쓰지 않고 멈춘다(플랜 §7)."""


@dataclass(frozen=True)
class TableResult:
    """표 1개의 upsert 결과."""

    n_rows: int                 # 실제로 넣은 행
    n_skipped: int              # v3 NOT NULL/PK 를 못 채워 넣지 못한 행(GAP-1 저녁 T 행 등)
    sources: dict[str, str]     # 원천 표 → build_id


@dataclass(frozen=True)
class ExportResult:
    """export() 한 번의 산출 — `_compat_meta` 에 그대로 실린다."""

    date: str
    basis: str
    target: str
    exported_at: str
    window: dict[str, object]
    consensus_asof: str
    # GAP-1/D-8 — `basis='evening'` 이라 daily_prices 에서 뺀 행 수(daily_prices 미포함이면 None)
    n_evening_rows_skipped: int | None = None
    tables: dict[str, TableResult] = field(default_factory=dict)
    equity_builds: dict[str, str] = field(default_factory=dict)
    stage_builds: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        """stdout 한 줄 요약 — 표별 행수(괄호는 못 넣은 행)."""
        parts = [f"{t}={r.n_rows}" + (f"(+{r.n_skipped} skipped)" if r.n_skipped else "")
                 for t, r in self.tables.items()]
        if self.n_evening_rows_skipped:
            parts.append(f"evening_skipped={self.n_evening_rows_skipped}")
        return (f"compat date={self.date} basis={self.basis} target={self.target} "
                f"consensus_asof={self.consensus_asof} | " + " ".join(parts))


def _parse_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as e:
        raise CompatError(f"{label} 은 YYYYMMDD 여야 한다: {value!r}") from e


def _parquet_source(root: Path, table: str, kind: str) -> tuple[str, str]:
    """`<root>/<table>` 의 current_build 파티션을 읽는 관계식과 build_id.

    equity 산출은 hive 축이 없고(`views.parquet_source` 와 같은 규약), stage 는 `year=` 축이
    있어 `hive_partitioning=true` + `union_by_name=true` 로 읽는다(`equity.contract` 규약).
    """
    pb = inputs.resolve(root, table)
    globs = ", ".join(f"'{Path(g).resolve()}'" for g in pb.globs)
    if kind == STAGE:
        return f"read_parquet([{globs}], hive_partitioning=true, union_by_name=true)", pb.build_id
    return f"read_parquet([{globs}], hive_partitioning=false)", pb.build_id


def _reference_schema() -> dict[str, list[tuple[str, str]]]:
    """`v3_schema.sql` 을 메모리 sqlite 에 적용해 얻은 표 → [(컬럼, 타입)]."""
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {n: [(r[1], r[2]) for r in con.execute(f"PRAGMA table_info({n})")] for n in names}
    finally:
        con.close()


def _open_target(target: Path) -> sqlite3.Connection:
    """v3 `backend/db/connection.py:9-17` 과 같은 PRAGMA.

    `BEGIN` 을 우리가 직접 쓰므로 autocommit(isolation_level=None) 이다.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(target), isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _ensure_schema(con: sqlite3.Connection, tables: list[str]) -> dict[str, list[str]]:
    """없는 표는 `v3_schema.sql` 로 만들고, 있는 표는 컬럼이 같은지 본다.

    다르면 `CompatSchemaError` — v3 가 마이그레이션으로 스키마를 바꾼 경우이므로 쓰지 않는다
    (플랜 §7 위험표 2행). 돌려주는 값은 표 → **필수 컬럼**(NOT NULL ∪ PK) 목록이다.
    """
    ref = _reference_schema()
    con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    required: dict[str, list[str]] = {}
    for table in tables:
        info = con.execute(f"PRAGMA table_info({table})").fetchall()
        got = [(r[1], r[2]) for r in info]
        if got != ref[table]:
            raise CompatSchemaError(
                f"대상 스키마가 v3 선언과 다르다: table={table} got={got} want={ref[table]}")
        # rowid 표의 TEXT PRIMARY KEY 는 notnull 플래그가 0 이라 PK 를 따로 더한다.
        required[table] = sorted({r[1] for r in info if r[3] == 1}
                                 | {r[1] for r in info if r[5] > 0})
    return required


def _render(mapping: TableMapping, roots: dict[str, Path], params: dict[str, str],
            builds: dict[str, dict[str, str]]) -> tuple[str, dict[str, str]]:
    """매핑 SQL 의 원천 자리를 `read_parquet(...)` 로, 나머지를 스칼라로 채운다."""
    kind = mapping.source_kind
    assert kind is not None
    used: dict[str, str] = {}
    sources: dict[str, str] = {}
    for table in mapping.sources:
        expr, build_id = _parquet_source(roots[kind], table, kind)
        sources[table] = expr
        used[table] = build_id
        builds[kind][table] = build_id
    return mapping.sql.format(**sources, **params), used


def _upsert(con: sqlite3.Connection, mapping: TableMapping, cur: duckdb.DuckDBPyConnection,
            required: list[str]) -> tuple[int, int]:
    """표 하나를 한 트랜잭션으로 넣는다. 돌려주는 값은 (넣은 행, 못 넣은 행)."""
    cols = list(mapping.columns)
    got = [d[0] for d in (cur.description or [])]
    if got != cols:
        raise CompatError(f"SELECT 컬럼이 선언과 다르다: table={mapping.v3_table} "
                          f"got={got} want={cols}")
    need = [cols.index(c) for c in required]
    placeholders = ", ".join("?" * len(cols))
    quoted = ", ".join(f'"{c}"' for c in cols)
    sql = f'INSERT OR REPLACE INTO "{mapping.v3_table}" ({quoted}) VALUES ({placeholders})'
    n_rows = n_skipped = 0
    con.execute("BEGIN")
    try:
        while True:
            chunk = cur.fetchmany(FETCH_CHUNK)
            if not chunk:
                break
            good = [r for r in chunk if all(r[i] is not None for i in need)]
            n_skipped += len(chunk) - len(good)
            if good:
                con.executemany(sql, good)
                n_rows += len(good)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    if n_rows == 0:
        raise CompatEmptyError(
            f"upsert 0행: table={mapping.v3_table} skipped={n_skipped} — 원천 판·창·as-of 확인")
    return n_rows, n_skipped


def _count_evening_skipped(duck: duckdb.DuckDBPyConnection, roots: dict[str, Path],
                           params: dict[str, str]) -> int:
    """GAP-1/D-8 — 같은 창에서 `basis='evening'` 이라 daily_prices 에서 뺀 행 수."""
    expr, _ = _parquet_source(roots[EQUITY], "price_daily", EQUITY)
    row = duck.execute(EVENING_SKIPPED_SQL.format(price_daily=expr, **params)).fetchone()
    return 0 if row is None else int(row[0])


def _write_meta(con: sqlite3.Connection, result: ExportResult) -> None:
    con.execute(META_DDL)
    con.execute("BEGIN")
    try:
        con.execute(
            f'INSERT OR REPLACE INTO {META_TABLE} (exported_at, date, basis, equity_builds, '
            f'stage_builds, tables, "window", consensus_asof, n_evening_rows_skipped) '
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result.exported_at, result.date, result.basis,
             json.dumps(result.equity_builds, ensure_ascii=False, sort_keys=True),
             json.dumps(result.stage_builds, ensure_ascii=False, sort_keys=True),
             json.dumps({t: asdict(r) for t, r in result.tables.items()},
                        ensure_ascii=False, sort_keys=True),
             json.dumps(result.window, ensure_ascii=False, sort_keys=True),
             result.consensus_asof, result.n_evening_rows_skipped))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _selected(tables: list[str] | None) -> list[TableMapping]:
    if tables is None:
        return [m for m in MAPPINGS if m.source_kind is not None]
    out: list[TableMapping] = []
    for name in tables:
        m = BY_TABLE.get(name)
        if m is None:
            raise CompatError(f"모르는 표: {name!r} (가능: {sorted(BY_TABLE)})")
        if m.source_kind is None:
            raise CompatError(f"{name} 은 아직 원천이 없다 — {m.note}")
        out.append(m)
    return out


def export(equity_root: Path, stage_root: Path, date: str, basis: str, target: Path,
           tables: list[str] | None = None, full: bool = False,
           window_days: int | None = None, consensus_asof: str | None = None) -> ExportResult:
    """equity/stage 판을 읽어 v3 `quant.db` 9표 중 지정 표를 upsert 한다.

    date·consensus_asof 는 YYYYMMDD. `full=False`(기본)면 최근 `INCREMENTAL_DAYS` 달력일만,
    `full=True` 면 창 전체를 다시 넣는다. 스냅샷 표(`stocks`·리비전·재무)는 창과 무관하게
    as_of 최신 한 판이다.
    """
    as_of = _parse_date(date, "--date")
    if basis not in BASES:
        raise CompatError(f"--basis 는 {BASES} 중 하나여야 한다: {basis!r}")
    asof_cons = _parse_date(consensus_asof, "--consensus-asof") if consensus_asof else as_of
    window = window_days if window_days is not None else (
        FULL_WINDOW_DAYS if full else DEFAULT_WINDOW_DAYS)
    if window <= 0:
        raise CompatError(f"--window-days 는 양수여야 한다: {window}")
    from_date = as_of - timedelta(days=window if full else INCREMENTAL_DAYS)
    params = {
        "date": as_of.isoformat(),
        "from_date": from_date.isoformat(),
        "snap_from": (as_of - timedelta(days=SNAPSHOT_LOOKBACK_DAYS)).isoformat(),
        "consensus_asof": asof_cons.isoformat(),
        "asof_ym": as_of.strftime("%Y%m"),
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    roots = {EQUITY: Path(equity_root), STAGE: Path(stage_root)}
    selected = _selected(tables)

    builds: dict[str, dict[str, str]] = {EQUITY: {}, STAGE: {}}
    results: dict[str, TableResult] = {}
    con = _open_target(Path(target))
    duck = duckdb.connect()
    try:
        duck.execute(f"SET threads = {DUCKDB_THREADS}")
        duck.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
        required = _ensure_schema(con, [m.v3_table for m in selected])
        evening_skipped: int | None = None
        for mapping in selected:
            sql, used = _render(mapping, roots, params, builds)
            cur = duck.execute(sql)
            n_rows, n_skipped = _upsert(con, mapping, cur, required[mapping.v3_table])
            results[mapping.v3_table] = TableResult(n_rows, n_skipped, used)
            if mapping.v3_table == "daily_prices":
                evening_skipped = _count_evening_skipped(duck, roots, params)
        result = ExportResult(
            date=as_of.isoformat(), basis=basis, target=str(target),
            exported_at=params["exported_at"],
            window={"days": window, "full": full, "from_date": params["from_date"],
                    "to_date": params["date"]},
            consensus_asof=params["consensus_asof"],
            n_evening_rows_skipped=evening_skipped, tables=results,
            equity_builds=builds[EQUITY], stage_builds=builds[STAGE])
        _write_meta(con, result)
        return result
    finally:
        duck.close()
        con.close()
