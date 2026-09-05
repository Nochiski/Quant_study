from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from strategy_workbench.domain.equity.facade.research_data import (
    DataLoadStatus,
    DatasetFieldProfile,
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseHistoryQuery,
)

from .ports.outgoing.equity_data import EquityDataPort


class ResearchWarningSeverity(Enum):
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True)
class ResearchDataWarning:
    warning_id: str
    code: str
    message: str
    severity: ResearchWarningSeverity
    field_id: str | None
    requires_confirmation: bool


@dataclass(frozen=True)
class PanelPreviewCostEstimate:
    session_count: int
    requested_rows: int
    requested_columns: int
    estimated_cells: int
    estimated_bytes: int
    returned_rows: int
    returned_columns: int
    returned_cells: int


@dataclass(frozen=True)
class ResearchPanelPreviewRequest:
    query: ResearchPanelQuery
    venue: str = "XKRX"
    row_limit: int = 50
    column_limit: int = 10
    confirmed_warning_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.row_limit <= 500:
            raise ValueError(
                f"panel preview row_limit must be within [1, 500] — row_limit={self.row_limit}"
            )
        if not 1 <= self.column_limit <= 50:
            raise ValueError(
                "panel preview column_limit must be within [1, 50] — "
                f"column_limit={self.column_limit}"
            )


@dataclass(frozen=True)
class ResearchPanelPreview:
    panel: ResearchPanelResult
    cost: PanelPreviewCostEstimate
    warnings: tuple[ResearchDataWarning, ...]
    confirmation_required: bool
    truncated: bool


def preview_research_panel(
    equity_data: EquityDataPort,
    request: ResearchPanelPreviewRequest,
) -> ResearchPanelPreview:
    profiles = {profile.field_id: profile for profile in equity_data.list_fields()}
    warnings = _warnings(request.query, profiles)
    pending_confirmations = tuple(
        warning
        for warning in warnings
        if warning.requires_confirmation and warning.warning_id not in request.confirmed_warning_ids
    )
    universe = equity_data.load_universe(
        UniverseHistoryQuery(
            venue=request.venue,
            start=request.query.start,
            end=request.query.end,
        )
    )
    session_count = len(universe.points)
    requested_rows = session_count * len(request.query.security_ids)
    requested_columns = len(request.query.field_ids)
    estimated_cells = requested_rows * requested_columns
    truncated = requested_rows > request.row_limit or requested_columns > request.column_limit
    if pending_confirmations:
        panel = ResearchPanelResult(
            cells=(),
            status=DataLoadStatus.CONFIRMATION_REQUIRED,
            snapshot_id=equity_data.snapshot().snapshot_id,
            detail="panel preview requires explicit warning confirmation",
        )
        return ResearchPanelPreview(
            panel=panel,
            cost=_cost_estimate(
                session_count=session_count,
                requested_rows=requested_rows,
                requested_columns=requested_columns,
                estimated_cells=estimated_cells,
                panel=panel,
            ),
            warnings=warnings,
            confirmation_required=True,
            truncated=truncated,
        )

    limited_query = replace(
        request.query,
        field_ids=request.query.field_ids[: request.column_limit],
    )
    loaded = equity_data.load_panel(limited_query)
    row_keys = tuple(sorted({(cell.as_of, cell.security_id) for cell in loaded.cells}))[
        : request.row_limit
    ]
    allowed_rows = set(row_keys)
    panel = replace(
        loaded,
        cells=tuple(
            cell for cell in loaded.cells if (cell.as_of, cell.security_id) in allowed_rows
        ),
    )
    return ResearchPanelPreview(
        panel=panel,
        cost=_cost_estimate(
            session_count=session_count,
            requested_rows=requested_rows,
            requested_columns=requested_columns,
            estimated_cells=estimated_cells,
            panel=panel,
        ),
        warnings=warnings,
        confirmation_required=False,
        truncated=truncated,
    )


def _warnings(
    query: ResearchPanelQuery,
    profiles: dict[str, DatasetFieldProfile],
) -> tuple[ResearchDataWarning, ...]:
    warnings: list[ResearchDataWarning] = []
    lag_overrides = {override.field_id: override.sessions for override in query.lag_overrides}
    for field_id in query.field_ids:
        profile = profiles.get(field_id)
        if profile is None:
            continue
        override_sessions = lag_overrides.get(field_id)
        if override_sessions is not None and override_sessions < profile.recommended_lag_sessions:
            warnings.append(
                ResearchDataWarning(
                    warning_id=f"lag_below_recommended:{field_id}",
                    code="lag_below_recommended",
                    message=(
                        "권장 랙보다 짧아 미래정보 편향 위험이 있습니다: "
                        f"field_id={field_id}, override={override_sessions}, "
                        f"recommended={profile.recommended_lag_sessions}"
                    ),
                    severity=ResearchWarningSeverity.WARNING,
                    field_id=field_id,
                    requires_confirmation=True,
                )
            )
        if profile.coverage.requires_confirmation:
            warnings.append(
                ResearchDataWarning(
                    warning_id=f"coverage_incomplete:{field_id}",
                    code="coverage_incomplete",
                    message=(
                        "데이터 커버리지가 완전하지 않습니다: "
                        f"field_id={field_id}, "
                        f"coverage={profile.coverage.estimated_coverage_pct:.1f}%"
                    ),
                    severity=ResearchWarningSeverity.WARNING,
                    field_id=field_id,
                    requires_confirmation=True,
                )
            )
    return tuple(warnings)


def _cost_estimate(
    *,
    session_count: int,
    requested_rows: int,
    requested_columns: int,
    estimated_cells: int,
    panel: ResearchPanelResult,
) -> PanelPreviewCostEstimate:
    returned_rows = len({(cell.as_of, cell.security_id) for cell in panel.cells})
    returned_columns = len({cell.field_id for cell in panel.cells})
    return PanelPreviewCostEstimate(
        session_count=session_count,
        requested_rows=requested_rows,
        requested_columns=requested_columns,
        estimated_cells=estimated_cells,
        estimated_bytes=estimated_cells * 64,
        returned_rows=returned_rows,
        returned_columns=returned_columns,
        returned_cells=len(panel.cells),
    )
