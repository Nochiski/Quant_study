"""P1.5-03 raw PIT observation port contract.

Every adapter behind `RawObservationPort` must pass this suite (today: the deterministic mock; the
Equity DuckDB adapter parameterises the same tests when it arrives). The invariants are the ones
the truthful pipeline (P1.5-04) relies on: adapter-owned snapshot id, no look-ahead, explicit
warm-up history, deterministic ordering, window-invariant facts, failures as values, and raw facts
only (no factor values).
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from dataclasses import replace
from datetime import date, timedelta

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    CancellableRawObservationPort,
    RawFieldValue,
    RawObservation,
    RawObservationPort,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.portfolio_design.ports.outgoing import (
    raw_observations as raw_observation_module,
)
from strategy_workbench.domain.equity.facade.research_data import (
    DataLoadStatus,
    ResearchPanelQuery,
)

START, END = date(2024, 1, 8), date(2024, 1, 12)
MARKET, UNIVERSE = "KRX", "krx.common-stock"
FIELDS = (
    "price.close",
    "price.market_cap",
    "financial.book_equity",
    "classification.sector",
)

ADAPTERS = [pytest.param(MockEquityDataAdapter.demo(), id="mock")]


def _query(
    start: date = START,
    end: date = END,
    fields: tuple[str, ...] = FIELDS,
    history: int = 0,
    universe: str = UNIVERSE,
) -> RawObservationQuery:
    return RawObservationQuery(
        market=MARKET,
        universe_id=universe,
        start=start,
        end=end,
        field_ids=fields,
        history_sessions_before_start=history,
    )


def _index(result: RawObservationSet) -> dict[tuple[date, str], RawObservation]:
    return {(o.as_of, o.security_id): o for o in result.observations}


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_snapshot_is_owned_by_the_adapter(adapter: MockEquityDataAdapter) -> None:
    result = adapter.load_raw_observations(_query())
    assert result.ok
    assert result.data_snapshot_id == adapter.snapshot().snapshot_id


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_no_field_value_is_visible_before_its_available_date(adapter: RawObservationPort) -> None:
    result = adapter.load_raw_observations(_query(history=3))
    assert result.observations
    for observation in result.observations:
        for field in observation.fields:
            assert field.available_date <= observation.as_of, (observation.as_of, field)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_history_is_exactly_the_requested_sessions_before_start(
    adapter: RawObservationPort,
) -> None:
    result = adapter.load_raw_observations(_query(history=2))
    assert result.sessions[0] >= START and result.sessions[-1] <= END
    assert len(result.history_sessions) == 2
    assert all(session < START for session in result.history_sessions)
    covered = {observation.as_of for observation in result.observations}
    assert covered == set(result.history_sessions) | set(result.sessions)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_lagged_fields_are_present_on_the_first_history_session(
    adapter: RawObservationPort,
) -> None:
    """Lag is the adapter's job: warm-up sessions must already carry lagged fields."""
    result = adapter.load_raw_observations(_query(fields=("price.market_cap",), history=2))
    first = result.history_sessions[0]
    present = [
        o
        for o in result.observations
        if o.as_of == first and any(f.value is not None for f in o.fields)
    ]
    assert present, "no lagged value on the first history session"


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_observations_are_deterministic_and_ordered(adapter: RawObservationPort) -> None:
    query = _query(history=1)
    first = adapter.load_raw_observations(query)
    second = adapter.load_raw_observations(query)
    assert first == second
    keys = [(o.as_of, o.security_id) for o in first.observations]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_long_raw_load_honours_the_application_cancellation_checkpoint(
    adapter: CancellableRawObservationPort,
) -> None:
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("cancelled by application")

    with pytest.raises(RuntimeError, match="cancelled by application"):
        adapter.load_raw_observations_cancellable(_query(), checkpoint=cancel)
    assert calls == 1


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_facts_do_not_depend_on_the_query_window(adapter: RawObservationPort) -> None:
    """(as_of, security) facts are window-invariant: membership and fields never flip."""
    narrow = _index(adapter.load_raw_observations(_query()))
    wide = _index(adapter.load_raw_observations(_query(start=START - timedelta(days=7), history=3)))
    assert set(narrow) <= set(wide)
    for key, observation in narrow.items():
        assert wide[key].universe_member == observation.universe_member, key
        assert wide[key].fields == observation.fields, key
        assert wide[key].sector_id == observation.sector_id, key


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_unknown_field_is_a_failure_value_not_a_synthetic_series(
    adapter: RawObservationPort,
) -> None:
    result = adapter.load_raw_observations(_query(fields=("price.close", "unknown.field")))
    assert result.status is DataLoadStatus.INVALID_QUERY
    assert not result.ok
    assert result.observations == ()
    assert result.detail is not None and "unknown.field" in result.detail


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_unknown_universe_is_a_failure_value(adapter: RawObservationPort) -> None:
    result = adapter.load_raw_observations(_query(universe="nope.universe"))
    assert result.status is DataLoadStatus.INVALID_QUERY
    assert result.detail is not None and "nope.universe" in result.detail


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_port_exposes_raw_facts_only(adapter: MockEquityDataAdapter) -> None:
    result = adapter.load_raw_observations(_query())
    assert {f.name for f in dataclass_fields(RawObservation)} == {
        "as_of",
        "security_id",
        "universe_member",
        "fields",
        "sector_id",
        "previous_weight",
    }
    known = {profile.field_id for profile in adapter.list_fields()}
    for observation in result.observations:
        assert {field.field_id for field in observation.fields} <= known
    assert result.observations[0].sector_id is not None


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_raw_port_and_research_panel_agree_cell_by_cell(adapter: MockEquityDataAdapter) -> None:
    """Same field, date, security → same value and publication date on both ports."""
    raw = adapter.load_raw_observations(_query())
    security_ids = tuple(sorted({o.security_id for o in raw.observations}))
    panel = adapter.load_panel(
        ResearchPanelQuery(start=START, end=END, security_ids=security_ids, field_ids=FIELDS)
    )
    cells = {(c.as_of, c.security_id, c.field_id): c for c in panel.cells}
    raw_cells = {
        (o.as_of, o.security_id, f.field_id): f for o in raw.observations for f in o.fields
    }
    assert set(raw_cells) == set(cells)
    for key, field in raw_cells.items():
        assert field.value == cells[key].value, key
        assert field.available_date == cells[key].available_date, key


