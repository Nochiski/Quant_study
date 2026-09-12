"""equity 층 parquet 어댑터 — 커널 3포트 `BarSource`·`UniverseSource`·`CorporateActionSource` (S07).

모듈 이름 `equity_duckdb` 는 `EQUITY_DESIGN.md` §7·`EQUITY_WORKFLOW.md` S07 이 고정한 것이다. 구현은
duckdb 가 아니라 **pyarrow** 로 parquet 를 직접 읽는다 — 결정 7: 커널 어댑터는 새 의존성 0, duckdb
는 S21 워크벤치 어댑터(`strategy_workbench/adapters/outbound/equity_duckdb`)의 몫이다.

읽기 계약 (DESIGN §2 판본 규약):
- `<equity_root>/<table>/MANIFEST.json` 의 `current_build` → 그 BuildRecord 의
  `partitions[].path` 디렉토리 안 `*.parquet` 만 읽는다. `v=*` 디렉토리 glob 은 금지 — keep=3 GC 로
  구버전이 공존한다.
- 테이블이 없거나 `current_build` 가 없으면 예외가 아니라 `LoadStatus` 실패 값이다
  (errors-as-values). 메시지에 root·table·build 를 담는다.

어휘 (FIELD_MAP §1):
- `InstrumentId.symbol` = `security_id` = `{ticker}:{span_seq}`(`UniverseSource` 가 내는 형식) 또는
  `{ticker}`(전 구간 — 계약 테스트·단일 구간 종목의 단축 표기). 재상장 2종(036220·101970)은 구간마다
  다른 id 라 첫 상장의 포지션이 8~10년 뒤 재상장으로 새지 않는다.
- venue 는 `UniverseQuery.venue` 를 그대로 `InstrumentId.venue` 에 싣는다(엔진 규약은 `XKRX`, 기존
  `krx_parquet` 와 같은 방식). `asset_class=EQUITY`·`currency=KRW`.
- `Bar.ts` = 세션 자정(naive datetime) — v1 규약, `krx_parquet`·`csv_bars` 와 같다.

포트별 규칙:
- `BarSource`: `price_daily` 원주가 그대로(`open/high/low/close`·`volume_shr`).
  `price_kind='reference'` 행(거래량 0·O/H/L NULL 기준가·정지일)은 방출하지 않고 `dropped_rows` 로
  센다(DESIGN §7 "reference 행 미방출"). `open IS NULL ∧ volume > 0`(GAP-14, 서버 127행) 은
  NULL → 0 으로 두고 `OhlcPolicy` 규약에 맡긴다 — STRICT 는 FORMAT_ERROR, CLAMP 는 drop +
  `dropped_rows`. 같은 (ticker, date) 중복은 FORMAT_ERROR. 요청 종목 중 하나라도 행이 없으면 전체
  NO_DATA(부분 성공 금지, `merge_results`).
- `UniverseSource`: `security_span` 구간 1행 = `Membership` 1개. `end_reason='coverage_gap'` 은
  구간을 끊지 않는다(마지막 세션 = `backfill_end`). `UniverseQuery.end > backfill_end`
  (`trading_calendar` max) 는 NO_DATA 로 거절한다 — 커버리지 밖 세션을 조용히 빈 유니버스로 돌리지
  않는다.
- `CorporateActionSource`: `adj_factor` 의 `factor_ok=true` 행만 → `CorporateActionEvent(
  ts=apply_date(컬럼이 있으면) 또는 effective_date, ratio=Decimal(share_factor), detail=event_id)`.
  `apply_date` 는 S06 후속(감자는 기준일이 아니라 거래재개일에 가격이 조정된다)으로 추가되는
  컬럼이라 **컬럼 존재로 분기**한다. `event_type` → enum 매핑은 `EVENT_TYPE_MAP`(아래); S06-2 의
  KRX 기준가 원천 행 `unknown_krx` 는 유형이 없어 share_factor 방향으로 SPLIT/REVERSE_SPLIT
  (`RATIO_DIRECTED_EVENT_TYPES`). 어휘 밖 유형·방향이 ratio 와 어긋나는 행은 FORMAT_ERROR.

pyarrow 는 optional extra `parquet` 로 설치한다: `uv sync --extra parquet`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from backtest_engine.data.cleaning import RawBar, clean_raw_bars, merge_results
from backtest_engine.ports.corporate_actions import CorporateActionQuery, CorporateActionResult
from backtest_engine.ports.market_data import BarQuery, LoadResult, LoadStatus
from backtest_engine.ports.universe import Membership, UniverseQuery, UniverseResult
from backtest_engine.types.events import CorporateActionEvent, CorporateActionType
from backtest_engine.types.instruments import AssetClass, InstrumentId

MANIFEST_NAME = "MANIFEST.json"
PRICE_TABLE = "price_daily"
SPAN_TABLE = "security_span"
CALENDAR_TABLE = "trading_calendar"
SECURITY_TABLE = "security"
FACTOR_TABLE = "adj_factor"
SECURITY_ID_SEP = ":"  # security_id = `{ticker}:{span_seq}` (FIELD_MAP §1)
CURRENCY = "KRW"
REFERENCE_KIND = "reference"  # price_daily.price_kind — 기준가·정지일(거래량 0)

# adj_factor.event_type → 엔진 enum. factor_ok 행은 전부 시총 불변(price×share=1)이므로 엔진이
# 포지션을 조정해야 하는 SPLIT/REVERSE_SPLIT 로 보낸다 — 무상증자(bonus)는 주식수 증가·가격 반비례라
# SPLIT 와 같은 적용이고, 무상감자(capred, share_factor < 1)는 REVERSE_SPLIT 와 같다.
# SHARE_COUNT_CHANGE(알림 전용)는 쓰지 않는다: adj_factor 에 실린 ok 행을 알림으로만 보내면 분할
# 구간의 수량이 조정되지 않은 채 백테스트가 돈다. 어휘 밖 유형은 FORMAT_ERROR(조용한 누락 금지).
EVENT_TYPE_MAP: dict[str, CorporateActionType] = {
    "split": CorporateActionType.SPLIT,
    "bonus": CorporateActionType.SPLIT,
    "reverse_split": CorporateActionType.REVERSE_SPLIT,
    "capred": CorporateActionType.REVERSE_SPLIT,
}
# S06-2 KRX 기준가 원천이 만든 사건 — corp_event 에 없어 유형을 모른다(`unknown_krx`: 기준가 변화 +
# 같은 날 주식수 변화, 시총 불변). 방향은 share_factor 가 정한다: > 1 → SPLIT, < 1 → REVERSE_SPLIT.
# `unknown_price_only`(기준가만 변화, 항상 factor_ok=false)는 방출되지 않고, ok 로 실려 오면 어휘 밖
# FORMAT_ERROR 가 맞다 — 시총 불변이 아닌 사건을 분할로 적용하면 안 된다.
RATIO_DIRECTED_EVENT_TYPES: frozenset[str] = frozenset({"unknown_krx"})

_PRICE_COLUMNS = ("date", "open", "high", "low", "close", "volume_shr", "price_kind", "basis")
# `basis` 는 규칙 e1.15.0 부터 있다(저녁 잠정판 'evening' / KRX 확정 'krx'). 옛 판에는 없으므로
# 선택 컬럼이고, 백테스트는 잠정 행(OHLC NULL·키움 종가)을 절대 소비하지 않는다 — 'krx' 가 아닌
# 행은 방출하지 않는다.
_PRICE_OPTIONAL_COLUMNS = frozenset({"basis"})
_PRICE_BASIS_CONFIRMED = "krx"
_SPAN_COLUMNS = ("ticker", "span_seq", "first_date", "last_date", "end_reason")
_FACTOR_COLUMNS = (
    "ticker", "effective_date", "event_id", "event_type", "share_factor", "factor_ok"
)
_FACTOR_APPLY_COLUMN = "apply_date"


class _SourceError(Exception):
    """어댑터 내부 전용 — 판본·형식 오류를 포트 경계에서 FORMAT_ERROR 값으로 바꾼다."""


@dataclass(frozen=True)
class TableBuild:
    """`MANIFEST.json` current_build 해석 결과. status 가 OK 가 아니면 partitions 는 비어 있다."""

    table: str
    status: LoadStatus
    detail: str | None
    build_id: str | None
    partitions: tuple[Path, ...]  # partitions[].path 를 절대경로로 푼 디렉토리

    @property
    def ok(self) -> bool:
        return self.status is LoadStatus.OK

    @property
    def label(self) -> str:
        return f"table={self.table} build={self.build_id}"


def resolve_table(equity_root: Path, table: str) -> TableBuild:
    """`<equity_root>/<table>/MANIFEST.json` 의 current_build 파티션 디렉토리를 푼다.

    없는 테이블·`current_build` 없음은 NO_DATA, MANIFEST 가 깨졌거나 파티션 디렉토리가 사라졌으면
    FORMAT_ERROR. 디렉토리 glob 은 하지 않는다.
    """
    manifest_path = equity_root / table / MANIFEST_NAME
    if not manifest_path.exists():
        return TableBuild(
            table,
            LoadStatus.NO_DATA,
            f"equity table not built — root={equity_root} table={table} manifest={manifest_path}",
            None,
            (),
        )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return TableBuild(
            table,
            LoadStatus.FORMAT_ERROR,
            f"unreadable MANIFEST — root={equity_root} table={table} error={error!r}",
            None,
            (),
        )
    current = raw.get("current_build") if isinstance(raw, dict) else None
    builds = raw.get("builds") if isinstance(raw, dict) else None
    if not isinstance(current, str) or not current:
        return TableBuild(
            table,
            LoadStatus.NO_DATA,
            f"no current_build — root={equity_root} table={table} manifest={manifest_path}",
            None,
            (),
        )
    record = next(
        (b for b in (builds or []) if isinstance(b, dict) and b.get("build_id") == current), None
    )
    if record is None:
        return TableBuild(
            table,
            LoadStatus.FORMAT_ERROR,
            f"current_build not in builds[] — root={equity_root} table={table} build={current} "
            f"builds={[b.get('build_id') for b in (builds or []) if isinstance(b, dict)]}",
            current,
            (),
        )
    partitions: list[Path] = []
    for part in record.get("partitions") or []:
        path = part.get("path") if isinstance(part, dict) else None
        if not isinstance(path, str):
            return TableBuild(
                table,
                LoadStatus.FORMAT_ERROR,
                f"partition without path — root={equity_root} table={table} build={current} "
                f"partition={part!r}",
                current,
                (),
            )
        directory = equity_root / table / path
        if not directory.is_dir():
            return TableBuild(
                table,
                LoadStatus.FORMAT_ERROR,
                f"partition directory missing — root={equity_root} table={table} build={current} "
                f"path={directory}",
                current,
                (),
            )
        partitions.append(directory)
    if not partitions:
        return TableBuild(
            table,
            LoadStatus.NO_DATA,
            f"build has no partitions — root={equity_root} table={table} build={current}",
            current,
            (),
        )
    return TableBuild(table, LoadStatus.OK, None, current, tuple(partitions))


def _partition_year(directory: Path) -> int | None:
    name = directory.name
    if name.startswith("year=") and name[5:].isdigit():
        return int(name[5:])
    return None


def _parquet_files(build: TableBuild, start: date | None, end: date | None) -> list[Path]:
    """파티션 디렉토리 안의 parquet 파일. `year=YYYY` 디렉토리는 기간 밖이면 건너뛴다."""
    files: list[Path] = []
    for directory in build.partitions:
        year = _partition_year(directory)
        if year is not None:
            if start is not None and year < start.year:
                continue
            if end is not None and year > end.year:
                continue
        files.extend(sorted(p for p in directory.iterdir() if p.suffix == ".parquet"))
    return files


def _read_rows(
    files: list[Path],
    columns: tuple[str, ...],
    filters: list[tuple[str, str, object]] | None,
    build: TableBuild,
    optional: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    """파일마다 필요한 컬럼만 읽어 dict 행으로 합친다. `optional` 밖 컬럼이 없으면 _SourceError."""
    import pyarrow.parquet as pq  # 어댑터 안에서만 import — 코어는 pyarrow 를 모른다

    rows: list[dict[str, object]] = []
    for path in files:
        present = set(pq.read_schema(path).names)
        missing = [c for c in columns if c not in present and c not in optional]
        if missing:
            raise _SourceError(
                f"columns missing in parquet — {build.label} file={path} missing={missing} "
                f"present={sorted(present)}"
            )
        wanted = [c for c in columns if c in present]
        table = pq.read_table(path, columns=wanted, filters=filters or None)
        rows.extend(table.to_pylist())
    return rows


def _as_date(value: object, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise _SourceError(f"{label} must be a date — got {type(value).__name__}: {value!r}")


def _as_float(value: object, label: str) -> float:
    """가격 컬럼. NULL(stage 가 O/H/L '0' 을 NULL 로 둔 것)은 0.0 — 정제 단계가 정책대로 다룬다."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        raise _SourceError(f"{label} must not be bool — got {value!r}")
    if isinstance(value, int | float | Decimal):
        return float(value)
    raise _SourceError(f"{label} must be numeric — got {type(value).__name__}: {value!r}")


