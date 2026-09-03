from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DataLoadStatus,
    DatasetFieldProfile,
    DataSnapshot,
    ResearchPanelCell,
    ResearchPanelQuery,
    ResearchPanelResult,
    SecurityRef,
    UniverseHistoryQuery,
    UniverseHistoryResult,
    UniversePoint,
)


@dataclass(frozen=True)
class _Membership:
    security: SecurityRef
    first_session: date
    last_session: date


@dataclass(frozen=True)
class _Observation:
    security_id: str
    field_id: str
    effective_date: date
    available_date: date
    value: float | None
    kind: CellKind


class MockEquityDataAdapter:
    """Small but adversarial Equity v0.2 fixture with vintages, lags, and gaps."""

    def __init__(
        self,
        *,
        snapshot: DataSnapshot,
        sessions: tuple[date, ...],
        profiles: tuple[DatasetFieldProfile, ...],
        memberships: tuple[_Membership, ...],
        observations: tuple[_Observation, ...],
    ) -> None:
        self._snapshot = snapshot
        self._sessions = sessions
        self._profiles = profiles
        self._memberships = memberships
        self._observations = observations

    @classmethod
    def demo(cls) -> MockEquityDataAdapter:
        sessions = tuple(
            date(2024, 1, day) for day in (2, 3, 4, 5, 8, 9, 10, 11, 12)
        )
        securities = (
            SecurityRef("sec-005930-1", "005930", "삼성전자", "XKRX"),
            SecurityRef("sec-000660-1", "000660", "SK하이닉스", "XKRX"),
            SecurityRef("sec-035420-1", "035420", "NAVER", "XKRX"),
        )
        profiles = (
            DatasetFieldProfile(
                "price.close",
                "price_daily",
                "종가",
                "KRW",
                "session close",
                0,
                "KRX 원주가. 조정값은 별도 factor로 적용한다.",
            ),
            DatasetFieldProfile(
                "price.market_cap",
                "price_daily",
                "시가총액",
                "KRW",
                "next session knowledge",
                1,
                "당일 값이지만 기본 연구 랙은 1 session이다.",
            ),
            DatasetFieldProfile(
                "financial.book_equity",
                "fin_std",
                "자본총계",
                "KRW",
                "filing available_date",
                0,
                "공시 available_date 이후에만 보인다.",
            ),
            DatasetFieldProfile(
                "consensus.forward_eps",
                "consensus_daily",
                "12개월 선행 EPS",
                "KRW/share",
                "first_seen_fetched_date",
                0,
                "관측점의 최초 수집 판본과 이후 revision을 보존한다.",
            ),
            DatasetFieldProfile(
                "flow.foreign_net_buy",
                "flow_daily",
                "외국인 순매수",
                "KRW",
                "session",
                0,
                "원천 생략 0과 미수집을 CellKind로 구분한다.",
            ),
        )
        memberships = (
            _Membership(securities[0], sessions[0], sessions[-1]),
            _Membership(securities[1], sessions[0], sessions[-1]),
            _Membership(securities[2], sessions[2], sessions[-2]),
        )
        observations: list[_Observation] = []
        for index, session in enumerate(sessions):
            for security_index, security in enumerate(securities):
                if security_index == 2 and session < sessions[2]:
                    continue
                observations.extend(
                    (
                        _Observation(
                            security.security_id,
                            "price.close",
                            session,
                            session,
                            70_000.0 + security_index * 40_000.0 + index * 500.0,
                            CellKind.OBSERVED,
                        ),
                        _Observation(
                            security.security_id,
                            "price.market_cap",
                            session,
                            session,
                            400_000_000_000_000.0
                            + security_index * 20_000_000_000_000.0
                            + index * 1_000_000_000.0,
                            CellKind.OBSERVED,
                        ),
                    )
                )
        observations.extend(
            (
                _Observation(
                    securities[0].security_id,
                    "financial.book_equity",
                    date(2023, 12, 31),
                    sessions[2],
                    363_000_000_000_000.0,
                    CellKind.OBSERVED,
                ),
                _Observation(
                    securities[0].security_id,
                    "consensus.forward_eps",
                    sessions[0],
                    sessions[1],
                    5_000.0,
                    CellKind.OBSERVED,
                ),
                _Observation(
                    securities[0].security_id,
                    "consensus.forward_eps",
                    sessions[0],
                    sessions[3],
                    5_400.0,
                    CellKind.OBSERVED,
                ),
                _Observation(
                    securities[0].security_id,
                    "flow.foreign_net_buy",
                    sessions[1],
                    sessions[1],
                    0.0,
                    CellKind.SOURCE_OMITTED_ZERO,
                ),
                _Observation(
                    securities[1].security_id,
                    "flow.foreign_net_buy",
                    sessions[1],
                    sessions[1],
                    None,
                    CellKind.MISSING,
                ),
                _Observation(
                    securities[2].security_id,
                    "flow.foreign_net_buy",
                    sessions[4],
                    sessions[4],
                    None,
                    CellKind.COVERAGE_GAP,
                ),
            )
        )
        return cls(
            snapshot=DataSnapshot(
                snapshot_id="mock-equity-v0.2-20260903",
                schema_version="equity-v0.2-mock",
                built_at=datetime(2026, 9, 3, tzinfo=UTC),
                source="deterministic-memory-fixture",
            ),
            sessions=sessions,
            profiles=profiles,
            memberships=memberships,
            observations=tuple(observations),
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
        known_security_ids = {
            membership.security.security_id for membership in self._memberships
        }
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
    ) -> _Observation | None:
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
