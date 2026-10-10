"""equity/stage 판 → v3 `quant.db` upsert (플랜 `2026-09-24-v3-merge.md` §5 T1.2 3).

읽기는 duckdb `read_parquet` 뿐이다 — `equity.duckdb` 카탈로그를 열지 않는다(카탈로그는 매크로
전용이고 그림자 실행은 표 직독이면 충분하다). 판 선택은 MANIFEST 정본(`equity.inputs.resolve`
의 `current_build`, 또는 `--builds-from` 이 준 build_id) — `v=*` 맨 glob 금지(STAGE_HANDOFF §1).

쓰기는 표 단위 한 트랜잭션(`BEGIN … INSERT OR REPLACE … COMMIT`)이다. 부분 기록이 읽히면
안 되기 때문(플랜 §2-1). 표 하나라도 0행이면 `CompatEmptyError` — 조용한 실패 금지(V2-7).

점수 두 표(`score_history`·`_v2`, QL-C · T-16)는 모델 판(`--model-root`)이 원천이다. 판은 `--date D
--basis` 의 모델 성공 판 하나로 고정하고(`_resolve_model` — 최신 판으로 대체하지 않는다, P1), 쓰기는
**날짜 단위 교체**다: 같은 트랜잭션에서 그 `score_date` 행을 모두 지우고 새 판 행을 넣는다. 다른
날짜는 건드리지 않는다. 판 id 는 `_compat_meta.model_builds` 에 spec 별로 남는다.

`daily_prices` 는 v3 가 쌓던 모양이다 — adj_close 는 KRX 기준가 사슬 K 로 창 안 마지막 행 = 원종가(T-40),
시·고·저·종가·거래량은 v3 '최근 5행 덮어쓰기' 관례(T-41, 식은 `mappings` daily_prices 주석). 창 안에 사건 단계가
든 종목은 대상의 창 밖 옛 행 adj_close 도 같은 트랜잭션에서 다시 맞춘다(`_rebase_outside` — v3 가 사건 뒤 종목
전 기간을 다시 쓰는 것과 같은 결과). 다시 맞춘 종목은 `_compat_meta.tables.daily_prices.rebase` 에 남고 제자리
반영(`v3_post`)의 범위가 된다.

`--basis evening`(장 마감 판, 컷오버 T-2)의 `daily_prices`·`investor_detail_flows` 는 판(직전
거래일 D' 까지)에 T 날짜 행을 원장에서 얹는다(QL-D — `compat.t_rows`, 장 마감 판에서만 불러온다).
두 표는 basis 와 무관하게 그 `--date` 행을 **날짜 단위로 교체**한다(QL-C 와 같은 규칙) — 저녁에
원장으로 만든 T 행은 다음 날 아침 `--basis morning --date T` 가 KRX 행으로 통째로 바꾼다. 새 원천에
그날 행이 0 이면 지우기 전에 멈춘다. 다른 날짜는 지금처럼 `INSERT OR REPLACE` 다.

M1~M3 대상은 별도 파일 `data/compat/quant.db`, M4 부터 v3 파일 제자리(결정 D-2)다. 제자리 반영은 v3
파일에 직접 쓰지 않고 `scripts/v3_post.sh`(`compat.v3_post` — 스테이징 → 게이트 → 9표 한 트랜잭션, QL-F)로 한다.

가드(1차 그림자 실행 뒤 리뷰 R1~R10 반영) — 전부 **쓰기 전/직후에 예외**로 멈춘다:
  · `--basis` 와 equity 판 접두(`e_`/`m_`) 불일치            (R5 — 장 마감 판은 `m_` 도 받는다)
  · 장 마감 판: T 비거래일 · 원장 없음 · 판 이음매(마지막 세션 ≠ D') · T 행 0 ·
    대상에 이번보다 나중 ok 반영 기록(T-35 순서, 재생은 `--allow-older`)              (QL-D)
  · 날짜 단위 교체 표의 새 원천에 `--date` 행 0                                       (QL-D)
  · `daily_prices` 의 `adj_close` 결측 비율 > 1%              (R9 — 장 마감 판 T 단계 미상 NULL 이 몰린 날)
  · 제자리 반영(`--in-place`) 창이 5세션 미만(daily.calendar)    (QL-E MINOR-1 — T-41 덮어쓰기 행이 창 안에서 끝나야 한다)
  · 증분 창 시작일(10거래일 전)을 판정 달력으로 못 센다          (K1-9d — 영업일 가정 없음, K1-9 ⑦)
  · 증분인데 대상 DB 가 얕다(종목당 세션 중앙값 < 260)         (R10)
  · `stocks` 종목 수 < 2,000 · `market` 어휘 위반             (R2 · R4)
  · 표별 건너뛴 행 비율 > 5%                                   (R7)
"""
from __future__ import annotations

import itertools
import json
import sqlite3
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from daily import calendar as daily_calendar
from deliver.reader import DeliverError, load_run
from equity import handoff, inputs
from stage.model import basis_of_build_id

from .mappings import (
    BY_TABLE,
    DATE_REPLACED,
    EQUITY,
    ESTIMATE_TICKERS_SQL,
    EVENING_SKIPPED_SQL,
    MAPPINGS,
    MIN_WINDOW_SESSIONS,
    MODEL,
    NO_T_STEP,
    REBASE_SQL,
    STAGE,
    TableMapping,
)

SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "v3_schema.sql"

# `--full` 초기 적재 창(달력일). v3 모멘텀이 보는 240 행 ≈ 1년에 여유를 둔 2년.
FULL_WINDOW_DAYS = 730
# 증분 창 — as_of 이하 마지막 거래일부터 거꾸로 센 거래일 수(그날 포함, daily.calendar —
# K1-9d · T-14). 달력일로 세면 설·추석처럼 평일 휴장이 끼는 연휴에 7~8거래일로 줄어
# T-41 덮어쓰기 재현·자가 복구 창이 좁아진다.
INCREMENTAL_SESSIONS = 10
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
# `daily_prices.adj_close` 결측 허용 비율. K 사슬(QL-E)은 종가가 있으면 늘 값이 있어 결측은 장 마감 판 T 행의
# 단계 미상(전일대비 없음 · D' 종가 없음) NULL 뿐이다 — 한 날에 몰리면 원장이나 D' 판이 어긋난 것이다. 결측이 늘면
# 리서치센터 S2·S11·drilldown·가설이 그 종목을 조용히 잃는다.
MAX_ADJ_NULL_RATIO = 0.01
# 증분 실행을 허용할 대상 DB 의 깊이(종목당 세션 수 중앙값). v3 모멘텀 240행 + 여유.
MIN_MEDIAN_SESSIONS = 260
# 판 접두어가 말하는 basis 중 '어느 쪽으로 내보내도 되는' 값. 수동 재빌드(`b_`)가 여기 든다.
_BASIS_ANY = "manual"
# `--basis` 별로 더 받는 판 basis. 장 마감 판(evening, 컷오버 T-2)은 직전 거래일 연구 확정판
# `m_`(D') 위에 T 행만 원장에서 얹는다(QL-D). 그 판이 이미 T 의 KRX 행을 가졌는지 — R5 가 막으려던
# '아침 판을 저녁으로' 의 위험 — 는 접두가 아니라 데이터(판 이음매, `t_rows.check_seam`)로 본다.
_BASIS_ALSO: dict[str, tuple[str, ...]] = {"evening": ("morning",)}
# 원천 관계식을 판 목록과 같은 dict 에 실을 때 쓰는 접두 — build_id 키와 섞이지 않게.
_EXPR = "__expr__"
# 모델 판의 점수 표 파일 이름 — `model/build.py` SCORES_FILE · `deliver/reader.py` 파일 규약과 같다.
SCORES_FILE = "scores.parquet"

