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
from backtest_engine.types.events import CorporateActionEvent
from backtest_engine.types.instruments import InstrumentId

KOSPI_TRADES_FILE = "krx_stk_bydd_trd.parquet"
KOSDAQ_TRADES_FILE = "krx_ksq_bydd_trd.parquet"
TRADES_FILES = (KOSPI_TRADES_FILE, KOSDAQ_TRADES_FILE)

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
            actions.extend(
                event
                for event in detect_share_count_events(rows, instrument)
                if query.includes(event.ts.date())
            )
        actions.sort(key=lambda event: (event.ts, event.instrument.symbol))
        return CorporateActionResult(actions=tuple(actions), status=LoadStatus.OK)
