"""P1.5-03 raw PIT observation port contract.

Every adapter behind `RawObservationPort` must pass this suite: the deterministic mock and the
equity DuckDB adapter (S21, on a hand-built equity_root — `tests/equity_fixture.py`). The invariants
are the ones the truthful pipeline (P1.5-04) relies on: adapter-owned snapshot id, no look-ahead,
explicit warm-up history, deterministic ordering, window-invariant facts, failures as values, and
raw facts only (no factor values).

`ADAPTERS` names the cases; the `adapter` fixture builds them. Each case answers the subset of
`FIELDS` its `list_fields()` declares, and must reject the rest as a failure value rather than
synthesise it. `FIELDS` is the union of what any adapter can serve: the mock declares nine of them
and ignores the rest, while the equity adapter (S21-3) serves the 29 EQUITY_FIELD_MAP ids whose
source tables are built plus the internal `price.adj_close`. That union is deliberate — the
window-invariance, PIT and cell-kind clauses below then run over every equity field, including the
as-of ones (financials, consensus, filings) whose `available_date` is a filing date far behind
`as_of` and the daily grids (flow, short, credit) whose empty cells carry a missing reason rather
than a zero.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.equity_workspace.facade.ports import EquityDataPort
from strategy_workbench.application.portfolio_design.facade.ports import (
    CancellableRawObservationPort,
    RawFieldValue,
    RawObservation,
    RawObservationContractViolation,
    RawObservationPort,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.portfolio_design.ports.outgoing import (
    raw_observations as raw_observation_module,
)
from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DataLoadStatus,
    ResearchPanelQuery,
)

START, END = date(2024, 1, 8), date(2024, 1, 12)
MARKET, UNIVERSE = "KRX", "krx.common-stock"
FIELDS = (
    # price grid
    "price.close",
    "price.open",
    "price.volume",
    "price.market_cap",
    "price.shares_outstanding",
    "price.trading_value",
    "price.adj_close",
    # as-of filings (fin_std via v_fin_latest)
    "financial.revenue",
    "financial.gross_profit",
    "financial.operating_income",
    "financial.net_income",
    "financial.operating_cash_flow",
    "financial.total_assets",
    "financial.total_liabilities",
    "financial.book_equity",
    # as-of estimates (consensus_daily via v_consensus, opinion_daily)
    "consensus.forward_eps",
    "consensus.forward_sales",
    "consensus.eps_dispersion",
    "consensus.target_price",
    "consensus.recommendation",
    "consensus.analyst_count",
    # as-of events (dividend_event, corp_event, holder_daily)
    "event.dividend_per_share",
    "event.buyback_amount",
    "event.insider_net_buy",
    # daily grids carrying an explicit missing-reason axis (flow_daily, short_daily, credit_daily)
    "flow.foreign_net_buy",
    "flow.institution_net_buy",
    "flow.retail_net_buy",
    "short.short_sale_value",
    "short.borrowed_quantity",
    "credit.margin_balance",
    # served by the mock only — the equity adapter answers INVALID_QUERY for these
    "short.short_balance_ratio",
    "event.earnings_surprise",
    "classification.sector",
)

ADAPTERS = [pytest.param("mock", id="mock"), pytest.param("equity_duckdb", id="equity_duckdb")]


class ContractAdapter(
    RawObservationPort, CancellableRawObservationPort, EquityDataPort, Protocol
):
    """All three ports on one object — the suite checks they agree cell by cell.

    Cancellation is not optional for a contract case: a long trace must be interruptible on
    every adapter the container can wire, so the suite demands the cancellable capability too.
    """


@pytest.fixture(scope="session")
def equity_duckdb_adapter(tmp_path_factory: pytest.TempPathFactory) -> ContractAdapter:
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    root: Path = tmp_path_factory.mktemp("contract-equity") / "equity"
    return EquityDuckdbAdapter(build_workbench_root(root))


@pytest.fixture
def adapter(request: pytest.FixtureRequest) -> ContractAdapter:
    if request.param == "mock":
        return MockEquityDataAdapter.demo()
    if request.param == "equity_duckdb":
        return request.getfixturevalue("equity_duckdb_adapter")
    raise ValueError(f"unknown contract adapter — id={request.param!r}")


def _fields(adapter: ContractAdapter) -> tuple[str, ...]:
    """The suite's canonical fields this adapter declares (order of `FIELDS` kept)."""
    known = {profile.field_id for profile in adapter.list_fields()}
    return tuple(field_id for field_id in FIELDS if field_id in known)


def _query(
    adapter: ContractAdapter,
    start: date = START,
    end: date = END,
    fields: tuple[str, ...] | None = None,
    history: int = 0,
    universe: str = UNIVERSE,
) -> RawObservationQuery:
    return RawObservationQuery(
        market=MARKET,
        universe_id=universe,
        start=start,
        end=end,
        field_ids=_fields(adapter) if fields is None else fields,
        history_sessions_before_start=history,
    )


