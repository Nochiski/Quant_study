"""S19 공개시점 대장 — `dataset_profile` (DESIGN v1.2 §4-7 · GATES v1.0 §2 27행 · §3 ㉒ ·
§4 FX-6-001·002 · EG2-P04/P06/P07 · EG9-P06 · WORKFLOW §3-2 S19).

grain `field_id` — 어댑터 `list_fields()`(`DatasetFieldProfile`)의 원천이다. 한 행 = 소비자에게
나가는 필드 하나이고, 두 갈래로 만들어진다:

  선언  `EquityTable.field_profiles` (각 `rules_s*.py` 가 자기 테이블의 필드를 선언한다).
        랙·PIT·cell kind·단위·판정은 **여기서만** 온다 — 문서를 베끼지 않는다. 이 모듈은
        `RULES` 를 훑어 모으고 `source_stage_tables`·`available_date_basis`·
        `supported_cell_kinds` 를 소유 테이블에서 기계적으로 유도한다(중복 선언 금지).
  실측  `coverage_from`·`coverage_to`·`n_observed`·`n_denominator`·`estimated_coverage_pct`·
        `coverage_by_mktcap_quintile` 은 고정한 equity 파티션을 직접 재서 넣는다. `_meta.json` 은
        파티션 라벨(`year=YYYY`)까지만 주므로 연 단위 해상도밖에 안 나온다 — 그래서 값 컬럼을
        직접 읽는다. **비율은 정수 분자·분모의 순수 함수**(파이썬 나눗셈 + 6자리 반올림)이고
        그 항등을 게이트가 SQL 로 다시 확인한다 — 산출에 실리는 유일한 부동소수라 엔진의 축약
        순서가 content_hash 에 섞이지 않게 못박는 장치다(§9 S19 2차, 서버 해시 불일치).

컬럼 `basis`(e1.15.0)는 이 대장이 나온 **빌드 판**이다 — `--basis evening` 으로 지은 잠정판인지
소비자가 `list_fields()` 한 번으로 안다(플랜 v2 §4 B.2).

선언표라 EG1 은 `skip(declaration_table)`(GATES §3 ㉒), 차원표라 프레임 EG2 는
`skip(dimension_table)` 이고 **EG2-P04/P06/P07 은 `EG2_dataset_profile` 이 대신 판정**한다.

입력은 **전 equity 테이블 26개**다(`SOURCE_TABLES`). 셋을 한꺼번에 얻기 위해서다:
  ① 필드의 값 컬럼(커버 실측) ② `universe_daily` 격자(커버율 분모·시총 분위) ③ 각 파티션
  `_meta.lag_known_inputs`(EG2-P04 의 stage `lag_known=false` 모집단 — 이 맵이 아니면 SQL 로
  stage 판본을 볼 방법이 없다). 그래서 6단계는 S08~S18 이 전부 커밋된 뒤에만 돈다.

**커버 축**(`FieldProfile.coverage_axis`) — 분모를 무엇으로 잡느냐가 이 표의 정직함을 정한다:
  `grid_session`  = `universe_daily` (ticker, date) 격자 셀 분모. 세션 빈도 필드(가격·유니버스)
  `grid_security` = 격자의 **종목** 분모. 월·이벤트 빈도(컨센서스·의견)를 일별 셀로 나누면 관측이
                    월 1회라 구조적으로 낮게 나온다 — FIELD_MAP §3 의 "커버 종목 804 / 810" 축이다
  `table_rows`    = 소유 테이블 행수 분모 — 법인·접수 축(`fin_std`·4B 6테이블)은 종목 격자가 없고,
                    "이 보고서 행들 중 이 계정이 실린 비율" 이 곧 GAP-02 의 판정이다
  `static_label`  = 차원표 현재값 라벨(`corp.induty_code`) — 창은 캘린더 전체
격자 두 축은 시총 분위별 커버율까지 낸다 — S17·S18 이 입력에 시총 축이 없어 미룬 EG9-P06 이다
(DESIGN §10 P36·P37 ⑥).

**창이 비면 폐기하지 않는다**: 관측이 0 인 필드(선언은 있으나 행이 없는 `event.buyback_amount`
부류)는 소유 테이블의 창 → 캘린더 창으로 물러나 `coverage_from` 을 채우고
`estimated_coverage_pct = 0` 으로 남는다. 그 0 을 팩터 판정으로 옮기는 것은 S20 EG10 의 일이다
(`blocked_reason='no_observations'`) — 여기서 격리하면 EG7 비율이 터지고 "필드가 없다" 와
"필드는 있는데 값이 없다" 가 구별되지 않는다.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
from stage.gates import GateResult, GateStatus

from . import (
    rules_s01,  # noqa: F401  # reason: 등록 부작용 — 선언 수집은 RULES 가 다 찬 뒤에만 옳다
    rules_s02,  # noqa: F401
    rules_s03,  # noqa: F401
    rules_s04,  # noqa: F401
    rules_s05,  # noqa: F401
    rules_s06,  # noqa: F401
    rules_s08,  # noqa: F401
    rules_s09,  # noqa: F401
    rules_s10,  # noqa: F401
    rules_s11,  # noqa: F401
    rules_s12,  # noqa: F401
    rules_s15,  # noqa: F401
    rules_s16,  # noqa: F401
    rules_s17,  # noqa: F401
    rules_s18,  # noqa: F401
    rules_s23,  # noqa: F401
)
from .gates import EquityGateContext
from .model import (
    AVAILABLE_NONE,
    CELL_KINDS,
    GRID_AXES,
    RULES,
    EquityTable,
    FieldProfile,
    register,
)

SQL_DIR = Path(__file__).parent / "sql"
TABLE_NAME = "dataset_profile"

# 프로파일이 훑는 equity 테이블 — 6단계 선행(S01~S18 · S23) 산출 전부. 목록을 코드에 고정하는 이유는
# `RULES` 를 그대로 쓰면 T0 샘플(`sample_table`)이나 import 순서에 따라 집합이 흔들리기 때문이다.
SOURCE_TABLES: tuple[str, ...] = (
    "adj_factor", "audit_opinion", "consensus_daily", "corp", "corp_event", "corp_ticker",
    "credit_daily", "disclosure_version", "dividend_event", "fin_std", "flow_daily",
    "holder_daily", "index_daily", "opinion_broker_daily", "opinion_daily", "ownership_snapshot",
    "price_adj_daily", "price_daily", "security", "security_span", "shares_outstanding",
    "short_daily", "trading_calendar", "treasury_stock", "universe_daily",
    "universe_policy",
)
# 격자 분모·시총 분위 축. 창 폴백의 마지막 단계는 캘린더다.
GRID_TABLE = "universe_daily"
CALENDAR_TABLE = "trading_calendar"
MKTCAP_QUINTILES = 5
# 격자 테이블이 아닌 산출의 cell kind — 값이 있으면 OBSERVED, NULL 이면 MISSING, 구간·backfill_end
# 밖이면 COVERAGE_GAP (FIELD_MAP §1 결측 어휘). `fill_kind` 를 가진 격자 테이블(S08~S10)이 붙으면
# 그 테이블의 필드만 5종 전부를 받는다.
BASE_CELL_KINDS: tuple[str, ...] = ("observed", "missing", "coverage_gap")
FILL_KIND_COLUMN = "fill_kind"
# `short_daily` 는 원천마다 fill_kind 를 두어 컬럼 이름이 `fill_kind_short_kiwoom` 부류다
# (DESIGN §4-3 구현 결과 ②) — 접두로 본다. 이름을 하나만 보면 그 테이블의 필드가 격자가 아닌
# 것처럼 3종만 받아 소비자가 not_collected·src_omitted 셀을 만나고도 어휘에서 못 찾는다.
FILL_KIND_PREFIX = FILL_KIND_COLUMN + "_"

REJECT_REASONS: tuple[str, ...] = ("no_coverage_window",)

_GRID_SQL = ", ".join(f"'{a}'" for a in GRID_AXES)

# `_decl_field` 스키마 — `.sql` 이 조인으로만 보는 선언 레지스트리(GATES §0-4 `_reg_*` 자리).
_DECL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("field_id", "VARCHAR"), ("table_name", "VARCHAR"), ("column_scope", "VARCHAR"),
    ("source_stage_tables", "VARCHAR[]"), ("label", "VARCHAR"), ("unit", "VARCHAR"),
    ("value_type", "VARCHAR"), ("frequency", "VARCHAR"), ("available_date_basis", "VARCHAR"),
    ("recommended_lag_sessions", "BIGINT"), ("recommended_lag_days", "BIGINT"),
    ("disclosure_basis", "VARCHAR"), ("evidence", "VARCHAR"), ("point_in_time", "BOOLEAN"),
    ("requires_confirmation", "BOOLEAN"), ("supported_cell_kinds", "VARCHAR[]"),
    ("coverage_basis", "VARCHAR"), ("field_scope", "VARCHAR"), ("owner_table", "VARCHAR"),
)
_COVERAGE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("field_id", "VARCHAR"), ("coverage_from", "DATE"), ("coverage_to", "DATE"),
    ("estimated_coverage_pct", "DOUBLE"), ("coverage_by_mktcap_quintile", "DOUBLE[]"),
    ("n_observed", "BIGINT"), ("n_denominator", "BIGINT"), ("n_outside_grid", "BIGINT"),
)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# ── 선언 수집 ────────────────────────────────────────────────────────────────

def owned_fields() -> tuple[tuple[str, FieldProfile], ...]:
    """`SOURCE_TABLES` 가 선언한 (소유 테이블, 필드) 전부. field_id 오름차순."""
    rows = [(t, fp) for t in SOURCE_TABLES for fp in RULES[t].field_profiles]
    dup = sorted({f.field_id for _, f in rows if
                  sum(1 for _, g in rows if g.field_id == f.field_id) > 1})
    if dup:
        raise ValueError(f"field_id declared by more than one table: {dup}")
    return tuple(sorted(rows, key=lambda r: r[1].field_id))


def stage_closure(table: str, seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """그 equity 테이블이 (equity 입력을 타고) 결국 읽는 stage 테이블 전부.

    EG2-P04 의 대조축이다 — `price.adj_close` 처럼 계수·가격·캘린더를 거쳐 오는 필드도 원천을
    빠짐없이 싣는다. 자기 참조는 model 이 이미 막았고, 사이클은 `seen` 이 끊는다.
    """
    rule = RULES[table]
    out: set[str] = set()
    for name in rule.inputs:
        if name.startswith("stg_"):
            out.add(name)
        elif name not in seen:
            out |= set(stage_closure(name, seen | {table, name}))
    return tuple(sorted(out))


def available_date_basis(owner: EquityTable, fp: FieldProfile) -> str:
    """행의 `available_basis` 어휘를 `|` 로 이은 값. 차원표는 `none`.

    뷰 필드(`view_name`)는 뷰가 읽는 테이블이 여럿이라 소유 테이블과 커버 측정 테이블의 어휘를
    합친다 — `price.adj_close` 는 원주가(default)와 계수(derived)를 함께 보고, 출력
    `available_date` 는 둘의 greatest 다(DESIGN §5).
    """
    if not owner.is_fact and fp.view_name is None:
        return AVAILABLE_NONE
    bases = set(owner.available_basis)
    if fp.view_name is not None and fp.coverage_table:
        bases |= set(RULES[fp.coverage_table].available_basis)
    return "|".join(sorted(bases)) if bases else AVAILABLE_NONE


def supported_cell_kinds(owner: EquityTable) -> tuple[str, ...]:
    """FIELD_MAP §1 결측 어휘 → 엔진 `CellKind`. 격자 테이블(`fill_kind*`)만 5종 전부."""
    grid = any(c == FILL_KIND_COLUMN or c.startswith(FILL_KIND_PREFIX) for c in owner.columns)
    return CELL_KINDS if grid else BASE_CELL_KINDS


def declaration_rows() -> list[tuple[object, ...]]:
    """`_decl_field` 에 들어갈 행. 문서가 아니라 이 함수가 프로파일 선언면의 정본이다."""
    rows: list[tuple[object, ...]] = []
    for table, fp in owned_fields():
        owner = RULES[table]
        rows.append((
            fp.field_id, fp.view_name or table, fp.column_scope,
            list(stage_closure(table)), fp.label, fp.unit, fp.value_type, fp.frequency,
            available_date_basis(owner, fp), fp.recommended_lag_sessions,
            fp.recommended_lag_days, fp.disclosure_basis, fp.evidence, fp.point_in_time,
            fp.requires_confirmation, list(supported_cell_kinds(owner)), fp.coverage_axis,
            fp.scope, table))
    return rows


# ── 커버 실측 ────────────────────────────────────────────────────────────────

def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[object, ...]:
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"coverage query returned no row: {sql[:200]}")
    return row


def _predicate(fp: FieldProfile) -> str:
    """값이 '있다' 는 술어 — 선언 컬럼 전부 NOT NULL ∧ 행 필터(있으면)."""
    parts = [f"{_q(c)} IS NOT NULL" for c in fp.measured_columns]
    if fp.row_filter:
        parts.append(f"({fp.row_filter})")
    return " AND ".join(parts)


def _window(con: duckdb.DuckDBPyConnection, table: str, date_col: str, pred: str,
            calendar: tuple[dt.date, dt.date]) -> tuple[dt.date, dt.date, bool]:
    """(coverage_from, coverage_to, 관측 있음). 값 창 → 테이블 창 → 캘린더 창 순으로 물러난다."""
    for where in (f"WHERE {pred}", ""):
        lo, hi = _one(con, f"SELECT min({_q(date_col)}), max({_q(date_col)}) "
                           f"FROM {_q(table)} {where}")
        if lo is not None and hi is not None:
            return (lo, hi, not where == "")
    return (*calendar, False)


# ── 시총 분위 지도 (빌드당 **한 번**만 만든다) ────────────────────────────────
# 두 가지를 동시에 해결한다.
#   ① **결정성**  `ntile` 의 `ORDER BY` 가 유일하지 않으면 동률 행이 어느 분위로 가는지 SQL 이
#      정하지 않는다 — 엔진의 정렬·병렬·스필 구현에 답이 맡겨진다(서버 실측: 같은 입력 두 빌드의
#      content_hash 불일치, §9 S19 2차). 그래서 **총순서**로 못박는다: 세션 축은 (date 안에서)
#      `mktcap_krw, ticker`, 종목 축은 `mktcap_krw, ticker`. `universe_daily` grain 이
#      (date, ticker) 라 두 키는 각 파티션 안에서 유일하고, 따라서 ntile 결과가 데이터의 순수
#      함수가 된다.
#   ② **비용**  분위는 필드마다 달라지지 않는다(같은 격자·같은 시총). 필드별로 다시 계산하면
#      10.9M 행 창 정렬을 22번 돌게 된다(서버 63초). 한 번 만들어 두고 창으로 자르기만 한다 —
#      세션 축 ntile 은 `PARTITION BY date` 라 날짜를 잘라도 각 날의 분위가 변하지 않는다.
_GRID_QUINTILE = "_grid_quintile"
_GRID_SECURITY_QUINTILE = "_grid_security_quintile"


def install_grid_quintiles(con: duckdb.DuckDBPyConnection) -> None:
    """격자 시총 분위 지도 2개를 TEMP TABLE 로 굽는다. 총순서라 결과는 데이터의 순수 함수다."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {_q(_GRID_QUINTILE)} AS
        SELECT ticker, date,
               ntile({MKTCAP_QUINTILES}) OVER (
                   PARTITION BY date ORDER BY mktcap_krw, ticker) AS q
        FROM {_q(GRID_TABLE)} WHERE mktcap_krw IS NOT NULL""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {_q(_GRID_SECURITY_QUINTILE)} AS
        WITH ranked AS (
            SELECT ticker, mktcap_krw,
                   row_number() OVER (
                       PARTITION BY ticker ORDER BY date DESC, mktcap_krw DESC) AS rn
            FROM {_q(GRID_TABLE)} WHERE mktcap_krw IS NOT NULL)
        SELECT ticker,
               ntile({MKTCAP_QUINTILES}) OVER (ORDER BY mktcap_krw, ticker) AS q
        FROM ranked WHERE rn = 1""")


def _quintiles(rows: list[tuple[object, ...]]) -> list[float]:
    """(q, 분자, 분모) 행 → 분위별 커버율(%) 5개. **나눗셈은 파이썬 정수 나눗셈 + 고정 반올림**
    이라 엔진의 DECIMAL/DOUBLE 축약 순서가 값에 섞이지 않는다."""
    by_q = {int(str(r[0])): _pct(r[1], r[2]) for r in rows}
    return [by_q.get(i + 1, 0.0) for i in range(MKTCAP_QUINTILES)]


def _quintile_session(con: duckdb.DuckDBPyConnection, table: str, fp: FieldProfile,
                      pred: str, lo: dt.date, hi: dt.date) -> list[float]:
    """세션 축 시총 분위별 커버율(%) 5개. q1 = 그날 시총 하위 20%, q5 = 상위 20%.

    분모는 그날 `mktcap_krw` 가 있는 격자 셀이고 분자는 그중 이 필드에 값이 있는 셀이다.
    """
    ticker_col, date_col = fp.axis_columns or ("ticker", "date")
    return _quintiles(con.execute(f"""
        WITH obs AS (
            SELECT DISTINCT {_q(ticker_col)} AS ticker, {_q(date_col)} AS date
            FROM {_q(table)} WHERE {pred}
              AND {_q(date_col)} BETWEEN DATE '{lo}' AND DATE '{hi}')
        SELECT g.q, count(o.ticker), count(*)
        FROM {_q(_GRID_QUINTILE)} g LEFT JOIN obs o
               ON o.ticker = g.ticker AND o.date = g.date
        WHERE g.date BETWEEN DATE '{lo}' AND DATE '{hi}'
        GROUP BY g.q ORDER BY g.q""").fetchall())


def _quintile_security(con: duckdb.DuckDBPyConnection, table: str, fp: FieldProfile,
                       pred: str) -> list[float]:
    """종목 축 시총 분위별 커버율(%) 5개 — 종목의 **격자 마지막 날 시총**으로 분위를 매긴다.

    S17·S18 이 입력에 시총 축이 없어 못 낸 EG9-P06 을 여기서 낸다(GATES §9 S17·S18,
    DESIGN §10 P36·P37 ⑥). 컨센서스·의견은 커버 종목 자체가 편향(대형주 쏠림)이라 이 표가
    선택편향의 크기를 그대로 보여 준다.
    """
    ticker_col, _ = fp.axis_columns or ("ticker", "date")
    return _quintiles(con.execute(f"""
        WITH obs AS (SELECT DISTINCT {_q(ticker_col)} AS ticker FROM {_q(table)} WHERE {pred})
        SELECT s.q, count(o.ticker), count(*)
        FROM {_q(_GRID_SECURITY_QUINTILE)} s LEFT JOIN obs o ON o.ticker = s.ticker
        GROUP BY s.q ORDER BY s.q""").fetchall())


def coverage_rows(con: duckdb.DuckDBPyConnection) -> list[tuple[object, ...]]:
    """`_field_coverage` 에 들어갈 행 — 고정한 equity 파티션 위 실측.

    커버율은 **정수 분자·분모를 산출에 함께 싣고**(`n_observed`·`n_denominator`) 비율은 그 둘의
    파이썬 나눗셈을 고정 자릿수로 반올림한 값이다 — 소비자가 표만 보고 재계산할 수 있고,
    엔진의 부동소수 축약이 content_hash 에 섞이지 않는다(§9 S19 2차).
    """
    cal_lo, cal_hi = _one(con, f"SELECT min(date), max(date) FROM {_q(CALENDAR_TABLE)}")
    if cal_lo is None or cal_hi is None:
        raise RuntimeError(f"{CALENDAR_TABLE} 가 비어 있다 — 커버 창을 잡을 수 없다")
    calendar = (cal_lo, cal_hi)
    install_grid_quintiles(con)
    grid_cells: dict[tuple[dt.date, dt.date], int] = {}      # 창별 격자 셀 수 (창은 몇 종류뿐)
    n_grid_tickers: int | None = None
    out: list[tuple[object, ...]] = []
    for table, fp in owned_fields():
        measured = fp.measured_table or table
        pred = _predicate(fp)
        if fp.coverage_axis == "static_label":
            n_obs, n_den = _one(con, f"SELECT count(*) FILTER (WHERE {pred}), count(*) "
                                     f"FROM {_q(measured)}")
            lo, hi, quint = *calendar, None
        elif fp.coverage_axis == "table_rows":
            lo, hi, _ = _window(con, measured, "available_date", pred, calendar)
            span = f"available_date BETWEEN DATE '{lo}' AND DATE '{hi}'"
            n_obs, n_den = _one(con, f"SELECT count(*) FILTER (WHERE {pred}), count(*) "
                                     f"FROM {_q(measured)} WHERE {span}")
            quint = None
        elif fp.coverage_axis == "grid_security":
            # 월·이벤트 빈도 필드 — 분모는 격자의 **종목** 이고 창에 무관하다(FIELD_MAP §3 의
            # "커버 종목 804 / 810" 축). 일별 격자로 나누면 관측이 월 1회라 구조적으로 낮게 나온다.
            ticker_col, date_col = fp.axis_columns or ("ticker", "date")
            lo, hi, _ = _window(con, measured, date_col, pred, calendar)
            n_obs, n_out = _one(con, f"""
                WITH obs AS (SELECT DISTINCT {_q(ticker_col)} AS ticker
                             FROM {_q(measured)} WHERE {pred})
                SELECT count(*) FILTER (WHERE g.ticker IS NOT NULL),
                       count(*) FILTER (WHERE g.ticker IS NULL)
                FROM obs o LEFT JOIN (SELECT DISTINCT ticker FROM {_q(GRID_TABLE)}) g
                       ON g.ticker = o.ticker""")
            if n_grid_tickers is None:
                n_grid_tickers = int(str(_one(
                    con, f"SELECT count(DISTINCT ticker) FROM {_q(GRID_TABLE)}")[0]))
            n_den = n_grid_tickers
            quint = _quintile_security(con, measured, fp, pred)
            out.append((fp.field_id, lo, hi, _pct(n_obs, n_den), quint, int(str(n_obs)),
                        int(str(n_den)), int(str(n_out))))
            continue
        else:                                       # grid_session
            ticker_col, date_col = fp.axis_columns or ("ticker", "date")
            lo, hi, _ = _window(con, measured, date_col, pred, calendar)
            span = f"{_q(date_col)} BETWEEN DATE '{lo}' AND DATE '{hi}'"
            n_obs, n_out = _one(con, f"""
                WITH obs AS (SELECT DISTINCT {_q(ticker_col)} AS ticker, {_q(date_col)} AS date
                             FROM {_q(measured)} WHERE {pred} AND {span})
                SELECT count(*) FILTER (WHERE g.ticker IS NOT NULL), count(*) FILTER (
                           WHERE g.ticker IS NULL)
                FROM obs o LEFT JOIN {_q(GRID_TABLE)} g
                       ON g.ticker = o.ticker AND g.date = o.date""")
            if (lo, hi) not in grid_cells:
                grid_cells[(lo, hi)] = int(str(_one(
                    con, f"SELECT count(*) FROM {_q(GRID_TABLE)} "
                         f"WHERE date BETWEEN DATE '{lo}' AND DATE '{hi}'")[0]))
            n_den = grid_cells[(lo, hi)]
            quint = _quintile_session(con, measured, fp, pred, lo, hi)
            out.append((fp.field_id, lo, hi, _pct(n_obs, n_den), quint, int(str(n_obs)),
                        int(str(n_den)), int(str(n_out))))
            continue
        out.append((fp.field_id, lo, hi, _pct(n_obs, n_den), quint, int(str(n_obs)),
                    int(str(n_den)), 0))
    return out


COVERAGE_PCT_DECIMALS = 6
"""커버율 반올림 자릿수. 산출에 실리는 유일한 부동소수라 자릿수를 고정해 두면 엔진의 축약 순서가
content_hash 에 섞일 여지가 없다(§9 S19 2차). 분자·분모는 정수로 함께 실린다."""


def _pct(n_obs: object, n_den: object) -> float:
    """커버율(%) — `FieldCoverageCapability.estimated_coverage_pct` 규약대로 0~100."""
    den = int(str(n_den))
    return 0.0 if den == 0 else round(100.0 * int(str(n_obs)) / den, COVERAGE_PCT_DECIMALS)


# ── 선언 주입 훅 (`EquityTable.declarations`) ────────────────────────────────

def _create(con: duckdb.DuckDBPyConnection, name: str,
            schema: tuple[tuple[str, str], ...], rows: list[tuple[object, ...]]) -> None:
    cols = ", ".join(f"{_q(c)} {t}" for c, t in schema)
    con.execute(f"CREATE OR REPLACE TEMP TABLE {_q(name)} ({cols})")
    if rows:
        marks = ", ".join("?" for _ in schema)
        con.executemany(f"INSERT INTO {_q(name)} VALUES ({marks})", rows)


def install_declarations(con: duckdb.DuckDBPyConnection, rule: EquityTable) -> None:
    """`_decl_field`(선언) + `_field_coverage`(실측)를 올린다. `_const` 와 같은 통로."""
    _create(con, "_decl_field", _DECL_COLUMNS, declaration_rows())
    _create(con, "_field_coverage", _COVERAGE_COLUMNS, coverage_rows(con))


# ── 게이트 ───────────────────────────────────────────────────────────────────

def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def lag_known_false_inputs(ctx: EquityGateContext) -> tuple[list[str], list[str]]:
    """(lag_known=false 인 stage 입력, lag_known 을 못 읽은 stage 입력).

    stage 판본을 SQL 로 볼 방법이 없으므로 고정한 equity 파티션의 `_meta.lag_known_inputs`
    (`build.py` 가 입력마다 stage `_meta.lag_known` 을 복사해 둔 맵)를 합집합으로 읽는다.
    절단본에는 stage `_meta.json` 이 없어 전부 미상으로 잡히고, 그 사실은 기록형으로 남는다.
    """
    false_set: set[str] = set()
    unknown: set[str] = set()
    for pb in ctx.pinned.values():
        known = pb.meta.get("lag_known_inputs")
        if not isinstance(known, dict):
            continue
        for name, flag in known.items():
            if not str(name).startswith("stg_"):
                continue
            if flag is False:
                false_set.add(str(name))
            elif flag is None:
                unknown.add(str(name))
    return (sorted(false_set), sorted(unknown - false_set))


def eg2_dataset_profile(ctx: EquityGateContext) -> GateResult:
    """EG2-P04·P06·P07 + 프로파일 어휘·정합 (GATES §1 EG2 · DESIGN §4-7).

    P04 는 **세션** 축으로 판정한다 — DESIGN §4-7 이 "`recommended_lag_sessions ≥ 1`" 이라
    적었고 FIELD_MAP §1 이 세션을 랙 정본으로 못박았다(GATES §1 초안 SQL 의 `_days` 는 §9 정정).
    """
    v = _q(ctx.out_view)
    false_inputs, unknown_inputs = lag_known_false_inputs(ctx)
    covered = {str(r[0]) for r in ctx.con.execute(
        f"SELECT unnest(source_stage_tables) FROM {v} WHERE recommended_lag_sessions >= 1"
    ).fetchall()}
    uncovered = sorted(t for t in false_inputs if t not in covered)
    kinds = ", ".join(f"'{k}'" for k in CELL_KINDS)
    checks = {
        # EG2-P04 — lag_known=false stage 원천은 세션 랙 ≥ 1 인 프로파일 행을 가져야 한다
        "n_lag_known_false_without_profile": len(uncovered),
        # EG2-P06 — basis 에 'default' 가 섞인 행은 evidence 필수
        "n_default_basis_without_evidence": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE available_date_basis LIKE '%default%' "
                 "AND coalesce(trim(evidence), '') = ''"),
        # EG2-P07 — coverage_from 전수
        "n_coverage_from_null": _n(ctx, f"SELECT count(*) FROM {v} WHERE coverage_from IS NULL"),
        "n_coverage_window_inverted": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE coverage_to < coverage_from"),
        "n_lag_negative": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE recommended_lag_sessions < 0 "
                 "OR recommended_lag_days < 0"),
        "n_lag_axes_disagree": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE (recommended_lag_sessions IS NULL) "
                 "<> (recommended_lag_days IS NULL)"),
        "n_pct_out_of_range": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE estimated_coverage_pct IS NULL "
                 "OR estimated_coverage_pct < 0 OR estimated_coverage_pct > 100"),
        # 커버율은 정수 분자·분모의 순수 함수다 — 표만 보고 재계산이 되어야 한다(§9 S19 2차)
        "n_pct_not_derivable": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE n_observed IS NULL OR n_denominator IS NULL "
                 "OR n_observed < 0 OR n_denominator < 0 OR n_observed > n_denominator "
                 "OR estimated_coverage_pct IS DISTINCT FROM (CASE WHEN n_denominator = 0 "
                 "THEN 0.0 ELSE round(100.0 * n_observed / n_denominator, "
                 f"{COVERAGE_PCT_DECIMALS}) END)"),
        "n_cell_kind_outside_vocab": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE len(supported_cell_kinds) = 0 OR EXISTS "
                 "(SELECT 1 FROM unnest(supported_cell_kinds) AS u(k) "
                 f"WHERE u.k NOT IN ({kinds}))"),
        "n_source_stage_tables_empty": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE len(source_stage_tables) = 0"),
        "n_quintile_wrong_width": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE coverage_by_mktcap_quintile IS NOT NULL "
                 f"AND len(coverage_by_mktcap_quintile) <> {MKTCAP_QUINTILES}"),
        # 격자 축 필드는 분위 커버율을 반드시 낸다(EG9-P06 기록형의 실체)
        "n_grid_without_quintile": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE coverage_basis IN ({_GRID_SQL}) "
                 "AND coverage_by_mktcap_quintile IS NULL"),
        "n_label_or_basis_blank": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE coalesce(trim(label), '') = '' "
                 "OR coalesce(trim(disclosure_basis), '') = ''"),
    }
    metrics: dict[str, object] = {
        "n_fields": ctx.n_out,
        "lag_known_false_inputs": false_inputs,
        "lag_known_unmeasured_inputs": unknown_inputs,      # 절단본은 stage _meta 가 없다
        "uncovered_lag_known_false": uncovered,
        "n_by_scope": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            f"SELECT field_scope, count(*) FROM {v} GROUP BY 1 ORDER BY 1").fetchall()},
        "n_by_coverage_basis": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            f"SELECT coverage_basis, count(*) FROM {v} GROUP BY 1 ORDER BY 1").fetchall()},
        "n_by_lag_sessions": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            f"SELECT recommended_lag_sessions, count(*) FROM {v} GROUP BY 1 ORDER BY 1"
        ).fetchall()},
        "n_requires_confirmation": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE requires_confirmation"),
        "n_not_point_in_time": _n(ctx, f"SELECT count(*) FROM {v} WHERE NOT point_in_time"),
        "n_zero_coverage": _n(ctx, f"SELECT count(*) FROM {v} WHERE estimated_coverage_pct = 0"),
    }
    return _result("EG2_dataset_profile", checks, metrics, "공개시점 대장 정합")


eg2_dataset_profile.gate_name = "EG2_dataset_profile"       # type: ignore[attr-defined]


def eg9_profile_coverage(ctx: EquityGateContext) -> GateResult:
    """EG9-P06 — 시총 분위별 커버율 **기록형**(GATES §2 27행). 임계 없이 표로만 남긴다.

    분위 축을 여기서 내는 것이 S17·S18 이 미룬 항목이다(DESIGN §10 P36·P37 ⑥) — 그 슬라이스는
    입력에 시총 축이 없었고, S19 는 `universe_daily` 를 입력으로 받는다.
    """
    v = _q(ctx.out_view)
    rows = ctx.con.execute(
        f"SELECT field_id, coverage_from, coverage_to, estimated_coverage_pct, "
        f"coverage_by_mktcap_quintile FROM {v} WHERE coverage_basis IN ({_GRID_SQL}) "
        "ORDER BY field_id").fetchall()
    quintiles = {str(r[0]): [round(float(str(x)), 4) for x in (r[4] or [])] for r in rows}
    metrics: dict[str, object] = {
        "n_grid_fields": len(rows),
        "coverage_by_mktcap_quintile": quintiles,
        "coverage_pct": {str(r[0]): round(float(str(r[1])), 4) for r in ctx.con.execute(
            f"SELECT field_id, estimated_coverage_pct FROM {v} ORDER BY 1").fetchall()},
        "coverage_window": {str(r[0]): [str(r[1]), str(r[2])] for r in ctx.con.execute(
            f"SELECT field_id, coverage_from, coverage_to FROM {v} ORDER BY 1").fetchall()},
        "quintile_note": "q1 = 시총 하위 20% · q5 = 상위 20%. grid_session 은 그날 격자 셀, "
                         "grid_security 는 종목의 격자 마지막 날 시총으로 분위를 매긴다.",
    }
    return GateResult("EG9", GateStatus.PASS, "시총 분위별 커버율 기록", metrics)


eg9_profile_coverage.gate_name = "EG9"                      # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

DATASET_PROFILE = register(EquityTable(
    name=TABLE_NAME,
    grain=("field_id",),
    # 순서 = DESIGN §4-7 컬럼 목록. `field_scope` 는 S19 가 더한 열이다 — FIELD_MAP 42 어휘와
    # equity 내부 스코프(`price.adj_close` 부류)를 소비자·문서가 기계적으로 갈라 세게 한다.
    columns={"field_id": "VARCHAR", "table_name": "VARCHAR", "column_scope": "VARCHAR",
             "source_stage_tables": "VARCHAR[]", "label": "VARCHAR", "unit": "VARCHAR",
             "value_type": "VARCHAR", "frequency": "VARCHAR",
             "available_date_basis": "VARCHAR", "recommended_lag_sessions": "BIGINT",
             "recommended_lag_days": "BIGINT", "disclosure_basis": "VARCHAR",
             "evidence": "VARCHAR", "point_in_time": "BOOLEAN",
             "requires_confirmation": "BOOLEAN", "supported_cell_kinds": "VARCHAR[]",
             "coverage_from": "DATE", "coverage_to": "DATE", "coverage_basis": "VARCHAR",
             "estimated_coverage_pct": "DOUBLE", "n_observed": "BIGINT",
             "n_denominator": "BIGINT", "coverage_by_mktcap_quintile": "DOUBLE[]",
             "field_scope": "VARCHAR",
             # e1.15.0 — 이 대장이 어느 판에서 나왔는가(manual·evening·morning). 소비자가
             # `list_fields()` 한 번으로 잠정판 여부를 읽는다. **빌드 시각은 싣지 않는다** —
             # 같은 입력으로 다시 지으면 값이 달라져 EG5a(같은 inputs → content_hash 동일)가
             # 매번 깨진다. 시각의 정본은 MANIFEST `built_at_utc` 와 `_catalog_meta.written_at_utc`.
             "basis": "VARCHAR"},
    inputs=SOURCE_TABLES,
    partition_class="whole",
    partition_key_expr=None,
    available_rule=AVAILABLE_NONE,      # 카탈로그 — 행 자체에 공개시점이 없다
    eg1_lhs_sql="",                     # 선언표 — skip(declaration_table) (GATES §3 ㉒)
    eg1_rhs_sql="",
    sql_path=SQL_DIR / f"{TABLE_NAME}.sql",
    # 전 컬럼 투영 — 커버 실측이 어느 값 컬럼을 읽을지는 선언이 정한다(EG0-P02 는 산출 스키마).
    input_columns=dict.fromkeys(SOURCE_TABLES, ()),
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg2_dataset_profile, eg9_profile_coverage),
    declaration_table=True,
    declarations=install_declarations,
))

TABLES: tuple[EquityTable, ...] = (DATASET_PROFILE,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s19.json"
"""이 슬라이스가 요구하는 baseline 상수 — 없다. 파일은 그 사실과 실측을 기록한다."""

__all__ = ["BASELINE_SEED", "DATASET_PROFILE", "SOURCE_TABLES", "TABLES", "declaration_rows",
           "owned_fields", "stage_closure"]