BASES = ("evening", "morning")
# 사용자 결정 09-24 — "v3 유니버스는 추정치 데이터가 있는 종목만".
#   all       : `stocks.market_cap` 을 있는 그대로 싣는다(지금까지의 동작)
#   estimates : 당해 12월기 WISE 추정치(op·ni)가 없는 종목의 `market_cap` 을 NULL 로 둔다
#               → v3 엔진의 `market_cap >= min_market_cap` 필터가 그 종목을 빼고 돈다
# **estimates 는 그림자(별도 파일) 전용이다**(T-19, QL-B). 제자리 반영(`in_place`) 뒤에는 v3 스코어링이
#   꺼지고(T-16) `market_cap` 소비자는 뉴스 preview 상위 100 · naver_ir 상위 600 · 엑셀 ·
#   unitelegram 이다 — NULL 을 넣으면 그들이 대부분의 종목을 잃는다. 그래서 제자리는 all 고정이고
#   estimates 를 함께 주면 쓰기 전에 멈춘다.
MODEL_UNIVERSES = ("all", "estimates")
# `--builds-from` 이 가리킨 판을 못 찾았을 때의 처리(서버 4일 재실행 실측).
#   error   : 멈춘다(기본). 그 판으로 재현해야 하는 비교에서는 이쪽이 맞다.
#   current : `current_build` 로 폴백하고 어느 표가 폴백했는지 남긴다.
# 폴백이 안전한 근거 — **stage 표**는 `fetched_date` 축의 append-only 다(STAGE_DESIGN §3).
#   새 판은 옛 행을 덮지 않고 뒤에 붙이므로 `current_build` + `consensus_asof` 필터는 그날
#   인계 판과 같은 행을 고른다(09-21 실측: 인계 판 m_20260922T000215_559494Z 가 stage keep=3
#   GC 로 사라졌지만 as-of 필터가 같은 스냅샷을 재현한다).
# ⚠ **equity 표 폴백은 다르다** — 격자·조정계수 표는 판마다 값이 바뀔 수 있어 과거 날짜 재현이
#   깨진다. `sector_snapshot` 처럼 표시용이고 자체 `snapshot_date` as-of 를 갖는 표만 안전하다
#   (09-18 실측: 그날 인계 JSON 에 `sector_snapshot` 키 자체가 없었다 — 표가 아직 없던 날).
#   QL-B(T-19) 뒤 `stocks.sector` 원천은 stage `stg_master_daily`(append-only)라 위 stage 규칙을 따른다.
#   가격·수급 표가 폴백 목록에 뜨면 그 날짜 비교 결과는 믿지 말고 원인을 먼저 본다.
BUILDS_MISSING = ("error", "current")
MARKET_VOCAB = ("KOSPI", "KOSDAQ")      # v3 `stocks.market` CHECK 제약과 같은 어휘
META_TABLE = "_compat_meta"
# `main.` 한정 — `compat.v3_post` 가 스테이징을 ATTACH 한 연결에서 본 파일 쪽 메타 표를 만들 때도 같은 DDL 을 쓴다.
META_DDL = f"""
CREATE TABLE IF NOT EXISTS main.{META_TABLE} (
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
    n_universe_with_estimates INTEGER,
    -- --builds-from 이 가리킨 판을 못 찾아 current_build 로 폴백한 표 목록(json 배열).
    builds_fallback TEXT,
    -- QL-C: 점수 표 원천 모델 판 json 객체(spec_id → build_id). 판 basis 는 위 basis 와 같다.
    model_builds   TEXT
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
    # 표별 추가 지표. `financial_summary` 는 원천별 채움 수(n_from_wise · n_from_dart)를, `daily_prices` 는
    # 이번에 쓴 trade_date = D 행 수(n_on_date — 제자리 반영 신선도 게이트, T-31 ③)를 싣는다.
    metrics: dict[str, int] = field(default_factory=dict)
    # 장 마감 판 T 행(QL-D) — 원천별 행 수 · 21:05 원장으로 대체한 종목 · 행 없는 종목
    # (`t_rows._info`)
    t_rows: dict[str, object] | None = None
    # `daily_prices` 창 밖 다시 맞춤(QL-E) — before(= 창 시작, 이 날 앞 행이 대상) · floor(대상 첫 날) ·
    # tickers(창 안에 KRX 기준가 단계가 든 종목) · n_rows(다시 쓴 창 밖 행) · n_null(equity 행이 없어 NULL 이 된 행).
    # `v3_post` 가 tickers 의 창 앞 행을 반영 범위에 더한다
    rebase: dict[str, object] | None = None


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
    # `--builds-from` 판을 못 찾아 current_build 로 떨어진 표(정렬된 실명)
    builds_fallback: tuple[str, ...] = ()
    tables: dict[str, TableResult] = field(default_factory=dict)
    equity_builds: dict[str, str] = field(default_factory=dict)
    stage_builds: dict[str, str] = field(default_factory=dict)
    # QL-C — 점수 표 원천 모델 판 {spec_id: build_id}
    model_builds: dict[str, str] = field(default_factory=dict)

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
        if self.builds_fallback:
            parts.append("fallback=" + ",".join(self.builds_fallback))
        for t, r in self.tables.items():
            rb_tickers = (r.rebase or {}).get("tickers")
            if isinstance(rb_tickers, list) and rb_tickers and r.rebase is not None:
                parts.append(f"{t}.rebase={len(rb_tickers)}종목/{r.rebase['n_rows']}행"
                             f"(null={r.rebase['n_null']})")
            if r.t_rows:
                i = r.t_rows
                parts.append(f"{t}.T={i['date']}(postclose={i['postclose']},"
                             f"kiwoom_2105={i['kiwoom_2105']},missing={i['missing']})")
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
    try:
        pb = inputs.resolve(root, table, build_id)
    except FileNotFoundError as e:
        raise CompatError(f"--builds-from 이 가리킨 판이 MANIFEST 에 없다: table={table} "
                          f"build_id={build_id} ({e})") from e
    return _globs_expr(pb.globs, kind), build_id


def _load_builds_from(path: Path) -> dict[str, dict[str, str]]:
    """인계 이력 JSON(`data/deliver/history/<D>_<basis>.json`)의 판 목록.

    읽기 규약은 `equity.handoff.load` 한 곳(factor_inputs `--builds-from` 과 공유)이다.
    compat 은 health 를 보지 않는다(지금 동작 유지).
    """
    try:
        h = handoff.load(path)
    except handoff.HandoffError as e:
        raise CompatError(str(e)) from e
    return {EQUITY: h.equity_builds, STAGE: h.stage_builds}


def _check_basis(equity_builds: dict[str, str], basis: str) -> None:
    """R5 — 저녁 판을 아침으로(또는 그 반대로) 내보내지 않는다.

    수동 재빌드(`b_`)는 판 축이 없으므로 어느 쪽으로도 허용한다 — 접두어가 명시적으로
    `e_`/`m_` 인 판만 `--basis` 와 맞춘다. 장 마감 판(evening)은 D' 아침 판 `m_` 도 받는다
    (`_BASIS_ALSO`).
    """
    for table, build_id in sorted(equity_builds.items()):
        got = basis_of_build_id(build_id)
        if got not in (_BASIS_ANY, basis, *_BASIS_ALSO.get(basis, ())):
            raise CompatError(
                f"판 접두어가 --basis 와 다르다: table={table} build_id={build_id} "
                f"판={got} --basis={basis}")


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
    비교는 열 이름 → 타입이다(순서 무관). 실물 v3 `stocks` 는 `delisted_date` 가 마이그레이션 ALTER 로
    `updated_at` 뒤에 붙어 선언 순서와 다르다(QL-F 로컬 v3 사본 실측). 쓰기는 열 이름으로 하므로 순서는
    결과에 영향이 없다.
    """
    ref = _reference_schema()
    con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    required: dict[str, list[str]] = {}
    for table in tables:
        info = con.execute(f"PRAGMA table_info({table})").fetchall()
        got = [(r[1], r[2]) for r in info]
        if sorted(got) != sorted(ref[table]):
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
    if kind == MODEL:
        # 점수 표 자리는 `{scores}` 하나다 — spec_id('scope@1.0')는 format 자리 이름이 될 수 없다
        spec = mapping.sources[0]
        return (mapping.sql.format(scores=builds[MODEL][_EXPR + spec], **params),
                {spec: builds[MODEL][spec]})
    pairs = [(kind, t) for t in mapping.sources] + list(mapping.cross_sources)
    sources = {t: builds[k][_EXPR + t] for k, t in pairs}
    used = {t: builds[k][t] for k, t in pairs}
    return mapping.sql.format(**sources, **params), used