def _as_int(value: object, label: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise _SourceError(f"{label} must not be bool — got {value!r}")
    if isinstance(value, int | Decimal):
        return int(value)
    raise _SourceError(f"{label} must be int/Decimal — got {type(value).__name__}: {value!r}")


def _session_ts(session: date) -> datetime:
    return datetime.combine(session, datetime.min.time())


def _instrument(venue: str, symbol: str) -> InstrumentId:
    return InstrumentId(
        venue=venue, symbol=symbol, asset_class=AssetClass.EQUITY, currency=CURRENCY
    )


@dataclass(frozen=True)
class _SecurityId:
    ticker: str
    span_seq: int | None  # None = 전 구간


def _parse_security_id(symbol: str) -> _SecurityId | None:
    """`{ticker}` 또는 `{ticker}:{span_seq}`. 형식이 틀리면 None."""
    ticker, sep, seq = symbol.partition(SECURITY_ID_SEP)
    if not ticker:
        return None
    if not sep:
        return _SecurityId(ticker, None)
    if not seq.isdigit():
        return None
    return _SecurityId(ticker, int(seq))


@dataclass(frozen=True)
class _Span:
    ticker: str
    span_seq: int
    first_date: date
    last_date: date
    end_reason: str


def _load_spans(equity_root: Path) -> tuple[TableBuild, dict[str, list[_Span]]]:
    build = resolve_table(equity_root, SPAN_TABLE)
    if not build.ok:
        return build, {}
    by_ticker: dict[str, list[_Span]] = {}
    for record in _read_rows(_parquet_files(build, None, None), _SPAN_COLUMNS, None, build):
        ticker = record["ticker"]
        if not isinstance(ticker, str):
            raise _SourceError(f"ticker must be str — {build.label} got={ticker!r}")
        span = _Span(
            ticker=ticker,
            span_seq=_as_int(record["span_seq"], "span_seq"),
            first_date=_as_date(record["first_date"], "first_date"),
            last_date=_as_date(record["last_date"], "last_date"),
            end_reason=str(record["end_reason"]),
        )
        by_ticker.setdefault(ticker, []).append(span)
    for spans in by_ticker.values():
        spans.sort(key=lambda s: s.span_seq)
    return build, by_ticker


def _span_window(
    spans: dict[str, list[_Span]], security: _SecurityId, root: Path
) -> tuple[date, date] | str:
    """security_id 가 가리키는 구간의 [first, last]. 없으면 진단 문자열."""
    candidates = spans.get(security.ticker, [])
    if not candidates:
        return f"ticker not in {SPAN_TABLE} — root={root} ticker={security.ticker}"
    if security.span_seq is None:
        return candidates[0].first_date, candidates[-1].last_date
    for span in candidates:
        if span.span_seq == security.span_seq:
            return span.first_date, span.last_date
    return (
        f"span_seq not in {SPAN_TABLE} — root={root} ticker={security.ticker} "
        f"span_seq={security.span_seq} available={[s.span_seq for s in candidates]}"
    )


def _clip(
    window: tuple[date, date], start: date | None, end: date | None
) -> tuple[date, date] | None:
    lo = window[0] if start is None else max(window[0], start)
    hi = window[1] if end is None else min(window[1], end)
    return None if lo > hi else (lo, hi)


class EquityBarSource:
    """`price_daily` 원주가를 `BarSource` 로 노출한다.

    Args:
        equity_root: `price_daily/MANIFEST.json`(·`security_span/`)이 있는 equity 루트.
    """

    def __init__(self, equity_root: Path) -> None:
        self._root = equity_root

    def load_bars(self, query: BarQuery) -> LoadResult:
        build = resolve_table(self._root, PRICE_TABLE)
        if not build.ok:
            return LoadResult(bars=(), status=build.status, detail=build.detail)
        try:
            spans: dict[str, list[_Span]] | None = None
            if any(SECURITY_ID_SEP in i.symbol for i in query.instruments):
                span_build, spans = _load_spans(self._root)
                if not span_build.ok:
                    return LoadResult(bars=(), status=span_build.status, detail=span_build.detail)
            return merge_results(
                self._load_one(instrument, query, build, spans) for instrument in query.instruments
            )
        except _SourceError as error:
            return LoadResult(bars=(), status=LoadStatus.FORMAT_ERROR, detail=str(error))

    def _load_one(
        self,
        instrument: InstrumentId,
        query: BarQuery,
        build: TableBuild,
        spans: dict[str, list[_Span]] | None,
    ) -> LoadResult:
        security = _parse_security_id(instrument.symbol)
        if security is None:
            return LoadResult(
                bars=(),
                status=LoadStatus.NO_DATA,
                detail=(
                    f"invalid security_id — symbol={instrument.symbol} "
                    f"expected=<ticker> or <ticker>{SECURITY_ID_SEP}<span_seq>"
                ),
            )
        start, end = query.start, query.end
        if security.span_seq is not None and spans is not None:
            window = _span_window(spans, security, self._root)
            if isinstance(window, str):
                return LoadResult(bars=(), status=LoadStatus.NO_DATA, detail=window)
            clipped = _clip(window, start, end)
            if clipped is None:
                return LoadResult(
                    bars=(),
                    status=LoadStatus.NO_DATA,
                    detail=(
                        f"query range outside span — symbol={instrument.symbol} "
                        f"span={window[0]}..{window[1]} start={start} end={end}"
                    ),
                )
            start, end = clipped
        filters: list[tuple[str, str, object]] = [("ticker", "==", security.ticker)]
        if start is not None:
            filters.append(("date", ">=", start))
        if end is not None:
            filters.append(("date", "<=", end))
        records = _read_rows(
            _parquet_files(build, start, end),
            _PRICE_COLUMNS,
            filters,
            build,
            optional=_PRICE_OPTIONAL_COLUMNS,
        )
        if not records:
            return LoadResult(
                bars=(),
                status=LoadStatus.NO_DATA,
                detail=(
                    f"no rows for instrument — symbol={instrument.symbol} root={self._root} "
                    f"{build.label} start={start} end={end}"
                ),
            )
        records.sort(key=lambda r: _as_date(r["date"], "date"))
        previous: date | None = None
        n_reference = 0
        n_provisional = 0
        raw_bars: list[RawBar] = []
        for record in records:
            session = _as_date(record["date"], "date")
            basis = record.get("basis")
            if basis is not None and basis != _PRICE_BASIS_CONFIRMED:
                n_provisional += 1  # 저녁 잠정 행 — 확정 전 값이라 백테스트 바로 방출하지 않는다
                continue
            if previous is not None and session == previous:
                return LoadResult(
                    bars=(),
                    status=LoadStatus.FORMAT_ERROR,
                    detail=(
                        f"duplicate session for instrument — symbol={instrument.symbol} "
                        f"session={session} root={self._root} {build.label}"
                    ),
                )
            previous = session
            if record["price_kind"] == REFERENCE_KIND:
                n_reference += 1  # 기준가·정지일 — 거래 불가 행, 방출하지 않는다
                continue
            raw_bars.append(
                RawBar(
                    ts=_session_ts(session),
                    open=_as_float(record["open"], "open"),
                    high=_as_float(record["high"], "high"),
                    low=_as_float(record["low"], "low"),
                    close=_as_float(record["close"], "close"),
                    volume=_as_int(record["volume_shr"], "volume_shr"),
                    origin=f"session={session}",
                )
            )
        if not raw_bars:
            return LoadResult(
                bars=(),
                status=LoadStatus.NO_DATA,
                detail=(
                    f"no tradable rows — symbol={instrument.symbol} root={self._root} "
                    f"{build.label} start={start} end={end} reference_rows={n_reference} "
                    f"provisional_rows={n_provisional}"
                ),
            )
        result = clean_raw_bars(
            raw_bars,
            instrument,
            query.ohlc_policy,
            source=f"{self._root / PRICE_TABLE} build={build.build_id}",
            drop_zero_volume=True,
        )
        if not result.ok:
            return result
        return replace(result, dropped_rows=result.dropped_rows + n_reference)


class EquityUniverseSource:
    """`security_span` 구간을 `Membership` 으로 노출한다 — 구간 1행 = 구간 1개.

    Args:
        equity_root: `security_span/`·`trading_calendar/`(·`security/`) MANIFEST 가 있는 루트.
        sec_types: `security.sec_type` 화이트리스트(예: `{'common'}`). None 이면 전 종목·ETF.
            정책 유니버스(`krx.common-stock` 등)는 S21 이 `universe_policy` 로 다룬다.
    """

    def __init__(self, equity_root: Path, sec_types: frozenset[str] | None = None) -> None:
        self._root = equity_root
        self._sec_types = sec_types

    def load_universe(self, query: UniverseQuery) -> UniverseResult:
        try:
            backfill_end = self._backfill_end()
            if isinstance(backfill_end, TableBuild):
                return UniverseResult((), backfill_end.status, backfill_end.detail)
            if query.end is not None and query.end > backfill_end:
                return UniverseResult(
                    memberships=(),
                    status=LoadStatus.NO_DATA,
                    detail=(
                        f"query end beyond coverage — venue={query.venue} start={query.start} "
                        f"end={query.end} backfill_end={backfill_end} root={self._root}"
                    ),
                )
            span_build, spans = _load_spans(self._root)
            if not span_build.ok:
                return UniverseResult((), span_build.status, span_build.detail)
            allowed = self._allowed_tickers(spans)
            if isinstance(allowed, TableBuild):
                return UniverseResult((), allowed.status, allowed.detail)
        except _SourceError as error:
            return UniverseResult((), LoadStatus.FORMAT_ERROR, str(error))
        memberships: list[Membership] = []
        for ticker in sorted(spans):
            if allowed is not None and ticker not in allowed:
                continue
            for span in spans[ticker]:
                clipped = _clip((span.first_date, span.last_date), query.start, query.end)
                if clipped is None:
                    continue
                symbol = f"{ticker}{SECURITY_ID_SEP}{span.span_seq}"
                memberships.append(Membership(_instrument(query.venue, symbol), *clipped))
        if not memberships:
            return UniverseResult(
                memberships=(),
                status=LoadStatus.NO_DATA,
                detail=(
                    f"no span overlaps query — venue={query.venue} start={query.start} "
                    f"end={query.end} sec_types={self._sec_types} root={self._root} "
                    f"{span_build.label}"
                ),
            )
        return UniverseResult(memberships=tuple(memberships), status=LoadStatus.OK)

    def _backfill_end(self) -> date | TableBuild:
        build = resolve_table(self._root, CALENDAR_TABLE)
        if not build.ok:
            return build
        sessions = [
            _as_date(r["date"], "date")
            for r in _read_rows(_parquet_files(build, None, None), ("date",), None, build)
        ]
        if not sessions:
            raise _SourceError(
                f"{CALENDAR_TABLE} has no sessions — root={self._root} {build.label}"
            )
        return max(sessions)

    def _allowed_tickers(self, spans: dict[str, list[_Span]]) -> frozenset[str] | None | TableBuild:
        if self._sec_types is None:
            return None
        build = resolve_table(self._root, SECURITY_TABLE)
        if not build.ok:
            return build
        rows = _read_rows(
            _parquet_files(build, None, None),
            ("ticker", "sec_type"),
            [("sec_type", "in", sorted(self._sec_types))],
            build,
        )
        return frozenset(str(r["ticker"]) for r in rows if str(r["ticker"]) in spans)


class EquityCorporateActionSource:
    """`adj_factor` 의 `factor_ok` 행을 `CorporateActionEvent` 로 노출한다.

    사건이 없는 종목은 빈 결과(OK)다 — 대신 종목 실존은 `security_span` 으로 확인해 없는 종목이
    조용히 빈 사건으로 통과하지 않게 한다(NO_DATA).
    """

    def __init__(self, equity_root: Path) -> None:
        self._root = equity_root

    def load_actions(self, query: CorporateActionQuery) -> CorporateActionResult:
        build = resolve_table(self._root, FACTOR_TABLE)
        if not build.ok:
            return CorporateActionResult((), build.status, build.detail)
        try:
            span_build, spans = _load_spans(self._root)
            if not span_build.ok:
                return CorporateActionResult((), span_build.status, span_build.detail)
            targets: list[tuple[InstrumentId, str, tuple[date, date]]] = []
            for instrument in query.instruments:
                security = _parse_security_id(instrument.symbol)
                if security is None:
                    return CorporateActionResult(
                        actions=(),
                        status=LoadStatus.NO_DATA,
                        detail=(
                            f"invalid security_id — symbol={instrument.symbol} "
                            f"expected=<ticker> or <ticker>{SECURITY_ID_SEP}<span_seq>"
                        ),
                    )
                window = _span_window(spans, security, self._root)
                if isinstance(window, str):
                    return CorporateActionResult((), LoadStatus.NO_DATA, window)
                targets.append((instrument, security.ticker, window))
            # adj_factor 는 작은 테이블(서버 수천 행)이라 질의당 한 번만 읽는다. 파티션은
            # year(effective_date) 인데 ts 는 apply_date 일 수 있어 연도 프루닝도 하지 않는다.
            by_ticker: dict[str, list[dict[str, object]]] = {}
            for record in _read_rows(
                _parquet_files(build, None, None),
                (*_FACTOR_COLUMNS, _FACTOR_APPLY_COLUMN),
                [("ticker", "in", sorted({t for _, t, _ in targets}))],
                build,
                optional=frozenset({_FACTOR_APPLY_COLUMN}),
            ):
                by_ticker.setdefault(str(record["ticker"]), []).append(record)
            actions: list[CorporateActionEvent] = []
            for instrument, ticker, window in targets:
                actions.extend(
                    self._events(instrument, by_ticker.get(ticker, []), window, query, build)
                )
        except _SourceError as error:
            return CorporateActionResult((), LoadStatus.FORMAT_ERROR, str(error))
        actions.sort(key=lambda event: (event.ts, event.instrument.symbol))
        return CorporateActionResult(actions=tuple(actions), status=LoadStatus.OK)

    def _events(
        self,
        instrument: InstrumentId,
        records: list[dict[str, object]],
        window: tuple[date, date],
        query: CorporateActionQuery,
        build: TableBuild,
    ) -> list[CorporateActionEvent]:
        events: list[CorporateActionEvent] = []
        for record in records:
            if record.get("factor_ok") is not True:
                continue
            event_id = str(record["event_id"])
            if _FACTOR_APPLY_COLUMN in record:
                if record[_FACTOR_APPLY_COLUMN] is None:
                    raise _SourceError(
                        f"apply_date is NULL on a factor_ok row — {build.label} event_id={event_id}"
                    )
                session = _as_date(record[_FACTOR_APPLY_COLUMN], _FACTOR_APPLY_COLUMN)
            else:
                session = _as_date(record["effective_date"], "effective_date")
            if not (window[0] <= session <= window[1]) or not query.includes(session):
                continue
            event_type = str(record["event_type"])
            share_factor = record["share_factor"]
            if isinstance(share_factor, bool) or not isinstance(
                share_factor, int | float | Decimal
            ):
                raise _SourceError(
                    f"share_factor must be numeric — {build.label} event_id={event_id} "
                    f"got={share_factor!r}"
                )
            ratio = Decimal(repr(float(share_factor)))
            if event_type in RATIO_DIRECTED_EVENT_TYPES:
                if ratio == 1:
                    raise _SourceError(
                        f"share_factor 1 cannot direct a ratio-directed event — {build.label} "
                        f"event_id={event_id} event_type={event_type}"
                    )
                action_type = (
                    CorporateActionType.SPLIT if ratio > 1 else CorporateActionType.REVERSE_SPLIT
                )
            else:
                action_type = EVENT_TYPE_MAP.get(event_type)
            if action_type is None:
                raise _SourceError(
                    f"event_type outside adapter vocabulary — {build.label} event_id={event_id} "
                    f"event_type={event_type!r} "
                    f"known={sorted(EVENT_TYPE_MAP) + sorted(RATIO_DIRECTED_EVENT_TYPES)}"
                )
            expects_increase = action_type is CorporateActionType.SPLIT
            if (ratio > 1) != expects_increase:
                raise _SourceError(
                    f"share_factor direction contradicts event_type — {build.label} "
                    f"event_id={event_id} event_type={event_type} share_factor={share_factor}"
                )
            events.append(
                CorporateActionEvent(
                    ts=_session_ts(session),
                    instrument=instrument,
                    action_type=action_type,
                    ratio=ratio,
                    detail=event_id,
                )
            )
        return events
