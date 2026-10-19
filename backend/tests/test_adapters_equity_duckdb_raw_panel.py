"""duckdb 어댑터의 열 지향 원시 패널 로딩(GRID 원천, columnar-tape T2-02).

- L0 대조(spec 5절): 원장 손 픽스처에서 `load_grid_raw_panel(q)` 와
  `RawPanel.from_observation_set(load_raw_observations(q), field_ids=q.field_ids)` 를
  field_id 기준으로 비교한다. 열 위치·코드가 아니라 풀어낸 값(present·kind·available·member·
  sector·previous_weight·값의 float 비트·메타)과 `observations()` 복원값을 본다. `tests/parity`
  (T1-01)가 아직 main 에 없어 비교기는 이 파일에 둔다.
- DECIMAL 경계(spec D3 원시 숫자 행): 비스케일 정수 2^53 ± 1, scale 0·4·10·18·22, 음수·0·−0.
- 취소: 열 질의 도중 checkpoint 가 던지면 감시 스레드가 질의를 끊는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Event, current_thread, main_thread
from typing import TYPE_CHECKING

import numpy as np
import pytest

from strategy_workbench.adapters.outbound.equity_duckdb import _raw_panel as raw_panel_module
from strategy_workbench.adapters.outbound.equity_duckdb._adapter import _GridSql, _interruptible
from strategy_workbench.adapters.outbound.equity_duckdb._raw_panel import (
    FAST_SCALE_LIMIT,
    FAST_UNSCALED_LIMIT,
    ColumnarSourceUnsupportedError,
    load_grid_raw_panel,
    numeric_column,
)
from strategy_workbench.adapters.outbound.equity_duckdb._specs import (
    FIELD_BY_ID,
    SOURCE_BY_NAME,
    SourceMode,
)
from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
    EquityDuckdbAdapter,
    EquityDuckdbSetupError,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RAW_CELL_KINDS,
    ColumnarRawObservationPort,
    RawFieldColumn,
    RawObservation,
    RawObservationQuery,
    RawPanel,
)
from strategy_workbench.domain.equity.facade.research_data import CellKind
from tests.equity_fixture import build_workbench_root

if TYPE_CHECKING:
    import duckdb

DUCKDB_EXTRA = "backend optional extra `equity` (uv sync --extra equity)"
START, END = date(2024, 1, 8), date(2024, 1, 12)


@pytest.fixture(scope="module")
def adapter(tmp_path_factory: pytest.TempPathFactory) -> EquityDuckdbAdapter:
    pytest.importorskip("duckdb", reason=DUCKDB_EXTRA)
    root: Path = tmp_path_factory.mktemp("columnar-grid") / "equity"
    return EquityDuckdbAdapter(
        build_workbench_root(root, extra_policy_rows=[("krx.none", "none", 1, "FALSE")])
    )


def _grid_fields(adapter: EquityDuckdbAdapter) -> tuple[str, ...]:
    return tuple(
        profile.field_id
        for profile in adapter.list_fields()
        if SOURCE_BY_NAME[FIELD_BY_ID[profile.field_id].source].mode is SourceMode.GRID
    )


# --- L0 비교기 ---------------------------------------------------------------------------------


def _float_bits(values: Sequence[float] | np.ndarray) -> list[int]:
    return np.asarray(values, dtype=np.float64).view(np.int64).tolist()


def _rows(panel: RawPanel) -> list[tuple[object, ...]]:
    """행 칸을 코드가 아니라 풀어낸 값으로."""
    return list(
        zip(
            [date.fromordinal(day) for day in panel.row_date.tolist()],
            [panel.securities[code] for code in panel.row_security.tolist()],
            panel.universe_member.tolist(),
            [None if code < 0 else panel.sectors[code] for code in panel.sector.tolist()],
            _float_bits(panel.previous_weight),
            strict=True,
        )
    )


def _cells(column: RawFieldColumn) -> list[tuple[object, ...]]:
    """한 필드의 칸을 풀어낸 값으로. 셀이 없는 칸은 None 이다."""
    result: list[tuple[object, ...]] = []
    for row in range(column.present.shape[0]):
        if not column.present[row]:
            result.append((None,))
            continue
        value: object = None
        if column.has_value[row]:
            code = int(column.text[row])
            value = (
                ("text", column.categories[code])
                if code >= 0
                else ("float", _float_bits(column.value[row : row + 1])[0])
            )
        result.append(
            (
                date.fromordinal(int(column.available[row])),
                RAW_CELL_KINDS[int(column.kind[row])],
                value,
            )
        )
    return result


def _assert_same_panel(columnar: RawPanel, reference: RawPanel) -> None:
    """L0: field_id 기준으로 모든 칸과 메타가 같다(허용오차 0, spec D1)."""
    meta = ("status", "data_snapshot_id", "sessions", "history_sessions", "detail", "warnings")
    assert {name: getattr(columnar, name) for name in meta} == {
        name: getattr(reference, name) for name in meta
    }
    assert _rows(columnar) == _rows(reference)
    by_id = {column.field_id: column for column in columnar.fields}
    assert list(by_id) == [column.field_id for column in reference.fields]
    for column in reference.fields:
        assert _cells(by_id[column.field_id]) == _cells(column), column.field_id
    restored, expected = columnar.observations(), reference.observations()
    assert restored == expected
    assert _observation_bits(restored) == _observation_bits(expected)


def _observation_bits(observations: tuple[RawObservation, ...]) -> list[object]:
    """복원한 관측의 값 타입과 float 비트(dataclass `==` 는 -0.0 과 0.0 을 같게 본다)."""
    return [
        [
            (field.field_id, type(field.value).__name__, _float_bits(np.array([field.value]))[0])
            for field in item.fields
            if isinstance(field.value, float)
        ]
        for item in observations
    ]


def _reference(adapter: EquityDuckdbAdapter, query: RawObservationQuery) -> RawPanel:
    return RawPanel.from_observation_set(
        adapter.load_raw_observations(query), field_ids=query.field_ids
    )


def _query(
    fields: tuple[str, ...],
    *,
    start: date = START,
    end: date = END,
    history: int = 0,
    universe: str = "krx.common-stock",
    market: str = "KRX",
) -> RawObservationQuery:
    return RawObservationQuery(market, universe, start, end, fields, history)


# --- L0 손 픽스처 ------------------------------------------------------------------------------


L0_CASES: dict[str, Callable[[tuple[str, ...]], RawObservationQuery]] = {
    "grid-fields-with-history": lambda grid: _query(grid, history=3),
    # 달력 첫 세션부터 — 워밍업·랙을 거슬러 올라갈 달력이 모자란 경고 둘이 난다
    "calendar-start": lambda grid: _query(
        grid, start=date(2023, 12, 26), end=date(2024, 1, 3), history=2
    ),
    "reversed-field-order": lambda grid: _query(tuple(reversed(grid)), history=1),
    # 랙 3 신용잔고 — 036220 재상장(2024-01-08) 구간은 이전 상장 값을 물지 않는다
    "lag-across-relisting": lambda _grid: _query(
        ("credit.margin_balance", "price.close", "flow.foreign_net_buy"),
        start=date(2024, 1, 4),
    ),
    "all-securities": lambda grid: _query(grid, universe="krx.all", history=2),
    "no-fields": lambda _grid: _query((), history=1),
    "no-members": lambda _grid: _query(("price.close",), universe="krx.none"),
    "unknown-market": lambda grid: _query(grid, market="NYSE"),
    "unknown-universe": lambda grid: _query(grid, universe="krx.unknown"),
    "unknown-field": lambda _grid: _query(("price.close", "price.unknown")),
    "outside-coverage": lambda grid: _query(grid, start=date(2023, 1, 2), end=date(2023, 1, 6)),
    "no-sessions-in-range": lambda grid: _query(
        grid, start=date(2023, 12, 30), end=date(2024, 1, 1)
    ),
}


@pytest.mark.parametrize("case", list(L0_CASES.values()), ids=list(L0_CASES))
def test_columnar_grid_load_matches_the_row_path_cell_by_cell(
    adapter: EquityDuckdbAdapter, case: Callable[[tuple[str, ...]], RawObservationQuery]
) -> None:
    query = case(_grid_fields(adapter))

    columnar = load_grid_raw_panel(adapter, query)

    _assert_same_panel(columnar, _reference(adapter, query))


def test_the_fixture_exercises_every_grid_cell_kind_and_lag(adapter: EquityDuckdbAdapter) -> None:
    """L0 사례가 덮는 범위의 확인 — 가림·0 채움·결측·미수집 셀과 랙 셀이 실제로 나온다."""
    panel = load_grid_raw_panel(adapter, _query(_grid_fields(adapter), history=3))
    kinds = {
        RAW_CELL_KINDS[code]
        for column in panel.fields
        for code in column.kind[column.present].tolist()
    }
    assert {
        CellKind.OBSERVED,
        CellKind.MASKED,
        CellKind.SOURCE_OMITTED_ZERO,
        CellKind.MISSING,
        CellKind.NOT_COLLECTED,
    } <= kinds
    credit = next(column for column in panel.fields if column.field_id == "credit.margin_balance")
    assert credit.present.any()  # 랙 3 셀이 실제로 나온다
    # 재상장 구간 첫 세션(036220:2, 2024-01-08)의 랙 1 셀은 이전 상장 구간 값을 물지 않는다
    relisted = panel.securities.index("036220:2")
    flow = next(column for column in panel.fields if column.field_id == "flow.foreign_net_buy")
    first_row = np.flatnonzero(panel.row_security == relisted)[0]
    assert panel.row_date[first_row] == date(2024, 1, 8).toordinal()
    assert not flow.present[first_row]
    # 달력 첫 세션부터 읽는 사례는 워밍업·랙 경고를 둘 다 낸다
    early = load_grid_raw_panel(adapter, L0_CASES["calendar-start"](_grid_fields(adapter)))
    assert len(early.warnings) == 2


def test_a_lag_reaching_the_previous_listing_reads_no_cell(
    adapter: EquityDuckdbAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """랙 5 이면 036220:2 첫 세션(2024-01-08)이 036220:1 의 마지막 세션(2023-12-29) 행에 닿는다.

    그 행은 격자에 있지만 `span_seq` 가 달라 셀이 없어야 한다(행 경로 `_cell`). 두 경로가 같은
    `_lags` 를 읽으므로 그것만 바꿔 대조한다.
    """
    monkeypatch.setattr(adapter, "_lags", lambda field_ids, _overrides: dict.fromkeys(field_ids, 5))
    query = _query(("price.close",), start=date(2023, 12, 26), universe="krx.all")

    columnar = load_grid_raw_panel(adapter, query)

    _assert_same_panel(columnar, _reference(adapter, query))
    relisted = columnar.securities.index("036220:2")
    first_row = np.flatnonzero(columnar.row_security == relisted)[0]
    assert columnar.row_date[first_row] == date(2024, 1, 8).toordinal()
    assert not columnar.fields[0].present[first_row]
    # 랙이 닿는 행(036220:1, 2023-12-29)은 격자에 있다 — 셀이 없는 이유는 span_seq 다
    earlier = columnar.securities.index("036220:1")
    reached = (columnar.row_security == earlier) & (
        columnar.row_date == date(2023, 12, 29).toordinal()
    )
    assert reached.sum() == 1


def test_a_query_with_a_latest_source_field_is_refused(adapter: EquityDuckdbAdapter) -> None:
    """LATEST 원천은 T2-03 범위다. 그 필드만 행 경로로 채우지 않고 질의 전체를 거절한다."""
    query = _query(("price.close", "financial.book_equity"))

    with pytest.raises(ColumnarSourceUnsupportedError) as caught:
        load_grid_raw_panel(adapter, query)

    message = str(caught.value)
    assert "T2-03" in message and "columnar_latest_unsupported" in message
    assert "financial.book_equity" in message and "price.close" not in message


def test_the_adapter_does_not_declare_the_columnar_capability_yet(
    adapter: EquityDuckdbAdapter,
) -> None:
    """능력 선언은 T2-03 이다. 지금 선언하면 application 이 LATEST 질의까지 열 경로로 보낸다."""
    assert not isinstance(adapter, ColumnarRawObservationPort)


def test_progress_is_monotonic_and_ends_at_one(adapter: EquityDuckdbAdapter) -> None:
    reported: list[float] = []
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    load_grid_raw_panel(
        adapter,
        _query(_grid_fields(adapter), history=3),
        checkpoint=checkpoint,
        progress=reported.append,
    )

    assert reported == sorted(reported) and reported[-1] == 1.0
    assert all(0.0 <= fraction <= 1.0 for fraction in reported)
    assert checkpoints > len(_grid_fields(adapter))


# --- 숫자 열 규칙(spec D3 원시 숫자 행) ---------------------------------------------------------


def _unscaled_values() -> list[int]:
    limit = FAST_UNSCALED_LIMIT
    boundary = [limit - 1, limit, limit + 1, -(limit - 1), -limit, -(limit + 1)]
    return [*boundary, 0, 1, -1, 123_456_789, 10**17 + 3, -(10**17 + 7)]


@pytest.mark.parametrize("scale", [0, 4, 10, 18, 22])
def test_decimal_values_are_bit_identical_to_float_of_decimal(scale: int) -> None:
    """|비스케일 정수| ≤ 2^53 은 ÷10^scale 한 번, 넘으면 `float(Decimal)` — 둘 다 행 경로와 같다."""
    duckdb = pytest.importorskip("duckdb", reason=DUCKDB_EXTRA)

    texts = [format(Decimal(value).scaleb(-scale), "f") for value in _unscaled_values()]
    texts.append("-0." + "0" * scale if scale else "-0")  # −0 은 DECIMAL 에서 0 이다
    con = duckdb.connect()
    try:
        source = (
            f"SELECT CAST(x AS DECIMAL(38, {scale})) AS v, n FROM (VALUES "
            + ", ".join(f"('{text}', {index})" for index, text in enumerate(texts))
            + ", (NULL, 99)) t(x, n)"
        )
        declared = str(con.execute(f"DESCRIBE {source}").fetchall()[0][1])
        column = numeric_column("v", declared, "test.value")
        fetched = con.execute(
            f"SELECT {', '.join(column.select('v'))}, n FROM ({source}) q ORDER BY n"
        ).fetchnumpy()
        expected = [row[0] for row in con.execute(f"{source} ORDER BY n").fetchall()]
    finally:
        con.close()

    has_value, values = column.decode(fetched, lambda: None)

    assert column.scale == scale and column.exact  # DECIMAL(38, s) 는 2^53 을 넘을 수 있다
    assert has_value.tolist() == [value is not None for value in expected]
    assert _float_bits(values[has_value]) == _float_bits(
        [float(value) for value in expected if value is not None]
    )
    fast = ~np.ma.getmaskarray(fetched["v_u"])
    beyond = [abs(value) > FAST_UNSCALED_LIMIT for value in _unscaled_values()]
    assert fast[: len(beyond)].tolist() == [not big for big in beyond]


@pytest.mark.parametrize(
    ("declared", "direct", "scale", "exact"),
    [
        ("DOUBLE", True, None, False),
        ("BIGINT", True, None, False),
        ("DECIMAL(9,0)", False, 0, False),
        ("DECIMAL(15,2)", False, 2, False),
        ("DECIMAL(16,0)", False, 0, True),
        ("DECIMAL(18,4)", False, 4, True),
        (f"DECIMAL(38,{FAST_SCALE_LIMIT})", False, FAST_SCALE_LIMIT, True),
        (f"DECIMAL(38,{FAST_SCALE_LIMIT + 1})", False, None, True),
        ("HUGEINT", False, None, True),
    ],
)
def test_each_declared_type_picks_its_reading_path(
    declared: str, direct: bool, scale: int | None, exact: bool
) -> None:
    column = numeric_column("v", declared, "test.value")

    assert (column.direct, column.scale, column.exact) == (direct, scale, exact)


def test_a_non_numeric_declared_type_is_refused() -> None:
    with pytest.raises(EquityDuckdbSetupError, match="price.close must be numeric"):
        numeric_column("v", "VARCHAR", "price.close")


@pytest.mark.parametrize(
    ("declared", "texts"),
    [
        ("DOUBLE", ["-0.0", "0.0", "1e300", "-2.5", "0.1"]),
        (f"DECIMAL(38,{FAST_SCALE_LIMIT + 4})", ["0.1", "-12.345678901234567890123456"]),
        ("HUGEINT", [str(2**64 + 1), str(-(2**60) - 3), "7"]),
    ],
)
def test_direct_and_exact_only_paths_match_the_row_path(declared: str, texts: list[str]) -> None:
    duckdb = pytest.importorskip("duckdb", reason=DUCKDB_EXTRA)

    con = duckdb.connect()
    try:
        source = (
            f"SELECT CAST(x AS {declared}) AS v, n FROM (VALUES "
            + ", ".join(f"('{text}', {index})" for index, text in enumerate(texts))
            + ") t(x, n)"
        )
        column = numeric_column("v", declared, "test.value")
        fetched = con.execute(
            f"SELECT {', '.join(column.select('v'))}, n FROM ({source}) q ORDER BY n"
        ).fetchnumpy()
        expected = [float(row[0]) for row in con.execute(f"{source} ORDER BY n").fetchall()]
    finally:
        con.close()

    has_value, values = column.decode(fetched, lambda: None)

    assert has_value.all()
    assert _float_bits(values) == _float_bits(expected)


# --- 취소 -------------------------------------------------------------------------------------


class _Cancelled(Exception):
    """테스트의 취소 예외."""


def _cancelled_once_the_query_runs() -> Callable[[], None]:
    """질의 전 호출 스레드의 checkpoint 는 지나가고, 질의 중 감시 스레드가 부르면 그때부터 취소다.

    취소 상태를 읽기만 하는 application 의 checkpoint 처럼, 한 번 취소되면 어느 스레드에서 불러도
    던진다(감시 스레드가 끊은 뒤 호출 스레드가 다시 불러 취소 예외로 올린다).
    """
    cancelled = Event()

    def checkpoint() -> None:
        if current_thread() is not main_thread():
            cancelled.set()
        if cancelled.is_set():
            raise _Cancelled

    return checkpoint


def test_both_columnar_queries_watch_the_callers_checkpoint(
    adapter: EquityDuckdbAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """선언 타입 확인(DESCRIBE)과 열 질의가 모두 감시 스레드 아래에서 돈다."""
    passed: list[Callable[[], None]] = []
    original = raw_panel_module._interruptible

    def recording(
        con: duckdb.DuckDBPyConnection, call: Callable[[], object], checkpoint: Callable[[], None]
    ) -> object:
        passed.append(checkpoint)
        return original(con, call, checkpoint)

    monkeypatch.setattr(raw_panel_module, "_interruptible", recording)

    def checkpoint() -> None:
        pass

    load_grid_raw_panel(adapter, _query(("price.close",)), checkpoint=checkpoint)

    assert passed == [checkpoint, checkpoint]


def test_a_cancelled_columnar_query_is_interrupted_into_the_callers_error(
    adapter: EquityDuckdbAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """열 질의(fetchnumpy) 도중 감시 스레드의 checkpoint 가 던지면 질의를 끊고 그 예외를 올린다."""
    import duckdb

    original = adapter._grid_sql

    def slow(
        grouped: dict[str, tuple[str, ...]],
        grid_sources: Sequence[str],
        fetch_start: date,
        fetch_end: date,
        *,
        tickers: tuple[str, ...] | None,
        predicate: str | None,
    ) -> _GridSql:
        grid = original(
            grouped, grid_sources, fetch_start, fetch_end, tickers=tickers, predicate=predicate
        )
        # 끊지 않으면 수십 초 걸리는 조인을 덧붙인다. 열 이름은 그대로라 투영은 바뀌지 않는다.
        heavy = (
            f"SELECT g.* FROM ({grid.sql}) g, (SELECT sum(a.range * b.range) AS s "
            "FROM range(0, 60000) a CROSS JOIN range(0, 60000) b) w WHERE w.s IS NOT NULL"
        )
        return _GridSql(heavy, grid.params, grid.layout)

    monkeypatch.setattr(adapter, "_grid_sql", slow)

    started = time.monotonic()
    with pytest.raises(_Cancelled) as caught:
        load_grid_raw_panel(
            adapter, _query(("price.close",)), checkpoint=_cancelled_once_the_query_runs()
        )

    assert time.monotonic() - started < 15
    assert isinstance(caught.value.__context__, duckdb.Error)


def test_interruptible_cuts_a_running_fetchnumpy() -> None:
    duckdb = pytest.importorskip("duckdb", reason=DUCKDB_EXTRA)

    con = duckdb.connect()
    checkpoint = _cancelled_once_the_query_runs()
    try:
        with pytest.raises(_Cancelled):
            _interruptible(
                con,
                lambda: con.execute(
                    "SELECT sum(a.range * b.range) FROM range(0, 60000) a "
                    "CROSS JOIN range(0, 60000) b"
                ).fetchnumpy(),
                checkpoint,
            )
    finally:
        con.close()
