from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.adapters.outbound.equity_mock._fixture import (
    MOCK_SPLIT,
    build_demo_fixture,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.application.equity_workspace.facade.workspace import (
    FieldCatalogQuery,
    ResearchPanelPreviewRequest,
)
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.domain.equity.facade.research_data import (
    SNAPSHOT_CONTRACT_SEPARATOR,
    CellKind,
    DataLoadStatus,
    DatasetFieldProfile,
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


_MOCK = "strategy_workbench.adapters.outbound.equity_mock"


def _changed(
    profiles: tuple[DatasetFieldProfile, ...], **changes: object
) -> tuple[DatasetFieldProfile, ...]:
    return tuple(
        replace(profile, **changes) if profile.field_id == "price.close" else profile
        for profile in profiles
    )


@pytest.mark.parametrize(
    ("edit", "moves"),
    [
        pytest.param(lambda mp, p: _changed(p, unit="USD"), (False, True), id="unit"),
        pytest.param(
            lambda mp, p: _changed(p, recommended_lag_sessions=2), (False, True), id="lag"
        ),
        pytest.param(
            lambda mp, p: mp.setattr(f"{_MOCK}._adapter.ADJUSTED_FIELD_BY_RAW", {}) or p,
            (False, True),
            id="adjusted-pair",
        ),
        pytest.param(
            lambda mp, p: (
                mp.setattr(
                    f"{_MOCK}._fixture.MOCK_SPLIT",
                    replace(MOCK_SPLIT, effective=date(2021, 5, 7)),
                )
                or p
            ),
            (True, False),
            id="fixture-data",
        ),
        pytest.param(
            lambda mp, p: (
                mp.setattr(f"{_MOCK}._fixture.adjusted_close", lambda raw_close, **_: raw_close)
                or p
            ),
            (True, False),
            id="observations",
        ),
        pytest.param(
            lambda mp, p: _changed(
                p,
                label="종가 ",
                description="문장만 바꿨다",
                evidence="-",
                disclosure_basis="-",
                available_date_basis="-",
            ),
            (False, False),
            id="prose",
        ),
        pytest.param(lambda mp, p: p[::-1], (False, False), id="declaration-order"),
    ],
)
def test_mock_snapshot_id_moves_with_data_and_declared_meaning_only(
    monkeypatch: pytest.MonkeyPatch,
    edit: Callable[[pytest.MonkeyPatch, tuple[DatasetFieldProfile, ...]], object],
    moves: tuple[bool, bool],
) -> None:
    """이슈 #235·#291 리뷰: mock id 는 "fixture 데이터의 판:선언표의 판" 이다.

    데이터(분할 사건·관측값 등)가 바뀌면 앞부분이, 선언의 뜻(단위·랙·조정 짝)이 바뀌면 뒷부분이
    바뀐다.
    문장이나 선언 순서만 바꾼 변경은 id 를 흔들지 않는다 — 흔들면 문장을 고친 PR 마다 재현 지문·캐시
    키·시각 기준선이 헛되이 바뀐다.
    """
    before = MockEquityDataAdapter.demo().snapshot().snapshot_id
    assert re.fullmatch(r"mock-equity-v0\.2-[0-9a-f]{16}:[0-9a-f]{16}", before)
    profiles = edit(monkeypatch, MockEquityDataAdapter.demo().list_fields())
    assert isinstance(profiles, tuple)
    changed = MockEquityDataAdapter(replace(build_demo_fixture(), profiles=profiles))
    after = changed.snapshot().snapshot_id

    source, _, contract = before.partition(SNAPSHOT_CONTRACT_SEPARATOR)
    after_source, _, after_contract = after.partition(SNAPSHOT_CONTRACT_SEPARATOR)
    assert (after_source != source, after_contract != contract) == moves


def test_panel_hides_future_consensus_revision_until_the_lagged_session() -> None:
    # 개정값의 available_date 는 01-05 다. 컨센서스 랙은 원장처럼 1세션이라(#230) 다음 세션인
    # 01-08 부터 보이고, 01-05 까지는 이전 값이다.
    container = build_container()
    result = container.equity_data.load_panel(
        ResearchPanelQuery(
            start=date(2024, 1, 5),
            end=date(2024, 1, 8),
            security_ids=("sec-005930-1",),
            field_ids=("consensus.forward_eps",),
        )
    )

    assert (
        _cell_value(
            result,
            as_of=date(2024, 1, 5),
            security_id="sec-005930-1",
            field_id="consensus.forward_eps",
        )
        == 5_000.0
    )
    assert (
        _cell_value(
            result,
            as_of=date(2024, 1, 8),
            security_id="sec-005930-1",
            field_id="consensus.forward_eps",
        )
        == 5_400.0
    )


def test_mock_net_income_is_pit_ttm_that_switches_one_session_after_filing() -> None:
    """mock 도 duckdb 와 같은 의미다 — `financial.net_income` 은 공시일 기준 최근 4분기 합(TTM)이다.

    #212: 기간 개념이 없던 mock 은 분기·연간 혼재를 못 잡았다. 000660 은 2023 3분기 보고서가 늦게
    (01-05) 접수돼, 재무 랙 1세션(원장과 같다, #230) 뒤인 01-08 부터 3분기 말 TTM 이고 그 전 세션은
    반기 말 TTM 이다. 4분기를 채울 수 없는 종목(035420)은 값이 없다 — 3개월 값으로 대신하지 않는다.
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
            start=date(2024, 1, 5),
            end=date(2024, 1, 8),
            security_ids=("sec-000660-1", "sec-035420-1"),
            field_ids=("financial.net_income",),
        )
    )
    cells = {(cell.as_of, cell.security_id): cell for cell in result.cells}
    before = cells[(date(2024, 1, 5), "sec-000660-1")]
    after = cells[(date(2024, 1, 8), "sec-000660-1")]
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
    assert kinds[("sec-005930-1", date(2024, 1, 4))] is CellKind.MISSING  # 수급 원천 생략(#371)
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