def _index(result: RawObservationSet) -> dict[tuple[date, str], RawObservation]:
    return {(o.as_of, o.security_id): o for o in result.observations}


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_snapshot_is_owned_by_the_adapter(adapter: ContractAdapter) -> None:
    result = adapter.load_raw_observations(_query(adapter))
    assert result.ok
    assert result.data_snapshot_id == adapter.snapshot().snapshot_id


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_no_field_value_is_visible_before_its_available_date(adapter: ContractAdapter) -> None:
    result = adapter.load_raw_observations(_query(adapter, history=3))
    assert result.observations
    for observation in result.observations:
        for field in observation.fields:
            assert field.available_date <= observation.as_of, (observation.as_of, field)


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_history_is_exactly_the_requested_sessions_before_start(
    adapter: ContractAdapter,
) -> None:
    result = adapter.load_raw_observations(_query(adapter, history=2))
    assert result.sessions[0] >= START and result.sessions[-1] <= END
    assert len(result.history_sessions) == 2
    assert all(session < START for session in result.history_sessions)
    covered = {observation.as_of for observation in result.observations}
    assert covered == set(result.history_sessions) | set(result.sessions)


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_lagged_fields_are_present_on_the_first_history_session(
    adapter: ContractAdapter,
) -> None:
    """Lag is the adapter's job: warm-up sessions must already carry lagged fields."""
    result = adapter.load_raw_observations(
        _query(adapter, fields=("price.market_cap",), history=2)
    )
    first = result.history_sessions[0]
    present = [
        o
        for o in result.observations
        if o.as_of == first and any(f.value is not None for f in o.fields)
    ]
    assert present, "no lagged value on the first history session"


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_observations_are_deterministic_and_ordered(adapter: ContractAdapter) -> None:
    query = _query(adapter, history=1)
    first = adapter.load_raw_observations(query)
    second = adapter.load_raw_observations(query)
    assert first == second
    keys = [(o.as_of, o.security_id) for o in first.observations]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_long_raw_load_honours_the_application_cancellation_checkpoint(
    adapter: ContractAdapter,
) -> None:
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("cancelled by application")

    with pytest.raises(RuntimeError, match="cancelled by application"):
        adapter.load_raw_observations_cancellable(_query(adapter), checkpoint=cancel)
    assert calls == 1


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_facts_do_not_depend_on_the_query_window(adapter: ContractAdapter) -> None:
    """(as_of, security) facts are window-invariant: membership and fields never flip."""
    narrow = _index(adapter.load_raw_observations(_query(adapter)))
    wide = _index(
        adapter.load_raw_observations(_query(adapter, start=START - timedelta(days=7), history=3))
    )
    assert set(narrow) <= set(wide)
    for key, observation in narrow.items():
        assert wide[key].universe_member == observation.universe_member, key
        assert wide[key].fields == observation.fields, key
        assert wide[key].sector_id == observation.sector_id, key


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_unknown_field_is_a_failure_value_not_a_synthetic_series(
    adapter: ContractAdapter,
) -> None:
    result = adapter.load_raw_observations(
        _query(adapter, fields=("price.close", "unknown.field"))
    )
    assert result.status is DataLoadStatus.INVALID_QUERY
    assert not result.ok
    assert result.observations == ()
    assert result.detail is not None and "unknown.field" in result.detail


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_unknown_universe_is_a_failure_value(adapter: ContractAdapter) -> None:
    result = adapter.load_raw_observations(_query(adapter, universe="nope.universe"))
    assert result.status is DataLoadStatus.INVALID_QUERY
    assert result.detail is not None and "nope.universe" in result.detail


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_port_exposes_raw_facts_only(adapter: ContractAdapter) -> None:
    result = adapter.load_raw_observations(_query(adapter))
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
    # sector_id has no publication date; an adapter that does not serve a sector field must
    # leave it None rather than label securities with a non-PIT classification.
    serves_sector = "classification.sector" in known
    assert all((o.sector_id is not None) == serves_sector for o in result.observations)


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_raw_port_and_research_panel_agree_cell_by_cell(adapter: ContractAdapter) -> None:
    """Same field, date, security → same value and publication date on both ports."""
    raw = adapter.load_raw_observations(_query(adapter))
    security_ids = tuple(sorted({o.security_id for o in raw.observations}))
    panel = adapter.load_panel(
        ResearchPanelQuery(
            start=START, end=END, security_ids=security_ids, field_ids=_fields(adapter)
        )
    )
    assert panel.ok, panel.detail
    cells = {(c.as_of, c.security_id, c.field_id): c for c in panel.cells}
    raw_cells = {
        (o.as_of, o.security_id, f.field_id): f for o in raw.observations for f in o.fields
    }
    assert set(raw_cells) == set(cells)
    for key, field in raw_cells.items():
        assert field.value == cells[key].value, key
        assert field.available_date == cells[key].available_date, key
        assert field.kind is cells[key].kind, key


