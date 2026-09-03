from __future__ import annotations

from datetime import date

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