def test_query_rejects_inverted_range_negative_history_and_missing_universe() -> None:
    with pytest.raises(ValueError, match="start must be <= end"):
        _query(start=END, end=START)
    with pytest.raises(ValueError, match="history_sessions_before_start"):
        _query(history=-1)
    with pytest.raises(ValueError, match="universe_id"):
        RawObservationQuery("KRX", "", START, END, FIELDS)


def test_result_rejects_unordered_or_duplicate_observations() -> None:
    snapshot = "snap"
    with pytest.raises(ValueError, match="history must precede"):
        RawObservationSet(DataLoadStatus.OK, snapshot, (START,), (START,), ())
    duplicate = RawObservation(START, "a", True, ())
    with pytest.raises(ValueError, match="unique"):
        RawObservationSet(DataLoadStatus.OK, snapshot, (START,), (), (duplicate, duplicate))


def test_mock_lag_shifts_availability_by_whole_sessions() -> None:
    """A field with recommended lag 1 shows, at session j, the value effective at session j-1."""
    demo = MockEquityDataAdapter.demo()
    profiles = tuple(
        replace(profile, recommended_lag_sessions=1)
        if profile.field_id == "price.close"
        else profile
        for profile in demo.list_fields()
    )
    lagged = MockEquityDataAdapter(
        snapshot=demo.snapshot(),
        sessions=demo._sessions,  # pyright: ignore[reportPrivateUsage]  # reason: test fixture wiring
        profiles=profiles,
        memberships=demo._memberships,  # pyright: ignore[reportPrivateUsage]  # reason: test fixture wiring
        observations=demo._observations,  # pyright: ignore[reportPrivateUsage]  # reason: test fixture wiring
    )
    query = _query(fields=("price.close",), history=1)

    plain = _index(demo.load_raw_observations(query))
    shifted = _index(lagged.load_raw_observations(query))

    def close(index: dict[tuple[date, str], RawObservation], as_of: date, security: str) -> object:
        return next(
            (f.value for f in index[(as_of, security)].fields if f.field_id == "price.close"), None
        )

    sessions = sorted({as_of for as_of, _ in plain})
    security = sorted({security for _, security in plain})[0]
    assert close(shifted, sessions[0], security) is not None  # warm-up already carries the lag
    for previous, current in zip(sessions, sessions[1:], strict=False):
        assert close(shifted, current, security) == close(plain, previous, security)


