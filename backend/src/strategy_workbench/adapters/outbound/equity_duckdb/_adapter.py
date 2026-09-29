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

`price.adj_close` = **전방 조정**(결정 09-05): S23 표 `price_adj_daily` 의 값이다. 원주가 × 그날까지
공개·적용된 계수의 누적 share_factor 이고, 종목의 첫 관측 수준을 고정하고 사건마다 이후 가격을
올린다(삼성전자 2018-05-03 2,650,000 그대로, 05-04 51,900 × 50 = 2,595,000). 값은 (security, date)
의 순수 함수라 창·as_of 에 무관하다. 워크벤치는 그 표를 **조정 공백 적용일 행만 가린 원장 뷰
`v_adj_close`** 로 읽는다(#220) — 원장이 그날 사건을 접지 못한 행(기준가 재설정일에 늦게 공개된
계수·계수를 못 낸 기준가 재설정)은 결측이다. S23 은 카탈로그가 낡으면 조정가가 통째로 unavailable
이 되는 것을 피하려고 표를 만들었지만, 가림 규칙을 뷰가 소유하므로 워크벤치는 다시 카탈로그에
기댄다. 낡으면 원천을 뺀다(fail-closed) — 표로 돌아가 읽으면 가린 공백이 조용히 다시 열린다. 표
자체는 parquet 소비자를 위해 그대로 있다.

법인 축 테이블(`fin_std`·`dividend_event`·`holder_daily`)은 티커 컬럼이 없어 `corp_ticker` 로
전개하고 **한 법인의 종류주 티커 전부가 같은 값**을 받는다(`_specs` 모듈 docstring). `corp_ticker`
는 시점축 없는 현재 스냅샷이다.

`load_universe` 는 정책 미적용(`krx.all`) — 그날 `universe_daily` 에 있는 전 종목(ETF·우선주 포함,
생존편향 방지). `load_factor_observations` 는 유니버스 인자가 없어 `RESEARCH_UNIVERSE_ID` 로 답한다.
`load_backtest_dataset` 은 원주가 bar(`price_kind='reference'` 행·GAP-14 행·저녁 잠정 행 미방출,
경고로 **각각** 건수 기록) + `security_span` 구간 + `adj_factor` factor_ok 행(S07 과 같은 유형
매핑)이며 `adj_factor` 가 없으면 예외다 — 분할 구간을 사건 없이 돌리는 백테스트는 조용히 틀린다.
저녁 잠정 행(`basis='evening'`, 규칙 e1.15.0)은 KRX 확정 전 키움 종가라 확정 행과 한 카운터에
섞지 않는다: "그날 데이터가 깨졌다"(`invalid_bars`)와 "잠정이라 뺐다"(`n_provisional`)는 다른
사실이다.
창 안 마지막 bar 뒤의 사건(정지 중 감자 뒤 상폐)은 엔진이 정산 못 하므로 빼고 경고로 남긴다.

duckdb 는 backend optional extra `equity` 다(`uv sync --extra equity`). 어댑터 생성 시 지연 import
하고 없으면 `EquityDuckdbSetupError` 로 알린다.

