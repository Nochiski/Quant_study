"""KRX 원장 parquet 어댑터 (`quant-data` 빌드의 `krx_*_bydd_trd.parquet`).

원장 특성 (데이터 README 실측):
- 원주가(미수정). 액면분할·감자 구간의 가격 불연속은 여기서 보정하지 않는다.
- 행이 날짜순이 아니므로 반드시 정렬 후 정제한다.
- 거래정지 중에도 행이 매일 존재하며 종가는 직전값 유지 또는 1원 등 종목마다 다르다.
  신뢰할 수 있는 정지 신호는 `acc_trdvol = 0` 하나뿐이므로 거래량 0 행은 항상 제거한다.
- 종목코드(`isu_cd`)는 6자리 단축코드다 (마스터 테이블의 ISIN과 다름).

pyarrow는 optional extra `parquet`로 설치한다: `uv sync --extra parquet`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from backtest_engine.data.cleaning import RawBar, clean_raw_bars, merge_results
from backtest_engine.data.corporate_actions import ShareCountRow, detect_share_count_events
from backtest_engine.ports.corporate_actions import CorporateActionQuery, CorporateActionResult
from backtest_engine.ports.market_data import BarQuery, LoadResult, LoadStatus
from backtest_engine.ports.universe import Membership, UniverseQuery, UniverseResult
from backtest_engine.types.events import CorporateActionEvent
from backtest_engine.types.instruments import AssetClass, InstrumentId

KOSPI_TRADES_FILE = "krx_stk_bydd_trd.parquet"
KOSDAQ_TRADES_FILE = "krx_ksq_bydd_trd.parquet"
TRADES_FILES = (KOSPI_TRADES_FILE, KOSDAQ_TRADES_FILE)
KOSPI_MASTER_FILE = "krx_stk_isu_base_info.parquet"
KOSDAQ_MASTER_FILE = "krx_ksq_isu_base_info.parquet"
MASTER_FILES = (KOSPI_MASTER_FILE, KOSDAQ_MASTER_FILE)

_COLUMNS = ("bas_dd", "isu_cd", "tdd_opnprc", "tdd_hgprc", "tdd_lwprc", "tdd_clsprc", "acc_trdvol")
_SHARE_COLUMNS = ("bas_dd", "isu_cd", "tdd_clsprc", "acc_trdvol", "list_shrs")


@dataclass(frozen=True)
class _TradeRow:
    session: date
    open: int
    high: int
    low: int
    close: int
    volume: int
    file: str


def _read_records(
    path: Path, symbol: str, start: date | None, end: date | None, columns: tuple[str, ...]
) -> list[dict[str, object]]:
    import pyarrow.parquet as pq  # 어댑터 안에서만 import — 코어는 pyarrow를 모른다

    filters: list[tuple[str, str, object]] = [("isu_cd", "==", symbol)]
    if start is not None:
        filters.append(("bas_dd", ">=", start))
    if end is not None:
        filters.append(("bas_dd", "<=", end))
    return pq.read_table(path, columns=list(columns), filters=filters).to_pylist()


def _read_rows(path: Path, symbol: str, start: date | None, end: date | None) -> list[_TradeRow]:
    rows: list[_TradeRow] = []
    for record in _read_records(path, symbol, start, end, _COLUMNS):
        rows.append(
            _TradeRow(
                session=_as_date(record["bas_dd"]),
                open=_as_int(record["tdd_opnprc"]),
                high=_as_int(record["tdd_hgprc"]),
                low=_as_int(record["tdd_lwprc"]),
                close=_as_int(record["tdd_clsprc"]),
                volume=_as_int(record["acc_trdvol"]),
                file=path.name,
            )
        )
    return rows


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"bas_dd must be a date — got {type(value).__name__}: {value!r}")


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError(f"numeric column must not be bool — got {value!r}")
    if isinstance(value, int | Decimal):
        return int(value)
    if value is None:
        return 0  # KRX 원장의 NULL 가격/거래량은 거래 없음 표기 — 0으로 두면 정제 단계에서 제거된다
    raise TypeError(f"numeric column must be int/Decimal — got {type(value).__name__}: {value!r}")


class KrxParquetBarSource:
    """`quant-data` 빌드 디렉토리의 KOSPI·KOSDAQ 일별시세 parquet를 BarSource로 노출한다.

    Args:
        root: `krx_stk_bydd_trd.parquet` / `krx_ksq_bydd_trd.parquet`가 있는 디렉토리.
            두 파일 중 존재하는 것만 읽는다 (둘 다 없으면 NO_DATA).
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def load_bars(self, query: BarQuery) -> LoadResult:
        available = [self._root / name for name in TRADES_FILES if (self._root / name).exists()]
        if not available:
            return LoadResult(
                bars=(),
                status=LoadStatus.NO_DATA,
                detail=f"no KRX trade parquet found — root={self._root} expected={TRADES_FILES}",
            )
        return merge_results(
            self._load_one(instrument, query, available) for instrument in query.instruments
        )

    def _load_one(self, instrument: InstrumentId, query: BarQuery, files: list[Path]) -> LoadResult:
        rows: list[_TradeRow] = []
        for path in files:
            rows.extend(_read_rows(path, instrument.symbol, query.start, query.end))
        if not rows:
            return LoadResult(
                bars=(),
                status=LoadStatus.NO_DATA,
                detail=(
                    f"no rows for instrument — symbol={instrument.symbol} root={self._root} "
                    f"start={query.start} end={query.end} files={[p.name for p in files]}"
                ),
            )
        rows.sort(key=lambda row: row.session)  # 원장은 날짜순이 아니다
        duplicate = _first_duplicate_session(rows)
        if duplicate is not None:
            return LoadResult(
                bars=(),
                status=LoadStatus.FORMAT_ERROR,
                detail=(
                    f"duplicate session for instrument — symbol={instrument.symbol} "
                    f"session={duplicate.session} files={sorted({r.file for r in rows})}"
                ),
            )
        raw_bars = (
            RawBar(
                ts=datetime.combine(row.session, datetime.min.time()),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=row.volume,
                origin=f"file={row.file} session={row.session}",
            )
            for row in rows
        )
        return clean_raw_bars(
            raw_bars,
            instrument,
            query.ohlc_policy,
            source=str(self._root),
            drop_zero_volume=True,
        )


