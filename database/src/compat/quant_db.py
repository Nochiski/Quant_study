"""equity/stage 판 → v3 `quant.db` upsert (플랜 `2026-09-24-v3-merge.md` §5 T1.2 3).

읽기는 duckdb `read_parquet` 뿐이다 — `equity.duckdb` 카탈로그를 열지 않는다(카탈로그는 매크로
전용이고 그림자 실행은 표 직독이면 충분하다). 판 선택은 MANIFEST 정본(`equity.inputs.resolve`
의 `current_build`, 또는 `--builds-from` 이 준 build_id) — `v=*` 맨 glob 금지(STAGE_HANDOFF §1).

쓰기는 표 단위 한 트랜잭션(`BEGIN … INSERT OR REPLACE … COMMIT`)이다. 부분 기록이 읽히면
안 되기 때문(플랜 §2-1). 표 하나라도 0행이면 `CompatEmptyError` — 조용한 실패 금지(V2-7).

M1~M3 대상은 별도 파일 `data/compat/quant.db`, M4 부터 v3 파일 제자리(결정 D-2)다.

가드(1차 그림자 실행 뒤 리뷰 R1~R10 반영) — 전부 **쓰기 전/직후에 예외**로 멈춘다:
  · `--basis` 와 equity 판 접두(`e_`/`m_`) 불일치            (R5)
  · `price_daily`·`price_adj_daily` 판이 서로 다른 체인       (R9)
  · `daily_prices` 의 `adj_close` 결측 비율 > 1%              (R9)
  · 증분인데 대상 DB 가 얕다(종목당 세션 중앙값 < 260)         (R10)
  · `stocks` 종목 수 < 2,000 · `market` 어휘 위반             (R2 · R4)
  · 표별 건너뛴 행 비율 > 5%                                   (R7)
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from equity import inputs
from stage import manifest
from stage.model import basis_of_build_id, build_id_time

from .mappings import (
    BY_TABLE,
    EQUITY,
    ESTIMATE_TICKERS_SQL,
    EVENING_SKIPPED_SQL,
    MAPPINGS,
    STAGE,
    TableMapping,
)

SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "v3_schema.sql"

# `--full` 초기 적재 창(달력일). v3 모멘텀이 보는 240 행 ≈ 1년에 여유를 둔 2년.
FULL_WINDOW_DAYS = 730
# 증분 창 — 최근 10세션을 확실히 덮는 달력일. 연휴를 포함해도 10세션이 들어온다.
INCREMENTAL_DAYS = 14
# 스냅샷 표(`stocks`)가 as_of 이하 최신 세션 행을 찾을 때 훑는 창. 최장 연휴보다 넉넉하다.
SNAPSHOT_LOOKBACK_DAYS = 30
# 한 번에 sqlite 로 넘기는 행 수. duckdb 결과를 통째로 파이썬 객체로 올리지 않기 위한 값이다.
FETCH_CHUNK = 50_000
# duckdb 자원 — `equity.catalog` 와 같은 한도(DEFECT-C04: 서버 4코어를 v3 체인과 나눠 쓴다).
DUCKDB_THREADS = 3
DUCKDB_MEMORY_LIMIT = "8GB"

# ── 가드 상수 ────────────────────────────────────────────────────────────────
# v3 `stocks` 하한(v3 MIN_STOCK_COUNT 와 같은 값). 이보다 적으면 유니버스 산출이 깨진 것이고,
# 그대로 두면 아래 `is_active=0` 표시가 멀쩡한 종목을 대거 폐지로 만든다.
MIN_STOCK_COUNT = 2_000
# 표별 '넣지 못한 행' 허용 비율. 저녁 행 제외(basis 필터)는 여기에 안 든다 — 그건 별도 카운트다.
MAX_SKIP_RATIO = 0.05
# `daily_prices.adj_close` 결측 허용 비율. v3 모멘텀은 adj_close 를 먼저 보므로 결측이 늘면
# 같은 날 점수가 조용히 달라진다.
MAX_ADJ_NULL_RATIO = 0.01
# 증분 실행을 허용할 대상 DB 의 깊이(종목당 세션 수 중앙값). v3 모멘텀 240행 + 여유.
MIN_MEDIAN_SESSIONS = 260
# `price_daily` 와 `price_adj_daily` 판이 같은 체인인지 보는 시각 차 한도(시간).
BUILD_CHAIN_MAX_GAP_H = 3
# 판 접두어가 말하는 basis 중 '어느 쪽으로 내보내도 되는' 값. 수동 재빌드(`b_`)가 여기 든다.
_BASIS_ANY = "manual"
# 원천 관계식을 판 목록과 같은 dict 에 실을 때 쓰는 접두 — build_id 키와 섞이지 않게.
_EXPR = "__expr__"

BASES = ("evening", "morning")
# 사용자 결정 09-24 — "v3 유니버스는 추정치 데이터가 있는 종목만".
#   all       : `stocks.market_cap` 을 있는 그대로 싣는다(지금까지의 동작)
#   estimates : 당해 12월기 WISE 추정치(op·ni)가 없는 종목의 `market_cap` 을 NULL 로 둔다
#               → v3 엔진의 `market_cap >= min_market_cap` 필터가 그 종목을 빼고 돈다
MODEL_UNIVERSES = ("all", "estimates")
MARKET_VOCAB = ("KOSPI", "KOSDAQ")      # v3 `stocks.market` CHECK 제약과 같은 어휘
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
    n_evening_rows_skipped INTEGER,
    -- R9: 이번 창에서 쓴 daily_prices 중 adj_close 가 NULL 인 행 수.
    n_adj_close_null INTEGER,
    -- R6: 'ok' | 'failed'. 실패한 실행도 흔적을 남긴다(조용한 실패 금지).
    status         TEXT NOT NULL DEFAULT 'ok',
    failed_table   TEXT,
    -- 사용자 결정 09-24: 'all' | 'estimates' 와 그때 추정치를 가진 종목 수.
    model_universe TEXT,
    n_universe_with_estimates INTEGER
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
    n_skipped: int              # v3 NOT NULL/PK 를 못 채워 넣지 못한 행
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
    # R9 — 이번 창에서 쓴 daily_prices 중 adj_close 결측 행 수
    n_adj_close_null: int | None = None
    status: str = "ok"
    failed_table: str | None = None
    model_universe: str = "all"
    # 사용자 결정 09-24 — `market_cap` 을 살려 둔(= 추정치를 가진) 종목 수. all 이면 None
    n_universe_with_estimates: int | None = None
    tables: dict[str, TableResult] = field(default_factory=dict)
    equity_builds: dict[str, str] = field(default_factory=dict)
    stage_builds: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        """stdout 한 줄 요약 — 표별 행수(괄호는 못 넣은 행)."""
        parts = [f"{t}={r.n_rows}" + (f"(+{r.n_skipped} skipped)" if r.n_skipped else "")
                 for t, r in self.tables.items()]
        if self.n_evening_rows_skipped:
            parts.append(f"evening_skipped={self.n_evening_rows_skipped}")
        if self.n_adj_close_null:
            parts.append(f"adj_close_null={self.n_adj_close_null}")
        if self.n_universe_with_estimates is not None:
            parts.append(f"universe({self.model_universe})="
                         f"{self.n_universe_with_estimates}")
        return (f"compat date={self.date} basis={self.basis} target={self.target} "
                f"consensus_asof={self.consensus_asof} | " + " ".join(parts))


def _parse_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as e:
        raise CompatError(f"{label} 은 YYYYMMDD 여야 한다: {value!r}") from e


# ── 판 해석 ──────────────────────────────────────────────────────────────────
def _globs_expr(globs: Iterable[str], kind: str) -> str:
    """equity 산출은 hive 축이 없고(`views.parquet_source` 규약), stage 는 `year=` 축이 있다."""
    lit = ", ".join(f"'{Path(g).resolve()}'" for g in globs)
    if kind == STAGE:
        return f"read_parquet([{lit}], hive_partitioning=true, union_by_name=true)"
    return f"read_parquet([{lit}], hive_partitioning=false)"


def _parquet_source(root: Path, table: str, kind: str,
                    build_id: str | None = None) -> tuple[str, str]:
    """`<root>/<table>` 의 판 하나를 읽는 관계식과 build_id.

    `build_id` 를 주면 MANIFEST `builds[]` 에서 그 판을 찾는다(`--builds-from` 경로 — 과거
    날짜 비교는 `current_build` 가 아니라 그날 인계 이력이 가리킨 판이어야 한다). 없으면
    `CompatError` — 조용히 최신 판으로 떨어지지 않는다.
    """
    if build_id is None:
        pb = inputs.resolve(root, table)
        return _globs_expr(pb.globs, kind), pb.build_id
    table_root = root / table
    m = manifest.load(table_root / "MANIFEST.json")
    rec = next((b for b in m.builds if b.build_id == build_id), None)
    if rec is None:
        raise CompatError(
            f"--builds-from 이 가리킨 판이 MANIFEST 에 없다: table={table} "
            f"build_id={build_id} builds={[b.build_id for b in m.builds]}")
    globs = [str(table_root / str(p["path"]) / "*.parquet") for p in rec.partitions]
    return _globs_expr(globs, kind), build_id


def _load_builds_from(path: Path) -> dict[str, dict[str, str]]:
    """인계 이력 JSON(`data/deliver/history/<D>_<basis>.json`)의 판 목록.

    `scripts/build_chain.sh:152-162` 가 쓰는
    `{"equity_builds": {표: build_id}, "stage_builds": {…}}` 구조를 그대로 읽는다.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CompatError(f"--builds-from 을 읽지 못했다: {path} ({e})") from e
    if not isinstance(raw, dict):
        raise CompatError(f"--builds-from 이 객체가 아니다: {path}")
    out: dict[str, dict[str, str]] = {}
    for kind, key in ((EQUITY, "equity_builds"), (STAGE, "stage_builds")):
        got = raw.get(key)
        if not isinstance(got, dict) or not got:
            raise CompatError(f"--builds-from 에 {key} 가 없다: {path}")
        out[kind] = {str(k): str(v) for k, v in got.items()}
    return out