def _chunks(cur: duckdb.DuckDBPyConnection) -> Iterator[list[tuple]]:
    """duckdb 결과를 통째로 올리지 않고 조각으로 흘린다."""
    while True:
        rows = cur.fetchmany(FETCH_CHUNK)
        if not rows:
            return
        yield rows


def _complete(row: tuple, need: list[int]) -> bool:
    """v3 필수 열(NOT NULL ∪ PK)이 다 찬 행 — `_upsert` 가 넣는 행의 정의."""
    return all(row[i] is not None for i in need)


def _count_on_date(chunks: Iterable[list[tuple]], columns: list[str], required: list[str],
                   column: str, value: str, tally: dict[str, int]) -> Iterator[list[tuple]]:
    """조각을 그대로 흘리며 `_upsert` 가 넣을 행 중 `column == value` 인 수를 `tally['n']` 에 센다.

    신선도(컷오버 트랙 T-31 ③) — 제자리 반영의 대상은 v3 본 파일 사본이라 D 행이 이미 있을 수 있다.
    '대상에 D 행이 있다' 가 아니라 '이번 실행이 D 행을 썼다' 를 남긴다.
    """
    i_col = columns.index(column)
    need = [columns.index(c) for c in required]
    for chunk in chunks:
        tally["n"] += sum(1 for r in chunk if r[i_col] == value and _complete(r, need))
        yield chunk