def test_raw_port_preserves_every_equity_cell_kind_without_collapsing_zero_and_missing() -> None:
    adapter = MockEquityDataAdapter.demo()
    raw = adapter.load_raw_observations(
        _query(
            adapter,
            start=date(2024, 1, 3),
            end=date(2024, 1, 8),
            fields=("flow.foreign_net_buy",),
        )
    )
    cells = {
        (item.as_of, item.security_id): field for item in raw.observations for field in item.fields
    }

    actual_zero = cells[(date(2024, 1, 3), "sec-005930-1")]
    missing = cells[(date(2024, 1, 3), "sec-000660-1")]
    omitted_zero = cells[(date(2024, 1, 4), "sec-005930-1")]
    not_collected = cells[(date(2024, 1, 4), "sec-000660-1")]
    coverage_gap = cells[(date(2024, 1, 8), "sec-035420-1")]
    assert (actual_zero.value, actual_zero.kind) == (0.0, CellKind.OBSERVED)
    assert (omitted_zero.value, omitted_zero.kind) == (
        0.0,
        CellKind.SOURCE_OMITTED_ZERO,
    )
    assert (missing.value, missing.kind) == (None, CellKind.MISSING)
    assert (not_collected.value, not_collected.kind) == (None, CellKind.NOT_COLLECTED)
    assert (coverage_gap.value, coverage_gap.kind) == (None, CellKind.COVERAGE_GAP)


def test_query_rejects_inverted_range_negative_history_and_missing_universe() -> None:
    mock = MockEquityDataAdapter.demo()
    with pytest.raises(ValueError, match="start must be <= end"):
        _query(mock, start=END, end=START)
    with pytest.raises(ValueError, match="history_sessions_before_start"):
        _query(mock, history=-1)
    with pytest.raises(ValueError, match="universe_id"):
        RawObservationQuery("KRX", "", START, END, FIELDS)


def test_result_rejects_unordered_or_duplicate_observations() -> None:
    snapshot = "snap"
    with pytest.raises(ValueError, match="history must precede"):
        RawObservationSet(DataLoadStatus.OK, snapshot, (START,), (START,), ())
    duplicate = RawObservation(START, "a", True, ())
    with pytest.raises(ValueError, match="unique"):
        RawObservationSet(DataLoadStatus.OK, snapshot, (START,), (), (duplicate, duplicate))


def test_result_rejects_blank_or_duplicate_field_identities_at_construction() -> None:
    with pytest.raises(RawObservationContractViolation, match="field_id must not be blank"):
        RawFieldValue(" \t", 1.0, START)

    field = RawFieldValue("price.close", 1.0, START)
    with pytest.raises(RawObservationContractViolation, match="field_ids must be unique"):
        RawObservationSet(
            DataLoadStatus.OK,
            "snap",
            (START,),
            (),
            (RawObservation(START, "a", True, (field, field)),),
        )


@pytest.mark.parametrize("violation", ["blank", "duplicate"])
def test_consumer_revalidation_rejects_mutated_field_identities(violation: str) -> None:
    first = RawFieldValue("price.close", 1.0, START)
    second = RawFieldValue("price.market_cap", 2.0, START)
    observation = RawObservation(START, "a", True, (first, second))
    result = RawObservationSet(DataLoadStatus.OK, "snap", (START,), (), (observation,))

    if violation == "blank":
        object.__setattr__(first, "field_id", "")
        expected = "field_id must not be blank"
    else:
        object.__setattr__(observation, "fields", (first, first))
        expected = "field_ids must be unique"

    with pytest.raises(RawObservationContractViolation, match=expected):
        result.validate_contract()


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
    query = _query(demo, fields=("price.close",), history=1)

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
    a = _index(
        demo.load_raw_observations(_query(demo, start=date(2025, 3, 3), end=date(2025, 3, 14)))
    )
    b = _index(
        demo.load_raw_observations(
            _query(demo, start=date(2025, 2, 17), end=date(2025, 3, 14), history=5)
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


@pytest.mark.parametrize("adapter", ADAPTERS, indirect=True)
def test_every_observation_date_is_declared(adapter: ContractAdapter) -> None:
    result = adapter.load_raw_observations(_query(adapter, history=2))
    declared = set(result.sessions) | set(result.history_sessions)
    assert {observation.as_of for observation in result.observations} <= declared
