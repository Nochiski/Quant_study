from __future__ import annotations

import hashlib
from datetime import date, timedelta

from strategy_workbench.application.factor_research.facade.ports import (
    FactorObservationQuery,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    PortfolioObservationQuery,
    PortfolioObservationSet,
)
from strategy_workbench.domain.equity.facade.research_data import (
    DataLoadStatus,
    DatasetFieldProfile,
    DataSnapshot,
    ResearchPanelCell,
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseHistoryQuery,
    UniverseHistoryResult,
    UniversePoint,
)
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
)
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
)

from ._fixture import Membership, Observation, build_demo_fixture


class MockEquityDataAdapter:
    """Small but adversarial Equity v0.2 fixture with vintages, lags, and gaps."""

    def __init__(
        self,
        *,
        snapshot: DataSnapshot,
        sessions: tuple[date, ...],
        profiles: tuple[DatasetFieldProfile, ...],
        memberships: tuple[Membership, ...],
        observations: tuple[Observation, ...],
    ) -> None:
        self._snapshot = snapshot
        self._sessions = sessions
        self._profiles = profiles
        self._memberships = memberships
        self._observations = observations

    @classmethod
    def demo(cls) -> MockEquityDataAdapter:
        fixture = build_demo_fixture()
        return cls(
            snapshot=fixture.snapshot,
            sessions=fixture.sessions,
            profiles=fixture.profiles,
            memberships=fixture.memberships,
            observations=fixture.observations,
        )

    def snapshot(self) -> DataSnapshot:
        return self._snapshot

    def list_fields(self) -> tuple[DatasetFieldProfile, ...]:
        return self._profiles

    def load_universe(self, query: UniverseHistoryQuery) -> UniverseHistoryResult:
        sessions = tuple(
            session for session in self._sessions if query.start <= session <= query.end
        )
        points = tuple(
            UniversePoint(
                session=session,
                members=tuple(
                    membership.security
                    for membership in self._memberships
                    if membership.security.venue == query.venue
                    and membership.first_session <= session <= membership.last_session
                ),
            )
            for session in sessions
        )
        status = DataLoadStatus.OK if points else DataLoadStatus.NO_DATA
        return UniverseHistoryResult(
            points=points,
            status=status,
            snapshot_id=self._snapshot.snapshot_id,
            detail=None if points else f"no mock universe sessions — query={query}",
        )

    def load_panel(self, query: ResearchPanelQuery) -> ResearchPanelResult:
        profile_by_id = {profile.field_id: profile for profile in self._profiles}
        known_security_ids = {membership.security.security_id for membership in self._memberships}
        unknown_fields = sorted(set(query.field_ids) - set(profile_by_id))
        unknown_securities = sorted(set(query.security_ids) - known_security_ids)
        if unknown_fields or unknown_securities:
            return ResearchPanelResult(
                cells=(),
                status=DataLoadStatus.INVALID_QUERY,
                snapshot_id=self._snapshot.snapshot_id,
                detail=(
                    "unknown mock panel identifiers — "
                    f"fields={unknown_fields} securities={unknown_securities}"
                ),
            )

        lag_by_field = {
            profile.field_id: profile.recommended_lag_sessions for profile in self._profiles
        }
        lag_by_field.update({item.field_id: item.sessions for item in query.lag_overrides})
        sessions = tuple(
            session for session in self._sessions if query.start <= session <= query.end
        )
        cells: list[ResearchPanelCell] = []
        warnings: set[str] = set()
        for session in sessions:
            for field_id in query.field_ids:
                cutoff = self._cutoff(session, lag_by_field[field_id])
                if cutoff is None:
                    warnings.add(
                        f"insufficient mock calendar for lag — field_id={field_id} as_of={session}"
                    )
                    continue
                for security_id in query.security_ids:
                    candidate = self._latest_observation(
                        security_id=security_id,
                        field_id=field_id,
                        as_of=session,
                        available_cutoff=cutoff,
                    )
                    if candidate is None:
                        continue
                    cells.append(
                        ResearchPanelCell(
                            as_of=session,
                            security_id=security_id,
                            field_id=field_id,
                            source_effective_date=candidate.effective_date,
                            available_date=candidate.available_date,
                            value=candidate.value,
                            kind=candidate.kind,
                        )
                    )
        return ResearchPanelResult(
            cells=tuple(cells),
            status=DataLoadStatus.OK if cells else DataLoadStatus.NO_DATA,
            snapshot_id=self._snapshot.snapshot_id,
            warnings=tuple(sorted(warnings)),
            detail=None if cells else f"no mock panel cells — query={query}",
        )

    def load_factor_observations(
        self, query: FactorObservationQuery
    ) -> tuple[FactorObservation, ...]:
        """Generate a deterministic PIT-shaped factor panel behind the replaceable port."""
        sessions = _factor_sessions(query)
        security_ids = tuple(membership.security.security_id for membership in self._memberships)
        return tuple(
            FactorObservation(
                as_of=session,
                security_id=security_id,
                fields=tuple(
                    FactorFieldValue(
                        field_id=field_id,
                        value=_factor_field_value(
                            field_id,
                            security_index=security_index,
                            session_index=session_index,
                        ),
                    )
                    for field_id in query.required_field_ids
                ),
                forward_return=(security_index - 1) * 0.003 + ((session_index % 5) - 2) * 0.0002,
            )
            for session_index, session in enumerate(sessions)
            for security_index, security_id in enumerate(security_ids)
        )

    def load_portfolio_observations(
        self, query: PortfolioObservationQuery
    ) -> PortfolioObservationSet:
        """Return a deterministic PIT portfolio panel behind the future Equity DB port."""
        sessions = _business_sessions(query.start, query.end)
        security_ids = tuple(membership.security.security_id for membership in self._memberships)
        observations = tuple(
            PortfolioObservation(
                as_of=session,
                security_id=security_id,
                # The generated research panel has its own deterministic PIT history:
                # the third name enters after two sessions and never leaks backward.
                universe_member=security_index < 2 or session_index >= 2,
                factor_values=tuple(
                    PortfolioFactorValue(
                        factor_id=factor_id,
                        value=_portfolio_factor_value(
                            factor_id,
                            security_index=security_index,
                            session_index=session_index,
                        ),
                        available_date=session,
                    )
                    for factor_id in query.factor_ids
                ),
                fields=tuple(
                    PortfolioFieldValue(
                        field_id=field_id,
                        value=_factor_field_value(
                            field_id,
                            security_index=security_index,
                            session_index=session_index,
                        ),
                        available_date=session,
                    )
                    for field_id in query.field_ids
                ),
                sector_id=("technology", "industrial", "consumer")[security_index % 3],
            )
            for session_index, session in enumerate(sessions)
            for security_index, security_id in enumerate(security_ids)
        )
        return PortfolioObservationSet(
            data_snapshot_id=self._snapshot.snapshot_id,
            sessions=sessions,
            observations=observations,
        )

    def _cutoff(self, session: date, lag_sessions: int) -> date | None:
        try:
            session_index = self._sessions.index(session)
        except ValueError:
            return None
        cutoff_index = session_index - lag_sessions
        return self._sessions[cutoff_index] if cutoff_index >= 0 else None

    def _latest_observation(
        self,
        *,
        security_id: str,
        field_id: str,
        as_of: date,
        available_cutoff: date,
    ) -> Observation | None:
        candidates = (
            observation
            for observation in self._observations
            if observation.security_id == security_id
            and observation.field_id == field_id
            and observation.effective_date <= as_of
            and observation.available_date <= available_cutoff
        )
        return max(
            candidates,
            key=lambda observation: (observation.effective_date, observation.available_date),
            default=None,
        )