def _check_basis(equity_builds: dict[str, str], basis: str) -> None:
    """R5 — 저녁 판을 아침으로(또는 그 반대로) 내보내지 않는다.

    수동 재빌드(`b_`)는 판 축이 없으므로 어느 쪽으로도 허용한다 — 접두어가 명시적으로
    `e_`/`m_` 인 판만 `--basis` 와 맞춘다.
    """
    for table, build_id in sorted(equity_builds.items()):
        got = basis_of_build_id(build_id)
        if got not in (_BASIS_ANY, basis):
            raise CompatError(
                f"판 접두어가 --basis 와 다르다: table={table} build_id={build_id} "
                f"판={got} --basis={basis}")


def _check_price_chain(equity_builds: dict[str, str]) -> None:
    """R9 — `price_daily` 와 `price_adj_daily` 가 같은 체인에서 나온 판인지.

    둘이 어긋나면 조정계수가 다른 날짜 기준이 되어 `adj_close` 가 조용히 틀린다. 판 접두어
    (판 축)와 build_id 가 박은 UTC 시각(≤ 3시간)으로 본다. 시각을 못 읽는 id(픽스처·구 기록)는
    접두어만 본다.
    """
    a, b = equity_builds.get("price_daily"), equity_builds.get("price_adj_daily")
    if a is None or b is None:
        return
    if basis_of_build_id(a) != basis_of_build_id(b):
        raise CompatError(f"price_daily 와 price_adj_daily 판 축이 다르다: {a} vs {b}")
    ta, tb = build_id_time(a), build_id_time(b)
    if ta is None or tb is None:
        return
    gap_h = abs((ta - tb).total_seconds()) / 3600
    if gap_h > BUILD_CHAIN_MAX_GAP_H:
        raise CompatError(
            f"price_daily 와 price_adj_daily 빌드 시각 차가 {gap_h:.1f}시간 "
            f"(한도 {BUILD_CHAIN_MAX_GAP_H}): {a} vs {b}")


