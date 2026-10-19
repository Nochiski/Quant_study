"""duckdb 어댑터의 열 지향 원시 패널 로딩(GRID·LATEST 원천과 법인 축, columnar-tape T2-02·T2-03).

- L0 대조(spec 5절): 원장 손 픽스처에서 `load_raw_panel(q)` 와
  `RawPanel.from_observation_set(load_raw_observations(q), field_ids=q.field_ids)` 를
  field_id 기준으로 비교한다. 열 위치·코드가 아니라 풀어낸 값(present·kind·available·member·
  sector·previous_weight·값·메타)과 `observations()` 복원값을 대조 하네스의 비트 비교기
  (`tests/parity/_compare.py`, 허용오차 0)로 본다.
- LATEST(T2-03): 컷오프 이하 마지막 관측, 늦게 공개되는 필드(재무 TTM, #238), 법인 축 대응
  (`_corp_map`, 같은 법인의 종류주·법인 없는 ETF), 재상장, GRID 와 섞인 질의.
- DECIMAL 경계(spec D3 원시 숫자 행): 비스케일 정수 2^53 ± 1, scale 0·4·10·18·22, 음수·0·−0.
- 숫자가 아닌 선언 타입(범주 값): 행 경로처럼 값이 실리면 실패, 모두 NULL 이면 값 없는 셀.
- 취소: 열 질의 도중 checkpoint 가 던지면 감시 스레드가 질의를 끊는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import replace
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
    load_raw_panel,
    numeric_column,
)
from strategy_workbench.adapters.outbound.equity_duckdb._specs import (
    FIELD_BY_ID,
    SOURCE_BY_NAME,
    SourceAxis,
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
    RawObservationContractViolation,
    RawObservationQuery,
    RawPanel,
)
from strategy_workbench.domain.equity.facade.research_data import CellKind
from tests.equity_fixture import (
    WB_FIN_ROWS,
    build_workbench_root,
    fin_std_table,
    write_catalog,
    write_equity_table,
)
from tests.parity._compare import compare, format_report

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


@pytest.fixture(scope="module")
def late_adapter(tmp_path_factory: pytest.TempPathFactory) -> EquityDuckdbAdapter:
    """#238 모양 — 000660 의 TTM 창 안 분기 정정본이 행보다 늦게 접수돼 흐름 필드가 늦게 보인다.

    `test_adapters_equity_duckdb.py::test_a_late_correction_inside_the_ttm_window_...` 와
    같은 행이다.
    """
    pytest.importorskip("duckdb", reason=DUCKDB_EXTRA)
    root = build_workbench_root(tmp_path_factory.mktemp("columnar-late") / "equity")
    fiscal_2022 = [
        {
            "corp_code": "C00660",
            "period_end": period_end,
            "report_code": report,
            "bsns_year": "2022",
            "rcept_no": f"R00660{report}_22",
            "available_date": available,
            "revenue": revenue,
            "net_income": net_income,
            "cf_operating_ytd": cf_ytd,
        }
        for period_end, report, available, revenue, net_income, cf_ytd in (
            (date(2022, 3, 31), "11013", date(2024, 1, 10), 150, 30, 20),
            (date(2022, 6, 30), "11012", date(2022, 8, 16), 160, 32, 45),
            (date(2022, 9, 30), "11014", date(2022, 11, 14), 170, 34, 70),
            (date(2022, 12, 31), "11011", date(2023, 3, 14), 660, 136, 100),
        )
    ]
    write_equity_table(
        root,
        "fin_std",
        fin_std_table([*WB_FIN_ROWS, *fiscal_2022]),
        build_id="b_fin_238",
        year_column="period_end",
    )
    write_catalog(root)
    return EquityDuckdbAdapter(root)


def _fields(adapter: EquityDuckdbAdapter, mode: SourceMode | None = None) -> tuple[str, ...]:
    return tuple(
        profile.field_id
        for profile in adapter.list_fields()
        if mode is None or SOURCE_BY_NAME[FIELD_BY_ID[profile.field_id].source].mode is mode
    )


def _corp_fields(adapter: EquityDuckdbAdapter) -> tuple[str, ...]:
    return tuple(
        field_id
        for field_id in _fields(adapter, SourceMode.LATEST)
        if SOURCE_BY_NAME[FIELD_BY_ID[field_id].source].axis is SourceAxis.CORP
    )


def _field(panel: RawPanel, field_id: str) -> RawFieldColumn:
    return next(column for column in panel.fields if column.field_id == field_id)


def _row(panel: RawPanel, session: date, security_id: str) -> int:
    hits = np.flatnonzero(
        (panel.row_date == session.toordinal())
        & (panel.row_security == panel.securities.index(security_id))
    )
    assert hits.size == 1, (session, security_id)
    return int(hits[0])


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
            panel.previous_weight.tolist(),
            strict=True,
        )
    )


def _cells(column: RawFieldColumn) -> list[tuple[object, ...] | None]:
    """한 필드의 칸을 풀어낸 값으로(공개일·종류·값). 셀이 없는 칸은 None 이다."""
    result: list[tuple[object, ...] | None] = []
    for row in range(column.present.shape[0]):
        if not column.present[row]:
            result.append(None)
            continue
        value: float | str | None = None
        if column.has_value[row]:
            code = int(column.text[row])
            value = column.categories[code] if code >= 0 else float(column.value[row])
        available = date.fromordinal(int(column.available[row]))
        result.append((available, RAW_CELL_KINDS[int(column.kind[row])], value))
    return result


def _projection(panel: RawPanel) -> dict[str, object]:
    """L0 대조 단위 — field_id 로 묶어 열 위치·사전 코드에 기대지 않는다."""
    meta = ("status", "data_snapshot_id", "sessions", "history_sessions", "detail", "warnings")
    return {
        "meta": {name: getattr(panel, name) for name in meta},
        "rows": _rows(panel),
        "fields": {column.field_id: _cells(column) for column in panel.fields},
        "categories": {column.field_id: column.categories for column in panel.fields},
        "observations": list(panel.observations()),
    }


def _assert_same_panel(columnar: RawPanel, reference: RawPanel) -> None:
    """L0: field_id 기준으로 모든 칸과 메타가 같다(허용오차 0, spec D1)."""
    report = compare("L0", [("raw", _projection(reference), _projection(columnar))])
    assert report.ok, format_report(report, values=True)


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


L0_CASES: dict[str, Callable[[EquityDuckdbAdapter], RawObservationQuery]] = {
    "grid-fields-with-history": lambda a: _query(_fields(a, SourceMode.GRID), history=3),
    "all-fields-with-history": lambda a: _query(_fields(a), history=3),
    "latest-fields-only": lambda a: _query(_fields(a, SourceMode.LATEST), history=2),
    # 법인 축 — 005930·005935 는 같은 법인, 069500(ETF)은 법인이 없다, 035420 은 관측이 없다
    "corp-axis-all-securities": lambda a: _query(_corp_fields(a), universe="krx.all", history=5),
    # 달력 첫 세션부터 — 워밍업·랙을 거슬러 올라갈 달력이 모자란 경고 둘이 난다
    "calendar-start": lambda a: _query(
        _fields(a), start=date(2023, 12, 26), end=date(2024, 1, 3), history=2
    ),
    "reversed-field-order": lambda a: _query(tuple(reversed(_fields(a))), history=1),
    # 랙 3 신용잔고 — 036220 재상장(2024-01-08) 구간은 이전 상장 값을 물지 않는다. 재무는
    # 법인 축이라 재상장 구간에도 같은 법인의 값이 실린다
    "lag-across-relisting": lambda _a: _query(
        (
            "credit.margin_balance",
            "financial.net_income",
            "price.close",
            "financial.revenue",
            "flow.foreign_net_buy",
        ),
        start=date(2024, 1, 4),
        universe="krx.all",
    ),
    # GRID 와 LATEST 가 섞이고 순서가 번갈아 든다
    "grid-and-latest-mixed": lambda _a: _query(
        (
            "consensus.forward_eps",
            "price.close",
            "financial.book_equity",
            "short.short_sale_volume",
            "event.buyback_amount",
            "event.dividend_per_share",
            "credit.margin_balance",
            "consensus.recommendation",
        ),
        history=4,
    ),
    "all-securities": lambda a: _query(_fields(a), universe="krx.all", history=2),
    # 창 끝이 모든 LATEST 관측의 공개일보다 앞이다 — 축 키는 있는데 관측이 하나도 없다
    "latest-before-any-observation": lambda _a: _query(
        ("event.insider_net_buy", "event.buyback_amount", "price.close"),
        start=date(2023, 12, 26),
        end=date(2023, 12, 27),
        universe="krx.all",
    ),
    "no-fields": lambda _a: _query((), history=1),
    "no-members": lambda a: _query(_fields(a), universe="krx.none"),
    "unknown-market": lambda a: _query(_fields(a), market="NYSE"),
    "unknown-universe": lambda a: _query(_fields(a), universe="krx.unknown"),
    "unknown-field": lambda _a: _query(("financial.revenue", "price.unknown")),
    "outside-coverage": lambda a: _query(_fields(a), start=date(2023, 1, 2), end=date(2023, 1, 6)),
    "no-sessions-in-range": lambda a: _query(
        _fields(a), start=date(2023, 12, 30), end=date(2024, 1, 1)
    ),
}


@pytest.mark.parametrize("case", list(L0_CASES.values()), ids=list(L0_CASES))
def test_columnar_load_matches_the_row_path_cell_by_cell(
    adapter: EquityDuckdbAdapter, case: Callable[[EquityDuckdbAdapter], RawObservationQuery]
) -> None:
    query = case(adapter)

    columnar = load_raw_panel(adapter, query)

    _assert_same_panel(columnar, _reference(adapter, query))


def test_the_fixture_exercises_every_grid_cell_kind_and_lag(adapter: EquityDuckdbAdapter) -> None:
    """L0 사례가 덮는 범위의 확인 — 가림·0 채움·결측·미수집 셀과 랙 셀이 실제로 나온다."""
    panel = load_raw_panel(adapter, _query(_fields(adapter, SourceMode.GRID), history=3))
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
    assert _field(panel, "credit.margin_balance").present.any()  # 랙 3 셀이 실제로 나온다
    # 재상장 구간 첫 세션(036220:2, 2024-01-08)의 랙 1 셀은 이전 상장 구간 값을 물지 않는다
    first_row = _row(panel, date(2024, 1, 8), "036220:2")
    assert not _field(panel, "flow.foreign_net_buy").present[first_row]
    # 달력 첫 세션부터 읽는 사례는 워밍업·랙 경고를 둘 다 낸다
    early = load_raw_panel(adapter, L0_CASES["calendar-start"](adapter))
    assert len(early.warnings) == 2


def test_the_fixture_exercises_latest_cutoffs_and_the_corp_axis(
    adapter: EquityDuckdbAdapter,
) -> None:
    """LATEST 사례가 덮는 범위 — 컷오프 전·후, 같은 법인의 종류주, 법인 없는 종목, 값 없는 관측."""
    panel = load_raw_panel(adapter, L0_CASES["corp-axis-all-securities"](adapter))
    equity = _field(panel, "financial.book_equity")
    # 005930 FY 행은 01-04 공개, 랙 1 이라 01-05 부터 615 다. 01-04 에는 3분기 행(610)이 보인다
    assert equity.value[_row(panel, date(2024, 1, 4), "005930:1")] == 610.0
    assert equity.value[_row(panel, date(2024, 1, 5), "005930:1")] == 615.0
    # 우선주 005935 는 같은 법인이라 같은 셀을 받고, ETF 069500 은 법인이 없어 셀이 없다
    for session in (date(2024, 1, 5), END):
        common, preferred = _row(panel, session, "005930:1"), _row(panel, session, "005935:1")
        assert equity.present[preferred] and equity.value[preferred] == equity.value[common]
        assert not equity.present[_row(panel, session, "069500:1")]
    # 035420 은 법인 대응은 있으나 관측이 없다
    assert not equity.present[_row(panel, END, "035420:1")]
    # 036220 은 01-09 공개 행 하나뿐이다 — 잔액(자본)은 OBSERVED, TTM 매출은 네 분기가 없어 MISSING
    relisted = _row(panel, END, "036220:2")
    revenue = _field(panel, "financial.revenue")
    assert RAW_CELL_KINDS[int(revenue.kind[relisted])] is CellKind.MISSING
    assert RAW_CELL_KINDS[int(equity.kind[relisted])] is CellKind.OBSERVED
    assert not revenue.present[_row(panel, date(2024, 1, 9), "036220:2")]  # 랙 1 컷오프 전


def test_a_late_ttm_field_is_missing_until_its_own_filing(
    late_adapter: EquityDuckdbAdapter,
) -> None:
    """#238: 늦게 공개되는 흐름 필드는 그 공개일까지 값 없는 MISSING, 공개일은 행의 것이다."""
    fields = (
        "financial.revenue",
        "financial.net_income",
        "financial.operating_cash_flow",
        "financial.book_equity",
    )
    query = _query(fields, start=date(2024, 1, 9), universe="krx.all", history=2)

    columnar = load_raw_panel(late_adapter, query)

    _assert_same_panel(columnar, _reference(late_adapter, query))
    revenue = _field(columnar, "financial.revenue")
    cash_flow = _field(columnar, "financial.operating_cash_flow")
    row = _row(columnar, date(2024, 1, 10), "000660:1")
    assert RAW_CELL_KINDS[int(revenue.kind[row])] is CellKind.MISSING
    assert not revenue.has_value[row]
    assert revenue.available[row] == date(2023, 8, 14).toordinal()  # 행의 공개일
    assert cash_flow.has_value[row] and cash_flow.available[row] == date(2024, 1, 9).toordinal()
    later = _row(columnar, END, "000660:1")
    assert revenue.has_value[later] and revenue.available[later] == date(2024, 1, 10).toordinal()


