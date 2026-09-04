from __future__ import annotations

import hashlib
from datetime import date, timedelta

from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataQuery,
    BacktestDataset,
    MarketBarRecord,
    UniverseMembershipRecord,
)
from strategy_workbench.application.factor_research.facade.ports import (
    FactorMetadataSnapshot,
    FactorObservationQuery,
    FactorObservationSet,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawFieldValue,
    RawObservation,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.domain.backtest.facade.runs import DataWarning, WarningSeverity
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
from strategy_workbench.domain.factor.facade.expression import (
    FieldMetadata,
    NodeValueType,
)

from ._fixture import Membership, Observation, build_demo_fixture

_MOCK_EPOCH = date(2000, 1, 3)  # Monday
_MOCK_SECTORS = ("technology", "industrial", "consumer")
# (market, universe_id) -> venue the fixture memberships are keyed by.
_MOCK_UNIVERSES: dict[tuple[str, str], str] = {("KRX", "krx.common-stock"): "XKRX"}


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

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        """Resolve graph field contracts inside the data adapter, never in the browser."""
        profile_by_id = {profile.field_id: profile for profile in self._profiles}
        group_fields = {"classification.sector", "sector"}
        fields: list[FieldMetadata] = []
        for field_id in field_ids:
            profile = profile_by_id.get(field_id)
            if field_id in group_fields:
                fields.append(
                    FieldMetadata(
                        field_id=field_id,
                        unit="category",
                        value_type=NodeValueType.GROUP_SERIES,
                    )
                )
            elif profile is not None:
                fields.append(
                    FieldMetadata(
                        field_id=field_id,
                        unit=profile.unit,
                        value_type=NodeValueType.NUMERIC_SERIES,
                    )
                )
        return FactorMetadataSnapshot(
            data_snapshot_id=self._snapshot.snapshot_id,
            fields=tuple(fields),
        )

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

    def load_factor_observations(self, query: FactorObservationQuery) -> FactorObservationSet:
        """Generate a deterministic PIT-shaped factor panel behind the replaceable port."""
        sessions = _factor_sessions(query)
        security_ids = tuple(membership.security.security_id for membership in self._memberships)
        observations = tuple(
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
        return FactorObservationSet(
            data_snapshot_id=self._snapshot.snapshot_id, observations=observations
        )

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        """Raw PIT panel for the truthful pipeline (P1.5-03).

        Inside the fixture calendar every value comes from the same fixture `Observation` rows and
        the same PIT cut-off as `load_panel`, so Data workspace and Portfolio preview agree cell by
        cell. Outside the calendar the mock synthesises a deterministic series keyed by the
        absolute business-day index (never by the query window), lagged by the field profile's
        recommended lag; `available_date` is the session the value became visible. Membership is a
        function of (security, date) only. The synthetic series is not continuous with the fixture
        values at the calendar boundary (a mock data-quality artifact, deterministic either way).
        """
        venue = _MOCK_UNIVERSES.get((query.market, query.universe_id))
        profile_by_id = {profile.field_id: profile for profile in self._profiles}
        unknown_fields = sorted(set(query.field_ids) - set(profile_by_id))
        if venue is None or unknown_fields:
            return RawObservationSet(
                status=DataLoadStatus.INVALID_QUERY,
                data_snapshot_id=self._snapshot.snapshot_id,
                sessions=(),
                history_sessions=(),
                observations=(),
                detail=(
                    "unknown mock raw observation identifiers — "
                    f"market={query.market!r} universe_id={query.universe_id!r} "
                    f"supported={sorted(_MOCK_UNIVERSES)} unknown_fields={unknown_fields}"
                ),
            )
        history, requested = _sessions_with_history(
            query.start, query.end, query.history_sessions_before_start
        )
        # Fixture order drives the synthetic seed and sector so values never remap when the
        # output ordering changes; the output itself is sorted by (as_of, security_id).
        memberships = [m for m in self._memberships if m.security.venue == venue]
        warnings: set[str] = set()
        observations: list[RawObservation] = []
        for session in history + requested:
            for security_index, membership in enumerate(memberships):
                security_id = membership.security.security_id
                fields: list[RawFieldValue] = []
                for field_id in query.field_ids:
                    raw = self._raw_field(
                        security_id=security_id,
                        security_index=security_index,
                        field_id=field_id,
                        session=session,
                        lag_sessions=profile_by_id[field_id].recommended_lag_sessions,
                        warnings=warnings,
                    )
                    if raw is not None:
                        fields.append(raw)
                observations.append(
                    RawObservation(
                        as_of=session,
                        security_id=security_id,
                        universe_member=self._member(membership, security_index, session),
                        fields=tuple(fields),
                        sector_id=_MOCK_SECTORS[security_index % len(_MOCK_SECTORS)],
                        previous_weight=0.0,
                    )
                )
        observations.sort(key=lambda item: (item.as_of, item.security_id))
        return RawObservationSet(
            status=DataLoadStatus.OK if observations else DataLoadStatus.NO_DATA,
            data_snapshot_id=self._snapshot.snapshot_id,
            sessions=requested,
            history_sessions=history,
            observations=tuple(observations),
            detail=None if observations else f"no mock raw observations — query={query}",
            warnings=tuple(sorted(warnings)),
        )

    def _raw_field(
        self,
        *,
        security_id: str,
        security_index: int,
        field_id: str,
        session: date,
        lag_sessions: int,
        warnings: set[str],
    ) -> RawFieldValue | None:
        if self._sessions[0] <= session <= self._sessions[-1]:
            if session not in self._sessions:
                return None  # a non-trading day inside the fixture calendar
            cutoff = self._cutoff(session, lag_sessions)
            if cutoff is None:
                warnings.add(
                    f"insufficient mock calendar for lag — field_id={field_id} as_of={session}"
                )
                return None
            candidate = self._latest_observation(
                security_id=security_id,
                field_id=field_id,
                as_of=session,
                available_cutoff=cutoff,
            )
            if candidate is None:
                return None
            return RawFieldValue(
                field_id=field_id, value=candidate.value, available_date=candidate.available_date
            )
        effective_index = _business_day_index(session) - lag_sessions
        return RawFieldValue(
            field_id=field_id,
            value=_factor_field_value(
                field_id, security_index=security_index, session_index=effective_index
            ),
            available_date=session,
        )

    def _member(self, membership: Membership, security_index: int, session: date) -> bool:
        if self._sessions[0] <= session <= self._sessions[-1]:
            return membership.first_session <= session <= membership.last_session
        # Synthetic period: the third name and beyond sit out the first two sessions of every
        # month. A pure function of the date, so the same (security, date) never flips.
        if security_index < 2:
            return True
        return _business_day_index(session) - _business_day_index(session.replace(day=1)) >= 2

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        """Generate deterministic OHLCV until the real Equity DB adapter is selected."""
        sessions = _business_sessions(query.start, query.end)
        if not sessions:
            raise ValueError("mock backtest dataset requires at least one business session")
        requested_ids = list(query.security_ids)
        benchmark_id = query.benchmark_security_id or requested_ids[0]
        if benchmark_id not in requested_ids:
            requested_ids.append(benchmark_id)
        security_ids = tuple(dict.fromkeys(requested_ids))
        bars: list[MarketBarRecord] = []
        for security_index, security_id in enumerate(security_ids):
            stable = int.from_bytes(hashlib.sha256(security_id.encode("utf-8")).digest()[:2], "big")
            base = 40_000.0 + security_index * 25_000.0 + stable % 5_000
            previous_close = base
            for session_index, session in enumerate(sessions):
                cycle = ((session_index + stable) % 17 - 8) * 0.0008
                trend = (security_index - 0.5) * 0.00015
                open_price = previous_close * (1 + cycle * 0.35)
                close_price = open_price * (1 + cycle + trend)
                bars.append(
                    MarketBarRecord(
                        session=session,
                        security_id=security_id,
                        open=open_price,
                        high=max(open_price, close_price) * 1.004,
                        low=min(open_price, close_price) * 0.996,
                        close=close_price,
                        volume=1_000_000 + security_index * 250_000 + session_index * 100,
                    )
                )
                previous_close = close_price
        return BacktestDataset(
            data_snapshot_id=self._snapshot.snapshot_id,
            bars=tuple(bars),
            memberships=tuple(
                UniverseMembershipRecord(
                    security_id=security_id,
                    first_session=sessions[0],
                    last_session=sessions[-1],
                )
                for security_id in security_ids
            ),
            corporate_actions=(),
            benchmark_security_id=benchmark_id,
            warnings=(
                DataWarning(
                    code="mock_equity_data",
                    message=(
                        "Deterministic mock OHLCV is active; replace the adapter for "
                        "production research."
                    ),
                    severity=WarningSeverity.INFO,
                ),
                DataWarning(
                    code="corporate_action_feed_empty",
                    message="The mock run declares an empty corporate-action feed.",
                ),
            ),
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


def _sessions_with_history(
    start: date, end: date, history_sessions: int
) -> tuple[tuple[date, ...], tuple[date, ...]]:
    """Business sessions in [start, end] plus `history_sessions` business days before start."""
    history: list[date] = []
    cursor = start - timedelta(days=1)
    while len(history) < history_sessions:
        if cursor.weekday() < 5:
            history.append(cursor)
        cursor -= timedelta(days=1)
    history.reverse()
    return tuple(history), _business_sessions(start, end)


def _business_day_index(session: date) -> int:
    """Weekday count from a fixed Monday epoch: the mock's absolute session clock."""
    days = (session - _MOCK_EPOCH).days
    weeks, remainder = divmod(days, 7)
    return weeks * 5 + min(remainder, 5)


def _business_sessions(start: date, end: date) -> tuple[date, ...]:
    sessions: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return tuple(sessions)


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
