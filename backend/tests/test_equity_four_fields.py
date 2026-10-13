"""#421: 네 원장 필드의 척도·PIT·결측·compile 계약을 연결한다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import EquityDuckdbAdapter
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.equity.facade.research_data import CellKind, ResearchPanelQuery
from strategy_workbench.domain.factor.facade.expression import FactorGraph, FieldNode, MissingPolicy
from strategy_workbench.domain.factor.facade.planning import compile_factor_plan
from tests.equity_fixture import build_workbench_root

FIELDS = (
    "flow.foreign_ownership",
    "flow.foreign_limit_exhaustion",
    "flow.pension_net_buy",
    "short.short_sale_volume",
)


@pytest.fixture(scope="module")
def ledger(tmp_path_factory: pytest.TempPathFactory) -> EquityDuckdbAdapter:
    root: Path = tmp_path_factory.mktemp("four-fields")
    return EquityDuckdbAdapter(build_workbench_root(root))


@pytest.mark.parametrize("provider", ["ledger", "mock"])
def test_four_fields_compile_with_the_same_public_metadata(
    ledger: EquityDuckdbAdapter,
    provider: str,
) -> None:
    adapter = ledger if provider == "ledger" else MockEquityDataAdapter.demo()
    profiles = {p.field_id: p for p in adapter.list_fields()}
    metadata = adapter.resolve_factor_fields(FIELDS).fields
    assert {m.field_id for m in metadata} == set(FIELDS)
    for field_id, unit in zip(FIELDS, ("pct", "pct", "KRW", "shares"), strict=True):
        assert profiles[field_id].unit == unit
        assert profiles[field_id].recommended_lag_sessions == 1
        graph = FactorGraph(nodes=(FieldNode("value", field_id, "field"),), output_node_id="value")
        plan = compile_factor_plan(
            graph,
            registry_version="test",
            missing=MissingPolicy.DROP,
            fields=metadata,
            require_field_metadata=True,
        )
        assert plan.required_field_ids == (field_id,)
        assert plan.steps[0].output_unit == unit


def test_percent_amount_and_volume_keep_the_ledger_values_and_pit(
    ledger: EquityDuckdbAdapter,
) -> None:
    result = ledger.load_panel(
        ResearchPanelQuery(
            date(2024, 1, 8),
            date(2024, 1, 12),
            ("005930:1", "000660:1"),
            FIELDS,
        )
    )
    assert result.ok
    cells = {(c.security_id, c.as_of, c.field_id): c for c in result.cells}
    for field_id, expected in zip(FIELDS, (52.79, 62.79, -123_000_000.0, 1_000.0), strict=True):
        cell = cells["005930:1", date(2024, 1, 9), field_id]
        assert cell.value == expected
        assert cell.kind is CellKind.OBSERVED
        assert cell.available_date == date(2024, 1, 8)
        assert cell.source_effective_date == date(2024, 1, 8)
        assert ("005930:1", date(2024, 1, 8), field_id) not in cells
    # KIS 단독 수급 행은 measured여도 키움 전용 연기금 칸이 없다. 다른 순매수로 채우지 않는다.
    pension = cells["000660:1", date(2024, 1, 9), "flow.pension_net_buy"]
    assert (pension.value, pension.kind) == (None, CellKind.MISSING)
    # 같은 src_omitted라도 수급은 결측, 공매도 거래량은 원천 계약에 따라 0이다.
    flow = cells["005930:1", date(2024, 1, 10), "flow.foreign_ownership"]
    volume = cells["005930:1", date(2024, 1, 10), "short.short_sale_volume"]
    assert (flow.value, flow.kind) == (None, CellKind.MISSING)
    assert (volume.value, volume.kind) == (0.0, CellKind.SOURCE_OMITTED_ZERO)
    for field_id in FIELDS[:3]:
        cell = cells["005930:1", date(2024, 1, 11), field_id]
        assert cell.value is None and cell.kind is CellKind.NOT_COLLECTED
    for field_id in FIELDS[:3]:
        zero = cells["005930:1", date(2024, 1, 12), field_id]
        assert (zero.value, zero.kind) == (0.0, CellKind.OBSERVED)
    raw = ledger.load_raw_observations(
        RawObservationQuery(
            "KRX",
            "krx.common-stock",
            date(2024, 1, 8),
            date(2024, 1, 12),
            FIELDS,
            0,
        )
    )
    assert raw.ok
    for observation in raw.observations:
        if observation.security_id not in ("005930:1", "000660:1"):
            continue
        for field in observation.fields:
            panel = cells[observation.security_id, observation.as_of, field.field_id]
            assert (field.value, field.kind, field.available_date) == (
                panel.value,
                panel.kind,
                panel.available_date,
            )


def test_new_field_coverage_counts_only_cells_the_source_can_supply(
    ledger: EquityDuckdbAdapter,
) -> None:
    profiles = {p.field_id: p for p in ledger.list_fields()}
    assert profiles["flow.pension_net_buy"].coverage.estimated_coverage_pct == pytest.approx(
        200 / 68
    )
    assert profiles["short.short_sale_volume"].coverage.estimated_coverage_pct == pytest.approx(
        300 / 68
    )


@pytest.mark.parametrize("provider", ["duckdb", "mock"])
def test_http_catalog_and_explanation_expose_the_four_field_contracts(
    tmp_path: Path,
    provider: str,
) -> None:
    root = build_workbench_root(tmp_path / "equity") if provider == "duckdb" else None
    with TestClient(build_http_app(equity_adapter=provider, equity_root=root)) as client:
        response = client.get("/api/v1/equity/catalog", params={"page_size": 100})
        assert response.status_code == 200
        fields = {field["field_id"]: field for field in response.json()["fields"]}
        for field_id, unit in zip(FIELDS, ("pct", "pct", "KRW", "shares"), strict=True):
            assert fields[field_id]["unit"] == unit
            assert fields[field_id]["recommended_lag_sessions"] == 1
            body = {
                "graph": {
                    "nodes": [{"kind": "field", "node_id": "value", "field_id": field_id}],
                    "output_node_id": "value",
                }
            }
            validation = client.post("/api/v1/factors/validate", json=body)
            assert validation.status_code == 200
            assert validation.json()["valid"]
            explanation = client.post("/api/v1/factors/explain", json=body)
            assert explanation.status_code == 200
            assert explanation.json()["plan"]["steps"][0]["output_unit"] == unit
