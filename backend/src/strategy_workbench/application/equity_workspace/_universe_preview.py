from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    UniverseHistoryResult,
)


@dataclass(frozen=True)
class UniverseCoverageSummary:
    session_count: int
    covered_session_count: int
    gap_session_count: int
    first_session: str | None
    last_session: str | None
    minimum_members: int
    maximum_members: int
    average_members: float


@dataclass(frozen=True)
class UniversePreview:
    universe: UniverseHistoryResult
    coverage: UniverseCoverageSummary


def summarize_universe(universe: UniverseHistoryResult) -> UniversePreview:
    member_counts = tuple(len(point.members) for point in universe.points)
    covered_session_count = sum(
        1 for point in universe.points if point.coverage is CellKind.OBSERVED
    )
    return UniversePreview(
        universe=universe,
        coverage=UniverseCoverageSummary(
            session_count=len(universe.points),
            covered_session_count=covered_session_count,
            gap_session_count=len(universe.points) - covered_session_count,
            first_session=(universe.points[0].session.isoformat() if universe.points else None),
            last_session=(universe.points[-1].session.isoformat() if universe.points else None),
            minimum_members=min(member_counts, default=0),
            maximum_members=max(member_counts, default=0),
            average_members=(sum(member_counts) / len(member_counts) if member_counts else 0.0),
        ),
    )