def test_a_lag_reaching_the_previous_listing_reads_no_cell(
    adapter: EquityDuckdbAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """랙 5 이면 036220:2 첫 세션(2024-01-08)이 036220:1 의 마지막 세션(2023-12-29) 행에 닿는다.

    그 행은 격자에 있지만 `span_seq` 가 달라 GRID 셀이 없어야 한다(행 경로 `_cell`). LATEST 는
    축 키 열이라 상장 구간과 무관하고, 컷오프가 달력 앞이면 셀이 없다. 두 경로가 같은 `_lags` 를
    읽으므로 그것만 바꿔 대조한다.
    """
    monkeypatch.setattr(adapter, "_lags", lambda field_ids, _overrides: dict.fromkeys(field_ids, 5))
    query = _query(
        ("price.close", "financial.net_income", "consensus.forward_eps"),
        start=date(2023, 12, 26),
        universe="krx.all",
        history=0,
    )

    columnar = load_raw_panel(adapter, query)

    _assert_same_panel(columnar, _reference(adapter, query))
    first_row = _row(columnar, date(2024, 1, 8), "036220:2")
    assert not _field(columnar, "price.close").present[first_row]
    # 달력 첫 세션의 컷오프는 달력 앞이라 LATEST 셀도 없다
    calendar_start = _row(columnar, date(2023, 12, 26), "005930:1")
    assert not _field(columnar, "financial.net_income").present[calendar_start]


@pytest.mark.parametrize(
    "fields",
    [
        ("price.close", "price.volume", "price.close"),
        ("credit.margin_balance",) * 2,
        ("financial.revenue", "price.close", "financial.revenue"),
    ],
    ids=["repeated-after-another", "repeated-lagged", "repeated-latest"],
)
def test_a_repeated_query_field_fails_like_the_row_path(
    adapter: EquityDuckdbAdapter, fields: tuple[str, ...]
) -> None:
    """질의에 같은 필드가 두 번 있으면 두 경로가 같은 계약 위반 문장으로 끝난다(#576 리뷰 P3)."""
    query = _query(fields, history=3)
    with pytest.raises(RawObservationContractViolation) as expected:
        adapter.load_raw_observations(query)

    with pytest.raises(RawObservationContractViolation) as actual:
        load_raw_panel(adapter, query)

    assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize(
    ("field_id", "expr"),
    [("price.volume", "CAST('x' AS VARCHAR)"), ("consensus.recommendation", "'buy'")],
    ids=["grid", "latest"],
)
def test_a_categorical_value_fails_like_the_row_path(
    adapter: EquityDuckdbAdapter, monkeypatch: pytest.MonkeyPatch, field_id: str, expr: str
) -> None:
    """duckdb 필드 계약에는 범주 필드가 없다. 문자열 값이 실리면 두 경로 모두 설정 오류다."""
    monkeypatch.setitem(adapter._fields, field_id, replace(adapter._fields[field_id], expr=expr))
    query = _query(("price.close", field_id), history=1)
    with pytest.raises(EquityDuckdbSetupError, match=f"{field_id} must be numeric"):
        adapter.load_raw_observations(query)

    with pytest.raises(EquityDuckdbSetupError, match=f"{field_id} must be numeric"):
        load_raw_panel(adapter, query)


@pytest.mark.parametrize(
    "field_id", ["price.volume", "consensus.recommendation"], ids=["grid", "latest"]
)
def test_an_all_null_categorical_column_is_cells_without_values(
    adapter: EquityDuckdbAdapter, monkeypatch: pytest.MonkeyPatch, field_id: str
) -> None:
    """숫자가 아닌 선언 타입이라도 값이 모두 NULL 이면 행 경로처럼 값 없는 셀이다(text -1)."""
    expr = "CAST(NULL AS VARCHAR)"
    monkeypatch.setitem(adapter._fields, field_id, replace(adapter._fields[field_id], expr=expr))
    query = _query(("price.close", field_id), history=1)

    columnar = load_raw_panel(adapter, query)

    _assert_same_panel(columnar, _reference(adapter, query))
    column = _field(columnar, field_id)
    assert column.present.any() and not column.has_value.any()
    assert (column.text == -1).all() and column.categories == ()


def test_the_adapter_declares_the_columnar_capability(adapter: EquityDuckdbAdapter) -> None:
    """능력 선언(T2-03). 운영 파이프라인은 아직 이 능력을 고르지 않는다(T4-01)."""
    query = _query(_fields(adapter), history=2)

    assert isinstance(adapter, ColumnarRawObservationPort)
    _assert_same_panel(
        adapter.load_raw_panel(query, checkpoint=lambda: None, progress=lambda _f: None),
        _reference(adapter, query),
    )


def test_progress_is_monotonic_and_ends_at_one(adapter: EquityDuckdbAdapter) -> None:
    reported: list[float] = []
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    load_raw_panel(
        adapter,
        _query(_fields(adapter), history=3),
        checkpoint=checkpoint,
        progress=reported.append,
    )

    assert reported == sorted(reported) and reported[-1] == 1.0
    assert all(0.0 <= fraction <= 1.0 for fraction in reported)
    assert checkpoints > len(_fields(adapter))


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


def test_a_non_numeric_declared_type_fails_only_when_a_value_is_carried() -> None:
    """행 경로 `_as_float` 는 값이 실린 칸에서만 실패한다 — 열 경로도 값 유무만 읽어 같이 간다."""
    duckdb = pytest.importorskip("duckdb", reason=DUCKDB_EXTRA)
    column = numeric_column("v", "VARCHAR", "price.close")
    con = duckdb.connect()
    try:
        carried = con.execute(
            f"SELECT {', '.join(column.select('v'))} FROM (VALUES ('a'), (NULL)) t(v)"
        ).fetchnumpy()
        empty = con.execute(
            f"SELECT {', '.join(column.select('v'))} FROM (VALUES (NULL::VARCHAR)) t(v)"
        ).fetchnumpy()
    finally:
        con.close()

    with pytest.raises(EquityDuckdbSetupError, match="price.close must be numeric"):
        column.decode(carried, lambda: None)
    has_value, values = column.decode(empty, lambda: None)
    assert not has_value.any() and values.tolist() == [0.0]


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


@pytest.mark.parametrize(
    ("fields", "queries"),
    [
        (("price.close",), 2),  # 격자 DESCRIBE·열 질의
        (("price.close", "financial.book_equity"), 4),  # + LATEST DESCRIBE·열 질의
    ],
    ids=["grid", "grid-and-latest"],
)
def test_every_columnar_query_watches_the_callers_checkpoint(
    adapter: EquityDuckdbAdapter,
    monkeypatch: pytest.MonkeyPatch,
    fields: tuple[str, ...],
    queries: int,
) -> None:
    """선언 타입 확인(DESCRIBE)과 열 질의가 모두 감시 스레드 아래에서 돈다.

    법인 대응(`_corp_map`)은 행 경로의 `_fetchall` 을 그대로 쓰므로 이 목록 밖이다.
    """
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

    load_raw_panel(adapter, _query(fields), checkpoint=checkpoint)

    assert passed == [checkpoint] * queries


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
        load_raw_panel(
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