def _factor_sessions(query: FactorObservationQuery) -> tuple[date, ...]:
    requested = list(_business_sessions(query.start, query.end))
    history: list[date] = []
    cursor = query.start - timedelta(days=1)
    while len(history) < max(query.minimum_history_sessions - 1, 0):
        if cursor.weekday() < 5:
            history.append(cursor)
        cursor -= timedelta(days=1)
    return tuple((*reversed(history), *requested))


def _business_sessions(start: date, end: date) -> tuple[date, ...]:
    sessions: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return tuple(sessions)


def _portfolio_factor_value(
    factor_id: str,
    *,
    security_index: int,
    session_index: int,
) -> float:
    stable = int.from_bytes(hashlib.sha256(factor_id.encode("utf-8")).digest()[:2], "big")
    direction = -1.0 if stable % 2 else 1.0
    cross_section = (security_index - 1) * direction
    time_component = ((session_index + stable) % 11 - 5) * 0.025
    return cross_section + time_component


def _factor_field_value(
    field_id: str,
    *,
    security_index: int,
    session_index: int,
) -> float | str:
    if field_id.endswith("sector") or field_id.endswith("sector_code"):
        return ("technology", "industrial", "consumer")[security_index % 3]
    stable = int.from_bytes(hashlib.sha256(field_id.encode("utf-8")).digest()[:2], "big")
    scale = 1 + stable % 17
    trend = (session_index + 1) * (security_index + 1) * scale
    if field_id == "price.close":
        return 40_000.0 + security_index * 20_000.0 + trend
    if field_id == "price.market_cap":
        return 10_000_000_000.0 + security_index * 2_000_000_000.0 + trend * 10_000
    if field_id == "financial.book_equity":
        return 4_000_000_000.0 + security_index * 900_000_000.0 + trend * 1_000
    if field_id == "consensus.forward_eps":
        return 2_000.0 + security_index * 350.0 + trend * 0.2
    if field_id == "flow.foreign_net_buy":
        return (security_index - 1) * 100_000_000.0 + trend * 10_000
    if field_id == "short.short_balance_ratio":
        return 0.01 + security_index * 0.015 + (session_index % 7) * 0.0001
    if field_id == "credit.margin_balance":
        return 1_000_000_000.0 + security_index * 100_000_000.0 + trend * 1_000
    if field_id == "event.earnings_surprise":
        return (security_index - 1) * 0.05 + (session_index % 3) * 0.005
    return float(stable + trend)
