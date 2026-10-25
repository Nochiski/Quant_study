"""열 원시 관측 포트(`RawPanel`, columnar-tape T2-01).

- 왕복: 객체 관측 → `from_observation_set` → `observations()` 가 dataclass `==` 와 float
  비트(±0)까지 원본과 같다. mock 어댑터, 원장 손 픽스처(duckdb), 손으로 만든 경계 사례를 쓴다.
  열 순서는 질의 field_ids 순서다(원시 계약의 순서 조항).
- 검증 동등성: 계약 위반 사례마다 열 검증과 객체 검증(`RawFieldValue` 생성 → `RawObservationSet`
  생성)이 같은 예외 타입·메시지를 낸다. 열 검증이 규칙을 두 벌로 갖는 대조 기간의 안전망이다(T7-03
  에서 한 벌로 줄인다).
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.portfolio_design import _service as portfolio_module
from strategy_workbench.application.portfolio_design.facade.design import (
    LookAheadViolationError,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawFieldValue,
    RawObservation,
    RawObservationContractViolation,
    RawObservationQuery,
    RawObservationSet,
    RawPanel,
)
from strategy_workbench.application.portfolio_design.ports.outgoing import (
    raw_panel as raw_panel_module,
)
from strategy_workbench.domain.equity.facade.research_data import CellKind, DataLoadStatus

D0, D1, D2, D3 = date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10)
OBSERVED, MISSING, MASKED = CellKind.OBSERVED, CellKind.MISSING, CellKind.MASKED


def _bits(observations: tuple[RawObservation, ...]) -> list[object]:
    """값 타입과 float 비트(`float.hex` 는 -0.0 과 0.0 을 가른다)를 칸마다 펼친다."""

    def cell(value: object) -> object:
        return (type(value).__name__, value.hex() if isinstance(value, float) else value)

    return [
        (
            item.as_of,
            item.security_id,
            cell(item.universe_member),
            item.sector_id,
            cell(item.previous_weight),
            [
                (field.field_id, cell(field.value), field.available_date, field.kind)
                for field in item.fields
            ],
        )
        for item in observations
    ]


def _query_order(raw: RawObservationSet) -> tuple[str, ...]:
    """관측에 나온 field_id 를 처음 나온 순서로. 손으로 만든 사례의 질의 순서로 쓴다."""
    return tuple(
        dict.fromkeys(field.field_id for item in raw.observations for field in item.fields)
    )


def _assert_round_trip(raw: RawObservationSet, field_ids: tuple[str, ...]) -> RawPanel:
    panel = RawPanel.from_observation_set(raw, field_ids=field_ids)
    restored = panel.observations()
    assert restored == raw.observations
    assert _bits(restored) == _bits(raw.observations)
    assert (panel.status, panel.data_snapshot_id, panel.detail, panel.warnings) == (
        raw.status,
        raw.data_snapshot_id,
        raw.detail,
        raw.warnings,
    )
    assert (panel.sessions, panel.history_sessions) == (raw.sessions, raw.history_sessions)
    panel.validate_contract()
    return panel


def _query(
    adapter: MockEquityDataAdapter, start: date, end: date, history: int
) -> RawObservationQuery:
    fields = tuple(profile.field_id for profile in adapter.list_fields())
    return RawObservationQuery("KRX", "krx.common-stock", start, end, fields, history)


@pytest.mark.parametrize(
    ("start", "end", "history"),
    [
        pytest.param(date(2024, 1, 8), date(2024, 1, 12), 3, id="fixture-calendar"),
        pytest.param(date(2025, 3, 3), date(2025, 3, 14), 5, id="synthetic-period"),
    ],
)
def test_mock_raw_observations_round_trip_bit_for_bit(start: date, end: date, history: int) -> None:
    mock = MockEquityDataAdapter.demo()
    query = _query(mock, start, end, history)
    raw = mock.load_raw_observations(query)
    assert raw.ok and raw.observations

    panel = _assert_round_trip(raw, query.field_ids)

    rows = [len(raw.observations) - 1, 0, 2]
    assert panel.observations(rows) == tuple(raw.observations[row] for row in rows)


def test_ledger_fixture_raw_observations_round_trip_bit_for_bit(tmp_path: Path) -> None:
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    adapter = EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity"))
    fields = tuple(profile.field_id for profile in adapter.list_fields())
    query = RawObservationQuery(
        "KRX", "krx.common-stock", date(2024, 1, 8), date(2024, 1, 12), fields, 3
    )
    raw = adapter.load_raw_observations(query)
    assert raw.ok and raw.observations
    kinds = {field.kind for item in raw.observations for field in item.fields}
    assert {CellKind.MASKED, CellKind.SOURCE_OMITTED_ZERO, CellKind.MISSING} <= kinds

    _assert_round_trip(raw, query.field_ids)


def test_round_trip_keeps_signed_zero_text_none_and_omission() -> None:
    rows = (
        # 앞 행에는 B 만 있고 뒤 행에 A, B 가 온다. 열 순서는 첫 등장이 아니라 질의 순서다.
        RawObservation(D1, "a", True, (RawFieldValue("b", -0.0, D0),), "s1", -0.0),
        RawObservation(
            D1,
            "b",
            False,
            (
                RawFieldValue("a", 0.0, D1),
                RawFieldValue("b", "x", D0),
                RawFieldValue("c", None, D1, MISSING),
            ),
            None,
            0.25,
        ),
        RawObservation(D2, "a", True, (RawFieldValue("c", None, D1, MASKED),), "s2", 0.0),
        RawObservation(D2, "b", True, (), "s1"),
    )
    raw = RawObservationSet(DataLoadStatus.OK, "snap", (D1, D2), (D0,), rows, warnings=("w",))

    panel = _assert_round_trip(raw, ("a", "b", "c", "never"))

    assert [column.field_id for column in panel.fields] == ["a", "b", "c", "never"]
    never = panel.fields[3]
    assert not never.present.any() and never.present.shape == (len(rows),)


def test_round_trip_of_a_failure_value() -> None:
    raw = RawObservationSet(DataLoadStatus.NO_DATA, "snap", (), (), (), detail="no rows")

    panel = _assert_round_trip(raw, ("p", "q"))

    assert not panel.ok
    assert [column.field_id for column in panel.fields] == ["p", "q"]


@pytest.mark.parametrize(
    ("fields", "field_ids"),
    [
        pytest.param(("b", "a"), ("a", "b"), id="out-of-query-order"),
        pytest.param(("a", "z"), ("a", "b"), id="field-outside-the-query"),
    ],
)
def test_fields_must_be_a_subsequence_of_the_query_field_ids(
    fields: tuple[str, ...], field_ids: tuple[str, ...]
) -> None:
    """원시 계약: 관측의 fields 는 질의 field_ids 순서의 부분열이다. 어기면 계약 위반이다."""
    rows = (
        RawObservation(D1, "a", True, (RawFieldValue("a", 1.0, D1),)),
        RawObservation(D1, "b", True, tuple(RawFieldValue(f, 1.0, D1) for f in fields)),
    )
    raw = RawObservationSet(DataLoadStatus.OK, "snap", (D1,), (), rows)

    with pytest.raises(
        RawObservationContractViolation, match="must follow the query field_ids order"
    ) as caught:
        RawPanel.from_observation_set(raw, field_ids=field_ids)

    assert "security_id='b'" in str(caught.value)


# --- 검증 동등성 -------------------------------------------------------------------------------


def _field(
    field_id: str, value: object, available: date = D1, kind: CellKind = OBSERVED
) -> RawFieldValue:
    """생성 시점 검사를 건너뛴 셀. 객체 경로는 `_object_error` 가 생성자로 다시 만든다."""
    cell = RawFieldValue("placeholder", 1.0, available)
    object.__setattr__(cell, "field_id", field_id)
    object.__setattr__(cell, "value", value)
    object.__setattr__(cell, "kind", kind)
    return cell


def _row(
    as_of: date, security_id: str, *fields: RawFieldValue, weight: float = 0.0
) -> RawObservation:
    return RawObservation(as_of, security_id, True, fields, previous_weight=weight)


def _unchecked(
    rows: tuple[RawObservation, ...],
    sessions: tuple[date, ...] = (D1, D2),
    history: tuple[date, ...] = (D0,),
) -> RawObservationSet:
    raw = object.__new__(RawObservationSet)
    for name, value in {
        "status": DataLoadStatus.OK,
        "data_snapshot_id": "snap",
        "sessions": sessions,
        "history_sessions": history,
        "observations": rows,
        "detail": None,
        "warnings": (),
    }.items():
        object.__setattr__(raw, name, value)
    return raw


def _object_error(raw: RawObservationSet) -> RawObservationContractViolation:
    """객체 경로: 셀을 (행, fields 순서)로 생성자에 다시 넣고, 집합을 생성자로 만든다."""
    with pytest.raises(RawObservationContractViolation) as caught:
        RawObservationSet(
            raw.status,
            raw.data_snapshot_id,
            raw.sessions,
            raw.history_sessions,
            tuple(
                replace(
                    item,
                    fields=tuple(
                        RawFieldValue(f.field_id, f.value, f.available_date, f.kind)
                        for f in item.fields
                    ),
                )
                for item in raw.observations
            ),
        )
    return caught.value


def _panel_error(raw: RawObservationSet) -> RawObservationContractViolation:
    with pytest.raises(RawObservationContractViolation) as caught:
        RawPanel.from_observation_set(raw, field_ids=_query_order(raw)).validate_contract()
    return caught.value


NAN, INF = float("nan"), float("inf")
CASES = {
    "unordered-dates": _unchecked((_row(D2, "a"), _row(D1, "a"))),
    "unordered-securities": _unchecked((_row(D1, "b"), _row(D1, "a"))),
    "duplicate-key": _unchecked((_row(D1, "a"), _row(D1, "a"))),
    "duplicate-key-at-chunk-edge": _unchecked((_row(D1, "a"), _row(D1, "b"), _row(D1, "b"))),
    "undeclared-dates": _unchecked((_row(D1, "a"), _row(D3, "a"), _row(D3, "b")), sessions=(D1,)),
    "undeclared-in-two-chunks": _unchecked(
        (_row(D0, "a"), _row(D1, "a"), _row(D2, "a"), _row(D3, "a")), sessions=(D1,), history=()
    ),
    "sessions-descend": _unchecked((), sessions=(D2, D1)),
    "sessions-repeat": _unchecked((), sessions=(D1, D1)),
    "history-descend": _unchecked((), history=(D0, date(2024, 1, 4)), sessions=(D1,)),
    "history-overlaps-range": _unchecked((), history=(D1,), sessions=(D1, D2)),
    "previous-weight-nan": _unchecked((_row(D1, "a"), _row(D1, "b", weight=NAN))),
    "previous-weight-inf": _unchecked((_row(D1, "a", weight=-INF),)),
    "previous-weight-before-fields": _unchecked((_row(D1, "a", _field("p", NAN), weight=INF),)),
    "value-nan-second-field": _unchecked(
        (_row(D1, "a", _field("p", 1.0), _field("q", NAN)), _row(D1, "b", _field("p", INF)))
    ),
    "value-inf-later-row": _unchecked(
        (_row(D1, "a", _field("p", 1.0)), _row(D1, "b"), _row(D2, "a", _field("p", -INF)))
    ),
    "observed-without-value": _unchecked((_row(D1, "a", _field("p", 1.0), _field("q", None)),)),
    "omitted-zero-without-value": _unchecked(
        (_row(D1, "a", _field("p", None, kind=CellKind.SOURCE_OMITTED_ZERO)),)
    ),
    "missing-with-number": _unchecked(
        (_row(D1, "a"), _row(D1, "b", _field("p", 2.5, kind=MISSING)))
    ),
    "masked-with-text": _unchecked((_row(D1, "a", _field("p", "x", kind=MASKED)),)),
    "blank-field-id": _unchecked(
        (_row(D1, "a", _field("p", 1.0)), _row(D1, "b", _field(" \t", 1.0)))
    ),
    "kind-mismatch-before-ordering": _unchecked((_row(D2, "a"), _row(D1, "a", _field("p", None)))),
    "ordering-before-non-finite": _unchecked((_row(D2, "a", _field("p", NAN)), _row(D1, "a"))),
    "bool-value": _unchecked((_row(D1, "a", _field("p", 1.0)), _row(D1, "b", _field("p", True)))),
    "bool-false-value": _unchecked((_row(D1, "a", _field("p", False)),)),
    "int-value": _unchecked((_row(D1, "a", _field("p", 1.0)), _row(D1, "b", _field("p", 3)))),
    "zero-int-value": _unchecked((_row(D1, "a", _field("p", 0)),)),
    "duplicate-field-id": _unchecked((_row(D1, "a", _field("p", 1.0), _field("p", 2.0)),)),
}


@pytest.fixture(params=["default-chunk", "two-row-chunk"])
def chunk_rows(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """두 행 청크로도 돌려 청크 경계를 넘는 비교·첫 위반 선택을 확인한다."""
    if request.param == "two-row-chunk":
        monkeypatch.setattr(raw_panel_module, "_CHUNK_ROWS", 2)


@pytest.mark.usefixtures("chunk_rows")
@pytest.mark.parametrize("raw", list(CASES.values()), ids=list(CASES))
def test_columnar_validation_matches_object_validation(raw: RawObservationSet) -> None:
    expected = _object_error(raw)

    actual = _panel_error(raw)

    assert type(actual) is type(expected)
    assert str(actual) == str(expected)


def test_bool_value_message_names_field_security_and_date() -> None:
    raw = CASES["bool-value"]

    message = str(_panel_error(raw))

    assert "field_id='p'" in message and "security_id='b'" in message
    assert f"as_of={D1}" in message and "value=True" in message


def test_int_value_message_has_the_bool_message_format() -> None:
    """P3-2: int 도 bool 과 같은 문장으로 거절한다(값 타입은 정확히 float·str·None)."""
    message = str(_panel_error(CASES["int-value"]))

    assert message.startswith("raw field value must be float, str or None — ")
    assert "field_id='p'" in message and "security_id='b'" in message and "value=3" in message


def _service_look_ahead(raw: RawObservationSet) -> str | None:
    try:
        for item in raw.observations:
            portfolio_module._to_factor_observation(item, checkpoint=lambda: None)
    except LookAheadViolationError as error:
        return str(error)
    return None


@pytest.mark.usefixtures("chunk_rows")
@pytest.mark.parametrize(
    "rows",
    [
        pytest.param((_row(D1, "a", _field("p", 1.0, D0)),), id="no-violation"),
        pytest.param(
            (
                _row(D1, "a", _field("p", 1.0, D1)),
                _row(D1, "b", _field("p", 1.0, D1), _field("q", 1.0, D2)),
                _row(D2, "a", _field("p", 1.0, D3)),
            ),
            id="second-field-of-second-row",
        ),
        pytest.param(
            (_row(D1, "a"), _row(D1, "b"), _row(D2, "a", _field("q", None, D3, MISSING))),
            id="missing-cell-in-later-chunk",
        ),
    ],
)
def test_look_ahead_detail_matches_the_consumer_boundary(rows: tuple[RawObservation, ...]) -> None:
    raw = RawObservationSet(DataLoadStatus.OK, "snap", (D1, D2), (D0,), rows)
    panel = RawPanel.from_observation_set(raw, field_ids=("p", "q"))

    position = panel.first_look_ahead()

    if position is None:
        assert _service_look_ahead(raw) is None
        return
    # 위치만 열이 주고 문장은 서비스가 만든다(P3-4: 문장의 owner 는 하나).
    row = panel.observations([position.row])[0]
    column = panel.fields[position.field]
    error = portfolio_module._look_ahead_violation(
        field_id=column.field_id,
        security_id=row.security_id,
        as_of=row.as_of,
        available_date=date.fromordinal(int(column.available[position.row])),
    )
    assert isinstance(error, LookAheadViolationError)
    assert str(error) == _service_look_ahead(raw)


def test_validation_checkpoints_and_reports_monotonic_progress() -> None:
    mock = MockEquityDataAdapter.demo()
    query = _query(mock, date(2024, 1, 8), date(2024, 1, 12), 3)
    raw = mock.load_raw_observations(query)
    panel = RawPanel.from_observation_set(raw, field_ids=query.field_ids)
    checkpoints = 0
    fractions: list[float] = []

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    panel.validate_contract(checkpoint=checkpoint, progress=fractions.append)

    assert checkpoints > 1
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert all(0.0 <= fraction <= 1.0 and not math.isnan(fraction) for fraction in fractions)


def test_validation_stops_at_a_cancelling_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_panel_module, "_CHUNK_ROWS", 2)
    raw = RawObservationSet(
        DataLoadStatus.OK, "snap", (D1,), (), tuple(_row(D1, f"s{index:02d}") for index in range(8))
    )
    panel = RawPanel.from_observation_set(raw, field_ids=())
    calls = 0

    def checkpoint() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("cancelled during columnar raw validation")

    with pytest.raises(RuntimeError, match="cancelled during columnar raw validation"):
        panel.validate_contract(checkpoint=checkpoint)


# --- 생성 시 구조 검사(P3-3) ------------------------------------------------------------------


def _one_cell_panel() -> RawPanel:
    raw = RawObservationSet(
        DataLoadStatus.OK, "snap", (D1,), (), (_row(D1, "a", _field("p", 1.5)),)
    )
    return RawPanel.from_observation_set(raw, field_ids=("p",))


@pytest.mark.parametrize(
    ("name", "wrong"),
    [
        ("value", np.array([1], dtype=np.int64)),
        ("present", np.array([1], dtype=np.int8)),
        ("has_value", np.array([1], dtype=np.int8)),
        ("available", np.array([D1.toordinal()], dtype=np.int64)),
        ("text", np.array([-1], dtype=np.int64)),
        ("kind", np.array([0], dtype=np.int32)),
    ],
)
def test_field_column_rejects_wrong_dtypes(name: str, wrong: np.ndarray) -> None:
    column = _one_cell_panel().fields[0]

    with pytest.raises(ValueError, match=f"wrong dtypes — got={{'{name}'"):
        replace(column, **{name: wrong})


@pytest.mark.parametrize(
    ("name", "wrong"),
    [
        ("row_date", np.array([D1.toordinal()], dtype=np.int64)),
        ("row_security", np.array([0], dtype=np.int64)),
        ("universe_member", np.array([1], dtype=np.int8)),
        ("sector", np.array([-1], dtype=np.int64)),
        ("previous_weight", np.array([0.0], dtype=np.float32)),
    ],
)
def test_panel_rejects_wrong_row_dtypes(name: str, wrong: np.ndarray) -> None:
    panel = _one_cell_panel()

    with pytest.raises(ValueError, match=f"wrong dtypes — got={{'{name}'"):
        replace(panel, **{name: wrong})


def test_present_cell_needs_an_available_ordinal_of_at_least_one() -> None:
    column = _one_cell_panel().fields[0]

    with pytest.raises(ValueError, match="available ordinal >= 1"):
        replace(column, available=np.array([0], dtype=np.int32))