def test_mock_synthetic_period_is_a_pure_function_of_the_date() -> None:
    """Outside the fixture calendar the mock still answers per (security, date), not per window."""
    demo = MockEquityDataAdapter.demo()
    a = _index(demo.load_raw_observations(_query(start=date(2025, 3, 3), end=date(2025, 3, 14))))
    b = _index(
        demo.load_raw_observations(
            _query(start=date(2025, 2, 17), end=date(2025, 3, 14), history=5)
        )
    )
    assert set(a) <= set(b)
    assert all(a[key] == b[key] for key in a)
    assert any(not o.universe_member for o in a.values())  # membership churn is date-based


def test_result_rejects_observations_on_undeclared_dates() -> None:
    """D-003: an undeclared row would shift every lag/window behind it without a word."""
    declared, stray = START, START + timedelta(days=1)
    rows = (RawObservation(declared, "a", True, ()), RawObservation(stray, "a", True, ()))

    with pytest.raises(ValueError, match="neither a session nor warm-up history"):
        RawObservationSet(DataLoadStatus.OK, "snap", (declared,), (), rows)

    accepted = RawObservationSet(DataLoadStatus.OK, "snap", (declared, stray), (), rows)
    assert len(accepted.observations) == 2


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_result_rejects_every_non_finite_raw_number(value: float) -> None:
    field = RawFieldValue("price.market_cap", value, START)
    with pytest.raises(ValueError, match="raw numeric field value must be finite"):
        RawObservationSet(
            DataLoadStatus.OK,
            "snap",
            (START,),
            (),
            (RawObservation(START, "a", True, (field,)),),
        )

    with pytest.raises(ValueError, match="previous_weight must be finite"):
        RawObservationSet(
            DataLoadStatus.OK,
            "snap",
            (START,),
            (),
            (RawObservation(START, "a", True, (), previous_weight=value),),
        )


@pytest.mark.parametrize("consumer_revalidation", [False, True])
def test_raw_contract_validation_cancels_after_first_numeric_check(
    monkeypatch: pytest.MonkeyPatch, consumer_revalidation: bool
) -> None:
    rows = tuple(
        RawObservation(
            START,
            f"security-{index:04d}",
            True,
            (RawFieldValue("price.close", float(index), START),),
        )
        for index in range(1_000)
    )
    existing = (
        RawObservationSet(DataLoadStatus.OK, "snap", (START,), (), rows)
        if consumer_revalidation
        else None
    )
    stopped = False
    numeric_checks = 0
    original = raw_observation_module._finite_number

    def latch_on_first_numeric(value: object) -> bool:
        nonlocal numeric_checks, stopped
        numeric_checks += 1
        stopped = True
        return original(value)

    def checkpoint() -> None:
        if stopped:
            raise RuntimeError("cancelled during raw contract validation")

    monkeypatch.setattr(raw_observation_module, "_finite_number", latch_on_first_numeric)

    with pytest.raises(RuntimeError, match="cancelled during raw contract validation"):
        if existing is None:
            RawObservationSet(
                DataLoadStatus.OK,
                "snap",
                (START,),
                (),
                rows,
                validation_checkpoint=checkpoint,
            )
        else:
            existing.validate_contract(checkpoint=checkpoint)

    assert numeric_checks == 1


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_every_observation_date_is_declared(adapter: RawObservationPort) -> None:
    result = adapter.load_raw_observations(_query(history=2))
    declared = set(result.sessions) | set(result.history_sessions)
    assert {observation.as_of for observation in result.observations} <= declared
