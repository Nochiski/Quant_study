"""`EquityDuckdbAdapter` — equity 층 parquet·카탈로그 위에서 워크벤치 5포트를 답한다 (S21 축소).

범위(EQUITY_WORKFLOW §3-5 MVP-B): 필드 3 — `price.close`(price_daily 원주가) · `price.market_cap`
(price_daily.mktcap_krw) · `price.adj_close`(카탈로그 매크로 `v_adj_price_fwd`). 그 밖의 field_id 는
`list_fields()` 에 없고 질의하면 `INVALID_QUERY`(detail 에 `unavailable`) 다 — mock 값으로 대신하지
않는다. `RawObservationPort` 가 축소 슬라이스의 본체이고, 같은 패널 코어 위에 `EquityDataPort`
(`snapshot`·`list_fields`·`load_universe`·`load_panel`), `FactorMetadataPort`,
`FactorObservationPort`, `BacktestDataPort` 를 올렸다 — 컨테이너(`build_container`)와 백테스트
파이프라인이 다섯을 다 요구한다.

어휘(FIELD_MAP §1): `security_id = {ticker}:{span_seq}` · `market = 'KRX'` · `venue = 'XKRX'` ·
`universe_id` 는 `universe_policy` 의 행(`krx.` || policy)이고 술어(`predicate`)를 `universe_daily`
위에서 AND 로 평가해 종목 집합·`universe_member` 를 만든다(정책표 driven, 하드코딩 없음).

PIT: 세 필드 모두 `price_daily.available_date = date`(S04, 공표 시각 미제공 → 세션 종가 확정) 라
랙 0 세션. `dataset_profile`(S19)이 아직 없어 본문 상수 `PRICE_LAG_SESSIONS` 로 두고, S19 가 생기면
프로필 값으로 바꾼다. 랙 n 은 as_of 에서 n 세션 전 행을 답한다(`available_date` = 그 행의 날짜).

`price.adj_close` = **전방 조정**(S21 후속, 사용자 결정 09-05): `v_adj_price_fwd(as_of := <질의
end>)` — 원주가 × 그날까지 공개·적용된 계수의 누적 share_factor. 종목의 첫 관측 수준을 고정하고
사건마다 이후 가격을 올린다(삼성전자 2018-05-03 2,650,000 그대로, 05-04 51,900 × 50 = 2,595,000).
값은 (security, date) 의 순수 함수라 창·as_of 에 무관하고(포트의 "창 불변 사실" 원칙),
`available_date` 는 뷰가 내는 greatest(원주가 공개일, 그날까지 접힌 계수의 available_date) — fold
규칙(공개 전 계수는 접지 않는다)상 date 와 같아 `available_date ≤ as_of` 가 어떤 랙에서도 선다.
수익률·모멘텀은 base = as_of 인 `v_adj_price` 와 종목별 상수배라 같다(DESIGN §10 P25 백테스트 동일).
이전 절충(base = 창 end, 수준값 비 PIT)은 DESIGN §11 ① 에서 닫혔다.

`load_universe` 는 정책 미적용(`krx.all`) — 그날 `universe_daily` 에 있는 전 종목(ETF·우선주 포함,
생존편향 방지). `load_factor_observations` 는 유니버스 인자가 없어 `RESEARCH_UNIVERSE_ID` 로 답한다.
`load_backtest_dataset` 은 원주가 bar(`price_kind='reference'` 행·GAP-14 행 미방출, 경고로 건수
기록) + `security_span` 구간 + `adj_factor` factor_ok 행(S07 과 같은 유형 매핑)이며 `adj_factor` 가
없으면 예외다 — 분할 구간을 사건 없이 돌리는 백테스트는 조용히 틀린다.

duckdb 는 backend optional extra `equity` 다(`uv sync --extra equity`). 어댑터 생성 시 지연 import
하고 없으면 `EquityDuckdbSetupError` 로 알린다.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataQuery,
    BacktestDataset,
    CorporateActionRecord,
    MarketBarRecord,
    UniverseMembershipRecord,
)
from strategy_workbench.application.factor_research.facade.ports import (
    FactorMetadataSnapshot,
    FactorObservationQuery,
    FactorObservationSet,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawFieldValue,
    RawObservation,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.domain.backtest.facade.runs import DataWarning, WarningSeverity
from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DataLoadStatus,
    DatasetFieldProfile,
    DatasetRevision,
    DataSnapshot,
    FieldCoverageCapability,
    FieldValueType,
    ResearchPanelCell,
    ResearchPanelQuery,
    ResearchPanelResult,
    SecurityRef,
    UniverseHistoryQuery,
    UniverseHistoryResult,
    UniversePoint,
)
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
)
from strategy_workbench.domain.factor.facade.expression import (
    FieldMetadata,
    NodeValueType,
)

from ._source import (
    CatalogState,
    EquityDuckdbSetupError,
    TableBuild,
    read_catalog,
    resolve_table,
    snapshot_id,
    table_builds,
)

if TYPE_CHECKING:
    import duckdb

MARKET = "KRX"
VENUE = "XKRX"
SCHEMA_VERSION = "equity-v1.2"
SECURITY_ID_SEP = ":"
PRICE_LAG_SESSIONS = 0  # 가격 계열 세션 랙 — 모듈 docstring 근거. S19 dataset_profile 이 오면 교체
RESEARCH_UNIVERSE_ID = "krx.common-stock"  # FactorObservationQuery 에 유니버스가 없다 — 계약 기본값
CALENDAR_TABLE = "trading_calendar"
SPAN_TABLE = "security_span"
UNIVERSE_TABLE = "universe_daily"
POLICY_TABLE = "universe_policy"
PRICE_TABLE = "price_daily"
SECURITY_TABLE = "security"
FACTOR_TABLE = "adj_factor"
ADJ_MACRO = "v_adj_price_fwd"
REQUIRED_TABLES = (CALENDAR_TABLE, SPAN_TABLE, UNIVERSE_TABLE, POLICY_TABLE, PRICE_TABLE)
REFERENCE_KIND = "reference"
# adj_factor.event_type → 커널 CorporateActionType 값 (S07 `EVENT_TYPE_MAP` 과 같은 판단: ok 행은
# 전부 시총 불변이라 주식수 증가는 split, 감소는 reverse_split 로 보내 수량이 조정되게 한다)
EVENT_TYPE_MAP: dict[str, str] = {
    "split": "split",
    "bonus": "split",
    "reverse_split": "reverse_split",
    "capred": "reverse_split",
}
_TICKER_RE = re.compile(r"^[0-9A-Za-z]{1,12}$")


@dataclass(frozen=True)
class FieldSpec:
    """어댑터가 답하는 field_id 하나. `column` 은 `_Row` 의 속성 이름."""

    field_id: str
    dataset_id: str
    column: str
    label: str
    unit: str
    value_type: FieldValueType
    available_date_basis: str
    description: str
    disclosure_basis: str
    evidence: str


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        field_id="price.close",
        dataset_id=PRICE_TABLE,
        column="close",
        label="종가(원주가)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        available_date_basis="price_daily.available_date = date (세션 종가 확정)",
        description="KRX 원주가 — 분할·증자 조정 없음(원칙 ②). 조정가는 price.adj_close.",
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.close ← stg_price_daily ∪ stg_etf_price_daily (EG20 원주가 불변)",
    ),
    FieldSpec(
        field_id="price.market_cap",
        dataset_id=PRICE_TABLE,
        column="mktcap_krw",
        label="시가총액",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        available_date_basis="price_daily.available_date = date (종가와 같은 시점)",
        description=(
            "원주가 × KRX 상장주식수(같은 날 listing). 우선주 합산 아님(v_firm_mktcap 별도)."
        ),
        disclosure_basis="정규장 종가 확정 시점 · KRX 상장주식수",
        evidence="price_daily.mktcap_krw = close × shares_out (stage MKTCAP 대조 불일치 0)",
    ),
    FieldSpec(
        field_id="price.adj_close",
        dataset_id=ADJ_MACRO,
        column="adj_close",
        label="조정 종가(전방 조정)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        available_date_basis=(
            "greatest(원주가 공개일 date, 그날까지 접힌 계수의 available_date) = "
            "v_adj_price_fwd.available_date — 공개 전 계수는 접지 않으므로 date 와 같다"
        ),
        description=(
            "원주가 × 그날까지 공개·적용된 계수(adj_factor factor_ok 행, apply_date 축)의 누적 "
            "share_factor. 첫 관측 수준 고정, 사건 뒤 가격을 올린다 — (security, date) 의 순수 "
            "함수라 창·as_of 에 무관(완전 PIT)."
        ),
        disclosure_basis=(
            "원주가 세션 확정 + 계수 available_date(min(공시 접수일, apply_date 다음 세션))"
        ),
        evidence=(
            "equity.duckdb v_adj_price_fwd(as_of) ← price_daily × adj_factor × trading_calendar"
        ),
    ),
)


@dataclass(frozen=True)
class _Row:
    """패널 코어 한 행 = universe_daily 격자 (date, ticker) + 구간 + 가격 컬럼."""

    session: date
    ticker: str
    span_seq: int
    member: bool
    has_price: bool
    close: float | None
    mktcap_krw: float | None
    adj_close: float | None
    adj_available_date: date | None  # v_adj_price_fwd.available_date (행이 있으면 = session)

    @property
    def security_id(self) -> str:
        return f"{self.ticker}{SECURITY_ID_SEP}{self.span_seq}"

    def available_date(self, column: str) -> date:
        """컬럼의 공개일 — 원주가·시총은 세션, 조정가는 뷰가 낸 available_date."""
        if column == "adj_close" and self.adj_available_date is not None:
            return self.adj_available_date
        return self.session

    def value(self, column: str) -> float | None:
        if column == "close":
            return self.close
        if column == "mktcap_krw":
            return self.mktcap_krw
        if column == "adj_close":
            return self.adj_close
        raise KeyError(f"unknown panel column — column={column!r}")


@dataclass(frozen=True)
class _Span:
    ticker: str
    span_seq: int
    first_date: date
    last_date: date


@dataclass(frozen=True)
class _Window:
    """질의 [start, end] 를 캘린더 세션으로 푼 것. `history` 는 start 앞 워밍업 세션."""

    history: tuple[date, ...]
    requested: tuple[date, ...]
    warnings: tuple[str, ...]

    @property
    def sessions(self) -> tuple[date, ...]:
        return (*self.history, *self.requested)


def _open(path: Path | None) -> duckdb.DuckDBPyConnection:
    """duckdb 연결 — 지연 import(optional extra `equity`). `path` 는 read_only 로 여는 카탈로그."""
    try:
        import duckdb as module
    except ImportError as error:
        raise EquityDuckdbSetupError(
            "duckdb is not installed — the equity_duckdb adapter needs the backend optional extra: "
            "`uv sync --extra equity` (pyproject [project.optional-dependencies] equity)"
        ) from error
    if path is None:
        return module.connect()
    return module.connect(str(path), read_only=True)


def _as_date(value: object, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise EquityDuckdbSetupError(f"{label} must be a date — got {type(value).__name__}: {value!r}")


def _as_float(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise EquityDuckdbSetupError(
            f"{label} must be numeric — got {type(value).__name__}: {value!r}"
        )
    return float(value)


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise EquityDuckdbSetupError(
            f"{label} must be an integer — got {type(value).__name__}: {value!r}"
        )
    return int(value)


def _lit(session: date) -> str:
    return f"DATE '{session.isoformat()}'"


def _parse_security_id(security_id: str) -> tuple[str, int] | None:
    ticker, sep, seq = security_id.partition(SECURITY_ID_SEP)
    if not sep or not seq.isdigit() or not _TICKER_RE.match(ticker):
        return None
    return ticker, int(seq)


class EquityDuckdbAdapter:
    """equity_root 위의 워크벤치 5포트 구현. 모듈 docstring 이 계약이다.

    Args:
        equity_root: `trading_calendar`·`security_span`·`universe_daily`·`universe_policy`·
            `price_daily` MANIFEST 가 있는 equity 루트. `equity.duckdb`·`adj_factor` 는 선택이며
            없으면 `price.adj_close` 가 unavailable, `load_backtest_dataset` 은 예외다.

    Raises:
        EquityDuckdbSetupError: duckdb 미설치 · 필수 테이블 미빌드 · MANIFEST/카탈로그 meta 손상.
    """

    def __init__(self, equity_root: Path) -> None:
        _open(None).close()  # duckdb 미설치면 부팅 시점에 EquityDuckdbSetupError
        self._root = equity_root.resolve()
        builds = table_builds(self._root)
        missing = [table for table in REQUIRED_TABLES if table not in builds]
        if missing:
            raise EquityDuckdbSetupError(
                f"required equity tables not built — root={self._root} missing={missing} "
                f"built={sorted(builds)}"
            )
        self._builds = builds
        self._snapshot_id = snapshot_id(builds)
        self._tables: dict[str, TableBuild] = {
            table: resolve_table(self._root, table) for table in builds
        }
        self._catalog: CatalogState = read_catalog(self._root, self._snapshot_id)
        self._sessions: tuple[date, ...] = self._load_sessions()
        self._session_index = {session: index for index, session in enumerate(self._sessions)}
        self._policies: dict[str, tuple[str, ...]] = self._load_policies()
        self._fields: dict[str, FieldSpec] = {
            spec.field_id: spec
            for spec in FIELD_SPECS
            if spec.field_id != "price.adj_close" or self._adj_unavailable_reason() is None
        }
        self._coverage_pct: dict[str, float] | None = None

    # ── 구성 ──────────────────────────────────────────────────────────────────

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """카탈로그가 쓸 만하면 그것을 read_only 로 연다(매크로 호출용), 아니면 메모리 연결."""
        return _open(self._catalog.path if self._catalog.usable else None)

    def _source(self, table: str) -> str:
        return self._tables[table].parquet_source()

    def _load_sessions(self) -> tuple[date, ...]:
        con = _open(None)
        try:
            rows = con.execute(
                f"SELECT date FROM {self._source(CALENDAR_TABLE)} ORDER BY date"
            ).fetchall()
        finally:
            con.close()
        sessions = tuple(_as_date(row[0], "trading_calendar.date") for row in rows)
        if not sessions:
            raise EquityDuckdbSetupError(
                f"trading_calendar has no sessions — root={self._root} "
                f"build={self._builds[CALENDAR_TABLE]}"
            )
        return sessions

    def _load_policies(self) -> dict[str, tuple[str, ...]]:
        con = _open(None)
        try:
            rows = con.execute(
                f"SELECT universe_id, predicate FROM {self._source(POLICY_TABLE)} "
                "ORDER BY universe_id, rule_seq"
            ).fetchall()
        finally:
            con.close()
        policies: dict[str, list[str]] = {}
        for universe_id, predicate in rows:
            text = str(predicate).strip() if predicate is not None else ""
            if not text:
                raise EquityDuckdbSetupError(
                    f"universe_policy has an empty predicate — root={self._root} "
                    f"universe_id={universe_id!r} build={self._builds[POLICY_TABLE]}"
                )
            policies.setdefault(str(universe_id), []).append(text)
        return {universe_id: tuple(rules) for universe_id, rules in policies.items()}

    def _adj_unavailable_reason(self) -> str | None:
        if FACTOR_TABLE not in self._builds:
            return f"{FACTOR_TABLE} not built — root={self._root}"
        if not self._catalog.usable:
            return self._catalog.reason
        if not self._catalog.has_macro(ADJ_MACRO):
            return (
                f"catalog macro {ADJ_MACRO} not published (macros_skipped) — "
                f"catalog={self._catalog.path} macros={list(self._catalog.macros)}"
            )
        return None

    @property
    def backfill_end(self) -> date:
        return self._sessions[-1]

    # ── EquityDataPort ────────────────────────────────────────────────────────

    def snapshot(self) -> DataSnapshot:
        return DataSnapshot(
            snapshot_id=self._snapshot_id,
            schema_version=SCHEMA_VERSION,
            built_at=max(build.built_at for build in self._tables.values()),
            source=f"equity_duckdb:{self._root}",
            point_in_time=True,
            dataset_revisions=tuple(
                DatasetRevision(table, build.build_id, self.backfill_end)
                for table, build in sorted(self._tables.items())
            ),
        )

    def list_fields(self) -> tuple[DatasetFieldProfile, ...]:
        """제공 필드의 프로필. 축소 범위 밖 field_id 는 싣지 않는다(질의하면 unavailable)."""
        coverage = self._coverage()
        return tuple(
            DatasetFieldProfile(
                field_id=spec.field_id,
                dataset_id=spec.dataset_id,
                label=spec.label,
                unit=spec.unit,
                value_type=spec.value_type,
                frequency="daily",
                available_date_basis=spec.available_date_basis,
                recommended_lag_sessions=PRICE_LAG_SESSIONS,
                description=spec.description,
                disclosure_basis=spec.disclosure_basis,
                evidence=spec.evidence,
                coverage=FieldCoverageCapability(
                    starts_on=self._sessions[0],
                    ends_on=self.backfill_end,
                    venues=(VENUE,),
                    estimated_coverage_pct=coverage[spec.field_id],
                    supported_cell_kinds=(CellKind.OBSERVED, CellKind.MISSING),
                    point_in_time=True,  # adj_close 도 전방 조정이라 (security, date) 의 함수
                ),
            )
            for spec in self._fields.values()
        )

    def _coverage(self) -> dict[str, float]:
        """필드별 커버율 = 값이 있는 price_daily 행 / universe_daily 격자 행 (한 번 재고 캐시)."""
        if self._coverage_pct is not None:
            return self._coverage_pct
        con = _open(None)
        try:
            counts = con.execute(
                f"SELECT count(close), count(mktcap_krw) FROM {self._source(PRICE_TABLE)}"
            ).fetchone()
            grid = con.execute(f"SELECT count(*) FROM {self._source(UNIVERSE_TABLE)}").fetchone()
        finally:
            con.close()
        if counts is None or grid is None:
            raise EquityDuckdbSetupError(f"coverage count returned no row — root={self._root}")
        n_grid = max(_as_int(grid[0], "universe_daily rows"), 1)

        def pct(n: object) -> float:
            return min(100.0, 100.0 * _as_int(n, "price_daily count") / n_grid)

        self._coverage_pct = {
            "price.close": pct(counts[0]),
            "price.market_cap": pct(counts[1]),
            "price.adj_close": pct(counts[0]),  # adj_close IS NULL ⇔ close IS NULL
        }
        return self._coverage_pct

    def load_universe(self, query: UniverseHistoryQuery) -> UniverseHistoryResult:
        if query.venue != VENUE:
            return UniverseHistoryResult(
                points=(),
                status=DataLoadStatus.INVALID_QUERY,
                snapshot_id=self._snapshot_id,
                detail=f"unknown venue — venue={query.venue!r} supported=({VENUE!r},)",
            )
        window = self._window(query.start, query.end, 0)
        if isinstance(window, str):
            return UniverseHistoryResult((), DataLoadStatus.NO_DATA, self._snapshot_id, window)
        names = (
            f"LEFT JOIN {self._source(SECURITY_TABLE)} sec ON sec.ticker = u.ticker"
            if SECURITY_TABLE in self._tables
            else ""
        )
        name_expr = "sec.name_current" if SECURITY_TABLE in self._tables else "NULL"
        con = self._connect()
        try:
            rows = con.execute(
                f"""
                SELECT u.date, u.ticker, s.span_seq, {name_expr}
                FROM {self._source(UNIVERSE_TABLE)} u
                JOIN {self._source(SPAN_TABLE)} s
                  ON s.ticker = u.ticker AND u.date BETWEEN s.first_date AND s.last_date
                {names}
                WHERE u.date BETWEEN {_lit(window.requested[0])} AND {_lit(window.requested[-1])}
                ORDER BY u.date, u.ticker, s.span_seq
                """
            ).fetchall()
        finally:
            con.close()
        members: dict[date, list[SecurityRef]] = {session: [] for session in window.requested}
        for raw_date, ticker, span_seq, name in rows:
            session = _as_date(raw_date, "universe_daily.date")
            ticker_text = str(ticker)
            members.setdefault(session, []).append(
                SecurityRef(
                    security_id=f"{ticker_text}{SECURITY_ID_SEP}{_as_int(span_seq, 'span_seq')}",
                    ticker=ticker_text,
                    name=str(name) if name is not None else ticker_text,
                    venue=VENUE,
                )
            )
        points = tuple(
            UniversePoint(session=session, members=tuple(members[session]))
            for session in window.requested
        )
        if not any(point.members for point in points):
            return UniverseHistoryResult(
                points=(),
                status=DataLoadStatus.NO_DATA,
                snapshot_id=self._snapshot_id,
                detail=(
                    f"no universe rows — venue={query.venue} start={query.start} end={query.end}"
                ),
            )
        return UniverseHistoryResult(points, DataLoadStatus.OK, self._snapshot_id)

    def load_panel(self, query: ResearchPanelQuery) -> ResearchPanelResult:
        unknown = self._unknown_fields(query.field_ids)
        if unknown:
            return ResearchPanelResult(
                (), DataLoadStatus.INVALID_QUERY, self._snapshot_id, (), unknown
            )
        parsed = {sid: _parse_security_id(sid) for sid in query.security_ids}
        malformed = sorted(security_id for security_id, item in parsed.items() if item is None)
        if malformed:
            return ResearchPanelResult(
                (),
                DataLoadStatus.INVALID_QUERY,
                self._snapshot_id,
                (),
                f"malformed security_id — expected <ticker>{SECURITY_ID_SEP}<span_seq> "
                f"got={malformed}",
            )
        wanted = {item for item in parsed.values() if item is not None}
        spans = self._spans(sorted({ticker for ticker, _ in wanted}))
        known = {(span.ticker, span.span_seq) for span in spans}
        unknown_ids = sorted(f"{t}{SECURITY_ID_SEP}{s}" for t, s in wanted - known)
        if unknown_ids:
            return ResearchPanelResult(
                (),
                DataLoadStatus.INVALID_QUERY,
                self._snapshot_id,
                (),
                f"unknown security_id — not in {SPAN_TABLE}: {unknown_ids} root={self._root}",
            )
        lag_by_field = {field_id: PRICE_LAG_SESSIONS for field_id in query.field_ids}
        lag_by_field.update({item.field_id: item.sessions for item in query.lag_overrides})
        window = self._window(query.start, query.end, 0)
        if isinstance(window, str):
            return ResearchPanelResult((), DataLoadStatus.NO_DATA, self._snapshot_id, (), window)
        rows, warnings = self._fetch(
            window,
            max(lag_by_field.values()),
            tickers=tuple(sorted({ticker for ticker, _ in wanted})),
            predicate=None,
            fields=query.field_ids,
        )
        cells: list[ResearchPanelCell] = []
        for row in self._rows_in(rows, window.requested):
            if (row.ticker, row.span_seq) not in wanted:
                continue
            for field_id in query.field_ids:
                found = self._lagged(rows, row, lag_by_field[field_id])
                if found is None:
                    continue
                column = self._fields[field_id].column
                value = found.value(column)
                cells.append(
                    ResearchPanelCell(
                        as_of=row.session,
                        security_id=row.security_id,
                        field_id=field_id,
                        source_effective_date=found.session,
                        available_date=found.available_date(column),
                        value=value,
                        kind=CellKind.OBSERVED if value is not None else CellKind.MISSING,
                    )
                )
        return ResearchPanelResult(
            cells=tuple(cells),
            status=DataLoadStatus.OK if cells else DataLoadStatus.NO_DATA,
            snapshot_id=self._snapshot_id,
            warnings=warnings,
            detail=None if cells else f"no panel cells — query={query} root={self._root}",
        )

    # ── FactorMetadataPort · FactorObservationPort ────────────────────────────

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        """제공 필드만 계약을 돌려준다 — 없는 필드는 그래프 컴파일이 막는다."""
        return FactorMetadataSnapshot(
            data_snapshot_id=self._snapshot_id,
            fields=tuple(
                FieldMetadata(
                    field_id=field_id,
                    unit=self._fields[field_id].unit,
                    value_type=NodeValueType.NUMERIC_SERIES,
                )
                for field_id in field_ids
                if field_id in self._fields
            ),
        )

    def load_factor_observations(self, query: FactorObservationQuery) -> FactorObservationSet:
        """`RESEARCH_UNIVERSE_ID` 위의 raw 패널을 팩터 관측으로. status 가 없어 실패는 예외."""
        unknown = self._unknown_fields(query.required_field_ids)
        if unknown:
            raise ValueError(unknown)
        window = self._window(query.start, query.end, max(query.minimum_history_sessions - 1, 0))
        if isinstance(window, str):
            raise ValueError(window)
        rows, _ = self._fetch(
            window,
            PRICE_LAG_SESSIONS,
            tickers=None,
            predicate=self._predicate(RESEARCH_UNIVERSE_ID),
            fields=query.required_field_ids,
        )
        observations: list[FactorObservation] = []
        for row in self._rows_in(rows, window.sessions):
            found = self._lagged(rows, row, PRICE_LAG_SESSIONS)
            fields = (
                ()
                if found is None
                else tuple(
                    FactorFieldValue(field_id, found.value(self._fields[field_id].column))
                    for field_id in query.required_field_ids
                )
            )
            observations.append(
                FactorObservation(
                    as_of=row.session,
                    security_id=row.security_id,
                    fields=fields,
                    forward_return=None,  # equity 소유 아님(FIELD_MAP §1)
                    universe_member=row.member,
                )
            )
        observations.sort(key=lambda item: (item.as_of, item.security_id))
        return FactorObservationSet(self._snapshot_id, tuple(observations))

    # ── RawObservationPort ────────────────────────────────────────────────────

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        def failure(status: DataLoadStatus, detail: str) -> RawObservationSet:
            return RawObservationSet(status, self._snapshot_id, (), (), (), detail)

        if query.market != MARKET:
            return failure(
                DataLoadStatus.INVALID_QUERY,
                f"unknown market — market={query.market!r} supported=({MARKET!r},)",
            )
        if query.universe_id not in self._policies:
            return failure(
                DataLoadStatus.INVALID_QUERY,
                f"unknown universe_id — universe_id={query.universe_id!r} "
                f"supported={sorted(self._policies)} ({POLICY_TABLE} build="
                f"{self._builds[POLICY_TABLE]})",
            )
        unknown = self._unknown_fields(query.field_ids)
        if unknown:
            return failure(DataLoadStatus.INVALID_QUERY, unknown)
        window = self._window(query.start, query.end, query.history_sessions_before_start)
        if isinstance(window, str):
            return failure(DataLoadStatus.NO_DATA, window)
        rows, fetch_warnings = self._fetch(
            window,
            PRICE_LAG_SESSIONS,
            tickers=None,
            predicate=self._predicate(query.universe_id),
            fields=query.field_ids,
        )
        observations: list[RawObservation] = []
        for row in self._rows_in(rows, window.sessions):
            fields: list[RawFieldValue] = []
            for field_id in query.field_ids:
                found = self._lagged(rows, row, PRICE_LAG_SESSIONS)
                if found is None:
                    continue
                column = self._fields[field_id].column
                fields.append(
                    RawFieldValue(
                        field_id=field_id,
                        value=found.value(column),
                        available_date=found.available_date(column),
                    )
                )
            observations.append(
                RawObservation(
                    as_of=row.session,
                    security_id=row.security_id,
                    universe_member=row.member,
                    fields=tuple(fields),
                    sector_id=None,  # classification.sector 는 축소 범위 밖(현재값 라벨, PIT 아님)
                    previous_weight=0.0,
                )
            )
        observations.sort(key=lambda item: (item.as_of, item.security_id))
        return RawObservationSet(
            status=DataLoadStatus.OK if observations else DataLoadStatus.NO_DATA,
            data_snapshot_id=self._snapshot_id,
            sessions=window.requested,
            history_sessions=window.history,
            observations=tuple(observations),
            detail=(
                None
                if observations
                else f"no members in universe — universe_id={query.universe_id} "
                f"start={query.start} end={query.end} root={self._root}"
            ),
            warnings=tuple(sorted({*window.warnings, *fetch_warnings})),
        )

    # ── BacktestDataPort ──────────────────────────────────────────────────────

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        """원주가 bar + 구간 + adj_factor 사건. `BacktestDataset` 에 status 가 없어 실패는 예외."""
        requested = list(query.security_ids)
        if query.benchmark_security_id is not None and query.benchmark_security_id not in requested:
            requested.append(query.benchmark_security_id)
        parsed: dict[str, tuple[str, int]] = {}
        for security_id in requested:
            item = _parse_security_id(security_id)
            if item is None:
                raise ValueError(
                    f"malformed security_id — expected <ticker>{SECURITY_ID_SEP}<span_seq> "
                    f"got={security_id!r} (benchmark idx:* is not served — GAP-09)"
                )
            parsed[security_id] = item
        spans = {
            (span.ticker, span.span_seq): span
            for span in self._spans(sorted({ticker for ticker, _ in parsed.values()}))
        }
        unknown = sorted(sid for sid, key in parsed.items() if key not in spans)
        if unknown:
            raise ValueError(
                f"unknown security_id — not in {SPAN_TABLE}: {unknown} root={self._root}"
            )
        if FACTOR_TABLE not in self._builds:
            raise EquityDuckdbSetupError(
                f"{FACTOR_TABLE} not built — a backtest without the corporate-action feed is "
                f"silently wrong across splits; root={self._root}"
            )
        tickers = tuple(sorted({ticker for ticker, _ in parsed.values()}))
        con = self._connect()
        try:
            price_rows = con.execute(
                f"""
                SELECT ticker, date, open, high, low, close, volume_shr, price_kind
                FROM {self._source(PRICE_TABLE)}
                WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
                  AND date BETWEEN {_lit(query.start)} AND {_lit(query.end)}
                ORDER BY ticker, date
                """,
                [list(tickers)],
            ).fetchall()
            factor_columns = {
                str(row[0])
                for row in con.execute(
                    f"DESCRIBE SELECT * FROM {self._source(FACTOR_TABLE)}"
                ).fetchall()
            }
            ts_column = "apply_date" if "apply_date" in factor_columns else "effective_date"
            factor_rows = con.execute(
                f"""
                SELECT ticker, event_id, event_type, share_factor, {ts_column}
                FROM {self._source(FACTOR_TABLE)}
                WHERE factor_ok AND ticker IN (SELECT unnest(?::VARCHAR[]))
                ORDER BY ticker, {ts_column}, event_id
                """,
                [list(tickers)],
            ).fetchall()
        finally:
            con.close()
        by_ticker: dict[str, list[tuple[str, tuple[str, int]]]] = {}
        for security_id, key in parsed.items():
            by_ticker.setdefault(key[0], []).append((security_id, key))
        bars: list[MarketBarRecord] = []
        n_reference = 0
        n_invalid = 0
        for ticker, raw_date, open_, high, low, close, volume, kind in price_rows:
            session = _as_date(raw_date, "price_daily.date")
            for security_id, key in by_ticker[str(ticker)]:
                span = spans[key]
                if not span.first_date <= session <= span.last_date:
                    continue
                if kind == REFERENCE_KIND:
                    n_reference += 1  # 기준가·정지일 — 커널 규약: 방출하지 않는다
                    continue
                prices = [_as_float(v, "price") for v in (open_, high, low, close)]
                if any(p is None or p <= 0 for p in prices):
                    n_invalid += 1  # GAP-14(open NULL ∧ volume>0) 류 — Bar 가 거절하는 행
                    continue
                o, h, lo, c = (float(p) for p in prices if p is not None)
                if h < max(o, c) or lo > min(o, c):
                    n_invalid += 1
                    continue
                bars.append(
                    MarketBarRecord(
                        session=session,
                        security_id=security_id,
                        open=o,
                        high=h,
                        low=lo,
                        close=c,
                        volume=_as_int(volume, "volume_shr"),
                    )
                )
        actions: list[CorporateActionRecord] = []
        for ticker, event_id, event_type, share_factor, raw_ts in factor_rows:
            if raw_ts is None:
                raise ValueError(
                    f"{ts_column} is NULL on a factor_ok row — event_id={event_id} "
                    f"root={self._root}"
                )
            session = _as_date(raw_ts, ts_column)
            action_type = EVENT_TYPE_MAP.get(str(event_type))
            if action_type is None:
                raise ValueError(
                    f"adj_factor.event_type outside adapter vocabulary — event_id={event_id} "
                    f"event_type={event_type!r} known={sorted(EVENT_TYPE_MAP)}"
                )
            ratio = _as_float(share_factor, "share_factor")
            if ratio is None or (ratio > 1) != (action_type == "split"):
                raise ValueError(
                    f"share_factor direction contradicts event_type — event_id={event_id} "
                    f"event_type={event_type} share_factor={share_factor!r}"
                )
            for security_id, key in by_ticker[str(ticker)]:
                span = spans[key]
                in_span = span.first_date <= session <= span.last_date
                if in_span and query.start <= session <= query.end:
                    actions.append(
                        CorporateActionRecord(
                            session=session,
                            security_id=security_id,
                            action_type=action_type,
                            ratio=repr(ratio),
                            detail=str(event_id),
                        )
                    )
        bars.sort(key=lambda bar: (bar.session, bar.security_id))
        actions.sort(key=lambda action: (action.session, action.security_id, action.detail))
        memberships = tuple(
            UniverseMembershipRecord(
                security_id=security_id,
                first_session=max(spans[key].first_date, query.start),
                last_session=min(spans[key].last_date, query.end),
            )
            for security_id, key in parsed.items()
            if spans[key].first_date <= query.end and spans[key].last_date >= query.start
        )
        warnings = [
            DataWarning(
                code="equity.reference_rows_dropped",
                message=(
                    f"price_kind='reference' rows (기준가·정지일) are not emitted as bars — "
                    f"dropped={n_reference}"
                ),
                severity=WarningSeverity.INFO,
            )
        ]
        if n_invalid:
            warnings.append(
                DataWarning(
                    code="equity.invalid_ohlc_rows_dropped",
                    message=(
                        f"rows with NULL/non-positive or inconsistent OHLC dropped (GAP-14) — "
                        f"dropped={n_invalid}"
                    ),
                )
            )
        return BacktestDataset(
            data_snapshot_id=self._snapshot_id,
            bars=tuple(bars),
            memberships=memberships,
            corporate_actions=tuple(actions),
            benchmark_security_id=query.benchmark_security_id,
            warnings=tuple(warnings),
        )

    # ── 패널 코어 ─────────────────────────────────────────────────────────────

    def _unknown_fields(self, field_ids: Sequence[str]) -> str | None:
        unknown = sorted(set(field_ids) - set(self._fields))
        if not unknown:
            return None
        adj_reason = self._adj_unavailable_reason()
        note = (
            f" price.adj_close unavailable: {adj_reason}"
            if adj_reason is not None and "price.adj_close" in unknown
            else ""
        )
        return (
            f"unavailable field_id — unknown_fields={unknown} "
            f"supported={sorted(self._fields)} (S21 축소 범위, mock 대체 없음).{note}"
        )

    def _predicate(self, universe_id: str) -> str:
        return " AND ".join(f"({rule})" for rule in self._policies[universe_id])

    def _window(self, start: date, end: date, history: int) -> _Window | str:
        """[start, end] 안 세션 + start 앞 `history` 세션. 커버리지 밖은 진단 문자열."""
        if start < self._sessions[0] or end > self.backfill_end:
            return (
                f"query outside coverage — start={start} end={end} "
                f"calendar={self._sessions[0]}..{self.backfill_end} root={self._root}"
            )
        first = bisect_left(self._sessions, start)
        last = bisect_right(self._sessions, end)
        if first >= last:
            return f"no sessions in range — start={start} end={end} root={self._root}"
        history_first = first - history
        warnings: tuple[str, ...] = ()
        if history_first < 0:
            warnings = (
                f"insufficient calendar for warm-up history — requested={history} "
                f"available={first} calendar_start={self._sessions[0]} start={start}",
            )
            history_first = 0
        return _Window(
            history=self._sessions[history_first:first],
            requested=self._sessions[first:last],
            warnings=warnings,
        )

    def _spans(self, tickers: Sequence[str]) -> tuple[_Span, ...]:
        if not tickers:
            return ()
        con = _open(None)
        try:
            rows = con.execute(
                f"SELECT ticker, span_seq, first_date, last_date FROM {self._source(SPAN_TABLE)} "
                "WHERE ticker IN (SELECT unnest(?::VARCHAR[])) ORDER BY ticker, span_seq",
                [list(tickers)],
            ).fetchall()
        finally:
            con.close()
        return tuple(
            _Span(
                str(ticker),
                _as_int(span_seq, "span_seq"),
                _as_date(first, "first_date"),
                _as_date(last, "last_date"),
            )
            for ticker, span_seq, first, last in rows
        )

    def _fetch(
        self,
        window: _Window,
        max_lag: int,
        *,
        tickers: tuple[str, ...] | None,
        predicate: str | None,
        fields: Sequence[str],
    ) -> tuple[dict[tuple[str, date], _Row], tuple[str, ...]]:
        """격자 행 (ticker, date) → `_Row`. `tickers` 가 없으면 `predicate` 가 창 안에서 한 번이라도
        참인 종목 집합(정책 driven)이고, 있으면 그 종목이다. 랙만큼 앞 세션까지 더 읽는다."""
        first_index = self._session_index[window.sessions[0]]
        fetch_first = max(first_index - max_lag, 0)
        warnings: list[str] = []
        if first_index - max_lag < 0 and max_lag > 0:
            warnings.append(
                f"insufficient calendar for lag — max_lag={max_lag} "
                f"first_session={window.sessions[0]} calendar_start={self._sessions[0]}"
            )
        fetch_start, fetch_end = self._sessions[fetch_first], window.sessions[-1]
        member_expr = (
            f"coalesce(({predicate}), FALSE)" if predicate is not None else "NULL::BOOLEAN"
        )
        selection = (
            "SELECT DISTINCT ticker FROM u WHERE member"
            if tickers is None
            else "SELECT unnest(?::VARCHAR[]) AS ticker"
        )
        params: list[object] = [] if tickers is None else [list(tickers)]
        wants_adj = "price.adj_close" in fields
        adj_join = (
            f"LEFT JOIN (SELECT ticker, date, adj_close, available_date "
            f"FROM {ADJ_MACRO}(as_of := {_lit(fetch_end)}) "
            f"WHERE date BETWEEN {_lit(fetch_start)} AND {_lit(fetch_end)}) a "
            "ON a.ticker = r.ticker AND a.date = r.date"
            if wants_adj
            else ""
        )
        adj_expr = "a.adj_close, a.available_date" if wants_adj else "NULL::DOUBLE, NULL::DATE"
        sql = f"""
            WITH u AS (
                SELECT u.date, u.ticker, {member_expr} AS member
                FROM {self._source(UNIVERSE_TABLE)} u
                WHERE u.date BETWEEN {_lit(fetch_start)} AND {_lit(fetch_end)}
            ),
            sel AS ({selection}),
            r AS (
                SELECT u.date, u.ticker, s.span_seq, u.member
                FROM u JOIN sel USING (ticker)
                JOIN {self._source(SPAN_TABLE)} s
                  ON s.ticker = u.ticker AND u.date BETWEEN s.first_date AND s.last_date
            ),
            px AS (
                SELECT ticker, date, close, mktcap_krw FROM {self._source(PRICE_TABLE)}
                WHERE date BETWEEN {_lit(fetch_start)} AND {_lit(fetch_end)}
                  AND ticker IN (SELECT ticker FROM sel)
            )
            SELECT r.date, r.ticker, r.span_seq, r.member, px.ticker IS NOT NULL,
                   px.close, px.mktcap_krw, {adj_expr}
            FROM r
            LEFT JOIN px ON px.ticker = r.ticker AND px.date = r.date
            {adj_join}
            ORDER BY r.date, r.ticker, r.span_seq
        """
        con = self._connect()
        try:
            raw_rows = con.execute(sql, params).fetchall()
        finally:
            con.close()
        rows: dict[tuple[str, date], _Row] = {}
        for raw_date, ticker, span_seq, member, has_price, close, mktcap, adj, adj_av in raw_rows:
            row = _Row(
                session=_as_date(raw_date, "universe_daily.date"),
                ticker=str(ticker),
                span_seq=_as_int(span_seq, "span_seq"),
                member=bool(member),
                has_price=bool(has_price),
                close=_as_float(close, "close"),
                mktcap_krw=_as_float(mktcap, "mktcap_krw"),
                adj_close=_as_float(adj, "adj_close"),
                adj_available_date=(
                    None if adj_av is None else _as_date(adj_av, f"{ADJ_MACRO}.available_date")
                ),
            )
            key = (row.ticker, row.session)
            if key in rows:
                raise EquityDuckdbSetupError(
                    f"duplicate (ticker, date) in {UNIVERSE_TABLE}×{SPAN_TABLE} — key={key} "
                    f"root={self._root}"
                )
            rows[key] = row
        return rows, tuple(warnings)

    @staticmethod
    def _rows_in(rows: dict[tuple[str, date], _Row], sessions: Sequence[date]) -> Iterator[_Row]:
        wanted = set(sessions)
        for row in rows.values():
            if row.session in wanted:
                yield row

    def _lagged(self, rows: dict[tuple[str, date], _Row], row: _Row, lag: int) -> _Row | None:
        """as_of 행에서 `lag` 세션 전의 같은 종목 행(가격 행이 있을 때만). 없으면 None(미공표)."""
        index = self._session_index[row.session] - lag
        if index < 0:
            return None
        found = rows.get((row.ticker, self._sessions[index]))
        if found is None or not found.has_price:
            return None
        return found