def _upsert(con: sqlite3.Connection, mapping: TableMapping, columns: list[str],
            chunks: Iterable[list[tuple]], required: list[str],
            post: Callable[[sqlite3.Connection], None] | None = None,
            pre: Callable[[sqlite3.Connection], None] | None = None) -> tuple[int, int]:
    """표 하나를 한 트랜잭션으로 넣는다. 돌려주는 값은 (넣은 행, 못 넣은 행).

    `pre` 는 BEGIN 직후 같은 트랜잭션에서 도는 앞정리다(점수 표의 그 날짜 행 삭제 — T-16).
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
        if pre is not None:
            pre(con)
        for chunk in chunks:
            good = [r for r in chunk if _complete(r, need)]
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


def _score_rows(cur: duckdb.DuckDBPyConnection, columns: list[str], table: str,
                d_iso: str, required: list[str]) -> list[tuple]:
    """점수 표는 날짜 단위로 갈아 끼우므로(T-16) 다 올려 두고 지우기 전에 본다.

    아래면 D 의 기존 행을 지운 채 일부만 남기지 않게 BEGIN 전에 멈춘다.
      · 0행
      · v3 필수 열(NOT NULL ∪ PK — `required`)이 빈 행 — `_upsert` 는 그 행만 건너뛰고
        나머지를 넣는다
      · 같은 `stock_code` 두 번 — PK 덮어쓰기로 한 행이 조용히 사라진다
      · 판 날짜(D)와 다른 `score_date` — 지운 날짜와 넣는 날짜가 어긋난다
    """
    rows = cur.fetchall()
    if not rows:
        raise CompatEmptyError(
            f"upsert 0행: table={table} — 모델 판 점수 표가 비었다"
            f"(D={d_iso} 기존 행은 그대로 둔다)")
    i_code = columns.index("stock_code")
    for col in required:
        i = columns.index(col)
        bad = [r[i_code] for r in rows if r[i] is None]
        if bad:
            raise CompatError(
                f"{table}: 필수 열 {col} 이 빈 행 {len(bad)}건 {bad[:5]} — 모델 판이 깨졌다, "
                "쓰지 않는다")
    dup = sorted(str(c) for c, n in Counter(r[i_code] for r in rows).items() if n > 1)
    if dup:
        raise CompatError(f"{table}: stock_code 중복 {len(dup)}종목 {dup[:5]} — 모델 판이 깨졌다, "
                          "쓰지 않는다")
    i_date = columns.index("score_date")
    other = sorted({str(r[i_date]) for r in rows if r[i_date] != d_iso})
    if other:
        raise CompatError(
            f"{table}: 판 날짜 {d_iso} 와 다른 score_date {other[:5]} — 모델 판이 깨졌다, "
            "쓰지 않는다")
    return rows


def _score_checked(duck: duckdb.DuckDBPyConnection, mapping: TableMapping,
                   params: dict[str, str], builds: dict[str, dict[str, str]],
                   required: list[str]) -> tuple[list[str], list[tuple]]:
    """점수 표 하나를 읽어 `_score_rows` 로 검사한다 — 쓰기 전 단계(리뷰 MINOR-2). (열, 행)."""
    sql, _ = _render(mapping, params, builds)
    cur = duck.execute(sql)
    columns = [d[0] for d in (cur.description or [])]
    return columns, _score_rows(cur, columns, mapping.v3_table, params["date"], required)


def _replace_score_date(table: str, d_iso: str) -> Callable[[sqlite3.Connection], None]:
    """T-16 — 그 `score_date` 행을 전부 지운다(새 행을 넣는 트랜잭션 안). 다른 날짜는 그대로다.

    `INSERT OR REPLACE` 만 쓰면 이번 판에 없는 종목의 그날 옛 행(v3 가 쓴 1,329종목 중 593 밖)이
    순위표에 남는다. 지우고 넣어야 그날 행이 이번 판과 정확히 같다.
    """
    def run(con: sqlite3.Connection) -> None:
        con.execute(f'DELETE FROM "{table}" WHERE score_date = ?', (d_iso,))
    return run


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


def _fin_source_counts(con: sqlite3.Connection) -> dict[str, int]:
    """T1.5 — `financial_summary` 행 중 각 원천이 채운 수.

    WISE 전용 열(`per`)과 DART 전용 열(`total_assets`)의 NOT NULL 수로 센다. 두 원천이 겹친
    행은 양쪽에 모두 계상된다(합이 행수보다 클 수 있다).
    """
    row = con.execute(
        "SELECT sum(CASE WHEN per IS NOT NULL THEN 1 ELSE 0 END), "
        "sum(CASE WHEN total_assets IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM financial_summary").fetchone()
    wise, dart = (0, 0) if row is None else (int(row[0] or 0), int(row[1] or 0))
    return {"n_from_wise": wise, "n_from_dart": dart}


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
            f"{MAX_ADJ_NULL_RATIO:.0%} — 장 마감 판이면 T 기준가를 모르는 종목(원장 전일대비 없음 · D' 종가 "
            "없음)이 몰렸다: 원장과 D' 판 price_daily 를 확인해라")
    return nulls


_REBASE_TABLE = "_compat_rebase"            # 임시 표(연결 단위) — 다시 쓸 (종목, 날짜, 시·고·저·종·거래량·adj)


def _rebase_plan(duck: duckdb.DuckDBPyConnection, con: sqlite3.Connection,
                 params: dict[str, str], builds: dict[str, dict[str, str]]
                 ) -> tuple[str, list[str], list[tuple]]:
    """QL-E — 창 안에 KRX 기준가 단계가 든 v3 종목과 그 종목의 창 밖 새 값(`mappings.REBASE_SQL`).

    대상 `daily_prices` 의 가장 이른 날을 하한으로 둔다(대상이 비었으면 창 시작 — 다시 맞출 행이 없다).
    돌려주는 값은 (하한, 종목 목록, [(종목, 날짜, 시, 고, 저, 종, 거래량, adj_close)]). 값이 없는 종목(그 구간에
    종가·거래량이 있는 equity 행 없음)도 목록에는 든다 — 그 종목의 창 밖 행은 기준을 모르므로 adj_close 가 NULL 이
    된다(P1).
    """
    row = con.execute("SELECT min(trade_date) FROM daily_prices").fetchone()
    floor = str(row[0]) if row is not None and row[0] is not None else params["from_date"]
    sources = {t: builds[EQUITY][_EXPR + t] for t in BY_TABLE["daily_prices"].sources}
    rows = duck.execute(REBASE_SQL.format(**sources, **params, rebase_floor=floor)).fetchall()
    tickers = sorted({str(r[0]) for r in rows})
    return floor, tickers, [(str(r[0]), *r[1:]) for r in rows if r[1] is not None]


def _rebase_outside(tickers: list[str], values: list[tuple], before: str,
                    tally: dict[str, object]) -> Callable[[sqlite3.Connection], None]:
    """QL-E — 목록 종목의 대상 창 밖 행(`trade_date < before`, 대상에 이미 있는 행만)을 다시 쓴다.

    시·고·저·종가·거래량은 T-41 값, adj_close 는 equity 원종가 × K(d) ÷ K(L) 이다(대상 close 는 덮어쓰기·옛 백필
    값일 수 있어 곱하지 않는다). 거래대금과 행 수는 그대로다(MINOR-1 — 반영이 며칠 끊겨 사건 직전 행이 창 밖으로
    밀려도 다음 반영이 바로잡는다). 새 값이 없는 날(equity 에 종가·거래량이 있는 행이 없다)은 시·고·저·종가·
    거래량을 두고 adj_close 만 NULL 로 한다(P1). `daily_prices` 를 넣는 트랜잭션 안(COMMIT 직전)에서 돈다. 다시 쓴
    행 수와 그중 NULL 로 둔 수를 `tally` 에 남긴다.
    """
    def run(con: sqlite3.Connection) -> None:
        t = _REBASE_TABLE
        con.execute(f"CREATE TEMP TABLE IF NOT EXISTS {t}_tickers (stock_code TEXT PRIMARY KEY)")
        con.execute(f"CREATE TEMP TABLE IF NOT EXISTS {t} (stock_code TEXT NOT NULL, "
                    "trade_date TEXT NOT NULL, open INTEGER, high INTEGER, low INTEGER, "
                    "close INTEGER, volume INTEGER, adj REAL, PRIMARY KEY (stock_code, trade_date))")
        con.execute(f"DELETE FROM temp.{t}_tickers")
        con.execute(f"DELETE FROM temp.{t}")
        con.executemany(f"INSERT INTO temp.{t}_tickers VALUES (?)", [(x,) for x in tickers])
        con.executemany(f"INSERT INTO temp.{t} VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
        set_rows = con.execute(
            "INSERT OR REPLACE INTO daily_prices (stock_code, trade_date, open, high, low, close, "
            "volume, amount, adj_close) "
            "SELECT r.stock_code, r.trade_date, r.open, r.high, r.low, r.close, r.volume, d.amount, "
            f"r.adj FROM temp.{t} r JOIN daily_prices d "
            "ON d.stock_code = r.stock_code AND d.trade_date = r.trade_date "
            "WHERE r.trade_date < ?", (before,)).rowcount
        null_rows = con.execute(
            "UPDATE daily_prices SET adj_close = NULL "
            f"WHERE stock_code IN (SELECT stock_code FROM temp.{t}_tickers) AND trade_date < ? "
            f"AND NOT EXISTS (SELECT 1 FROM temp.{t} r WHERE r.stock_code = daily_prices.stock_code "
            "AND r.trade_date = daily_prices.trade_date)", (before,)).rowcount
        tally["n_rows"] = set_rows + null_rows
        tally["n_null"] = null_rows
    return run


def _incremental_from(as_of: date, calendar_dir: Path | None) -> date:
    """K1-9d — 증분 창 시작일. as_of 이하 마지막 거래일에서 거꾸로 `INCREMENTAL_SESSIONS` 번째
    거래일(그날 포함).

    `_guard_window_sessions` 와 같은 달력·같은 계산이다. 달력을 못 읽으면 멈춘다(영업일 가정 없음,
    K1-9 ⑦).
    """
    try:
        cal = daily_calendar.load() if calendar_dir is None else daily_calendar.load(calendar_dir)
        last = as_of if cal.is_trading_day(as_of) else cal.prev_trading_day(as_of)
        return cal.prev_trading_day(last, INCREMENTAL_SESSIONS - 1)
    except (daily_calendar.CalendarUnavailable, KeyError) as e:
        raise CompatError(f"증분 창 시작일을 셀 수 없다(daily.calendar, "
                          f"calendar_dir={calendar_dir}): {e}") from e


def _guard_window_sessions(as_of: date, from_iso: str, calendar_dir: Path | None) -> None:
    """MINOR-1 — 제자리 반영 창이 `MIN_WINDOW_SESSIONS` 거래일 이상인가(daily.calendar).

    T-41 덮어쓰기 기준일 c 는 d 뒤 4번째 행이라 창이 그보다 좁으면 행이 최종값이 되기 전에 창을 떠난다. 창 끝(D, 휴장이면
    직전 거래일)에서 거꾸로 `MIN_WINDOW_SESSIONS` 번째 거래일이 창 시작 이상이어야 한다 — 창 전체를 걷지 않으므로 `--full`
    730일 창도 최근 연도 판정 파일만 있으면 된다. 달력을 못 읽으면 멈춘다(영업일 가정 없음, K1-9 ⑦).
    """
    try:
        cal = daily_calendar.load() if calendar_dir is None else daily_calendar.load(calendar_dir)
        last = as_of if cal.is_trading_day(as_of) else cal.prev_trading_day(as_of)
        first = cal.prev_trading_day(last, MIN_WINDOW_SESSIONS - 1)
    except (daily_calendar.CalendarUnavailable, KeyError) as e:
        raise CompatError(f"제자리 반영 창 세션을 셀 수 없다(daily.calendar, calendar_dir={calendar_dir}): "
                          f"{e}") from e
    if first.isoformat() < from_iso:
        raise CompatError(
            f"제자리 반영 창이 {MIN_WINDOW_SESSIONS}거래일보다 좁다: [{from_iso}, {as_of.isoformat()}] — "
            f"{MIN_WINDOW_SESSIONS}번째 거래일 {first.isoformat()} 이 창 밖이다. 사건 직전 행 덮어쓰기(T-41)가 창 "
            "안에서 끝나지 않는다 — --window-days 를 늘린다")


def _ensure_meta_columns(con: sqlite3.Connection) -> None:
    """옛 판이 만든 `_compat_meta` 에 뒤에 생긴 열을 덧댄다(v3 MIGRATION_SQL 과 같은 방식).

    `main.` 한정 — 스테이징을 ATTACH 한 연결(`compat.v3_post`)에서도 본 파일 쪽 표만 본다.
    """
    have = {r[1] for r in con.execute(f"PRAGMA main.table_info({META_TABLE})")}
    for name, decl in (("n_evening_rows_skipped", "INTEGER"), ("n_adj_close_null", "INTEGER"),
                       ("status", "TEXT"), ("failed_table", "TEXT"),
                       ("model_universe", "TEXT"), ("n_universe_with_estimates", "INTEGER"),
                       ("builds_fallback", "TEXT"), ("model_builds", "TEXT")):
        if name not in have:
            con.execute(f"ALTER TABLE main.{META_TABLE} ADD COLUMN {name} {decl}")


def _write_meta(con: sqlite3.Connection, result: ExportResult) -> None:
    con.execute(META_DDL)
    _ensure_meta_columns(con)
    con.execute("BEGIN")
    try:
        con.execute(
            f'INSERT OR REPLACE INTO {META_TABLE} (exported_at, date, basis, equity_builds, '
            f'stage_builds, tables, "window", consensus_asof, n_evening_rows_skipped, '
            f"n_adj_close_null, status, failed_table, model_universe, "
            f"n_universe_with_estimates, builds_fallback, model_builds) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result.exported_at, result.date, result.basis,
             json.dumps(result.equity_builds, ensure_ascii=False, sort_keys=True),
             json.dumps(result.stage_builds, ensure_ascii=False, sort_keys=True),
             json.dumps({t: asdict(r) for t, r in result.tables.items()},
                        ensure_ascii=False, sort_keys=True),
             json.dumps(result.window, ensure_ascii=False, sort_keys=True),
             result.consensus_asof, result.n_evening_rows_skipped, result.n_adj_close_null,
             result.status, result.failed_table, result.model_universe,
             result.n_universe_with_estimates,
             json.dumps(list(result.builds_fallback), ensure_ascii=False),
             json.dumps(result.model_builds, ensure_ascii=False, sort_keys=True)))
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
                     extra: Iterable[tuple[str, str]] = (),
                     on_missing: str = "error"
                     ) -> tuple[dict[str, dict[str, str]], tuple[str, ...]]:
    """쓰기 전에 원천 판을 전부 해석한다 — 판 가드(R5)를 먼저 걸기 위해서다.

    `on_missing='current'` 면 `--builds-from` 에 **키가 없는 표**와 그 build_id 가 이미
    **GC 로 MANIFEST 에서 사라진 표**를 `current_build` 로 떨어뜨리고 목록을 함께 돌려준다
    (근거·경고는 `BUILDS_MISSING` 주석).
    """
    builds: dict[str, dict[str, str]] = {EQUITY: {}, STAGE: {}}
    fallback: set[str] = set()
    # 모델 판 점수 표는 MANIFEST 가 아니라 그날 판 manifest 로 고정한다 — `_resolve_model`
    wanted = [(m.source_kind, t) for m in selected if m.source_kind != MODEL for t in m.sources]
    wanted += [pair for m in selected for pair in m.cross_sources]
    wanted += list(extra)
    for kind, table in wanted:
        assert kind is not None
        if table in builds[kind]:
            continue
        want = pinned[kind].get(table) if pinned is not None else None
        if pinned is not None and want is None:
            if on_missing == "error":
                raise CompatError(f"--builds-from 에 {kind} {table} 판이 없다")
            fallback.add(table)
        try:
            expr, build_id = _parquet_source(roots[kind], table, kind, want)
        except CompatError:
            if want is None or on_missing == "error":
                raise
            fallback.add(table)          # 인계 판이 GC 로 사라졌다
            expr, build_id = _parquet_source(roots[kind], table, kind, None)
        builds[kind][table] = build_id
        builds[kind][_EXPR + table] = expr
    return builds, tuple(sorted(fallback))


def _resolve_model(selected: list[TableMapping], model_root: Path | None, d_iso: str,
                   basis: str) -> dict[str, str]:
    """점수 표 원천 — `--date D --basis` 의 모델 성공 판 하나로 고정한다(P1).

    판 해석은 `deliver.reader.load_run` 한 곳이다: `_runs/<D>_<basis>.json`(정본) → 없으면 날짜가
    D 인 `latest_<basis>.json`. 그날 성공 판이 없거나, spec 이 그 판에서 빠졌거나(N-11 격리), 점수
    파일이 없으면 다른 날 판으로 대체하지 않고 멈춘다 — 쓰기 전이라 대상의 D 행은 그대로 남는다.
    돌려주는 값은 `_resolve_sources` 와 같은 모양(spec_id → build_id, `_EXPR`+spec_id → 관계식).
    """
    picked = [m for m in selected if m.source_kind == MODEL]
    out: dict[str, str] = {}
    if not picked:
        return out
    if model_root is None:
        raise CompatError(f"{', '.join(m.v3_table for m in picked)} 은 모델 판이 원천이다 — "
                          "--model-root(모델 판 루트, 예: data/model)가 필요하다")
    root = Path(model_root)
    try:
        run = load_run(root, d_iso, basis)
    except DeliverError as e:
        raise CompatError(f"점수 표 원천 모델 판을 고정하지 못했다 — 다른 날 판으로 대체하지 "
                          f"않는다(P1): {e}") from e
    for spec in sorted({m.sources[0] for m in picked}):
        if spec not in run.specs:
            excluded = run.meta.get("excluded_specs")
            why = excluded.get(spec) if isinstance(excluded, dict) else None
            raise CompatError(
                f"모델 판 {run.build_id}({run.date} {basis})에 {spec} 가 없다"
                + (f" — 이번 판에서 빠졌다(N-11): {why}" if why else "")
                + " — 그 점수 표는 쓰지 않는다")
        path = root / spec / f"v={run.build_id}" / SCORES_FILE
        if not path.exists():
            raise CompatError(f"모델 판 점수 표가 없다: spec={spec} build={run.build_id} {path}")
        out[spec] = run.build_id
        out[_EXPR + spec] = f"read_parquet('{path.resolve()}', hive_partitioning=false)"
    return out


def _replace_date(table: str, column: str, d_iso: str) -> Callable[[sqlite3.Connection], None]:
    """그 날짜 행을 전부 지운다(넣는 트랜잭션 안 — `_replace_score_date` 와 같은 규칙, QL-C).

    `INSERT OR REPLACE` 만 쓰면 이번 판에 없는 종목의 그날 옛 행(저녁에 원장으로 만든 T 행 중 아침
    KRX 에 없는 종목)이 남는다. 다른 날짜는 그대로다.
    """
    def run(con: sqlite3.Connection) -> None:
        con.execute(f'DELETE FROM "{table}" WHERE "{column}" = ?', (d_iso,))
    return run


def _guard_date_rows(duck: duckdb.DuckDBPyConnection, mapping: TableMapping,
                     params: dict[str, str], builds: dict[str, dict[str, str]],
                     n_extra: int) -> None:
    """날짜 단위 교체 전 — 새 원천에 `--date` 행이 하나도 없으면 BEGIN 전에 멈춘다(`_score_rows` 와
    같은 규칙). 판이 그날을 담지 못했는데(예: 저녁 반영 뒤 D' 판으로 아침 `--date T`) 지우고 넣으면
    그날 행이 조용히 사라진다. `n_extra` 는 판 밖에서 얹는 그날 행 수(장 마감 판 T 행).
    """
    sql, _ = _render(mapping, {**params, "from_date": params["date"]}, builds)
    row = duck.execute(f"SELECT count(*) FROM ({sql}) q").fetchone()
    n_day = (0 if row is None else int(row[0])) + n_extra
    if n_day == 0:
        raise CompatEmptyError(
            f"{mapping.v3_table}: 새 원천에 {params['date']} 행이 0 — 날짜 단위 교체로 그날 "
            "기존 행을 지우지 않는다(아침이면 그날 KRX 확정판, 저녁이면 T 원장을 확인)")


def _guard_evening_order(con: sqlite3.Connection, d_iso: str) -> None:
    """장 마감 판이 더 나중 반영을 덮지 않는다 — 대상 `_compat_meta` 에 이번(T, 장 마감)보다 순서가
    뒤인 ok 기록(날짜가 뒤 · 같은 날 아침)이 있으면 쓰기 전에 멈춘다. 늦게 돈 저녁이 KRX 확정 행이나
    다음 날 T 행을 옛 원장 값으로 되돌리는 것을 막는다. 순서 판정은 QL-F 반영 순서 가드(T-35)와 한
    곳이다(`v3_post._newer` — v3_post 가 이 모듈을 import 하므로 함수 안에서 부른다). 재생은
    `allow_older`.
    """
    from . import v3_post
    newer = v3_post._newer(v3_post._meta_rows(con), d_iso, "evening")
    if newer:
        raise CompatError(
            f"대상에 이번({d_iso} 장 마감)보다 나중 반영 기록 {newer[-1]} 이 있다 — 그 위에 쓰면 "
            "확정 값이 옛 원장 값으로 되돌아간다(T-35 순서). 재생이면 --allow-older")


def _build_ids(builds: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in builds.items() if not k.startswith(_EXPR)}


def export(equity_root: Path, stage_root: Path, date: str, basis: str, target: Path,
           tables: list[str] | None = None, full: bool = False,
           window_days: int | None = None, consensus_asof: str | None = None,
           builds_from: Path | None = None, builds_from_missing: str = "error",
           model_universe: str = "all", in_place: bool = False,
           model_root: Path | None = None, postclose_db: Path | None = None,
           kiwoom_db: Path | None = None, calendar_dir: Path | None = None,
           allow_older: bool = False) -> ExportResult:
    """equity/stage/model 판을 읽어 v3 `quant.db` 9표 중 지정 표를 upsert 한다.

    date·consensus_asof 는 YYYYMMDD. `full=False`(기본)면 최근 `INCREMENTAL_SESSIONS`
    거래일(판정 달력 `calendar_dir`)만,
    `full=True` 면 `window_days`(기본 `FULL_WINDOW_DAYS`) 창 전체를 다시 넣는다. 스냅샷
    표(`stocks`·리비전·재무)는 창과 무관하게 as_of 최신 한 판이다.
    `builds_from` 은 인계 이력 JSON 경로 — 그 판으로 고정해 읽는다(과거 날짜 비교용).
    그 판을 못 찾았을 때 `builds_from_missing='current'` 면 `current_build` 로 폴백하고
    폴백한 표를 `_compat_meta.builds_fallback` 에 남긴다(기본 'error' 는 멈춘다).
    `model_universe='estimates'` 면 당해 12월기 WISE 추정치가 없는 종목의 `stocks.market_cap`
    을 NULL 로 두어 v3 엔진 유니버스에서 뺀다(사용자 결정 09-24). 그림자 전용 —
    `in_place=True`(v3 quant.db 제자리 반영)이면 `all` 만 허용한다(T-19).
    `model_root` 는 모델 판 루트(`data/model`) — 점수 두 표를 고르면(표를 안 고르면 기본으로 고른다)
    반드시 있어야 한다. 그날·그 basis 의 모델 판으로 고정하고 `score_date` 단위로 갈아 끼운다(T-16).
    `postclose_db`·`kiwoom_db`·`calendar_dir` 는 장 마감 판(`basis='evening'`)의 `daily_prices`·
    `investor_detail_flows` T 행 원천 원장(`data/raw/postclose.db`·`data/raw/kiwoom.db`)과 D' 를 셀
    판정 달력 폴더(없으면 `daily.calendar` 기본 경로)다(QL-D). 원장은 아침판에서 쓰지 않는다. 달력은 제자리
    반영(`in_place`)의 창 세션 가드에도 쓴다(QL-E MINOR-1 — 아침판 포함). 증분(`full=False`)이면
    창 시작일도 이 달력으로 센다(K1-9d — 못 읽으면 멈춘다).
    `allow_older` 는 장 마감 판이 대상의 더 나중 반영 기록(T-35 순서)을 무시하게 한다(재생 전용).
    """
    as_of = _parse_date(date, "--date")
    if basis not in BASES:
        raise CompatError(f"--basis 는 {BASES} 중 하나여야 한다: {basis!r}")
    if builds_from_missing not in BUILDS_MISSING:
        raise CompatError(
            f"--builds-from-missing 은 {BUILDS_MISSING} 중 하나여야 한다: "
            f"{builds_from_missing!r}")
    if model_universe not in MODEL_UNIVERSES:
        raise CompatError(
            f"--model-universe 는 {MODEL_UNIVERSES} 중 하나여야 한다: {model_universe!r}")
    if in_place and model_universe != "all":
        raise CompatError(
            f"--model-universe {model_universe} 는 그림자 전용이다 — 제자리 반영(--in-place)의 "
            "stocks.market_cap 은 전 종목(all) 고정(T-19)")
    asof_cons = _parse_date(consensus_asof, "--consensus-asof") if consensus_asof else as_of
    if window_days is not None and not full:
        raise CompatError("--window-days 는 --full 과 함께만 쓴다 — 증분 창은 "
                          f"{INCREMENTAL_SESSIONS}거래일 고정이다")
    if window_days is not None and window_days <= 0:
        raise CompatError(f"--window-days 는 양수여야 한다: {window_days}")
    from_d = (as_of - timedelta(days=window_days or FULL_WINDOW_DAYS) if full
              else _incremental_from(as_of, calendar_dir))
    span = (as_of - from_d).days        # `_compat_meta.window.days` — 증분도 달력일 폭으로 남긴다
    params = {
        "date": as_of.isoformat(),
        "from_date": from_d.isoformat(),
        "snap_from": (as_of - timedelta(days=SNAPSHOT_LOOKBACK_DAYS)).isoformat(),
        "consensus_asof": asof_cons.isoformat(),
        "asof_ym": as_of.strftime("%Y%m"),
        "asof_fy": f"{as_of.year}12",        # 당해 12월기 — 추정치 유니버스 판정 기준
        "exported_at": datetime.now(UTC).isoformat(timespec="microseconds"),
    }
    roots = {EQUITY: Path(equity_root), STAGE: Path(stage_root)}
    selected = _selected(tables)
    pinned = _load_builds_from(builds_from) if builds_from is not None else None
    # QL-D — 장 마감 판. T 행 모듈은 stage 빌더·규칙을 끌어오므로 저녁에만 불러온다
    # (t_rows docstring).
    # 인자·달력·판 이음매 준비는 대상 파일을 열기 전에 끝낸다.
    evening = basis == "evening"
    t_mod = None
    if evening:
        from . import t_rows as t_mod
    t_tables = [m.v3_table for m in selected if t_mod is not None and m.v3_table in t_mod.TABLE_SQL]
    t_paths = t_mod.ledgers(postclose_db, kiwoom_db) if t_mod is not None and t_tables else None
    # 이음매 검사에 쓰는 `universe_daily` — 장 마감 판이 equity 표를 하나라도 읽으면 함께 고정한다
    reads_equity = any(m.source_kind == EQUITY or any(k == EQUITY for k, _ in m.cross_sources)
                       for m in selected)

    want_estimates = (model_universe == "estimates"
                      and any(m.v3_table == "stocks" for m in selected))
    builds, builds_fallback = _resolve_sources(
        selected, roots, pinned,
        ([(STAGE, "stg_consensus_annual")] if want_estimates else [])
        + ([(EQUITY, "universe_daily")] if evening and reads_equity else []),
        builds_from_missing)
    builds[MODEL] = _resolve_model(selected, model_root, params["date"], basis)
    equity_builds, stage_builds = _build_ids(builds[EQUITY]), _build_ids(builds[STAGE])
    model_builds = _build_ids(builds[MODEL])
    _check_basis(equity_builds, basis)
    # 장 마감 판 이음매(MINOR-2) — T 행을 만들 때, 그리고 D' 아침 판(`m_`)을 받았을 때는 표 선택과
    # 무관하게 본다
    seam = t_mod is not None and reads_equity and (
        bool(t_tables) or any(basis_of_build_id(b) == "morning" for b in equity_builds.values()))
    if t_mod is not None and seam:
        params.update(t_iso=params["date"], t_ymd=as_of.strftime("%Y%m%d"),
                      d_prime=t_mod.dprime(as_of, calendar_dir).isoformat())

    if in_place and any(m.v3_table == "daily_prices" for m in selected):
        _guard_window_sessions(as_of, params["from_date"], calendar_dir)
    params["t_step"] = NO_T_STEP                    # 장 마감 판이면 T 행 원장으로 아래에서 바꾼다(QL-E)

    window = {"days": span, "full": full, "from_date": params["from_date"],
              "to_date": params["date"]}
    results: dict[str, TableResult] = {}
    evening_skipped: int | None = None
    adj_null: int | None = None
    n_with_est: int | None = None
    rebase: dict[str, object] | None = None       # daily_prices 창 밖 다시 맞춤 기록(QL-E)
    con = _open_target(Path(target))
    duck = duckdb.connect()
    try:
        duck.execute(f"SET threads = {DUCKDB_THREADS}")
        duck.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
        # 진행 막대가 `| tee` 로그를 제어문자로 덮는다(1차 그림자 실행 관찰).
        duck.execute("SET enable_progress_bar=false")
        if evening and not allow_older:
            _guard_evening_order(con, params["date"])
        existed = _existing_tables(con)
        required = _ensure_schema(con, [m.v3_table for m in selected])
        if not full and any(m.v3_table == "daily_prices" for m in selected):
            _guard_incremental(con, existed)
        current: str | None = None
        # 리뷰 MINOR-2 — 함께 고른 점수 표는 첫 점수 표를 쓰기 전에 전부 읽어 검사해 둔다
        # (표 → (열, 행)).
        # 한 표라도 깨졌으면 점수 표는 하나도 쓰지 않는다. 9표 한 트랜잭션은 QL-F 몫이다.
        scores: dict[str, tuple[list[str], list[tuple]]] = {}
        t_parts: dict = {}                  # 표 → t_rows.TPart
        try:
            if t_mod is not None and seam:
                current = t_tables[0] if t_tables else selected[0].v3_table
                t_mod.check_seam(duck, builds[EQUITY][_EXPR + "universe_daily"], params)
                if t_paths is not None:
                    exprs = {k[len(_EXPR):]: v for k, v in builds[EQUITY].items()
                             if k.startswith(_EXPR)}
                    t_parts = t_mod.build(duck, selected, exprs, params, t_paths)
                    if "daily_prices" in t_parts:   # 사슬 끝에 T 단계(QL-E MAJOR-A)
                        params["t_step"] = t_mod.STEP_TABLE
            for mapping in selected:
                current = mapping.v3_table
                on_date = {"n": 0}       # daily_prices 에 이번에 쓴 trade_date = D 행(T-31 ③)
                sql, used = _render(mapping, params, builds)
                part = t_parts.get(mapping.v3_table)
                if mapping.source_kind == MODEL:
                    if not scores:
                        for m in (x for x in selected if x.source_kind == MODEL):
                            current = m.v3_table
                            scores[m.v3_table] = _score_checked(
                                duck, m, params, builds, required[m.v3_table])
                        current = mapping.v3_table
                    columns, rows = scores[mapping.v3_table]
                    n_rows, n_skipped = _upsert(
                        con, mapping, columns, [rows], required[mapping.v3_table],
                        pre=_replace_score_date(mapping.v3_table, params["date"]))
                else:
                    day_col = DATE_REPLACED.get(mapping.v3_table)
                    if day_col is not None:         # 날짜 단위 교체 0행 가드 — 스트림 열기 전에
                        _guard_date_rows(duck, mapping, params, builds,
                                         0 if part is None else len(part.rows))
                    post = None
                    if mapping.v3_table == "daily_prices":   # QL-E — 스트림 열기 전에(같은 duckdb 연결)
                        floor, rb_tickers, rb_values = _rebase_plan(duck, con, params, builds)
                        rebase = {"before": params["from_date"], "floor": floor,
                                  "tickers": rb_tickers, "n_rows": 0, "n_null": 0}
                        post = _rebase_outside(rb_tickers, rb_values, params["from_date"], rebase)
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
                        chunks: Iterable[list[tuple]] = _chunks(cur)
                        if part is not None:
                            chunks = itertools.chain(chunks, [part.rows])
                        # 신선도 셈(T-31 ③)은 T 행을 얹은 뒤에 건다 — 거꾸로면 저녁 n_on_date 가
                        # 0 이 되어 제자리 반영 게이트가 매일 멈춘다
                        if mapping.v3_table == "daily_prices":
                            chunks = _count_on_date(chunks, columns, required["daily_prices"],
                                                    "trade_date", params["date"], on_date)
                        n_rows, n_skipped = _upsert(
                            con, mapping, columns, chunks, required[mapping.v3_table],
                            post=post,
                            pre=None if day_col is None else _replace_date(
                                mapping.v3_table, day_col, params["date"]))
                metrics = (_fin_source_counts(con)
                           if mapping.v3_table == "financial_summary" else
                           {"n_on_date": on_date["n"]}
                           if mapping.v3_table == "daily_prices" else {})
                results[mapping.v3_table] = TableResult(
                    n_rows, n_skipped, used, metrics, None if part is None else part.info,
                    rebase if mapping.v3_table == "daily_prices" else None)
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
                n_universe_with_estimates=n_with_est, builds_fallback=builds_fallback,
                tables=results,
                equity_builds=equity_builds, stage_builds=stage_builds,
                model_builds=model_builds))
            raise
        result = ExportResult(
            date=params["date"], basis=basis, target=str(target),
            exported_at=params["exported_at"], window=window,
            consensus_asof=params["consensus_asof"],
            n_evening_rows_skipped=evening_skipped, n_adj_close_null=adj_null,
            model_universe=model_universe, n_universe_with_estimates=n_with_est,
            builds_fallback=builds_fallback,
            tables=results, equity_builds=equity_builds, stage_builds=stage_builds,
            model_builds=model_builds)
        _write_meta(con, result)
        return result
    finally:
        duck.close()
        con.close()