def _first_duplicate_session(sorted_rows: list[_TradeRow]) -> _TradeRow | None:
    for previous, current in zip(sorted_rows, sorted_rows[1:], strict=False):
        if previous.session == current.session:
            return current
    return None


class KrxParquetCorporateActionSource:
    """원장 시세 테이블의 `list_shrs`(상장주식수) 변화로 액면분할·병합을 검출한다.

    검출 규칙은 `data.corporate_actions.detect_share_count_events`. 기간 필터는 사건 세션
    기준이며, 직전 세션 행이 필요하므로 읽기는 기간 제한 없이 하고 결과만 자른다.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def load_actions(self, query: CorporateActionQuery) -> CorporateActionResult:
        available = [self._root / name for name in TRADES_FILES if (self._root / name).exists()]
        if not available:
            return CorporateActionResult(
                actions=(),
                status=LoadStatus.NO_DATA,
                detail=f"no KRX trade parquet found — root={self._root} expected={TRADES_FILES}",
            )
        actions: list[CorporateActionEvent] = []
        for instrument in query.instruments:
            rows: list[ShareCountRow] = []
            for path in available:
                for record in _read_records(path, instrument.symbol, None, None, _SHARE_COLUMNS):
                    rows.append(
                        ShareCountRow(
                            session=_as_date(record["bas_dd"]),
                            listed_shares=_as_int(record["list_shrs"]),
                            close=_as_int(record["tdd_clsprc"]),
                            volume=_as_int(record["acc_trdvol"]),
                        )
                    )
            if not rows:
                return CorporateActionResult(
                    actions=(),
                    status=LoadStatus.NO_DATA,
                    detail=(
                        f"no rows for instrument — symbol={instrument.symbol} root={self._root} "
                        f"files={[p.name for p in available]}"
                    ),
                )
            rows.sort(key=lambda row: row.session)
            duplicate = next(
                (b for a, b in zip(rows, rows[1:], strict=False) if a.session == b.session), None
            )
            if duplicate is not None:
                return CorporateActionResult(
                    actions=(),
                    status=LoadStatus.FORMAT_ERROR,
                    detail=(
                        f"duplicate session for instrument — symbol={instrument.symbol} "
                        f"session={duplicate.session} files={[p.name for p in available]}"
                    ),
                )
            actions.extend(
                event
                for event in detect_share_count_events(rows, instrument)
                if query.includes(event.ts.date())
            )
        actions.sort(key=lambda event: (event.ts, event.instrument.symbol))
        return CorporateActionResult(actions=tuple(actions), status=LoadStatus.OK)


class KrxParquetUniverseSource:
    """원장 종목마스터(`krx_*_isu_base_info`, 일별 스냅샷)에서 종목별 상장 구간을 만든다.

    마스터는 `bas_dd_req`(기준일)마다 그날 상장된 종목 한 행씩이다. 종목별
    min/max(bas_dd_req)가 구간이며, 기간 필터는 구간을 잘라낸다.

    Args:
        root: 마스터 parquet가 있는 디렉토리. 둘 중 존재하는 파일만 읽는다.
        security_groups: `secugrp_nm`(증권군) 화이트리스트. None이면 전체.
    """

    _COLUMNS = ("bas_dd_req", "isu_srt_cd", "secugrp_nm")

    def __init__(self, root: Path, security_groups: frozenset[str] | None = None) -> None:
        self._root = root
        self._security_groups = security_groups

    def load_universe(self, query: UniverseQuery) -> UniverseResult:
        import pyarrow.parquet as pq  # 어댑터 안에서만 import

        available = [self._root / name for name in MASTER_FILES if (self._root / name).exists()]
        if not available:
            return UniverseResult(
                memberships=(),
                status=LoadStatus.NO_DATA,
                detail=f"no KRX master parquet found — root={self._root} expected={MASTER_FILES}",
            )
        filters: list[tuple[str, str, object]] = []
        if query.start is not None:
            filters.append(("bas_dd_req", ">=", query.start))
        if query.end is not None:
            filters.append(("bas_dd_req", "<=", query.end))
        if self._security_groups is not None:
            filters.append(("secugrp_nm", "in", sorted(self._security_groups)))

        sessions_by_symbol: dict[str, set[date]] = {}
        all_sessions: set[date] = set()
        for path in available:
            table = pq.read_table(path, columns=list(self._COLUMNS), filters=filters or None)
            for record in table.to_pylist():
                symbol = record["isu_srt_cd"]
                if not isinstance(symbol, str):
                    return UniverseResult(
                        memberships=(),
                        status=LoadStatus.FORMAT_ERROR,
                        detail=f"isu_srt_cd must be str — file={path.name} got={symbol!r}",
                    )
                session = _as_date(record["bas_dd_req"])
                sessions_by_symbol.setdefault(symbol, set()).add(session)
                all_sessions.add(session)
        if not sessions_by_symbol:
            return UniverseResult(
                memberships=(),
                status=LoadStatus.NO_DATA,
                detail=(
                    f"no master rows — root={self._root} venue={query.venue} "
                    f"start={query.start} end={query.end} groups={self._security_groups}"
                ),
            )
        # 마스터에 있는 날(all_sessions) 중 그 종목이 빠진 날이 있으면 구간을 끊는다 —
        # 재상장·수집 누락을 하나의 구간으로 덮어쓰지 않는다 (연속 구간마다 Membership 하나).
        calendar = sorted(all_sessions)
        memberships: list[Membership] = []
        for symbol, sessions in sorted(sessions_by_symbol.items()):
            instrument = InstrumentId(
                venue=query.venue, symbol=symbol, asset_class=AssetClass.EQUITY, currency="KRW"
            )
            start: date | None = None
            previous: date | None = None
            for session in calendar:
                present = session in sessions
                if present and start is None:
                    start = session
                if not present and start is not None and previous is not None:
                    memberships.append(Membership(instrument, start, previous))
                    start = None
                if present:
                    previous = session
            if start is not None and previous is not None:
                memberships.append(Membership(instrument, start, previous))
        return UniverseResult(memberships=tuple(memberships), status=LoadStatus.OK)
