from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.application.equity_workspace.facade.workspace import (
    FieldCatalogQuery,
    ResearchPanelPreviewRequest,
)
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DataLoadStatus,
    FieldLag,
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseHistoryQuery,
)
from strategy_workbench.domain.factor.facade.registry import (
    FactorAvailability,
    build_default_factor_registry,
)
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph


def _cell_value(
    result: ResearchPanelResult,
    *,
    as_of: date,
    security_id: str,
    field_id: str,
) -> float:
    cells = result.cells
    matching = [
        cell
        for cell in cells
        if cell.as_of == as_of and cell.security_id == security_id and cell.field_id == field_id
    ]
    assert len(matching) == 1
    value = matching[0].value
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return value


def test_container_uses_explicit_mock_adapter_without_silent_fallback() -> None:
    container = build_container()

    catalog = container.equity_workspace.catalog()

    assert catalog.snapshot.schema_version == "equity-v0.2-mock"
    assert {profile.field_id for profile in catalog.fields} >= {
        "price.close",
        "financial.book_equity",
        "consensus.forward_eps",
    }
    with pytest.raises(ValueError, match="unsupported equity adapter"):
        build_container(equity_adapter="nope")
    with pytest.raises(ValueError, match="requires equity_root"):
        build_container(equity_adapter="duckdb")


def test_panel_hides_future_consensus_revision_until_available_date() -> None:
    container = build_container()
    result = container.equity_data.load_panel(
        ResearchPanelQuery(
            start=date(2024, 1, 4),
            end=date(2024, 1, 5),
            security_ids=("sec-005930-1",),
            field_ids=("consensus.forward_eps",),
        )
    )

    assert (
        _cell_value(
            result,
            as_of=date(2024, 1, 4),
            security_id="sec-005930-1",
            field_id="consensus.forward_eps",
        )
        == 5_000.0
    )
    assert (
        _cell_value(
            result,
            as_of=date(2024, 1, 5),
            security_id="sec-005930-1",
            field_id="consensus.forward_eps",
        )
        == 5_400.0
    )


def test_mock_net_income_is_pit_ttm_that_switches_on_the_filing_date() -> None:
    """mock 도 duckdb 와 같은 의미다 — `financial.net_income` 은 공시일 기준 최근 4분기 합(TTM)이다.

    #212: 기간 개념이 없던 mock 은 분기·연간 혼재를 못 잡았다. 000660 은 2023 3분기 보고서가 늦게
    (01-05) 접수돼, 그 전 세션은 반기 말 TTM, 그날부터 3분기 말 TTM 이다. 4분기를 채울 수 없는 종목
    (035420)은 값이 없다 — 3개월 값으로 대신하지 않는다.
    """
    container = build_container()
    profile = next(
        item
        for item in container.equity_workspace.catalog().fields
        if item.field_id == "financial.net_income"
    )
    assert "TTM" in profile.label
    result = container.equity_data.load_panel(
        ResearchPanelQuery(
            start=date(2024, 1, 4),
            end=date(2024, 1, 5),
            security_ids=("sec-000660-1", "sec-035420-1"),
            field_ids=("financial.net_income",),
        )
    )
    cells = {(cell.as_of, cell.security_id): cell for cell in result.cells}
    before = cells[(date(2024, 1, 4), "sec-000660-1")]
    after = cells[(date(2024, 1, 5), "sec-000660-1")]
    assert (before.value, before.source_effective_date, before.available_date) == (
        -8_000_000_000_000.0,
        date(2023, 6, 30),
        date(2023, 8, 14),
    )
    assert (after.value, after.source_effective_date, after.available_date) == (
        -6_000_000_000_000.0,
        date(2023, 9, 30),
        date(2024, 1, 5),
    )
    assert not any(security_id == "sec-035420-1" for _, security_id in cells)


def test_panel_applies_recommended_lag_and_allows_explicit_override() -> None:
    container = build_container()
    default_lag = container.equity_data.load_panel(
        ResearchPanelQuery(
            start=date(2024, 1, 3),
            end=date(2024, 1, 3),
            security_ids=("sec-005930-1",),
            field_ids=("price.market_cap",),
        )
    )
    no_lag = container.equity_data.load_panel(
        ResearchPanelQuery(
            start=date(2024, 1, 3),
            end=date(2024, 1, 3),
            security_ids=("sec-005930-1",),
            field_ids=("price.market_cap",),
            lag_overrides=(FieldLag("price.market_cap", 0),),
        )
    )

    assert (
        _cell_value(
            default_lag,
            as_of=date(2024, 1, 3),
            security_id="sec-005930-1",
            field_id="price.market_cap",
        )
        == 400_000_000_000_000.0
    )
    assert (
        _cell_value(
            no_lag,
            as_of=date(2024, 1, 3),
            security_id="sec-005930-1",
            field_id="price.market_cap",
        )
        == 400_001_000_000_000.0
    )


