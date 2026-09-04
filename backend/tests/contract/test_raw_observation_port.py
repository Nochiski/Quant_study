"""P1.5-03 raw PIT observation port contract.

Every adapter behind `RawObservationPort` must pass this suite (today: the deterministic mock; the
Equity DuckDB adapter parameterises the same tests when it arrives). The invariants are the ones
the truthful pipeline (P1.5-04) relies on: adapter-owned snapshot id, no look-ahead, explicit
warm-up history, deterministic ordering, and raw facts only (no factor values).
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservation,
    RawObservationQuery,
)

START, END = date(2024, 1, 8), date(2024, 1, 12)
FIELDS = ("price.close", "price.market_cap", "financial.book_equity", "sector")


def _adapters() -> list[tuple[str, MockEquityDataAdapter]]:
    return [("mock", MockEquityDataAdapter.demo())]


@pytest.mark.parametrize(
    ("name", "adapter"), _adapters(), ids=lambda item: item if isinstance(item, str) else ""
)
def test_snapshot_is_owned_by_the_adapter(name: str, adapter: MockEquityDataAdapter) -> None:
    result = adapter.load_raw_observations(RawObservationQuery(START, END, FIELDS))
    assert result.data_snapshot_id == adapter.snapshot().snapshot_id


@pytest.mark.parametrize(
    ("name", "adapter"), _adapters(), ids=lambda item: item if isinstance(item, str) else ""
)
def test_no_field_value_is_visible_before_its_available_date(
    name: str, adapter: MockEquityDataAdapter
) -> None:
    result = adapter.load_raw_observations(RawObservationQuery(START, END, FIELDS))
    assert result.observations
    for observation in result.observations:
        for field in observation.fields:
            assert field.available_date <= observation.as_of, (observation.as_of, field)


@pytest.mark.parametrize(
    ("name", "adapter"), _adapters(), ids=lambda item: item if isinstance(item, str) else ""
)
def test_history_is_explicit_and_sessions_cover_the_range(
    name: str, adapter: MockEquityDataAdapter
) -> None:
    result = adapter.load_raw_observations(
        RawObservationQuery(START, END, FIELDS, minimum_history_sessions=3)
    )
    assert result.sessions[0] >= START and result.sessions[-1] <= END
    assert len(result.history_sessions) == 3
    assert all(session < START for session in result.history_sessions)
    covered = {observation.as_of for observation in result.observations}
    assert covered == set(result.history_sessions) | set(result.sessions)


@pytest.mark.parametrize(
    ("name", "adapter"), _adapters(), ids=lambda item: item if isinstance(item, str) else ""
)
def test_observations_are_deterministic_and_ordered(
    name: str, adapter: MockEquityDataAdapter
) -> None:
    query = RawObservationQuery(START, END, FIELDS, minimum_history_sessions=1)
    first = adapter.load_raw_observations(query)
    second = adapter.load_raw_observations(query)
    assert first == second
    keys = [(o.as_of, o.security_id) for o in first.observations]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    ("name", "adapter"), _adapters(), ids=lambda item: item if isinstance(item, str) else ""
)
def test_port_exposes_raw_facts_only(name: str, adapter: MockEquityDataAdapter) -> None:
    result = adapter.load_raw_observations(RawObservationQuery(START, END, FIELDS))
    assert {f.name for f in dataclass_fields(RawObservation)} == {
        "as_of",
        "security_id",
        "universe_member",
        "fields",
        "sector_id",
        "previous_weight",
    }
    observation = result.observations[0]
    assert {field.field_id for field in observation.fields} <= set(FIELDS)
    assert observation.sector_id is not None


def test_query_rejects_inverted_range_and_negative_history() -> None:
    with pytest.raises(ValueError, match="start must be <= end"):
        RawObservationQuery(END, START, FIELDS)
    with pytest.raises(ValueError, match="minimum_history_sessions"):
        RawObservationQuery(START, END, FIELDS, minimum_history_sessions=-1)


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
    query = RawObservationQuery(START, END, ("price.close",), minimum_history_sessions=1)

    plain = demo.load_raw_observations(query)
    shifted = lagged.load_raw_observations(query)

    def close(result, as_of: date, security: str) -> float | None:
        for observation in result.observations:
            if observation.as_of == as_of and observation.security_id == security:
                return next(
                    (f.value for f in observation.fields if f.field_id == "price.close"), None
                )
        raise AssertionError((as_of, security))

    sessions = plain.history_sessions + plain.sessions
    security = plain.observations[0].security_id
    assert close(shifted, sessions[0], security) is None  # nothing was available yet
    for previous, current in zip(sessions, sessions[1:], strict=False):
        assert close(shifted, current, security) == close(plain, previous, security)
