"""duckdb 어댑터의 열 지향 원시 패널 로딩 — GRID 원천(columnar-tape spec D3·D4·D5, T2-02).

`EquityDuckdbAdapter.load_raw_observations` 와 같은 셀을 행 객체 없이 `RawPanel` 열로 낸다. 결과는
`RawPanel.from_observation_set(load_raw_observations(q), field_ids=q.field_ids)` 와 셀마다 같다(spec
5절 L0). 규칙은 행 경로와 한 벌이다.

- 질의 SQL 은 행 경로의 `_grid_sql` 그대로이고, 그 위에 열 받기용 투영만 씌운다.
- 셀 종류는 행 경로의 `_cell_kind` 를 (값 유무, 결측 사유, 가림) 조합마다 한 번 불러 만든 표로
  정한다(`_cell_rules`). 규칙을 다시 적지 않는다.
- 숫자는 spec D3 원시 숫자 행을 따른다. DECIMAL 은 |비스케일 정수| ≤ 2^53 이고 scale ≤ 22 인 칸만
  비스케일 정수 ÷ 10^scale 나눗셈 한 번(두 수가 정확히 표현돼 정확 반올림)이고, 나머지 칸은 SQL 이
  내준 10진 문자열의 `float(Decimal)` 이다. `fetchnumpy` 의 DECIMAL → float64 와 `CAST AS DOUBLE` 은
  늘 정확 반올림이지는 않아 쓰지 않는다. DOUBLE·FLOAT·정수 열은 그대로 받는다.
- 랙 n 셀은 달력 세션 index − n 행에서 읽되 `span_seq` 가 같아야 한다(행 경로 `_cell`).
- 취소는 행 경로와 같은 감시 스레드 방식(`_interruptible`)이다.

LATEST 원천과 법인 축(`_corp_map`)은 T2-03 범위다. 그 필드가 섞인 질의는
`ColumnarSourceUnsupportedError` 로 거절한다. 어댑터는 아직 `ColumnarRawObservationPort` 능력을
선언하지 않는다(T2-03 에서 선언). 운영 경로에도 연결하지 않았다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import numpy as np
from numpy.typing import NDArray

from strategy_workbench.application.portfolio_design.facade.ports import (
    RAW_CELL_KINDS,
    RawFieldColumn,
    RawObservationContractViolation,
    RawObservationQuery,
    RawPanel,
)
from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus

from ._adapter import (
    _FILL_KIND_TO_CELL,
    SECURITY_ID_SEP,
    EquityDuckdbAdapter,
    _cell_kind,
    _interruptible,
    _no_members_detail,
    _noop_checkpoint,
    _noop_progress,
    _RawRejection,
)
from ._source import EquityDuckdbSetupError
from ._specs import (
    FIELD_BY_ID,
    SOURCE_BY_NAME,
    SPAN_TABLE,
    UNIVERSE_TABLE,
    SourceMode,
    SourceSpec,
)

# fetchnumpy 가 그대로 받는 숫자 열 — 값이 float64 로 정확하거나(FLOAT·작은 정수) numpy 의
# int64 → float64 변환이 Python `float(int)` 와 같은 최근접 짝수 반올림이다.
_DIRECT_TYPES = frozenset(
    {"DOUBLE", "FLOAT", "REAL", "TINYINT", "SMALLINT", "INTEGER", "BIGINT"}
    | {"UTINYINT", "USMALLINT", "UINTEGER"}
)
# fetchnumpy 가 float64 로 바꾸며 값을 잃을 수 있는 정수 열 — 전 칸을 정확 경로로 읽는다.
_EXACT_TYPES = frozenset({"HUGEINT", "UBIGINT", "UHUGEINT"})
_DECIMAL_TYPE = re.compile(r"DECIMAL\((\d+),\s*(\d+)\)")
# 빠른 길의 한계(spec D3): 2^53 이하 정수와 10^22 이하 10의 거듭제곱은 float64 로 정확하다.
FAST_UNSCALED_LIMIT = 2**53
FAST_SCALE_LIMIT = 22
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_FETCHED = 0.5  # 진행 구간: 질의·fetchnumpy 완료. 이후 필드 열 조립
_ASSEMBLED = 0.95  # 필드 열 조립 완료. 이후 행 정렬
_EXACT_CHECKPOINT = 256  # 정확 경로 루프의 checkpoint 간격(칸, spec D7)
_CODE_CHUNK = 1 << 18  # 티커 코드 매기기 루프의 checkpoint 간격(행, spec D7)

# `_cell_kind` 에 넣어 볼 결측 사유 대표값 — NULL, 어휘 하나씩, 어휘 밖 문자열 하나. 어휘 밖 값은
# 모두 같은 결과(MISSING)라 대표 하나로 충분하다.
_OTHER_FILL_KIND = "\x00out-of-vocabulary"
_FILL_CODES: tuple[str | None, ...] = (None, *_FILL_KIND_TO_CELL, _OTHER_FILL_KIND)
_KIND_CODE = {kind: code for code, kind in enumerate(RAW_CELL_KINDS)}
# 값을 그대로 두는 조합과 상수로 바꾸는 조합을 가르는 시험값 두 개.
_PROBES = (1.5, 2.5)


class ColumnarSourceUnsupportedError(ValueError):
    """열 로딩이 아직 읽지 못하는 원천의 필드가 질의에 있다(LATEST·법인 축, T2-03)."""


@dataclass(frozen=True)
class CellRules:
    """`_cell_kind` 를 (값 유무, 결측 사유 코드, 가림) 조합마다 평가한 표. 축 순서도 같다."""

    kind: NDArray[np.int8]  # `RAW_CELL_KINDS` 코드
    keep: NDArray[np.bool_]  # 원천 값을 그대로 쓴다
    fill: NDArray[np.bool_]  # 원천 값 대신 `constant` 를 쓴다(값 없는 칸에 0 을 싣는 원천)
    constant: NDArray[np.float64]


def cell_rules(omitted_is_zero: bool) -> CellRules:
    """행 경로의 셀 종류 규칙 `_cell_kind` 를 열 경로가 쓸 표로 옮긴다(규칙은 한 벌)."""
    shape = (2, len(_FILL_CODES), 2)
    kind = np.empty(shape, dtype=np.int8)
    keep = np.zeros(shape, dtype=np.bool_)
    fill = np.zeros(shape, dtype=np.bool_)
    constant = np.zeros(shape, dtype=np.float64)
    for has_value in (0, 1):
        for code, fill_kind in enumerate(_FILL_CODES):
            for masked in (0, 1):
                results = [
                    _cell_kind(
                        probe if has_value else None, fill_kind, bool(masked), omitted_is_zero
                    )
                    for probe in _PROBES
                ]
                kinds = {result[1] for result in results}
                values = [result[0] for result in results]
                if len(kinds) != 1:
                    raise RuntimeError(
                        "cell kind depends on the value itself and has no columnar table — "
                        f"has_value={has_value} fill_kind={fill_kind!r} masked={masked} "
                        f"kinds={sorted(k.value for k in kinds)}"
                    )
                position = (has_value, code, masked)
                kind[position] = _KIND_CODE[kinds.pop()]
                if all(value is None for value in values):
                    continue
                if has_value and values == list(_PROBES):
                    keep[position] = True
                elif values[0] is not None and values[0] == values[1]:
                    fill[position] = True
                    constant[position] = values[0]
                else:
                    raise RuntimeError(
                        "cell value rule has no columnar table — "
                        f"has_value={has_value} fill_kind={fill_kind!r} masked={masked} "
                        f"values={values}"
                    )
    return CellRules(kind, keep, fill, constant)


def _sql_text(text: str) -> str:
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def _fill_kind_sql(column: str) -> str:
    """결측 사유 열을 `_FILL_CODES` 위치로 바꾸는 식. 행 경로의 `str(fill_kind)` 비교와 같다."""
    cases = " ".join(
        f"WHEN CAST({column} AS VARCHAR) = {_sql_text(name)} THEN {code}"
        for code, name in enumerate(_FILL_CODES)
        if name is not None and name is not _OTHER_FILL_KIND
    )
    return f"CASE WHEN {column} IS NULL THEN 0 {cases} ELSE {len(_FILL_CODES) - 1} END"


@dataclass(frozen=True)
class NumericColumn:
    """값 열 하나를 받는 방식(spec D3 원시 숫자 행).

    - `direct`: fetchnumpy 값을 float64 로 그대로 쓴다.
    - `scale`: DECIMAL 빠른 길 — 비스케일 정수 열 `<alias>_u` 를 `10**scale` 로 한 번 나눈다.
    - `exact`: 정확 경로 열 `<alias>_x`(10진 문자열)가 있다. 빠른 길이 못 받는 칸만 채워진다.
    """

    alias: str
    direct: bool = False
    scale: int | None = None
    exact: bool = False

    def select(self, column: str) -> list[str]:
        if self.direct:
            return [f"{column} AS {self.alias}"]
        if self.scale is None:
            return [f"CAST({column} AS VARCHAR) AS {self.alias}_x"]
        scale = self.scale
        bound = format(Decimal(FAST_UNSCALED_LIMIT).scaleb(-scale), "f")
        bound_sql = f"CAST('{bound}' AS DECIMAL(38, {scale}))"
        # 범위 밖 칸은 곱하기 전에 NULL 로 빼서 DECIMAL(38, scale) 곱이 넘치지 않는다.
        unscaled = (
            f"CAST(CAST(CASE WHEN abs({column}) <= {bound_sql} THEN {column} END "
            f"AS DECIMAL(38, {scale})) * {10**scale} AS BIGINT) AS {self.alias}_u"
        )
        if not self.exact:
            return [unscaled]
        exact = (
            f"CASE WHEN abs({column}) > {bound_sql} THEN CAST({column} AS VARCHAR) END "
            f"AS {self.alias}_x"
        )
        return [unscaled, exact]

    def decode(
        self, fetched: Mapping[str, NDArray[np.generic]], checkpoint: Callable[[], None]
    ) -> tuple[NDArray[np.bool_], NDArray[np.float64]]:
        """(값 있음, 값). 값이 없는 칸은 0.0 이다."""
        key = next(iter(fetched))
        rows = len(fetched[key])
        has_value = np.zeros(rows, dtype=np.bool_)
        value = np.zeros(rows, dtype=np.float64)
        if self.direct:
            column = fetched[self.alias]
            has_value = ~np.ma.getmaskarray(column)
            value[has_value] = np.ma.getdata(column)[has_value].astype(np.float64)
        if self.scale is not None:
            unscaled = fetched[f"{self.alias}_u"]
            fast = ~np.ma.getmaskarray(unscaled)
            # 두 수가 float64 로 정확하므로 IEEE 나눗셈 한 번이 정확 반올림이다.
            value[fast] = np.ma.getdata(unscaled)[fast].astype(np.float64) / float(10**self.scale)
            has_value |= fast
        if self.exact:
            texts = fetched[f"{self.alias}_x"]
            for count, row in enumerate(np.flatnonzero(~np.ma.getmaskarray(texts)).tolist()):
                if count % _EXACT_CHECKPOINT == 0:
                    checkpoint()
                value[row] = float(Decimal(str(texts[row])))
                has_value[row] = True
        return has_value, value


def numeric_column(alias: str, declared: str, label: str) -> NumericColumn:
    """선언 타입에 맞는 받기 방식. 숫자가 아닌 타입은 행 경로 `_as_float` 처럼 실패다."""
    kind = declared.strip().upper()
    if kind in _DIRECT_TYPES:
        return NumericColumn(alias, direct=True)
    if kind in _EXACT_TYPES:
        return NumericColumn(alias, exact=True)
    match = _DECIMAL_TYPE.fullmatch(kind)
    if match is None:
        raise EquityDuckdbSetupError(
            f"{label} must be numeric — the columnar load cannot read declared type {declared!r}"
        )
    precision, scale = int(match[1]), int(match[2])
    if scale > FAST_SCALE_LIMIT:
        return NumericColumn(alias, exact=True)
    # 10^precision ≤ 2^53 이면 비스케일 정수가 늘 빠른 길 안이라 정확 경로 열이 필요 없다.
    return NumericColumn(alias, scale=scale, exact=10**precision > FAST_UNSCALED_LIMIT)


def _ordinals(
    column: NDArray[np.generic], label: str
) -> tuple[NDArray[np.bool_], NDArray[np.int32]]:
    """날짜 열 → (값 있음, 일 서수). 값이 없는 칸은 0 이다."""
    data = np.ma.getdata(column)
    if not np.issubdtype(data.dtype, np.datetime64):
        raise EquityDuckdbSetupError(f"{label} must be a date — got dtype {data.dtype}")
    present = ~np.ma.getmaskarray(column)
    days = np.zeros(present.shape, dtype=np.int32)
    # NULL 칸(NaT)은 정수로 바꾸지 않는다. 날짜 단위 내림은 행 경로 `datetime.date()` 와 같다.
    days[present] = data[present].astype("datetime64[D]").astype(np.int64) + _EPOCH_ORDINAL
    return present, days


def _flags(column: NDArray[np.generic]) -> NDArray[np.bool_]:
    """참거짓 열. NULL 은 거짓이다(행 경로 `bool(None)`)."""
    return np.ma.filled(np.ma.asarray(column), False).astype(np.bool_)


def _codes(
    values: NDArray[np.generic], checkpoint: Callable[[], None]
) -> tuple[NDArray[np.int32], list[str]]:
    """문자열 열을 처음 나온 순서의 코드로 바꾼다."""
    lookup: dict[str, int] = {}
    codes = np.empty(len(values), dtype=np.int32)
    for low in range(0, len(values), _CODE_CHUNK):
        checkpoint()
        part = values[low : low + _CODE_CHUNK]
        codes[low : low + len(part)] = np.fromiter(
            (lookup.setdefault(str(item), len(lookup)) for item in part),
            dtype=np.int32,
            count=len(part),
        )
    return codes, list(lookup)


@dataclass(frozen=True)
class _SourceCells:
    """GRID 원천 하나의 격자 행별 공통 칸(행 경로 `_grid` 의 원천 루프와 같은 판정)."""

    present: NDArray[np.bool_]  # 원천 행이 있고 공개일·내용일이 다 있다
    available: NDArray[np.int32]
    fill_code: NDArray[np.intp]
    masked: NDArray[np.bool_]
    rules: CellRules


def _failure_panel(
    snapshot_id: str, rejection: _RawRejection, field_ids: Sequence[str]
) -> RawPanel:
    """실패 값 — 행 경로의 실패 `RawObservationSet` 을 열로 옮긴 것과 같다."""
    empty_bool = np.zeros(0, dtype=np.bool_)
    empty_int = np.zeros(0, dtype=np.int32)
    return RawPanel(
        status=rejection.status,
        data_snapshot_id=snapshot_id,
        sessions=(),
        history_sessions=(),
        securities=(),
        row_date=empty_int,
        row_security=empty_int,
        universe_member=empty_bool,
        sector=empty_int,
        previous_weight=np.zeros(0, dtype=np.float64),
        fields=tuple(
            RawFieldColumn(
                field_id=field_id,
                present=empty_bool,
                has_value=empty_bool,
                value=np.zeros(0, dtype=np.float64),
                text=empty_int,
                available=empty_int,
                kind=np.zeros(0, dtype=np.int8),
            )
            for field_id in field_ids
        ),
        detail=rejection.detail,
    )


def load_grid_raw_panel(
    adapter: EquityDuckdbAdapter,
    query: RawObservationQuery,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
    progress: Callable[[float], None] = _noop_progress,
) -> RawPanel:
    """GRID 원천 필드만 담은 질의의 원시 관측을 열로 읽는다.

    결과는 `RawPanel.from_observation_set(adapter.load_raw_observations(query),
    field_ids=query.field_ids)` 와 셀마다 같다. 계약 검사는 하지 않는다(소비 경계가
    `RawPanel.validate_contract` 를 부른다). `progress` 는 0~1 로 단조 증가하고 성공하면 1.0 으로
    끝난다. 실패 값으로 끝나는 경로는 보고하지 않는다(행 경로와 같다).

    Raises:
        ColumnarSourceUnsupportedError: 질의에 LATEST 원천 필드가 있다(T2-03 전).
        EquityDuckdbSetupError: 원천 열 타입·격자 중복이 행 경로도 거절할 모양이다.
    """
    checkpoint()
    request = adapter._raw_request(query)
    if isinstance(request, _RawRejection):
        return _failure_panel(adapter._snapshot_id, request, query.field_ids)
    latest = [
        field_id
        for field_id in query.field_ids
        if SOURCE_BY_NAME[FIELD_BY_ID[field_id].source].mode is not SourceMode.GRID
    ]
    if latest:
        raise ColumnarSourceUnsupportedError(
            "열 로딩은 아직 GRID 원천 필드만 읽는다. LATEST 원천·법인 축 필드는 T2-03 에서 "
            "지원한다 (columnar_latest_unsupported) — "
            f"latest_fields={latest} "
            f"sources={sorted({FIELD_BY_ID[field_id].source for field_id in latest})}"
        )
    window, lags = request.window, request.lags
    fetch = adapter._fetch_range(window, lags, query.field_ids)
    grouped = adapter._fields_by_source(query.field_ids)
    grid = adapter._grid_sql(
        grouped,
        list(grouped),
        fetch.start,
        fetch.end,
        tickers=None,
        predicate=adapter._predicate(query.universe_id),
    )
    con = adapter._connect(SOURCE_BY_NAME[name] for name in grouped)
    try:
        declared = {
            str(row[0]): str(row[1])
            for row in _interruptible(
                con, lambda: con.execute(f"DESCRIBE {grid.sql}", grid.params).fetchall(), checkpoint
            )
        }
        numeric: dict[str, NumericColumn] = {}
        selects = ["d", "ticker", "span_seq", "member"]
        for index, (name, fields) in enumerate(grid.layout):
            source = SOURCE_BY_NAME[name]
            kind = _fill_kind_sql(f"k{index}") if source.kind_expr else "0"
            selects += [f"p{index}", f"a{index}", f"t{index}", f"{kind} AS k{index}", f"m{index}"]
            for position, field_id in enumerate(fields):
                alias = f"v{index}_{position}"
                numeric[field_id] = numeric_column(alias, declared[alias], field_id)
                selects += numeric[field_id].select(alias)
        sql = f"SELECT {', '.join(selects)} FROM ({grid.sql}) AS grid"
        fetched: dict[str, NDArray[np.generic]] = _interruptible(
            con, lambda: con.execute(sql, grid.params).fetchnumpy(), checkpoint
        )
    finally:
        con.close()
    progress(_FETCHED)

    day_present, day = _ordinals(fetched["d"], "universe_daily.date")
    if not bool(day_present.all()):
        raise EquityDuckdbSetupError("universe_daily.date must be a date — got NULL")
    ticker, tickers = _codes(fetched["ticker"], checkpoint)
    span_seq = np.ma.getdata(fetched["span_seq"]).astype(np.int64)
    member = _flags(fetched["member"])
    _reject_duplicate_keys(ticker, day, tickers)
    checkpoint()

    # 격자 행 → 달력 세션 index(달력 밖 날짜는 -1). 행은 창 세션만, 랙은 index 로 센다.
    calendar = np.array([session.toordinal() for session in adapter._sessions], dtype=np.int32)
    found = np.minimum(np.searchsorted(calendar, day), len(calendar) - 1)
    session_index = np.where(calendar[found] == day, found, -1).astype(np.int64)
    first_index = adapter._session_index[window.sessions[0]]
    rows = np.flatnonzero(session_index >= first_index)
    lookup = _GridLookup(ticker, session_index, len(calendar))
    checkpoint()

    sources: dict[str, _SourceCells] = {}
    for index, (name, _fields) in enumerate(grid.layout):
        checkpoint()
        sources[name] = _source_cells(fetched, index, SOURCE_BY_NAME[name])
    source_of = {field_id: name for name, fields in grid.layout for field_id in fields}
    targets: dict[int, NDArray[np.int64]] = {}
    columns: list[RawFieldColumn] = []
    for count, field_id in enumerate(query.field_ids):
        checkpoint()
        progress(_FETCHED + (_ASSEMBLED - _FETCHED) * count / len(query.field_ids))
        lag = lags[field_id]
        if lag not in targets:
            targets[lag] = lookup.lagged(rows, lag, span_seq)
        cells = sources[source_of[field_id]]
        has_value, value = numeric[field_id].decode(fetched, checkpoint)
        columns.append(_field_column(field_id, targets[lag], cells, has_value, value))
    progress(_ASSEMBLED)

    # 행 순서는 (as_of, security_id) 다. security_id 는 문자열 순서로 코드를 매긴다.
    width = int(span_seq.max(initial=0)) + 1
    pairs = ticker[rows].astype(np.int64) * width + span_seq[rows]
    unique_pairs, pair_of_row = np.unique(pairs, return_inverse=True)
    checkpoint()
    security_ids = [
        f"{tickers[pair // width]}{SECURITY_ID_SEP}{pair % width}" for pair in unique_pairs.tolist()
    ]
    rank = np.empty(len(security_ids), dtype=np.int32)
    rank[sorted(range(len(security_ids)), key=security_ids.__getitem__)] = np.arange(
        len(security_ids), dtype=np.int32
    )
    row_security = rank[pair_of_row].astype(np.int32)
    row_date = day[rows]
    order = np.lexsort((row_security, row_date))
    reordered: list[RawFieldColumn] = []
    for column in columns:
        checkpoint()
        reordered.append(_reordered(column, order))
    securities = tuple(sorted(security_ids))
    _reject_repeated_fields(
        query.field_ids,
        reordered,
        row_date[order],
        row_security[order],
        securities,
        adapter._snapshot_id,
    )
    panel = RawPanel(
        status=DataLoadStatus.OK if rows.size else DataLoadStatus.NO_DATA,
        data_snapshot_id=adapter._snapshot_id,
        sessions=window.requested,
        history_sessions=window.history,
        securities=securities,
        row_date=row_date[order],
        row_security=row_security[order],
        universe_member=member[rows][order],
        sector=np.full(rows.size, -1, dtype=np.int32),  # 행 경로처럼 섹터는 미제공(DESIGN §7)
        previous_weight=np.zeros(rows.size, dtype=np.float64),
        fields=tuple(reordered),
        detail=None if rows.size else _no_members_detail(query),
        warnings=tuple(sorted({*window.warnings, *fetch.warnings})),
    )
    progress(1.0)
    return panel


def _reject_repeated_fields(
    field_ids: Sequence[str],
    columns: Sequence[RawFieldColumn],
    row_date: NDArray[np.int32],
    row_security: NDArray[np.int32],
    securities: Sequence[str],
    snapshot_id: str,
) -> None:
    """질의 field_ids 에 같은 필드가 두 번 있으면 행 경로와 같은 계약 위반으로 거절한다.

    행 경로는 관측마다 fields 를 질의 순서로 싣고 `RawObservationSet` 생성 검사가 (행 순서, fields
    순서)로 처음 겹친 칸을 거절한다. 같은 필드의 열은 present 가 같으므로 다시 나온 자리의 열이
    present 인 첫 (행, 자리)가 그 칸이다. 그런 셀이 하나도 없으면 행 경로는 통과하고 열 경로는
    `RawPanel` 생성의 중복 field_id 검사(`from_observation_set` 과 같은 ValueError)로 끝난다.
    """
    seen: set[str] = set()
    repeats: list[int] = []
    for position, field_id in enumerate(field_ids):
        if field_id in seen:
            repeats.append(position)
        seen.add(field_id)
    best: tuple[int, int] | None = None
    for position in repeats:
        hits = np.flatnonzero(columns[position].present)
        if hits.size and (best is None or int(hits[0]) < best[0]):
            best = (int(hits[0]), position)
    if best is None:
        return
    row, position = best
    raise RawObservationContractViolation(
        "raw observation field_ids must be unique — "
        f"as_of={date.fromordinal(int(row_date[row]))} "
        f"security_id={securities[int(row_security[row])]!r} "
        f"field_id={field_ids[position]!r} snapshot={snapshot_id!r}"
    )


def _reject_duplicate_keys(
    ticker: NDArray[np.int32], day: NDArray[np.int32], tickers: Sequence[str]
) -> None:
    """(ticker, date) 가 겹치면 행 경로(`_grid`)와 같은 오류다."""
    order = np.lexsort((day, ticker))
    same = (ticker[order][1:] == ticker[order][:-1]) & (day[order][1:] == day[order][:-1])
    hits = np.flatnonzero(same)
    if hits.size:
        row = int(order[hits[0] + 1])
        key = (tickers[int(ticker[row])], date.fromordinal(int(day[row])))
        raise EquityDuckdbSetupError(
            f"duplicate (ticker, date) in {UNIVERSE_TABLE}×{SPAN_TABLE} — key={key}"
        )


class _GridLookup:
    """(티커, 달력 세션 index) → 격자 행. 달력 밖 날짜의 행은 찾지 않는다."""

    def __init__(
        self, ticker: NDArray[np.int32], session_index: NDArray[np.int64], sessions: int
    ) -> None:
        self._sessions = sessions
        self._ticker = ticker
        self._session_index = session_index
        calendar_rows = np.flatnonzero(session_index >= 0)
        keys = ticker[calendar_rows].astype(np.int64) * sessions + session_index[calendar_rows]
        order = np.argsort(keys, kind="stable")
        self._keys = keys[order]
        self._rows = calendar_rows[order]

    def lagged(
        self, rows: NDArray[np.intp], lag: int, span_seq: NDArray[np.int64]
    ) -> NDArray[np.int64]:
        """행마다 랙 `lag` 셀을 읽을 격자 행. 없으면 -1 이다(행 경로 `_cell` 의 GRID 갈래).

        달력 세션 index − lag 행이 같은 티커에 있고 `span_seq` 가 같아야 한다. 랙이 상장 공백을
        건너도 이전 상장 구간의 값을 새 종목 신원에 싣지 않는다.
        """
        target_index = self._session_index[rows] - lag
        keys = self._ticker[rows].astype(np.int64) * self._sessions + target_index
        if not self._keys.size:
            return np.full(rows.size, -1, dtype=np.int64)
        position = np.minimum(np.searchsorted(self._keys, keys), self._keys.size - 1)
        target = self._rows[position].astype(np.int64)
        found = (target_index >= 0) & (self._keys[position] == keys)
        found &= span_seq[target] == span_seq[rows]
        return np.where(found, target, -1)


def _source_cells(
    fetched: Mapping[str, NDArray[np.generic]], index: int, source: SourceSpec
) -> _SourceCells:
    present = _flags(fetched[f"p{index}"])
    has_available, available = _ordinals(
        fetched[f"a{index}"], f"{source.relation}.{source.available_expr}"
    )
    has_content, _content = _ordinals(
        fetched[f"t{index}"], f"{source.relation}.{source.content_expr}"
    )
    return _SourceCells(
        present=present & has_available & has_content,
        available=available,
        fill_code=np.ma.getdata(fetched[f"k{index}"]).astype(np.intp),
        masked=_flags(fetched[f"m{index}"]),
        rules=cell_rules(source.omitted_is_zero),
    )


def _field_column(
    field_id: str,
    target: NDArray[np.int64],
    cells: _SourceCells,
    has_value: NDArray[np.bool_],
    value: NDArray[np.float64],
) -> RawFieldColumn:
    """랙을 적용한 필드 열. 셀이 없는 칸은 `RawPanel.from_observation_set` 과 같은 기본값이다."""
    source_row = np.maximum(target, 0)
    present = (target >= 0) & cells.present[source_row]
    has_source = has_value[source_row]
    fill_code = cells.fill_code[source_row]
    masked = cells.masked[source_row]
    position = (has_source.astype(np.intp), fill_code, masked.astype(np.intp))
    keep = cells.rules.keep[position]
    fill = cells.rules.fill[position]
    cell_value = np.where(
        keep, value[source_row], np.where(fill, cells.rules.constant[position], 0.0)
    )
    return RawFieldColumn(
        field_id=field_id,
        present=present,
        has_value=present & (keep | fill),
        value=np.where(present, cell_value, 0.0).astype(np.float64),
        text=np.full(target.size, -1, dtype=np.int32),
        available=np.where(present, cells.available[source_row], 0).astype(np.int32),
        kind=np.where(present, cells.rules.kind[position], -1).astype(np.int8),
    )


def _reordered(column: RawFieldColumn, order: NDArray[np.intp]) -> RawFieldColumn:
    return RawFieldColumn(
        field_id=column.field_id,
        present=column.present[order],
        has_value=column.has_value[order],
        value=column.value[order],
        text=column.text[order],
        available=column.available[order],
        kind=column.kind[order],
    )