사용자에게 가는 문장(포트 결과 `detail`, 원천을 뺀 사유, `snapshot().source`, 질의 중 예외)에는 서버
경로를 싣지 않는다 — preview·trace 422 와 run `error` 로 그대로 나간다. 루트·카탈로그 경로는 운영자
채널인 부팅 예외와 경고 로그에만 남긴다(#163).
"""

from __future__ import annotations

import json
import logging
import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING

from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataQuery,
    BacktestDataset,
    CorporateActionRecord,
    InvalidBarRecord,
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
    CATALOG_REBUILD,
    CatalogState,
    EquityDuckdbSetupError,
    TableBuild,
    read_catalog,
    resolve_table,
    snapshot_id,
    table_builds,
    unreadable_catalog,
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

logger = logging.getLogger(__name__)

MARKET = "KRX"
VENUE = "XKRX"
SCHEMA_VERSION = "equity-v1.2"
SECURITY_ID_SEP = ":"
_CHECKPOINT_ROWS = 256  # 취소 체크포인트 간격(행) — 포트의 `_CHECKPOINT_BATCH` 와 같은 크기
# duckdb 질의 동안 감시 스레드가 checkpoint 를 부르는 간격(초). 질의 하나는 나눌 수 없어 행 단위
# checkpoint 가 닿지 않는다(#160).
_INTERRUPT_POLL_SECONDS = 0.1
# 종목·법인 목록을 질의에 넘기는 자리. 짝이 되는 파라미터는 `_keys_param` 이 만든다. duckdb Python
# 클라이언트(1.5)는 리스트 파라미터를 원소마다 변환하며 pandas 가 없으면 원소마다 import 를 다시
# 시도해, 2천 종목 목록 하나에 약 2초를 쓴다 — 질의가 돌기 전이라 interrupt 로도 끊지 못한다(#160).
# JSON 문자열 하나로 넘기고 SQL 안에서 푼다(같은 목록 약 0.01초).
_KEYS_SQL = """unnest(from_json(?, '["VARCHAR"]'))"""
# 원시 로딩 진행 구간 경계(이슈 #162). 실데이터 4년 구간 실측(질의 약 7초, 격자 행 조립 약 16초,
# 관측 조립 약 17.5초, 생성 시 계약 검증 약 4초) 비율을 따른다.
_GRID_FETCHED = 0.3  # 격자 안: 질의·fetchall 완료
_LOAD_PANEL_END = 0.52  # 격자·LATEST 원천 완료
_LOAD_ROWS_END = 0.905  # 관측 조립 완료
_LOAD_SORTED = 0.91  # 정렬 완료, 이후 생성 시 계약 검증
RESEARCH_UNIVERSE_ID = "krx.common-stock"  # FactorObservationQuery 에 유니버스가 없다 — 계약 기본값
REFERENCE_KIND = "reference"
# `price_daily.basis`(규칙 e1.15.0) — 'krx' 확정 / 'evening' 저녁 잠정(키움 종가·거래량만, OHL
# NULL). 옛 판 루트(e1.5.0 등)에는 컬럼 자체가 없고 그때는 전 행이 확정이다.
BASIS_COLUMN = "basis"
CONFIRMED_BASIS = "krx"
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


def _noop_progress(fraction: float) -> None:
    """진행 보고를 요구하지 않는 호출자용 콜백 — `load_raw_observations_cancellable` 의 기본값."""


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


def _catalog_error_types() -> tuple[type[Exception], ...]:
    """원천을 빼도 되는 duckdb 오류 — 카탈로그·원장 파일의 성질이라 재시작해도 그대로인 것.

    duckdb 예외 계층(`duckdb.Error` 아래 약 30종)을 훑어 고른다(#245 리뷰 P3-1·P3-4).
    - 파일: `IOException`(누락·읽기 실패, `HTTPException` 포함) · `InvalidInputException`(손상·
      0바이트 parquet) · `PermissionException`(권한)
    - 카탈로그: `CatalogException`(매크로·표 누락) · `ParserException`(구운 매크로 본문을 못 읽음)
      · `BinderException`(열·스키마 드리프트)
    나머지는 일시적이거나 원천과 무관해 올린다 — `InterruptException` · `OutOfMemoryException` ·
    `InternalException` · `FatalException` · `ConnectionException` · `TransactionException` ·
    `SerializationException` 과 값 변환 계열(`ConversionException` 등, 바인딩만 하는 DESCRIBE 에서는
    나지 않는다).
    """
    import duckdb as module  # 지연 import — `_open` 과 같은 이유(optional extra `equity`)

    return (
        module.IOException,
        module.InvalidInputException,
        module.PermissionException,
        module.CatalogException,
        module.ParserException,
        module.BinderException,
    )


# 잠금 충돌도 손상과 같은 `IOException` 이라 duckdb 가 붙이는 자기 문장(OS 로캘과 무관한 영문)으로만
# 가른다. Windows 는 Restart Manager 가 찾은 점유 프로세스를 "File is already open in",
# POSIX 는 fcntl 잠금 실패를 "Could not set lock on file" 로 적는다(duckdb 1.5.5 실측, #247).
# Windows 표식은 Restart Manager 가 점유 프로세스를 찾았을 때만 붙는다. 못 찾으면(다른 사용자·서비스
# 등) 잠김이 손상으로 분류돼 원천이 빠지고, 사유는 카탈로그 재생성을 안내한다(#275 리뷰 P3-3).
_LOCK_CONFLICT_MARKERS = ("File is already open in", "Could not set lock on file")


def _raise_unless_persistent(error: Exception, catalog: Path) -> None:
    """부팅 때 카탈로그를 열거나 읽다 난 duckdb 오류 중 원천을 빼도 되는 것만 돌려보낸다(#245·#247).

    원천을 뺀 사유는 재시작 전까지 캐시되므로, 뺄 수 있는 오류는 손상·누락처럼 재시작해도 그대로인
    카탈로그·파일 성격의 것(`_catalog_error_types`)뿐이다. 잠김과 일시 오류는 풀리면 원천이 돌아와야
    하므로 빼지 않고 코드화된 `EquityDuckdbSetupError` 로 부팅을 멈춘다. 부팅 예외는 운영자 채널이라
    경로와 duckdb 원문을 싣는다.
    """
    if any(marker in str(error) for marker in _LOCK_CONFLICT_MARKERS):
        raise EquityDuckdbSetupError(
            "다른 프로세스가 카탈로그 파일을 쓰기 모드로 열고 있어 부팅을 멈춘다 — 그 프로세스"
            "(DuckDB CLI·DB 도구·카탈로그를 여는 스크립트)를 닫은 뒤 다시 띄워야 한다 "
            f"(catalog_locked) — catalog={catalog} error={error!r}"
        ) from error
    if not isinstance(error, _catalog_error_types()):
        raise EquityDuckdbSetupError(
            "카탈로그를 확인하다 일시적인 duckdb 오류가 나서 부팅을 멈춘다 — 원천을 빼지 "
            "않았으므로 다시 띄우면 다시 확인한다 (catalog_transient_error) — "
            f"catalog={catalog} error={error!r}"
        ) from error


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


def _keys_param(keys: Sequence[str]) -> str:
    """`_KEYS_SQL` 자리에 넘길 파라미터 — 종목·법인 목록의 JSON 배열 문자열."""
    return json.dumps(list(keys))


def _fetchall(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: list[object],
    checkpoint: Callable[[], None],
) -> list[tuple[object, ...]]:
    """`con.execute(sql, params).fetchall()` 과 같되, 도는 동안 취소되면 질의를 끊는다(#160).

    질의와 결과 변환은 호출 한 번이라 행 단위 checkpoint 가 닿지 않는다(실원장 6개월 구간 실측:
    격자 약 1초). 그동안 감시 스레드가 `_INTERRUPT_POLL_SECONDS` 마다 `checkpoint` 를 부르고,
    예외가 나면 호출이 끝날 때까지 폴링마다 `con.interrupt()` 로 끊는다 — 질의가 시작되기 전에 보낸
    interrupt 는 duckdb 가 버리므로 한 번으로는 모자란다. 끊긴 질의는 단계에 따라
    `InterruptException` 이나 `InvalidInputException`("INTERRUPT Error")으로 오므로 종류로 가리지
    않는다. 감시 스레드가 끊은 뒤의 오류면 `checkpoint()` 를 다시 불러 application 의 취소 예외로
    올린다 — 예외 정책은 application 소유다.
    """
    done = Event()
    interrupted = Event()

    def watch() -> None:
        while not done.wait(_INTERRUPT_POLL_SECONDS):
            try:
                checkpoint()
            except BaseException:  # 취소 신호다. 예외 종류는 application 이 정하므로 가리지 않는다
                interrupted.set()
                con.interrupt()

    watchdog = Thread(target=watch, name="equity-duckdb-interrupt", daemon=True)
    watchdog.start()
    try:
        return con.execute(sql, params).fetchall()
    except Exception:
        if interrupted.is_set():
            checkpoint()
        raise
    finally:
        done.set()
        watchdog.join()


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
        EquityDuckdbSetupError: duckdb 미설치 · 필수 테이블 미빌드 · MANIFEST 손상 ·
            카탈로그 파일 잠김(`catalog_locked`)·일시 오류(`catalog_transient_error`).
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
        self._catalog: CatalogState = self._checked_catalog(
            read_catalog(self._root, self._snapshot_id)
        )
        self._sessions: tuple[date, ...] = self._load_sessions()
        self._session_index = {session: index for index, session in enumerate(self._sessions)}
        self._policies: dict[str, tuple[str, ...]] = self._load_policies()
        self._source_reason: dict[str, str | None] = {
            name: self._source_unavailable_reason(spec) for name, spec in SOURCE_BY_NAME.items()
        }
        self._fields: dict[str, FieldSpec] = {
            spec.field_id: spec for spec in FIELD_SPECS if self._source_reason[spec.source] is None
        }
        self._profile: dict[str, tuple[int, str]] = self._load_profile()
        self._warn_lag_fallback()
        self._coverage_cache: dict[str, tuple[float, date]] | None = None

    # ── 구성 ──────────────────────────────────────────────────────────────────

    def _connect(self, sources: Iterable[SourceSpec]) -> duckdb.DuckDBPyConnection:
        """`sources` 를 읽을 연결 — 매크로 원천이 있으면 카탈로그를 read_only 로 연다(#278).

        표 원천은 parquet 를 절대 경로로 읽어(`TableBuild.parquet_source`) 메모리 연결로 충분하다.
        매크로를 읽지 않는 질의(유니버스·표 원천 격자·백테스트 bar)까지 카탈로그를 열면, 부팅 뒤
        다른 프로세스가 카탈로그를 쓰기 모드로 잡은 동안 그 질의도 실패한다. 쓸 수 없는
        카탈로그(stale 등)는 열지 않는다 — 옛 판본을 가리키는 매크로를 조용히 읽지 않게 한다.
        """
        macro = self._catalog.usable and any(source.is_macro for source in sources)
        return _open(self._catalog.path if macro else None)

    def _checked_catalog(self, catalog: CatalogState) -> CatalogState:
        """원천을 판정하기 전에 카탈로그 파일을 한 번 열어 본다(#247).

        열리지 않는 파일(손상 등)은 카탈로그가 없을 때처럼 쓸 수 없는 것으로 두고 매크로를 읽는
        원천을 모두 빼고 경고한다(`catalog_unreadable`, meta 손상과 같은 규칙). 잠김·일시 오류는
        부팅을 멈춘다.
        """
        if not catalog.usable:
            # 없음·meta 없음·낡음(`catalog_missing`·`catalog_stale`) — 매크로를 읽는 원천(목록은
            # FIELD_MAP §3 「부팅 검사」)이 모두 빠진다. 부팅 로그에 남겨야 재생성한다
            logger.warning(f"{catalog.reason} path={catalog.path}")
            return catalog
        import duckdb as module  # 지연 import — `_open` 과 같은 이유(optional extra `equity`)

        try:
            _open(catalog.path).close()
        except module.Error as error:
            _raise_unless_persistent(error, catalog.path)
            return unreadable_catalog(catalog.path, catalog.path, error)
        return catalog

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
        # 지연 import — 모듈 머리의 duckdb 는 TYPE_CHECKING 전용이라 except 절이 이름으로 참조하면
        # 이 경로에서 NameError 가 난다(#245).
        import duckdb as module

        con = _open(None)
        try:
            rows = con.execute(
                "SELECT field_id, recommended_lag_sessions, available_date_basis "
                f"FROM {self._source(PROFILE_TABLE)}"
            ).fetchall()
        except module.Error as exc:  # 파일이 빠졌거나 컬럼이 없는 구판 표 — 진단을 담아 멈춘다
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

    def _warn_lag_fallback(self) -> None:
        """대장(`dataset_profile`)에 랙 행이 없는 필드가 있으면 부팅 때 한 번 경고한다(이슈 #246).

        폴백 상수는 원장 선언과 같게 맞췄지만, 원장 선언이 바뀌면 이 루트에서만 조용히 어긋난다.
        근거 문자열의 `fallback` 표시만으로는 아무도 보지 않으므로 운영 로그에 남긴다.
        """
        fallback = sorted(field_id for field_id in self._fields if field_id not in self._profile)
        if not fallback:
            return
        rest = len(fallback) - 10
        shown = ", ".join(fallback[:10]) + (f" (+{rest})" if rest > 0 else "")
        logger.warning(
            f"{PROFILE_TABLE} 에 랙 행이 없는 필드는 어댑터의 폴백 랙(원천 상수)으로 읽는다 — "
            "폴백 값은 원장 선언과 맞췄지만 원장이 바뀌면 조용히 어긋나므로 원장의 "
            f"{PROFILE_TABLE}(S19)을 동기화해야 한다(`ledger_sync`) (profile_lag_fallback) — "
            f"table_present={PROFILE_TABLE in self._tables} fields={len(fallback)} "
            f"field_ids=[{shown}] root={self._root}"
        )

    def _field_lag(self, field_id: str) -> tuple[int, str]:
        """field_id 의 (랙, 근거). 대장에 있으면 대장, 없으면 필드·원천 상수 폴백.

        폴백 상수는 원장 선언과 같은 값이다(이슈 #246). 한 원천 안에서 랙이 갈리는 필드는
        `FieldSpec.lag_sessions` 가 원천 값을 덮는다.
        """
        entry = self._profile.get(field_id)
        if entry is not None:
            return entry
        spec = self._fields[field_id]
        source = SOURCE_BY_NAME[spec.source]
        if spec.lag_sessions is not None:
            basis = spec.lag_basis or source.lag_basis
            return spec.lag_sessions, f"{basis} (fallback: no {PROFILE_TABLE} row)"
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
        """원천을 읽을 수 없으면 왜인지 — 미빌드 테이블 · 쓸 수 없는 카탈로그 · 없는 매크로."""
        macros = {name for name in spec.requires if name.startswith("v_")}
        tables = [name for name in spec.requires if name not in macros]
        absent = [table for table in tables if table not in self._builds]
        if absent:
            return f"equity tables not built — missing={absent}"
        if not macros:
            return None
        if not self._catalog.usable:
            return self._catalog.reason
        skipped = [name for name in sorted(macros) if not self._catalog.has_macro(name)]
        if skipped:
            # 매크로를 더한 코드를 받고 카탈로그를 다시 만들지 않은 루트가 여기 온다(입력 표가 없는
            # 경우는 위 표 검사가 먼저 거른다) — 부팅 로그에 남겨야 운영 안내대로 재생성한다
            # (#292 리뷰 P2-2).
            reason = (
                f"카탈로그에 원천 {spec.name} 이 읽는 매크로가 없어 이 원천의 필드를 뺀다 — "
                f"{CATALOG_REBUILD} (catalog_macro_missing) — missing={skipped} "
                f"macros={list(self._catalog.macros)}"
            )
            logger.warning(f"{reason} catalog={self._catalog.path}")
            return reason
        return self._macro_unavailable_reason(spec)

    def _macro_unavailable_reason(self, spec: SourceSpec) -> str | None:
        """매크로 원천을 부팅 때 한 번 읽어 보고, 못 읽거나 요구하는 열이 없으면 뺄 사유를 돌려준다.

        매크로가 가리키는 parquet 가 빠졌거나 손상됐으면 `catalog_macro_unreadable`, 옛 카탈로그라
        원천이 읽는 열(`required_columns`)이 없으면 `catalog_columns_missing` 으로 경고한다.
        읽어 보지 않고 두면 커버율 질의가 원시 duckdb 오류를 던져 `list_fields()` 전체가 죽는다
        (#233 리뷰 P2-1, #275 리뷰 P3-5). DESCRIBE 는 바인딩만 하므로 매크로 본문을 실행하지 않는다.
        """
        if not spec.is_macro:
            return None
        import duckdb as module  # 지연 import — `_open` 과 같은 이유(optional extra `equity`)

        try:
            with self._connect([spec]) as con:
                described = con.execute(
                    f"DESCRIBE SELECT * FROM {self._relation(spec, self.backfill_end)}"
                ).fetchall()
        except module.Error as error:
            # 스냅샷은 맞는데 매크로가 가리키는 parquet 가 빠졌거나 손상된 카탈로그 등 — 생성자
            # 밖으로 던지면 어댑터 전체가 뜨지 못한다(#233 리뷰 후속). 이 원천만 빼고 사유를 남긴다.
            # 연결도 같은 판정을 받는다 — 잠김·일시 오류는 빼지 않고 부팅을 멈춘다(#245·#247).
            _raise_unless_persistent(error, self._catalog.path)
            reason = (
                f"카탈로그 매크로 {spec.relation} 를 읽을 수 없어 원천 {spec.name} 의 필드를 "
                f"뺀다 — 원장 파일을 확인하고(`ledger_sync verify`) {CATALOG_REBUILD} "
                f"(catalog_macro_unreadable) — error={type(error).__name__}"
            )
            logger.warning(f"{reason} catalog={self._catalog.path} detail={error!r}")
            return reason
        present = {str(row[0]) for row in described}
        missing = [column for column in spec.required_columns if column not in present]
        if not missing:
            return None
        reason = (
            f"카탈로그 매크로 {spec.relation} 에 원천 {spec.name} 이 읽는 열이 없어 이 원천의 "
            f"필드를 뺀다 — {CATALOG_REBUILD} (catalog_columns_missing) — missing={missing}"
        )
        logger.warning(f"{reason} catalog={self._catalog.path}")
        return reason

    @property
    def backfill_end(self) -> date:
        return self._sessions[-1]

    # ── EquityDataPort ────────────────────────────────────────────────────────

    def snapshot(self) -> DataSnapshot:
        return DataSnapshot(
            snapshot_id=self._snapshot_id,
            schema_version=SCHEMA_VERSION,
            built_at=max(build.built_at for build in self._tables.values()),
            source="equity_duckdb",
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
        grouped = self._fields_by_source(tuple(self._fields))
        con = self._connect(SOURCE_BY_NAME[name] for name in grouped)
        try:
            grid = con.execute(f"SELECT count(*) FROM {self._source(UNIVERSE_TABLE)}").fetchone()
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
            for name, fields in grouped.items():
                source = SOURCE_BY_NAME[name]
                relation = self._relation(source, self.backfill_end)
                where = f"WHERE {source.row_filter}" if source.row_filter else ""
                denominator = (
                    n_grid
                    if source.mode is SourceMode.GRID
                    else (n_ticker if source.axis is SourceAxis.TICKER else n_corp)
                )
                group = f" GROUP BY {source.key_column}" if source.reduce is Reduce.SUM else ""
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
        row = con.execute(f"SELECT min({source.available_expr}) FROM {relation} {where}").fetchone()
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
        con = _open(None)
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
                f"unknown security_id — not in {SPAN_TABLE}: {unknown_ids}",
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
            detail=None if cells else f"no panel cells — query={query}",
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
                    adjusted_field_id=self._fields[field_id].adjusted_field_id,
                )
                for field_id in field_ids
                if field_id in self._fields
            ),
        )

    def factor_field_catalog(self) -> tuple[FieldMetadata, ...]:
        """compile 이 읽는 필드 계약 전부(P2-07). `resolve_factor_fields` 와 같은 변환을 거친다.

        그룹 필드(`group_series`)는 주지 않으므로 그래프의 그룹 연산은 compile 에서 unsupported
        다. P2-08 스파이크가 원장을 확인했다: `dataset_profile` 의 `classification.sector` 는 시점
        축 없는 KSIC 현재값(`point_in_time=false`)이고, WICS `sector_snapshot` 은 스냅샷 하나뿐이라
        과거 세션에 값이 없다. 월별 WICS 백필(`database/docs/WICS_PROBE.md` 7-5절, 라이선스 미결)이
        들어와야 PIT 그룹 필드를 낼 수 있다.
        """
        return self.resolve_factor_fields(tuple(self._fields)).fields

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
        """`load_raw_observations` 와 같은 결과 + 협조적 취소. 진행 보고 없이 위임한다."""
        return self.load_raw_observations_reporting(
            query, checkpoint=checkpoint, progress=_noop_progress
        )

    def load_raw_observations_reporting(
        self,
        query: RawObservationQuery,
        *,
        checkpoint: Callable[[], None],
        progress: Callable[[float], None],
    ) -> RawObservationSet:
        """`load_raw_observations` 와 같은 결과 + 협조적 취소 + 진행 보고.

        `progress` 는 격자 질의·격자 행 조립(0~`_LOAD_PANEL_END`), 관측 조립(~`_LOAD_ROWS_END`),
        정렬, `RawObservationSet` 생성 시 계약 검증(~1.0)을 지나며 오르고, 생성이 끝난 뒤 1.0 을
        받는다(이슈 #162). SQL 은 로딩 시간의 일부이고(실측 2년 약 2초, 4년 콜드 캐시 약 7초)
        대부분은 파이썬 행 조립과 검증이라 그 루프들이 `_CHECKPOINT_ROWS` 행마다 보고한다. SQL
        한 번은 나눌 수 없어 그 동안만 보고가 없다. 실패 값으로 끝나는 경로는 보고하지 않는다.

        `checkpoint` 는 (1) duckdb 로 내려가기 전 1회, (2) 행 조립 루프에서
        `_CHECKPOINT_ROWS` 행마다, (3) `RawObservationSet` 계약 검증 중
        (`validation_checkpoint`) 호출된다. 콜백이 던지는 예외는 그대로 올라간다 —
        정책은 애플리케이션 소유다. duckdb 질의 동안은 감시 스레드가 불러 취소면 질의를 끊는다
        (`_fetchall`, #160). checkpoint 없이 가장 오래 도는 구간은 관측 정렬이다(실원장 6개월
        구간 약 0.3초).
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
            checkpoint=checkpoint,
            progress=lambda fraction: progress(fraction * _LOAD_PANEL_END),
        )
        rows = tuple(self._rows_in(panel, window.sessions))
        observations: list[RawObservation] = []
        for index, row in enumerate(rows):
            if index % _CHECKPOINT_ROWS == 0:
                checkpoint()
                progress(_LOAD_PANEL_END + (_LOAD_ROWS_END - _LOAD_PANEL_END) * index / len(rows))
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
        progress(_LOAD_SORTED)
        result = RawObservationSet(
            status=DataLoadStatus.OK if observations else DataLoadStatus.NO_DATA,
            data_snapshot_id=self._snapshot_id,
            sessions=window.requested,
            history_sessions=window.history,
            observations=tuple(observations),
            detail=(
                None
                if observations
                else f"no members in universe — universe_id={query.universe_id} "
                f"start={query.start} end={query.end}"
            ),
            warnings=tuple(sorted({*window.warnings, *panel.warnings})),
            validation_checkpoint=checkpoint,
            validation_progress=lambda fraction: progress(
                _LOAD_SORTED + (1.0 - _LOAD_SORTED) * fraction
            ),
        )
        progress(1.0)
        return result

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
            raise ValueError(f"unknown security_id — not in {SPAN_TABLE}: {unknown}")
        if FACTOR_TABLE not in self._builds:
            raise EquityDuckdbSetupError(
                f"{FACTOR_TABLE} not built — a backtest without the corporate-action feed is "
                "silently wrong across splits"
            )
        tickers = tuple(sorted({ticker for ticker, _ in parsed.values()}))
        # 워밍업은 start 앞 거래일 달력으로 센다(`_window` 와 같은 달력). 모자라면 있는 만큼 읽는다.
        warmup = query.history_sessions_before_start
        first = max(bisect_left(self._sessions, query.start) - warmup, 0)
        read_from = min(query.start, self._sessions[first]) if warmup else query.start
        con = _open(None)
        try:
            price_columns = {
                str(row[0])
                for row in con.execute(
                    f"DESCRIBE SELECT * FROM {self._source(PRICE_TABLE)}"
                ).fetchall()
            }
            basis_expr = BASIS_COLUMN if BASIS_COLUMN in price_columns else f"'{CONFIRMED_BASIS}'"
            price_rows = con.execute(
                f"""
                SELECT ticker, date, open, high, low, close, volume_shr, value_krw, price_kind,
                       {basis_expr} AS basis
                FROM {self._source(PRICE_TABLE)}
                WHERE ticker IN (SELECT {_KEYS_SQL})
                  AND date BETWEEN {_lit(read_from)} AND {_lit(query.end)}
                ORDER BY ticker, date
                """,
                [_keys_param(tickers)],
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
                WHERE factor_ok AND ticker IN (SELECT {_KEYS_SQL})
                ORDER BY ticker, {ts_column}, event_id
                """,
                [_keys_param(tickers)],
            ).fetchall()
        finally:
            con.close()
        by_ticker: dict[str, list[tuple[str, tuple[str, int]]]] = {}
        for security_id, key in parsed.items():
            by_ticker.setdefault(key[0], []).append((security_id, key))
        bars: list[MarketBarRecord] = []
        history_bars: list[MarketBarRecord] = []
        n_reference = 0
        invalid_bars: list[InvalidBarRecord] = []
        n_provisional = 0
        for ticker, raw_date, open_, high, low, close, volume, value, kind, basis in price_rows:
            session = _as_date(raw_date, "price_daily.date")
            if basis is not None and str(basis) != CONFIRMED_BASIS:
                # 저녁 잠정 행 — 확정 전 키움 종가라 bar 로 내보내지 않는다(엔진 어댑터
                # `backtest_engine/adapters/equity_duckdb.py` 와 같은 판단). 구간 루프 **밖**에서
                # 세는 이유: `security_span` 은 KRX 축(stg_listing_daily)이라 저녁 판에서도 D 에
                # 멈춰 T 행이 아래 구간 필터에 조용히 걸린다 — 안에서 세면 건수가 0 이 된다.
                n_provisional += 1
                continue
            # 워밍업 행은 bar 와 같은 규칙으로 거르되 측정 구간 경고(버린 행 수)에는 세지 않는다.
            measured = session >= query.start
            for security_id, key in by_ticker[str(ticker)]:
                span = spans[key]
                if not span.first_date <= session <= span.last_date:
                    continue
                if kind == REFERENCE_KIND:
                    n_reference += int(measured)  # 기준가·정지일 — 커널 규약: 방출하지 않는다
                    continue
                prices = [_as_float(v, "price") for v in (open_, high, low, close)]
                o, h, lo, c = (0.0 if p is None else float(p) for p in prices)
                if min(o, h, lo, c) <= 0 or h < max(o, c) or lo > min(o, c):
                    # GAP-14(open NULL ∧ volume>0) 류 — Bar 가 거절하는 행. 거래된 날이라 정지와
                    # 가를 수 있게 세션을 넘긴다(이슈 #241).
                    if measured:
                        invalid_bars.append(InvalidBarRecord(session, security_id))
                    continue
                (bars if measured else history_bars).append(
                    MarketBarRecord(
                        session=session,
                        security_id=security_id,
                        open=o,
                        high=h,
                        low=lo,
                        close=c,
                        volume=_as_int(volume, "volume_shr"),
                        trading_value=_as_float(value, "value_krw"),
                    )
                )
        actions: list[CorporateActionRecord] = []
        for ticker, event_id, event_type, share_factor, raw_ts in factor_rows:
            if raw_ts is None:
                raise ValueError(f"{ts_column} is NULL on a factor_ok row — event_id={event_id}")
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
        for records in (bars, history_bars):
            records.sort(key=lambda bar: (bar.session, bar.security_id))
        actions.sort(key=lambda action: (action.session, action.security_id, action.detail))
        # 창 안 마지막 bar 뒤에 오는 사건(정지 중 감자·병합 뒤 상폐)은 엔진이 정산할 세션이 없어
        # run 전체를 죽인다(`CorporateActionWithoutBar`, engine/loop.py). 그 포지션은 이미 마지막
        # 체결가에 동결된 상태이므로 사건을 빼고 경고로 남긴다.
        last_bar: dict[str, date] = {}
        for bar in bars:  # session 오름차순이라 마지막 대입이 마지막 bar
            last_bar[bar.security_id] = bar.session
        unsettleable = [
            action
            for action in actions
            if action.security_id not in last_bar or last_bar[action.security_id] < action.session
        ]
        if unsettleable:
            skipped = {(a.security_id, a.session, a.detail) for a in unsettleable}
            actions = [a for a in actions if (a.security_id, a.session, a.detail) not in skipped]
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
                    "기준가 행(price_kind='reference', 거래정지일)은 bar로 내보내지 않았다 — "
                    f"dropped={n_reference}"
                ),
                severity=WarningSeverity.INFO,
            )
        ]
        if n_provisional:
            warnings.append(
                DataWarning(
                    code="equity.provisional_rows_dropped",
                    message=(
                        f"price_daily.basis <> '{CONFIRMED_BASIS}' 행(저녁 잠정판 T 세션 — "
                        "KRX 확정 전 키움 종가)은 bar로 내보내지 않았다 — "
                        f"dropped={n_provisional}"
                    ),
                )
            )
        if invalid_bars:
            warnings.append(
                DataWarning(
                    code="equity.invalid_ohlc_rows_dropped",
                    message=(
                        "OHLC가 NULL·0 이하이거나 서로 맞지 않는 행을 버렸다(GAP-14) — "
                        f"dropped={len(invalid_bars)}"
                    ),
                )
            )
        if unsettleable:
            warnings.append(
                DataWarning(
                    code="equity.corporate_action_without_bar_dropped",
                    message=(
                        "창 안에서 사건 세션이나 그 뒤에 거래된 bar가 없는 기업 행동을 뺐다"
                        "(포지션은 마지막 체결가에 동결된다) — "
                        f"dropped={len(unsettleable)} "
                        + ", ".join(
                            f"{a.security_id}@{a.session}:{a.action_type}"
                            for a in unsettleable[:10]
                        )
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
            invalid_bars=tuple(invalid_bars),
            history_bars=tuple(history_bars),
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
                f"calendar={self._sessions[0]}..{self.backfill_end}"
            )
        first = bisect_left(self._sessions, start)
        last = bisect_right(self._sessions, end)
        if first >= last:
            return f"no sessions in range — start={start} end={end}"
        history_first = first - history
        warnings: tuple[str, ...] = ()
        if history_first < 0:
            warnings = (
                "워밍업 이력에 쓸 거래일 달력이 모자라 달력 시작부터 읽었다 — "
                f"requested={history} "
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
                f"WHERE ticker IN (SELECT {_KEYS_SQL}) ORDER BY ticker, span_seq",
                [_keys_param(tickers)],
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
        checkpoint: Callable[[], None] = _noop_checkpoint,
        progress: Callable[[float], None] = _noop_progress,
    ) -> _Panel:
        """격자 행 + 원천별 관측을 한 번에 읽는다.

        `tickers` 가 없으면 `predicate` 가 창 안에서 한 번이라도 참인 종목 집합(정책 driven)이고,
        있으면 그 종목이다. GRID 원천은 랙만큼 앞 세션까지 더 읽고, LATEST 원천은 창 끝까지의
        관측을 전부 읽어 세션별 컷오프를 파이썬에서 bisect 한다. `progress` 는 격자(0~0.9)와
        LATEST 원천(~1.0) 진행을, `checkpoint` 는 duckdb 질의·격자 행·LATEST 관측 조립 중
        협조적 취소를 받는다.
        """
        grouped = self._fields_by_source(field_ids)
        grid_sources = [name for name in grouped if SOURCE_BY_NAME[name].mode is SourceMode.GRID]
        max_lag = max((lags[f] for f in field_ids), default=0)
        first_index = self._session_index[window.sessions[0]]
        fetch_first = max(first_index - max_lag, 0)
        warnings: list[str] = []
        if first_index - max_lag < 0 and max_lag > 0:
            warnings.append(
                "랙만큼 거슬러 올라갈 거래일 달력이 모자라 달력 시작부터 읽었다 — "
                f"max_lag={max_lag} "
                f"first_session={window.sessions[0]} calendar_start={self._sessions[0]}"
            )
        fetch_start, fetch_end = self._sessions[fetch_first], window.sessions[-1]
        rows = self._grid(
            grouped,
            grid_sources,
            fetch_start,
            fetch_end,
            tickers=tickers,
            predicate=predicate,
            checkpoint=checkpoint,
            progress=lambda fraction: progress(0.9 * fraction),
        )
        panel_tickers = tuple(sorted({row.ticker for row in rows.values()}))
        latest_sources = [
            name for name in grouped if SOURCE_BY_NAME[name].mode is SourceMode.LATEST
        ]
        corp_by_ticker: dict[str, str] = {}
        if any(SOURCE_BY_NAME[name].axis is SourceAxis.CORP for name in latest_sources):
            corp_by_ticker = self._corp_map(panel_tickers, checkpoint=checkpoint)
        latest: dict[str, dict[str, _LatestSeries]] = {}
        for name in latest_sources:
            source = SOURCE_BY_NAME[name]
            keys = (
                panel_tickers
                if source.axis is SourceAxis.TICKER
                else tuple(sorted(set(corp_by_ticker.values())))
            )
            latest[name] = self._latest(
                source, grouped[name], keys, fetch_end, checkpoint=checkpoint
            )
        progress(1.0)
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
        checkpoint: Callable[[], None] = _noop_checkpoint,
        progress: Callable[[float], None] = _noop_progress,
    ) -> dict[tuple[str, date], _Row]:
        """`universe_daily` × `security_span` 격자에 GRID 원천을 (ticker, date) 로 붙인 행들.

        질의가 끝나면 `_GRID_FETCHED`, 파이썬 행 조립 동안 `_CHECKPOINT_ROWS` 행마다 그 뒤를 채워
        1.0 까지 보고한다. 조립 루프가 질의보다 훨씬 길다(2년 구간 실측 약 8.5초 대 2초).
        """
        member_expr = (
            f"coalesce(({predicate}), FALSE)" if predicate is not None else "NULL::BOOLEAN"
        )
        selection = (
            "SELECT DISTINCT ticker FROM u WHERE member"
            if tickers is None
            else f"SELECT {_KEYS_SQL} AS ticker"
        )
        params: list[object] = [] if tickers is None else [_keys_param(tickers)]
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
                f"LEFT JOIN ({picked}) g{index} ON g{index}.k = r.ticker AND g{index}.d = r.date"
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
        con = self._connect(SOURCE_BY_NAME[name] for name in grid_sources)
        try:
            raw_rows = _fetchall(con, sql, params, checkpoint)
        finally:
            con.close()
        progress(_GRID_FETCHED)
        rows: dict[tuple[str, date], _Row] = {}
        for index, raw in enumerate(raw_rows):
            if index % _CHECKPOINT_ROWS == 0:
                checkpoint()
                progress(_GRID_FETCHED + (1.0 - _GRID_FETCHED) * index / len(raw_rows))
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
                    f"duplicate (ticker, date) in {UNIVERSE_TABLE}×{SPAN_TABLE} — key={key}"
                )
            rows[key] = row
        progress(1.0)
        return rows

    def _corp_map(
        self, tickers: Sequence[str], *, checkpoint: Callable[[], None]
    ) -> dict[str, str]:
        """티커 → 법인. `corp_ticker` 는 시점축 없는 현재 스냅샷이다(DESIGN §4-5 6)."""
        if not tickers or CORP_TICKER_TABLE not in self._builds:
            return {}
        con = _open(None)
        try:
            rows = _fetchall(
                con,
                f"SELECT ticker, corp_code FROM {self._source(CORP_TICKER_TABLE)} "
                f"WHERE ticker IN (SELECT {_KEYS_SQL}) AND corp_code IS NOT NULL",
                [_keys_param(tickers)],
                checkpoint,
            )
        finally:
            con.close()
        return {str(ticker): str(corp) for ticker, corp in rows}

    def _latest(
        self,
        source: SourceSpec,
        fields: tuple[str, ...],
        keys: Sequence[str],
        fetch_end: date,
        *,
        checkpoint: Callable[[], None],
    ) -> dict[str, _LatestSeries]:
        """축 키 → `available_date` 오름차순 관측열. `reduce` 가 grain 을 (키, 공개일)로 줄인다.

        `checkpoint` 는 질의 동안(`_fetchall`)과 관측 조립 중 `_CHECKPOINT_ROWS` 행마다 부른다.
        """
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
        where.append(f"{source.key_column} IN (SELECT {_KEYS_SQL})")
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
        con = self._connect([source])
        try:
            raw_rows = _fetchall(con, sql, [_keys_param(keys)], checkpoint)
        finally:
            con.close()
        dates: dict[str, list[date]] = {}
        cells: dict[str, list[dict[str, _Observed]]] = {}
        for index, raw in enumerate(raw_rows):
            if index % _CHECKPOINT_ROWS == 0:
                checkpoint()
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
                entry[field_id] = _Observed(value, available, content, _cell_kind(value, None))
            dates.setdefault(key, []).append(available)
            cells.setdefault(key, []).append(entry)
        return {key: _LatestSeries(tuple(dates[key]), tuple(cells[key])) for key in dates}

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
            row.ticker if source.axis is SourceAxis.TICKER else panel.corp_by_ticker.get(row.ticker)
        )
        series = panel.latest.get(source.name, {}).get(key) if key is not None else None
        if series is None:
            return None
        position = bisect_right(series.dates, cutoff) - 1
        return None if position < 0 else series.cells[position][field_id]