def test_panel_preserves_zero_missing_and_coverage_gap_kinds() -> None:
    container = build_container()
    result = container.equity_data.load_panel(
        ResearchPanelQuery(
            start=date(2024, 1, 3),
            end=date(2024, 1, 8),
            security_ids=("sec-005930-1", "sec-000660-1", "sec-035420-1"),
            field_ids=("flow.foreign_net_buy",),
        )
    )
    kinds = {(cell.security_id, cell.as_of): cell.kind for cell in result.cells}

    assert kinds[("sec-005930-1", date(2024, 1, 3))] is CellKind.OBSERVED
    assert kinds[("sec-000660-1", date(2024, 1, 3))] is CellKind.MISSING
    assert kinds[("sec-005930-1", date(2024, 1, 4))] is CellKind.SOURCE_OMITTED_ZERO
    assert kinds[("sec-000660-1", date(2024, 1, 4))] is CellKind.NOT_COLLECTED
    assert kinds[("sec-035420-1", date(2024, 1, 8))] is CellKind.COVERAGE_GAP


def test_catalog_search_filter_pagination_and_capabilities_are_explicit() -> None:
    container = build_container()

    catalog = container.equity_workspace.catalog(
        FieldCatalogQuery(search="시가", dataset_ids=("price_daily",), page_size=1)
    )

    assert catalog.total == 1
    assert catalog.page_count == 1
    assert catalog.fields[0].field_id == "price.market_cap"
    assert catalog.fields[0].coverage.point_in_time
    assert catalog.fields[0].available_date_basis == "next session knowledge"
    assert catalog.snapshot.point_in_time
    assert {revision.dataset_id for revision in catalog.snapshot.dataset_revisions} >= {
        "price_daily",
        "fin_std",
    }
    assert "price_daily" in catalog.facets.dataset_ids


def test_universe_preview_reports_session_coverage_summary() -> None:
    container = build_container()

    preview = container.equity_workspace.preview_universe(
        UniverseHistoryQuery(
            venue="XKRX",
            start=date(2024, 1, 2),
            end=date(2024, 1, 12),
        )
    )

    assert preview.coverage.session_count == 9
    assert preview.coverage.covered_session_count == 9
    assert preview.coverage.minimum_members == 2
    assert preview.coverage.maximum_members == 3
    assert preview.coverage.first_session == "2024-01-02"
    assert preview.coverage.last_session == "2024-01-12"


def test_panel_preview_requires_warning_confirmation_and_enforces_limits() -> None:
    container = build_container()
    query = ResearchPanelQuery(
        start=date(2024, 1, 3),
        end=date(2024, 1, 5),
        security_ids=("sec-005930-1",),
        field_ids=("price.market_cap", "flow.foreign_net_buy"),
        lag_overrides=(FieldLag("price.market_cap", 0),),
    )

    blocked = container.equity_workspace.preview_panel(
        ResearchPanelPreviewRequest(query=query, row_limit=2, column_limit=1)
    )

    assert blocked.confirmation_required
    assert blocked.panel.status is DataLoadStatus.CONFIRMATION_REQUIRED
    assert {warning.code for warning in blocked.warnings} == {
        "coverage_incomplete",
        "lag_below_recommended",
    }
    assert blocked.cost.estimated_cells == 6

    confirmed = container.equity_workspace.preview_panel(
        ResearchPanelPreviewRequest(
            query=query,
            row_limit=2,
            column_limit=1,
            confirmed_warning_ids=tuple(warning.warning_id for warning in blocked.warnings),
        )
    )

    assert not confirmed.confirmation_required
    assert confirmed.truncated
    assert confirmed.cost.returned_rows == 2
    assert confirmed.cost.returned_columns == 1
    assert confirmed.cost.returned_cells == 2


def test_unknown_field_is_an_expected_invalid_query_result() -> None:
    container = build_container()
    result = container.equity_data.load_panel(
        ResearchPanelQuery(
            start=date(2024, 1, 3),
            end=date(2024, 1, 3),
            security_ids=("sec-005930-1",),
            field_ids=("unknown.field",),
        )
    )

    assert result.status is DataLoadStatus.INVALID_QUERY
    assert result.detail is not None and "unknown.field" in result.detail


def test_mock_resolves_every_field_of_implemented_default_graphs() -> None:
    """implemented 팩터는 mock 에서 바로 preview 된다(FACTORS.md) — 기본 graph 의 필드를 다 안다.

    #234 는 신용잔고 변화에 `price.shares_outstanding` 을 더했다. mock 이 그 필드를 모르면 graph 가
    필드 메타데이터 없이 검증에 떨어진다.
    """
    adapter = MockEquityDataAdapter.demo()
    for definition in build_default_factor_registry().all():
        if definition.availability is not FactorAvailability.IMPLEMENTED:
            continue
        assert definition.default_graph is not None
        metadata = adapter.resolve_factor_fields(definition.required_field_ids)
        resolved = {item.field_id for item in metadata.fields}
        assert resolved == set(definition.required_field_ids), definition.factor_id
        validation = validate_factor_graph(
            definition.default_graph, fields=metadata.fields, require_field_metadata=True
        )
        assert validation.valid, (definition.factor_id, validation)