# ── 대상 sqlite ──────────────────────────────────────────────────────────────
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
    """대상 sqlite 연결.

    PRAGMA 는 v3 `backend/db/connection.py:9-17` 의 여섯 중 **셋**(WAL · synchronous NORMAL ·
    busy_timeout 5s)만 건다. 나머지 셋은 우리 쓰기와 무관하다 — cache_size·mmap_size 는 성능
    조정값이고 foreign_keys 는 이 9표에 외래키가 없다. `BEGIN` 을 우리가 직접 쓰므로
    autocommit(isolation_level=None) 이다.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(target), isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _existing_tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


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


def _guard_incremental(con: sqlite3.Connection, existed: set[str]) -> None:
    """R10 — 얕은 대상 DB 에 증분을 얹으면 모멘텀 창이 모자란 채로 v3 가 돈다.

    `daily_prices` 가 없거나 종목당 세션 수 중앙값이 `MIN_MEDIAN_SESSIONS` 미만이면 거부하고
    `--full` 을 요구한다.
    """
    if "daily_prices" not in existed:
        raise CompatError("증분 거부: 대상 DB 에 daily_prices 가 없다 — 첫 적재는 --full 로")
    counts = [int(r[0]) for r in con.execute(
        "SELECT count(*) FROM daily_prices GROUP BY stock_code")]
    med = statistics.median(counts) if counts else 0
    if med < MIN_MEDIAN_SESSIONS:
        raise CompatError(
            f"증분 거부: 대상 daily_prices 가 얕다(종목당 세션 중앙값 {med:.0f} < "
            f"{MIN_MEDIAN_SESSIONS}) — v3 모멘텀 창이 모자란다. --full 로 다시 적재해라")


# ── 표 upsert ────────────────────────────────────────────────────────────────
def _render(mapping: TableMapping, params: dict[str, str],
            builds: dict[str, dict[str, str]]) -> tuple[str, dict[str, str]]:
    """매핑 SQL 의 원천 자리를 `read_parquet(...)` 로, 나머지를 스칼라로 채운다."""
    kind = mapping.source_kind
    assert kind is not None
    sources = {t: builds[kind][_EXPR + t] for t in mapping.sources}
    used = {t: builds[kind][t] for t in mapping.sources}
    return mapping.sql.format(**sources, **params), used


def _chunks(cur: duckdb.DuckDBPyConnection) -> Iterator[list[tuple]]:
    """duckdb 결과를 통째로 올리지 않고 조각으로 흘린다."""
    while True:
        rows = cur.fetchmany(FETCH_CHUNK)
        if not rows:
            return
        yield rows


def _upsert(con: sqlite3.Connection, mapping: TableMapping, columns: list[str],
            chunks: Iterable[list[tuple]], required: list[str],
            post: Callable[[sqlite3.Connection], None] | None = None) -> tuple[int, int]:
    """표 하나를 한 트랜잭션으로 넣는다. 돌려주는 값은 (넣은 행, 못 넣은 행).

    `post` 는 COMMIT 직전에 같은 트랜잭션에서 도는 뒷정리다(`stocks` 의 `is_active=0` 표시).
    """
    cols = list(mapping.columns)
    if columns != cols:
        raise CompatError(f"SELECT 컬럼이 선언과 다르다: table={mapping.v3_table} "
                          f"got={columns} want={cols}")
    need = [cols.index(c) for c in required]
    placeholders = ", ".join("?" * len(cols))
    quoted = ", ".join(f'"{c}"' for c in cols)
    sql = f'INSERT OR REPLACE INTO "{mapping.v3_table}" ({quoted}) VALUES ({placeholders})'
    n_rows = n_skipped = 0
    con.execute("BEGIN")
    try:
        for chunk in chunks:
            good = [r for r in chunk if all(r[i] is not None for i in need)]
            n_skipped += len(chunk) - len(good)
            if good:
                con.executemany(sql, good)
                n_rows += len(good)
        if post is not None:
            post(con)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    if n_rows == 0:
        raise CompatEmptyError(
            f"upsert 0행: table={mapping.v3_table} skipped={n_skipped} — 원천 판·창·as-of 확인")
    ratio = n_skipped / (n_rows + n_skipped)
    if ratio > MAX_SKIP_RATIO:
        raise CompatError(
            f"건너뛴 행 비율 {ratio:.1%} > {MAX_SKIP_RATIO:.0%}: table={mapping.v3_table} "
            f"rows={n_rows} skipped={n_skipped} — v3 NOT NULL 열이 비어 있다")
    return n_rows, n_skipped


def _stocks_rows(cur: duckdb.DuckDBPyConnection, columns: list[str]) -> list[tuple]:
    """`stocks` 는 종목당 1행이라 전부 올려 두고 어휘·하한을 먼저 본다(R2 · R4)."""
    rows = cur.fetchall()
    if len(rows) < MIN_STOCK_COUNT:
        raise CompatError(
            f"stocks 종목 수 {len(rows)} < {MIN_STOCK_COUNT} — 유니버스 산출이 깨졌다. "
            f"이대로 넣으면 빠진 종목이 폐지로 표시된다")
    i_code, i_market = columns.index("stock_code"), columns.index("market")
    bad = [(r[i_code], r[i_market]) for r in rows if r[i_market] not in MARKET_VOCAB]
    if bad:
        raise CompatError(
            f"stocks.market 어휘 위반 {len(bad)}건(v3 CHECK {MARKET_VOCAB}): {bad[:5]}")
    return rows


def _estimate_tickers(duck: duckdb.DuckDBPyConnection, expr: str,
                      params: dict[str, str]) -> set[str]:
    """당해 12월기 WISE 추정치(op·ni)를 가진 종목 — 사용자 결정 09-24."""
    rows = duck.execute(
        ESTIMATE_TICKERS_SQL.format(stg_consensus_annual=expr, **params)).fetchall()
    return {str(r[0]) for r in rows}


def _apply_model_universe(rows: list[tuple], columns: list[str],
                          keep: set[str]) -> tuple[list[tuple], int]:
    """추정치가 없는 종목의 `market_cap` 을 NULL 로 바꾼다(행은 그대로 둔다).

    v3 엔진은 `market_cap` 으로만 유니버스를 자르므로(engine.py:71-78) 이것이 v3 코드를
    고치지 않고 "추정치 보유 종목" 유니버스를 만드는 방법이다. 이름·시장 열은 남아
    브리핑·뉴스·unitelegram 의 조인이 깨지지 않는다.
    """
    i_code, i_cap = columns.index("stock_code"), columns.index("market_cap")
    out: list[tuple] = []
    n_kept = 0
    for r in rows:
        if r[i_code] in keep:
            n_kept += 1
            out.append(r)
            continue
        row = list(r)
        row[i_cap] = None
        out.append(tuple(row))
    return out, n_kept


def _mark_delisted(exported_at: str) -> Callable[[sqlite3.Connection], None]:
    """R2 — 이번 판에 없는 종목을 `is_active=0` 으로(v3 `mark_delisted` 와 같은 성질).

    이번 판의 행은 전부 `updated_at = exported_at` 으로 들어갔으므로 그 값이 아닌 행이
    '이번 유니버스에 없는 종목' 이다. `stocks` 트랜잭션 안에서 돈다 — 하한(`MIN_STOCK_COUNT`)을
    통과한 실행에서만 여기까지 온다.
    """
    def run(con: sqlite3.Connection) -> None:
        con.execute("UPDATE stocks SET is_active = 0 WHERE updated_at <> ? AND is_active <> 0",
                    (exported_at,))
    return run


def _count_evening_skipped(duck: duckdb.DuckDBPyConnection, expr: str,
                           params: dict[str, str]) -> int:
    """GAP-1/D-8 — 같은 창에서 `basis='evening'` 이라 daily_prices 에서 뺀 행 수."""
    row = duck.execute(EVENING_SKIPPED_SQL.format(price_daily=expr, **params)).fetchone()
    return 0 if row is None else int(row[0])


def _check_adj_close(con: sqlite3.Connection, params: dict[str, str]) -> int:
    """R9 — 이번 창에 쓴 daily_prices 의 `adj_close` 결측 비율. 1% 넘으면 예외."""
    row = con.execute(
        "SELECT count(*), sum(CASE WHEN adj_close IS NULL THEN 1 ELSE 0 END) "
        "FROM daily_prices WHERE trade_date >= ? AND trade_date <= ?",
        (params["from_date"], params["date"])).fetchone()
    total, nulls = (0, 0) if row is None else (int(row[0]), int(row[1] or 0))
    if total and nulls / total > MAX_ADJ_NULL_RATIO:
        raise CompatError(
            f"daily_prices.adj_close 결측 {nulls}/{total} = {nulls / total:.1%} > "
            f"{MAX_ADJ_NULL_RATIO:.0%} — price_adj_daily 판을 확인해라(v3 모멘텀 입력)")
    return nulls


def _ensure_meta_columns(con: sqlite3.Connection) -> None:
    """옛 판이 만든 `_compat_meta` 에 뒤에 생긴 열을 덧댄다(v3 MIGRATION_SQL 과 같은 방식)."""
    have = {r[1] for r in con.execute(f"PRAGMA table_info({META_TABLE})")}
    for name, decl in (("n_evening_rows_skipped", "INTEGER"), ("n_adj_close_null", "INTEGER"),
                       ("status", "TEXT"), ("failed_table", "TEXT"),
                       ("model_universe", "TEXT"), ("n_universe_with_estimates", "INTEGER")):
        if name not in have:
            con.execute(f"ALTER TABLE {META_TABLE} ADD COLUMN {name} {decl}")


def _write_meta(con: sqlite3.Connection, result: ExportResult) -> None:
    con.execute(META_DDL)
    _ensure_meta_columns(con)
    con.execute("BEGIN")
    try:
        con.execute(
            f'INSERT OR REPLACE INTO {META_TABLE} (exported_at, date, basis, equity_builds, '
            f'stage_builds, tables, "window", consensus_asof, n_evening_rows_skipped, '
            f"n_adj_close_null, status, failed_table, model_universe, "
            f"n_universe_with_estimates) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result.exported_at, result.date, result.basis,
             json.dumps(result.equity_builds, ensure_ascii=False, sort_keys=True),
             json.dumps(result.stage_builds, ensure_ascii=False, sort_keys=True),
             json.dumps({t: asdict(r) for t, r in result.tables.items()},
                        ensure_ascii=False, sort_keys=True),
             json.dumps(result.window, ensure_ascii=False, sort_keys=True),
             result.consensus_asof, result.n_evening_rows_skipped, result.n_adj_close_null,
             result.status, result.failed_table, result.model_universe,
             result.n_universe_with_estimates))
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


def _resolve_sources(selected: list[TableMapping], roots: dict[str, Path],
                     pinned: dict[str, dict[str, str]] | None,
                     extra: Iterable[tuple[str, str]] = ()) -> dict[str, dict[str, str]]:
    """쓰기 전에 원천 판을 전부 해석한다 — 판 가드(R5 · R9)를 먼저 걸기 위해서다."""
    builds: dict[str, dict[str, str]] = {EQUITY: {}, STAGE: {}}
    wanted = [(m.source_kind, t) for m in selected for t in m.sources] + list(extra)
    for kind, table in wanted:
        assert kind is not None
        if table in builds[kind]:
            continue
        want = pinned[kind].get(table) if pinned is not None else None
        if pinned is not None and want is None:
            raise CompatError(f"--builds-from 에 {kind} {table} 판이 없다")
        expr, build_id = _parquet_source(roots[kind], table, kind, want)
        builds[kind][table] = build_id
        builds[kind][_EXPR + table] = expr
    return builds


def _build_ids(builds: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in builds.items() if not k.startswith(_EXPR)}


def export(equity_root: Path, stage_root: Path, date: str, basis: str, target: Path,
           tables: list[str] | None = None, full: bool = False,
           window_days: int | None = None, consensus_asof: str | None = None,
           builds_from: Path | None = None,
           model_universe: str = "all") -> ExportResult:
    """equity/stage 판을 읽어 v3 `quant.db` 9표 중 지정 표를 upsert 한다.

    date·consensus_asof 는 YYYYMMDD. `full=False`(기본)면 최근 `INCREMENTAL_DAYS` 달력일만,
    `full=True` 면 `window_days`(기본 `FULL_WINDOW_DAYS`) 창 전체를 다시 넣는다. 스냅샷
    표(`stocks`·리비전·재무)는 창과 무관하게 as_of 최신 한 판이다.
    `builds_from` 은 인계 이력 JSON 경로 — 그 판으로 고정해 읽는다(과거 날짜 비교용).
    `model_universe='estimates'` 면 당해 12월기 WISE 추정치가 없는 종목의 `stocks.market_cap`
    을 NULL 로 두어 v3 엔진 유니버스에서 뺀다(사용자 결정 09-24).
    """
    as_of = _parse_date(date, "--date")
    if basis not in BASES:
        raise CompatError(f"--basis 는 {BASES} 중 하나여야 한다: {basis!r}")
    if model_universe not in MODEL_UNIVERSES:
        raise CompatError(
            f"--model-universe 는 {MODEL_UNIVERSES} 중 하나여야 한다: {model_universe!r}")
    asof_cons = _parse_date(consensus_asof, "--consensus-asof") if consensus_asof else as_of
    if window_days is not None and not full:
        raise CompatError("--window-days 는 --full 과 함께만 쓴다 — 증분 창은 "
                          f"{INCREMENTAL_DAYS}일 고정이다")
    if window_days is not None and window_days <= 0:
        raise CompatError(f"--window-days 는 양수여야 한다: {window_days}")
    span = (window_days or FULL_WINDOW_DAYS) if full else INCREMENTAL_DAYS
    params = {
        "date": as_of.isoformat(),
        "from_date": (as_of - timedelta(days=span)).isoformat(),
        "snap_from": (as_of - timedelta(days=SNAPSHOT_LOOKBACK_DAYS)).isoformat(),
        "consensus_asof": asof_cons.isoformat(),
        "asof_ym": as_of.strftime("%Y%m"),
        "asof_fy": f"{as_of.year}12",        # 당해 12월기 — 추정치 유니버스 판정 기준
        "exported_at": datetime.now(UTC).isoformat(timespec="microseconds"),
    }
    roots = {EQUITY: Path(equity_root), STAGE: Path(stage_root)}
    selected = _selected(tables)
    pinned = _load_builds_from(builds_from) if builds_from is not None else None

    want_estimates = (model_universe == "estimates"
                      and any(m.v3_table == "stocks" for m in selected))
    builds = _resolve_sources(
        selected, roots, pinned,
        [(STAGE, "stg_consensus_annual")] if want_estimates else [])
    equity_builds, stage_builds = _build_ids(builds[EQUITY]), _build_ids(builds[STAGE])
    _check_basis(equity_builds, basis)
    _check_price_chain(equity_builds)

    window = {"days": span, "full": full, "from_date": params["from_date"],
              "to_date": params["date"]}
    results: dict[str, TableResult] = {}
    evening_skipped: int | None = None
    adj_null: int | None = None
    n_with_est: int | None = None
    con = _open_target(Path(target))
    duck = duckdb.connect()
    try:
        duck.execute(f"SET threads = {DUCKDB_THREADS}")
        duck.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
        # 진행 막대가 `| tee` 로그를 제어문자로 덮는다(1차 그림자 실행 관찰).
        duck.execute("SET enable_progress_bar=false")
        existed = _existing_tables(con)
        required = _ensure_schema(con, [m.v3_table for m in selected])
        if not full and any(m.v3_table == "daily_prices" for m in selected):
            _guard_incremental(con, existed)
        current: str | None = None
        try:
            for mapping in selected:
                current = mapping.v3_table
                sql, used = _render(mapping, params, builds)
                cur = duck.execute(sql)
                columns = [d[0] for d in (cur.description or [])]
                if mapping.v3_table == "stocks":
                    rows = _stocks_rows(cur, columns)
                    if want_estimates:
                        rows, n_with_est = _apply_model_universe(
                            rows, columns,
                            _estimate_tickers(
                                duck, builds[STAGE][_EXPR + "stg_consensus_annual"], params))
                    n_rows, n_skipped = _upsert(
                        con, mapping, columns, [rows], required["stocks"],
                        _mark_delisted(params["exported_at"]))
                else:
                    n_rows, n_skipped = _upsert(con, mapping, columns, _chunks(cur),
                                                required[mapping.v3_table])
                results[mapping.v3_table] = TableResult(n_rows, n_skipped, used)
                if mapping.v3_table == "daily_prices":
                    evening_skipped = _count_evening_skipped(
                        duck, builds[EQUITY][_EXPR + "price_daily"], params)
                    adj_null = _check_adj_close(con, params)
            current = None
        except Exception:
            # R6 — 실패한 실행도 `_compat_meta` 에 남긴다(어느 표에서 멈췄는지 포함).
            _write_meta(con, ExportResult(
                date=params["date"], basis=basis, target=str(target),
                exported_at=params["exported_at"], window=window,
                consensus_asof=params["consensus_asof"],
                n_evening_rows_skipped=evening_skipped, n_adj_close_null=adj_null,
                status="failed", failed_table=current,
                model_universe=model_universe,
                n_universe_with_estimates=n_with_est, tables=results,
                equity_builds=equity_builds, stage_builds=stage_builds))
            raise
        result = ExportResult(
            date=params["date"], basis=basis, target=str(target),
            exported_at=params["exported_at"], window=window,
            consensus_asof=params["consensus_asof"],
            n_evening_rows_skipped=evening_skipped, n_adj_close_null=adj_null,
            model_universe=model_universe, n_universe_with_estimates=n_with_est,
            tables=results, equity_builds=equity_builds, stage_builds=stage_builds)
        _write_meta(con, result)
        return result
    finally:
        duck.close()
        con.close()
