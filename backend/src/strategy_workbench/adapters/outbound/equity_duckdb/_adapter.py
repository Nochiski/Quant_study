"""`EquityDuckdbAdapter` — equity 층 parquet·카탈로그 위에서 워크벤치 5포트를 답한다 (S21 본판).

범위(EQUITY_WORKFLOW §3-5 · FIELD_MAP §2·§3): 선언표 `_specs.py` 가 내는 field_id 전부. 이 빌드에
원천 테이블이 있는 것만 `list_fields()` 에 오르고, 나머지(FIELD_MAP 42 중 미지원·미확인)는 목록
밖이며 질의하면 `INVALID_QUERY`(detail 에 `unavailable` + 사유)다 — mock 값으로 대신하지 않는다.
`RawObservationPort` 가 본체이고, 같은 패널 코어 위에
`EquityDataPort`(`snapshot`·`list_fields`·`load_universe`·`load_panel`), `FactorMetadataPort`,
`FactorObservationPort`, `BacktestDataPort` 를 올렸다 — 컨테이너(`build_container`)와 백테스트
파이프라인이 다섯을 다 요구한다.

**필드별 분기가 없다.** (원천, 컬럼식, 축 변환, 랙, 결측 규칙)은 전부 `_specs.SOURCE_SPECS` ·
`_specs.FIELD_SPECS` 의 데이터이고 이 모듈은 그 표를 SQL 과 셀 조회로 해석한다. 두 가지 읽는
방식만 있다(`SourceMode`):
  `GRID`   (ticker, session) 격자 행. 랙 n = 정확히 n 세션 전 행, 없으면 셀 없음(합성 금지).
           격자 셀에 원장 행이 둘 이상 올 수 있는 원천(`flow_daily` — grain 에 `src` 가 든다)은
           선언된 `pick_order` 로 한 행을 결정적으로 고른다(값을 섞지 않는다).
  `LATEST` `available_date` 축 관측. 셀 = 컷오프(as_of 에서 n 세션 전 거래일) 이하의 마지막 관측
           이고 `available_date` 는 그 관측의 공개일이다. 관측이 없으면 셀 없음.
값이 있으면 `CellKind.OBSERVED`, 없으면 격자 3테이블(S08~S10)의 `fill_kind.kind` 가 말하는 종류
(`_FILL_KIND_TO_CELL`: not_collected → NOT_COLLECTED, 나머지 → MISSING)이고 `fill_kind` 축이 없는
원천은 MISSING 이다 — `load_panel` 과 `RawObservationPort` 가 같은 `_Observed.kind` 를 쓴다
(둘의 셀 집합·값·공개일·kind 가 계약상 같아야 한다).

어휘(FIELD_MAP §1): `security_id = {ticker}:{span_seq}` · `market = 'KRX'` · `venue = 'XKRX'` ·
`universe_id` 는 `universe_policy` 의 행(`krx.` || policy)이고 술어(`predicate`)를 `universe_daily`
위에서 AND 로 평가해 종목 집합·`universe_member` 를 만든다(정책표 driven, 하드코딩 없음).

PIT: 모든 셀은 `available_date ≤ as_of` 다. 랙은 컬럼군별 상수(`SourceSpec.lag_sessions`, 근거는
`lag_basis`)이고 `dataset_profile`(S19)이 오면 프로필 값으로 바꾼다. 창 독립: `GRID` 는 (security,
date) 의 값, `LATEST` 는 (security, cutoff) 의 값이라 질의 창을 바꿔도 같은 셀은 같다.

`price.adj_close` = **전방 조정**(결정 09-05): **`price_adj_daily` 표를 직접 읽는다**(S23,
2026-09-06). 원주가 × 그날까지 공개·적용된 계수의 누적 share_factor 이고, 종목의 첫 관측 수준을
고정하고 사건마다 이후 가격을 올린다(삼성전자 2018-05-03 2,650,000 그대로, 05-04 51,900 × 50 =
2,595,000). 값은 (security, date) 의 순수 함수라 창·as_of 에 무관하다. 예전에는 카탈로그 매크로
`v_adj_price_fwd` 를 불렀는데, 그러면 카탈로그가 낡거나(snapshot 불일치) 없으면 조정가가 통째로
unavailable 이 됐다 — 표를 읽으면서 그 의존이 끊겼다(매크로는 같은 값을 내는 읽기 경로로 남고,
동일성은 equity `EG3_price_adj_daily` 가 매 빌드 증명한다).

법인 축 테이블(`fin_std`·`dividend_event`·`holder_daily`)은 티커 컬럼이 없어 `corp_ticker` 로
전개하고 **한 법인의 종류주 티커 전부가 같은 값**을 받는다(`_specs` 모듈 docstring). `corp_ticker`
는 시점축 없는 현재 스냅샷이다.

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
from collections.abc import Callable, Iterator, Sequence
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
from ._specs import (
    CALENDAR_TABLE,
    CORP_TICKER_TABLE,
    FACTOR_TABLE,
    FIELD_BY_ID,
    FIELD_SPECS,
    POLICY_TABLE,
    PRICE_TABLE,
    PROFILE_TABLE,
    REQUIRED_TABLES,
    SECURITY_TABLE,
    SOURCE_BY_NAME,
    SPAN_TABLE,
    UNIVERSE_TABLE,
    UNSUPPORTED_FIELDS,
    FieldSpec,
    Reduce,
    SourceAxis,
    SourceMode,
    SourceSpec,
)

if TYPE_CHECKING:
    import duckdb

MARKET = "KRX"
VENUE = "XKRX"
SCHEMA_VERSION = "equity-v1.2"
SECURITY_ID_SEP = ":"
_CHECKPOINT_ROWS = 256  # 취소 체크포인트 간격(행) — 포트의 `_CHECKPOINT_BATCH` 와 같은 크기
RESEARCH_UNIVERSE_ID = "krx.common-stock"  # FactorObservationQuery 에 유니버스가 없다 — 계약 기본값
REFERENCE_KIND = "reference"
# adj_factor.event_type → 커널 CorporateActionType 값 (S07 `EVENT_TYPE_MAP` 과 같은 판단: ok 행은
# 전부 시총 불변이라 주식수 증가는 split, 감소는 reverse_split 로 보내 수량이 조정되게 한다)
EVENT_TYPE_MAP: dict[str, str] = {
    "split": "split",
    "bonus": "split",
    "reverse_split": "reverse_split",
    "capred": "reverse_split",
}
# S06-2 의 KRX 기준가 원천이 만든 사건 — `corp_event` 에 없어 유형을 모른다(기준가 변화 + 같은 날
# 주식수 변화, 시총 불변). 방향은 share_factor 가 정한다: > 1 → split, < 1 → reverse_split.
# 엔진 어댑터(`backtest_engine/adapters/equity_duckdb.py::RATIO_DIRECTED_EVENT_TYPES`)와 **같은
# 어휘를 써야 한다** — 한쪽만 알면 같은 데이터로 한쪽에서만 run 이 죽는다(서버 factor_ok 55행).
# `unknown_price_only`(기준가만 변화)는 항상 factor_ok=false 라 여기 오지 않고, ok 로 실려 오면
# 어휘 밖이 맞다 — 시총 불변이 아닌 사건을 분할로 적용하면 안 된다.
RATIO_DIRECTED_EVENT_TYPES: frozenset[str] = frozenset({"unknown_krx"})
_TICKER_RE = re.compile(r"^[0-9A-Za-z]{1,12}$")
# equity 격자 3테이블(S08~S10)의 `fill_kind.kind` → 워크벤치 `CellKind`. 정본 어휘는
# `database/src/equity/model.py::FILL_KINDS` 이고 대응 원칙은 FIELD_MAP §1 「결측 어휘」다.
# `src_omitted` 만 그 표와 다르게 접힌다 — 도메인이 `SOURCE_OMITTED_ZERO` 셀에 값을 요구하는데
# (`RawFieldValue.__post_init__`·`ResearchPanelCell.__post_init__`) equity 는 그 자리를 NULL 로
# 두기로 못박았다(DESIGN §9 결정 8 「격자 빈칸에 0 을 굽지 않는다」). 도메인을 고치지 않는 쪽을
# 골랐으므로 라벨을 잃고 MISSING 으로 접는다 — 사유는 필드 프로필 description 이 문장으로 남긴다.
_FILL_KIND_TO_CELL: dict[str, CellKind] = {
    "measured": CellKind.OBSERVED,
    "src_omitted": CellKind.MISSING,
    "empty_response": CellKind.MISSING,
    "not_collected": CellKind.NOT_COLLECTED,
}


def _cell_kind(value: float | None, fill_kind: object) -> CellKind:
    """셀 종류 — 값이 있으면 OBSERVED, 없으면 `fill_kind` 가 말하는 대로(없으면 MISSING).

    값이 있는 셀을 무조건 OBSERVED 로 두는 것은 계약이다(관측 셀은 값을 가져야 한다). 값이
    없는 셀만 격자 테이블의 결측 어휘를 읽고, 어휘 밖 문자열·NULL 은 MISSING 으로 접는다.
    """
    if value is not None:
        return CellKind.OBSERVED
    if fill_kind is None:
        return CellKind.MISSING
    return _FILL_KIND_TO_CELL.get(str(fill_kind), CellKind.MISSING)


def _noop_checkpoint() -> None:
    """취소를 요구하지 않는 호출자용 체크포인트 — `load_raw_observations` 의 기본값."""


@dataclass(frozen=True)
class _Observed:
    """셀 하나 — 값 · 공개일 · 내용일(관측이 가리키는 기간·사건의 날짜) · 셀 종류."""

    value: float | None
    available_date: date
    content_date: date
    kind: CellKind


@dataclass(frozen=True)
class _Row:
    """패널 코어 한 행 = universe_daily 격자 (date, ticker) + 구간 + GRID 원천의 셀."""

    session: date
    ticker: str
    span_seq: int
    member: bool
    cells: dict[str, _Observed]  # field_id → 그 세션의 GRID 셀 (원천 행이 없으면 키가 없다)

    @property
    def security_id(self) -> str:
        return f"{self.ticker}{SECURITY_ID_SEP}{self.span_seq}"


@dataclass(frozen=True)
class _LatestSeries:
    """한 축 키의 `available_date` 오름차순 관측열 — 컷오프로 bisect 해서 마지막 관측을 찾는다."""

    dates: tuple[date, ...]
    cells: tuple[dict[str, _Observed], ...]


@dataclass(frozen=True)
class _Panel:
    """한 질의의 읽은 것 전부 — GRID 격자 행 + LATEST 원천별 관측열 + 티커→법인 대응."""

    rows: dict[tuple[str, date], _Row]
    latest: dict[str, dict[str, _LatestSeries]]  # source name → 축 키 → 관측열
    corp_by_ticker: dict[str, str]
    warnings: tuple[str, ...]


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
            `price_daily` MANIFEST 가 있는 equity 루트. 나머지 테이블·`equity.duckdb` 매크로는
            선택이며 없으면 그 원천의 field_id 가 `list_fields()` 에서 빠진다
            (`load_backtest_dataset` 만은 `adj_factor` 를 요구해 예외를 던진다).

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
        self._source_reason: dict[str, str | None] = {
            name: self._source_unavailable_reason(spec) for name, spec in SOURCE_BY_NAME.items()
        }
        self._fields: dict[str, FieldSpec] = {
            spec.field_id: spec
            for spec in FIELD_SPECS
            if self._source_reason[spec.source] is None
        }
        self._profile: dict[str, tuple[int, str]] = self._load_profile()
        self._coverage_cache: dict[str, tuple[float, date]] | None = None

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

    def _load_profile(self) -> dict[str, tuple[int, str]]:
        """`dataset_profile` 의 field_id → (랙 세션, 근거). 표가 없으면 빈 dict(폴백).

        equity 층이 필드마다 확정한 공개시차가 정본이다(TECH_DEBT §4). 어댑터의
        `SourceSpec.lag_sessions` 는 이 표가 없는 루트를 위한 폴백이며, 둘이 갈리면 대장이 이긴다.
        """
        if PROFILE_TABLE not in self._tables:
            return {}
        con = _open(None)
        try:
            rows = con.execute(
                "SELECT field_id, recommended_lag_sessions, available_date_basis "
                f"FROM {self._source(PROFILE_TABLE)}"
            ).fetchall()
        except duckdb.Error as exc:  # 컬럼이 없는 구판 표 — 폴백으로 내려간다
            raise EquityDuckdbSetupError(
                f"{PROFILE_TABLE} exists but is unreadable — root={self._root} error={exc}"
            ) from exc
        finally:
            con.close()
        return {
            str(field_id): (int(lag), str(basis))
            for field_id, lag, basis in rows
            if field_id is not None and lag is not None
        }

    def _field_lag(self, field_id: str) -> tuple[int, str]:
        """field_id 의 (랙, 근거). 대장에 있으면 대장, 없으면 원천 상수 폴백."""
        entry = self._profile.get(field_id)
        if entry is not None:
            return entry
        source = SOURCE_BY_NAME[self._fields[field_id].source]
        return source.lag_sessions, f"{source.lag_basis} (fallback: no {PROFILE_TABLE} row)"

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

    def _source_unavailable_reason(self, spec: SourceSpec) -> str | None:
        """원천을 읽을 수 없으면 왜인지 — 미빌드 테이블 · stale 카탈로그 · 건너뛴 매크로."""
        macros = {name for name in spec.requires if name.startswith("v_")}
        tables = [name for name in spec.requires if name not in macros]
        absent = [table for table in tables if table not in self._builds]
        if absent:
            return f"equity tables not built — missing={absent} root={self._root}"
        if not macros:
            return None
        if not self._catalog.usable:
            return self._catalog.reason
        skipped = [name for name in sorted(macros) if not self._catalog.has_macro(name)]
        if skipped:
            return (
                f"catalog macros not published (macros_skipped) — missing={skipped} "
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
        """제공 필드의 프로필. 원천이 없는 field_id 는 싣지 않는다(질의하면 unavailable)."""
        coverage = self._coverage()
        profiles = []
        for spec in self._fields.values():
            source = SOURCE_BY_NAME[spec.source]
            pct, starts_on = coverage[spec.field_id]
            lag_sessions, lag_basis = self._field_lag(spec.field_id)
            profiles.append(
                DatasetFieldProfile(
                    field_id=spec.field_id,
                    dataset_id=source.dataset_id,
                    label=spec.label,
                    unit=spec.unit,
                    value_type=spec.value_type,
                    frequency=source.frequency,
                    available_date_basis=lag_basis,
                    recommended_lag_sessions=lag_sessions,
                    description=f"[{spec.verdict}] {spec.description}",
                    disclosure_basis=spec.disclosure_basis,
                    evidence=spec.evidence,
                    coverage=FieldCoverageCapability(
                        starts_on=starts_on,
                        ends_on=self.backfill_end,
                        venues=(VENUE,),
                        estimated_coverage_pct=pct,
                        supported_cell_kinds=self._cell_kinds(source),
                        point_in_time=True,
                    ),
                )
            )
        return tuple(profiles)

    @staticmethod
    def _cell_kinds(source: SourceSpec) -> tuple[CellKind, ...]:
        """이 원천이 낼 수 있는 셀 종류 — 선언(`kind_expr`)에서 곧바로 나온다.

        `fill_kind` 축이 없는 원천은 값 유무만 있어 OBSERVED/MISSING 이다. 격자 3테이블은
        `not_collected` 를 더 낸다. `SOURCE_OMITTED_ZERO` 는 어느 원천도 내지 않는다 —
        equity 가 그 셀을 NULL 로 두고 도메인은 그 종류에 값을 요구해서다(`_FILL_KIND_TO_CELL`).
        `COVERAGE_GAP` 도 내지 않는다: 구간·백필 밖은 셀 자체가 없다(합성 금지).
        """
        base = (CellKind.OBSERVED, CellKind.MISSING)
        return base if source.kind_expr is None else (*base, CellKind.NOT_COLLECTED)

    def _coverage(self) -> dict[str, tuple[float, date]]:
        """필드별 (커버율 %, 시작 세션). 한 번 재고 캐시한다.

        `GRID` 는 값이 있는 원천 행 / `universe_daily` 격자 행이고 시작은 캘린더 시작이다.
        `LATEST` 는 **종목 축 커버율** — 값이 하나라도 있는 축 키 / 유니버스의 축 키 — 이고
        시작은 첫 `available_date`(캘린더 안으로 자른다). 두 뜻이 달라 프로필 evidence 에 적는다.
        """
        if self._coverage_cache is not None:
            return self._coverage_cache
        con = self._connect()
        try:
            grid = con.execute(
                f"SELECT count(*) FROM {self._source(UNIVERSE_TABLE)}"
            ).fetchone()
            n_grid = max(_as_int((grid or (0,))[0], "universe_daily rows"), 1)
            n_ticker = _as_int(
                (
                    con.execute(
                        f"SELECT count(DISTINCT ticker) FROM {self._source(UNIVERSE_TABLE)}"
                    ).fetchone()
                    or (0,)
                )[0],
                "universe tickers",
            )
            n_corp = n_ticker
            if CORP_TICKER_TABLE in self._builds:
                n_corp = _as_int(
                    (
                        con.execute(
                            "SELECT count(DISTINCT corp_code) FROM "
                            f"{self._source(CORP_TICKER_TABLE)} WHERE ticker IN "
                            f"(SELECT DISTINCT ticker FROM {self._source(UNIVERSE_TABLE)})"
                        ).fetchone()
                        or (0,)
                    )[0],
                    "universe corps",
                )
            out: dict[str, tuple[float, date]] = {}
            for name, fields in self._fields_by_source(tuple(self._fields)).items():
                source = SOURCE_BY_NAME[name]
                relation = self._relation(source, self.backfill_end)
                where = f"WHERE {source.row_filter}" if source.row_filter else ""
                denominator = (
                    n_grid
                    if source.mode is SourceMode.GRID
                    else (n_ticker if source.axis is SourceAxis.TICKER else n_corp)
                )
                group = (
                    f" GROUP BY {source.key_column}" if source.reduce is Reduce.SUM else ""
                )
                for field_id in fields:
                    expr = self._fields[field_id].expr
                    if source.mode is SourceMode.GRID:
                        sql = f"SELECT count({expr}) FROM {relation} {where}"
                    else:
                        sql = (
                            f"SELECT count(DISTINCT k) FROM (SELECT {source.key_column} AS k, "
                            f"{expr} AS v FROM {relation} {where}{group}) WHERE v IS NOT NULL"
                        )
                    n = _as_int((con.execute(sql).fetchone() or (0,))[0], f"{field_id} coverage")
                    pct = min(100.0, 100.0 * n / max(denominator, 1))
                    out[field_id] = (pct, self._starts_on(con, source, relation, where))
        finally:
            con.close()
        self._coverage_cache = out
        return out

    def _starts_on(
        self, con: duckdb.DuckDBPyConnection, source: SourceSpec, relation: str, where: str
    ) -> date:
        """`LATEST` 원천의 첫 공개일(캘린더 안으로 자른다). `GRID` 는 캘린더 시작이다."""
        if source.mode is SourceMode.GRID:
            return self._sessions[0]
        row = con.execute(
            f"SELECT min({source.available_expr}) FROM {relation} {where}"
        ).fetchone()
        first = row[0] if row is not None else None
        if first is None:
            return self._sessions[0]
        opened = max(_as_date(first, source.available_expr), self._sessions[0])
        return min(opened, self.backfill_end)

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
        lags = self._lags(query.field_ids, tuple(query.lag_overrides))
        window = self._window(query.start, query.end, 0)
        if isinstance(window, str):
            return ResearchPanelResult((), DataLoadStatus.NO_DATA, self._snapshot_id, (), window)
        panel = self._panel(
            window,
            lags,
            tickers=tuple(sorted({ticker for ticker, _ in wanted})),
            predicate=None,
            field_ids=query.field_ids,
        )
        cells: list[ResearchPanelCell] = []
        for row in self._rows_in(panel, window.requested):
            if (row.ticker, row.span_seq) not in wanted:
                continue
            for field_id in query.field_ids:
                found = self._cell(panel, row, field_id, lags[field_id])
                if found is None:
                    continue
                cells.append(
                    ResearchPanelCell(
                        as_of=row.session,
                        security_id=row.security_id,
                        field_id=field_id,
                        source_effective_date=found.content_date,
                        available_date=found.available_date,
                        value=found.value,
                        kind=found.kind,
                    )
                )
        cells.sort(key=lambda cell: (cell.as_of, cell.security_id, cell.field_id))
        return ResearchPanelResult(
            cells=tuple(cells),
            status=DataLoadStatus.OK if cells else DataLoadStatus.NO_DATA,
            snapshot_id=self._snapshot_id,
            warnings=panel.warnings,
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
        lags = self._lags(query.required_field_ids, ())
        panel = self._panel(
            window,
            lags,
            tickers=None,
            predicate=self._predicate(RESEARCH_UNIVERSE_ID),
            field_ids=query.required_field_ids,
        )
        observations: list[FactorObservation] = []
        for row in self._rows_in(panel, window.sessions):
            fields = []
            for field_id in query.required_field_ids:
                found = self._cell(panel, row, field_id, lags[field_id])
                if found is not None:
                    fields.append(FactorFieldValue(field_id, found.value))
            observations.append(
                FactorObservation(
                    as_of=row.session,
                    security_id=row.security_id,
                    fields=tuple(fields),
                    forward_return=None,  # equity 소유 아님(FIELD_MAP §1)
                    universe_member=row.member,
                )
            )
        observations.sort(key=lambda item: (item.as_of, item.security_id))
        return FactorObservationSet(self._snapshot_id, tuple(observations))

    # ── RawObservationPort ────────────────────────────────────────────────────

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        """원래의 preview/backtest 호출 계약 — 취소는 선택 능력이라 no-op 체크포인트로 위임한다."""
        return self.load_raw_observations_cancellable(query, checkpoint=_noop_checkpoint)

    def load_raw_observations_cancellable(
        self,
        query: RawObservationQuery,
        *,
        checkpoint: Callable[[], None],
    ) -> RawObservationSet:
        """`load_raw_observations` 와 같은 결과 + 협조적 취소.

        `checkpoint` 는 (1) duckdb 로 내려가기 전 1회, (2) 행 조립 루프에서
        `_CHECKPOINT_ROWS` 행마다, (3) `RawObservationSet` 계약 검증 중
        (`validation_checkpoint`) 호출된다. 콜백이 던지는 예외는 그대로 올라간다 —
        정책은 애플리케이션 소유다. duckdb 질의 자체는 원자적이라 그 안에서는 끊지 못한다.
        """

        def failure(status: DataLoadStatus, detail: str) -> RawObservationSet:
            return RawObservationSet(
                status,
                self._snapshot_id,
                (),
                (),
                (),
                detail,
                validation_checkpoint=checkpoint,
            )

        checkpoint()
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
        lags = self._lags(query.field_ids, ())
        panel = self._panel(
            window,
            lags,
            tickers=None,
            predicate=self._predicate(query.universe_id),
            field_ids=query.field_ids,
        )
        observations: list[RawObservation] = []
        for index, row in enumerate(self._rows_in(panel, window.sessions)):
            if index % _CHECKPOINT_ROWS == 0:
                checkpoint()
            fields: list[RawFieldValue] = []
            for field_id in query.field_ids:
                found = self._cell(panel, row, field_id, lags[field_id])
                if found is None:
                    continue
                fields.append(
                    RawFieldValue(
                        field_id=field_id,
                        value=found.value,
                        available_date=found.available_date,
                        # load_panel 과 **같은 `_Observed.kind`** 를 쓴다 — 값이 있으면 OBSERVED,
                        # 없으면 격자 테이블의 `fill_kind` 가 말하는 종류(없으면 MISSING)다.
                        # 값 있는 셀을 OBSERVED 밖으로 보내면 포트 계약이 생성 시점에 깨지고,
                        # 두 포트가 다른 규칙을 쓰면 kind 가 셀 단위로 어긋난다.
                        kind=found.kind,
                    )
                )
            observations.append(
                RawObservation(
                    as_of=row.session,
                    security_id=row.security_id,
                    universe_member=row.member,
                    fields=tuple(fields),
                    sector_id=None,  # classification.sector 는 현재값 라벨이라 미제공(DESIGN §7)
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
            warnings=tuple(sorted({*window.warnings, *panel.warnings})),
            validation_checkpoint=checkpoint,
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
            ratio = _as_float(share_factor, "share_factor")
            if str(event_type) in RATIO_DIRECTED_EVENT_TYPES:
                if ratio is None or ratio == 1:
                    raise ValueError(
                        f"share_factor cannot direct a ratio-directed event — "
                        f"event_id={event_id} event_type={event_type} "
                        f"share_factor={share_factor!r}"
                    )
                action_type = "split" if ratio > 1 else "reverse_split"
            else:
                action_type = EVENT_TYPE_MAP.get(str(event_type))
            if action_type is None:
                raise ValueError(
                    f"adj_factor.event_type outside adapter vocabulary — event_id={event_id} "
                    f"event_type={event_type!r} "
                    f"known={sorted(EVENT_TYPE_MAP) + sorted(RATIO_DIRECTED_EVENT_TYPES)}"
                )
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
        notes = []
        for field_id in unknown:
            if field_id in UNSUPPORTED_FIELDS:
                notes.append(f"{field_id}: {UNSUPPORTED_FIELDS[field_id]}")
            elif field_id in FIELD_BY_ID:
                reason = self._source_reason[FIELD_BY_ID[field_id].source]
                notes.append(f"{field_id}: {reason}")
        detail = (
            f"unavailable field_id — unknown_fields={unknown} "
            f"supported={sorted(self._fields)} (mock 대체 없음)."
        )
        return detail + (" " + " | ".join(notes) if notes else "")

    def _lags(self, field_ids: Sequence[str], overrides: Sequence[object]) -> dict[str, int]:
        """field_id → 세션 랙. 기본은 `dataset_profile` 값이고 질의의 override 가 이긴다."""
        lags = {field_id: self._field_lag(field_id)[0] for field_id in field_ids}
        for item in overrides:
            field_id = getattr(item, "field_id", None)
            sessions = getattr(item, "sessions", None)
            if isinstance(field_id, str) and isinstance(sessions, int) and field_id in lags:
                lags[field_id] = sessions
        return lags

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

    @staticmethod
    def _fields_by_source(field_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """원천 이름 → 그 원천에서 읽을 field_id 들(선언 순서 유지)."""
        grouped: dict[str, list[str]] = {}
        for field_id in field_ids:
            grouped.setdefault(FIELD_BY_ID[field_id].source, []).append(field_id)
        return {name: tuple(items) for name, items in grouped.items()}

    def _relation(self, source: SourceSpec, as_of: date) -> str:
        """원천을 읽는 duckdb 관계식 — 테이블이면 parquet, 매크로면 as_of 를 넘긴 호출."""
        if not source.is_macro:
            return self._source(source.relation)
        return f"{source.relation}(as_of := {_lit(as_of)})"

    def _panel(
        self,
        window: _Window,
        lags: dict[str, int],
        *,
        tickers: tuple[str, ...] | None,
        predicate: str | None,
        field_ids: Sequence[str],
    ) -> _Panel:
        """격자 행 + 원천별 관측을 한 번에 읽는다.

        `tickers` 가 없으면 `predicate` 가 창 안에서 한 번이라도 참인 종목 집합(정책 driven)이고,
        있으면 그 종목이다. GRID 원천은 랙만큼 앞 세션까지 더 읽고, LATEST 원천은 창 끝까지의
        관측을 전부 읽어 세션별 컷오프를 파이썬에서 bisect 한다.
        """
        grouped = self._fields_by_source(field_ids)
        grid_sources = [name for name in grouped if SOURCE_BY_NAME[name].mode is SourceMode.GRID]
        max_lag = max((lags[f] for f in field_ids), default=0)
        first_index = self._session_index[window.sessions[0]]
        fetch_first = max(first_index - max_lag, 0)
        warnings: list[str] = []
        if first_index - max_lag < 0 and max_lag > 0:
            warnings.append(
                f"insufficient calendar for lag — max_lag={max_lag} "
                f"first_session={window.sessions[0]} calendar_start={self._sessions[0]}"
            )
        fetch_start, fetch_end = self._sessions[fetch_first], window.sessions[-1]
        rows = self._grid(
            grouped, grid_sources, fetch_start, fetch_end, tickers=tickers, predicate=predicate
        )
        panel_tickers = tuple(sorted({row.ticker for row in rows.values()}))
        latest_sources = [
            name for name in grouped if SOURCE_BY_NAME[name].mode is SourceMode.LATEST
        ]
        corp_by_ticker: dict[str, str] = {}
        if any(SOURCE_BY_NAME[name].axis is SourceAxis.CORP for name in latest_sources):
            corp_by_ticker = self._corp_map(panel_tickers)
        latest: dict[str, dict[str, _LatestSeries]] = {}
        for name in latest_sources:
            source = SOURCE_BY_NAME[name]
            keys = (
                panel_tickers
                if source.axis is SourceAxis.TICKER
                else tuple(sorted(set(corp_by_ticker.values())))
            )
            latest[name] = self._latest(source, grouped[name], keys, fetch_end)
        return _Panel(rows, latest, corp_by_ticker, tuple(warnings))

    def _grid(
        self,
        grouped: dict[str, tuple[str, ...]],
        grid_sources: Sequence[str],
        fetch_start: date,
        fetch_end: date,
        *,
        tickers: tuple[str, ...] | None,
        predicate: str | None,
    ) -> dict[tuple[str, date], _Row]:
        """`universe_daily` × `security_span` 격자에 GRID 원천을 (ticker, date) 로 붙인 행들."""
        member_expr = (
            f"coalesce(({predicate}), FALSE)" if predicate is not None else "NULL::BOOLEAN"
        )
        selection = (
            "SELECT DISTINCT ticker FROM u WHERE member"
            if tickers is None
            else "SELECT unnest(?::VARCHAR[]) AS ticker"
        )
        params: list[object] = [] if tickers is None else [list(tickers)]
        joins: list[str] = []
        selects: list[str] = []
        layout: list[tuple[str, tuple[str, ...]]] = []
        for index, name in enumerate(grid_sources):
            source = SOURCE_BY_NAME[name]
            fields = grouped[name]
            columns = ", ".join(
                f"{self._fields[field_id].expr} AS c{position}"
                for position, field_id in enumerate(fields)
            )
            where = [
                f"date BETWEEN {_lit(fetch_start)} AND {_lit(fetch_end)}",
                f"{source.key_column} IN (SELECT ticker FROM sel)",
            ]
            # `row_filter` 는 두 모드에 다 건다 — `_coverage` 가 이미 GRID 에도 걸고 있어
            # 여기서 빠뜨리면 커버율과 실제로 읽는 행이 어긋난다(현재 GRID 원천 중 선언한 것은
            # 없지만 선언표의 뜻은 모드와 무관하다).
            if source.row_filter:
                where.append(f"({source.row_filter})")
            picked = (
                f"SELECT {source.key_column} AS k, date AS d, {source.available_expr} AS av, "
                f"{source.content_expr} AS ct, {source.kind_expr or 'NULL'} AS kd, {columns} "
                f"FROM {self._relation(source, fetch_end)} WHERE {' AND '.join(where)}"
            )
            if source.pick_order is not None:
                # 격자 셀에 원장 행이 둘 이상 올 수 있는 원천(`flow_daily` 는 grain 에 src 가
                # 든다) — 선언된 순서로 한 행을 고른다. 고르지 않으면 LEFT JOIN 이 격자 행을
                # 불려 (ticker, date) 중복으로 죽는다.
                picked = (
                    f"{picked} QUALIFY row_number() OVER (PARTITION BY {source.key_column}, "
                    f"date ORDER BY {source.pick_order}) = 1"
                )
            joins.append(
                f"LEFT JOIN ({picked}) g{index} "
                f"ON g{index}.k = r.ticker AND g{index}.d = r.date"
            )
            selects.append(
                f"g{index}.k IS NOT NULL, g{index}.av, g{index}.ct, g{index}.kd, "
                + ", ".join(f"g{index}.c{position}" for position in range(len(fields)))
            )
            layout.append((name, fields))
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
            )
            SELECT r.date, r.ticker, r.span_seq, r.member{"".join(", " + s for s in selects)}
            FROM r
            {" ".join(joins)}
            ORDER BY r.date, r.ticker, r.span_seq
        """
        con = self._connect()
        try:
            raw_rows = con.execute(sql, params).fetchall()
        finally:
            con.close()
        rows: dict[tuple[str, date], _Row] = {}
        for raw in raw_rows:
            session = _as_date(raw[0], "universe_daily.date")
            ticker = str(raw[1])
            cells: dict[str, _Observed] = {}
            offset = 4
            for name, fields in layout:
                present = bool(raw[offset])
                available = raw[offset + 1]
                content = raw[offset + 2]
                fill_kind = raw[offset + 3]
                if present and available is not None and content is not None:
                    source = SOURCE_BY_NAME[name]
                    for position, field_id in enumerate(fields):
                        value = _as_float(raw[offset + 4 + position], field_id)
                        cells[field_id] = _Observed(
                            value,
                            _as_date(available, f"{source.relation}.{source.available_expr}"),
                            _as_date(content, f"{source.relation}.{source.content_expr}"),
                            _cell_kind(value, fill_kind),
                        )
                offset += 4 + len(fields)
            row = _Row(
                session=session,
                ticker=ticker,
                span_seq=_as_int(raw[2], "span_seq"),
                member=bool(raw[3]),
                cells=cells,
            )
            key = (ticker, session)
            if key in rows:
                raise EquityDuckdbSetupError(
                    f"duplicate (ticker, date) in {UNIVERSE_TABLE}×{SPAN_TABLE} — key={key} "
                    f"root={self._root}"
                )
            rows[key] = row
        return rows

    def _corp_map(self, tickers: Sequence[str]) -> dict[str, str]:
        """티커 → 법인. `corp_ticker` 는 시점축 없는 현재 스냅샷이다(DESIGN §4-5 6)."""
        if not tickers or CORP_TICKER_TABLE not in self._builds:
            return {}
        con = _open(None)
        try:
            rows = con.execute(
                f"SELECT ticker, corp_code FROM {self._source(CORP_TICKER_TABLE)} "
                "WHERE ticker IN (SELECT unnest(?::VARCHAR[])) AND corp_code IS NOT NULL",
                [list(tickers)],
            ).fetchall()
        finally:
            con.close()
        return {str(ticker): str(corp) for ticker, corp in rows}

    def _latest(
        self,
        source: SourceSpec,
        fields: tuple[str, ...],
        keys: Sequence[str],
        fetch_end: date,
    ) -> dict[str, _LatestSeries]:
        """축 키 → `available_date` 오름차순 관측열. `reduce` 가 grain 을 (키, 공개일)로 줄인다."""
        if not keys:
            return {}
        columns = ", ".join(
            f"{self._fields[field_id].expr} AS c{position}"
            for position, field_id in enumerate(fields)
        )
        relation = self._relation(source, fetch_end)
        where = [f"{source.available_expr} <= {_lit(fetch_end)}"]
        if source.row_filter:
            where.append(f"({source.row_filter})")
        where.append(f"{source.key_column} IN (SELECT unnest(?::VARCHAR[]))")
        predicate = " AND ".join(where)
        picks = ", ".join(f"c{position}" for position in range(len(fields)))
        if source.reduce is Reduce.SUM:
            sql = (
                f"SELECT {source.key_column} AS k, {source.available_expr} AS av, "
                f"max({source.content_expr}) AS ct, {columns} "
                f"FROM {relation} WHERE {predicate} "
                f"GROUP BY {source.key_column}, {source.available_expr} ORDER BY k, av"
            )
        else:
            sql = (
                f"SELECT k, av, ct, {picks} FROM ("
                f"SELECT {source.key_column} AS k, {source.available_expr} AS av, "
                f"{source.content_expr} AS ct, {columns}, row_number() OVER ("
                f"PARTITION BY {source.key_column}, {source.available_expr} "
                f"ORDER BY {source.pick_order}) AS rn "
                f"FROM {relation} WHERE {predicate}) WHERE rn = 1 ORDER BY k, av"
            )
        con = self._connect()
        try:
            raw_rows = con.execute(sql, [list(keys)]).fetchall()
        finally:
            con.close()
        dates: dict[str, list[date]] = {}
        cells: dict[str, list[dict[str, _Observed]]] = {}
        for raw in raw_rows:
            key = str(raw[0])
            available = _as_date(raw[1], f"{source.relation}.{source.available_expr}")
            content_raw = raw[2]
            content = (
                available
                if content_raw is None
                else _as_date(content_raw, f"{source.relation}.{source.content_expr}")
            )
            # LATEST 원천에는 `fill_kind` 축이 없다 — 셀 종류는 값 유무로만 갈린다.
            entry: dict[str, _Observed] = {}
            for position, field_id in enumerate(fields):
                value = _as_float(raw[3 + position], field_id)
                entry[field_id] = _Observed(
                    value, available, content, _cell_kind(value, None)
                )
            dates.setdefault(key, []).append(available)
            cells.setdefault(key, []).append(entry)
        return {
            key: _LatestSeries(tuple(dates[key]), tuple(cells[key])) for key in dates
        }

    @staticmethod
    def _rows_in(panel: _Panel, sessions: Sequence[date]) -> Iterator[_Row]:
        wanted = set(sessions)
        for row in panel.rows.values():
            if row.session in wanted:
                yield row

    def _cell(self, panel: _Panel, row: _Row, field_id: str, lag: int) -> _Observed | None:
        """`row.session` 에서 랙 `lag` 만큼 물린 셀. 관측이 없으면 None(합성하지 않는다)."""
        index = self._session_index[row.session] - lag
        if index < 0:
            return None
        cutoff = self._sessions[index]
        source = SOURCE_BY_NAME[self._fields[field_id].source]
        if source.mode is SourceMode.GRID:
            found = panel.rows.get((row.ticker, cutoff))
            return None if found is None else found.cells.get(field_id)
        key = (
            row.ticker
            if source.axis is SourceAxis.TICKER
            else panel.corp_by_ticker.get(row.ticker)
        )
        series = panel.latest.get(source.name, {}).get(key) if key is not None else None
        if series is None:
            return None
        position = bisect_right(series.dates, cutoff) - 1
        return None if position < 0 else series.cells[position][field_id]
