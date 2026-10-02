"""예제용 데이터가 기존 회귀 앱을 바꾸지 않고 실제 선정 경로를 제공하는지 검증한다."""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import SourceFormat
from strategy_workbench.bootstrap import _container
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from tests.e2e_ideas_app import build_e2e_http_app, ideas_adapter

IDEAS = Path(__file__).parents[1] / "fixtures" / "strategy_documents" / "ideas"
ENVIRONMENT = RunEnvironment(
    start=date(2021, 1, 1), end=date(2021, 12, 31), universe_id="krx.common-stock"
)


def test_root_and_ideas_have_distinct_data_and_runtime_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STRATEGY_WORKBENCH_E2E_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("STRATEGY_WORKBENCH_E2E_IDEAS_PREFIX", "/ideas")
    monkeypatch.setenv("STRATEGY_WORKBENCH_DB_PATH", str(tmp_path / "root-strategy.sqlite3"))
    monkeypatch.setenv(
        "STRATEGY_WORKBENCH_RESEARCH_DB_PATH", str(tmp_path / "root-research.sqlite3")
    )
    monkeypatch.setenv(
        "STRATEGY_WORKBENCH_ASSISTANT_DB_PATH", str(tmp_path / "root-assistant.sqlite3")
    )
    monkeypatch.setenv(
        "STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH", str(tmp_path / "root-secrets.json")
    )
    monkeypatch.setenv("STRATEGY_WORKBENCH_EQUITY_ADAPTER", "mock")
    monkeypatch.setattr(_container, "DEFAULT_RUN_ARTIFACT_ROOT", tmp_path / "root-runs")
    original = MockEquityDataAdapter.demo()
    ideas = ideas_adapter()
    assert len(original.universe_securities("KRX", ENVIRONMENT.universe_id, ENVIRONMENT.start)) == 3
    assert len(ideas.universe_securities("KRX", ENVIRONMENT.universe_id, ENVIRONMENT.start)) == 100
    with TestClient(build_e2e_http_app()) as client:
        root = client.get("/api/v1/strategy-documents/contract").json()["contract"]
        wide = client.get("/ideas/api/v1/strategy-documents/contract").json()["contract"]
    assert root["dataset_snapshot_id"] == original.snapshot().snapshot_id
    assert wide["dataset_snapshot_id"] == ideas.snapshot().snapshot_id
    assert root["dataset_snapshot_id"] != wide["dataset_snapshot_id"]
    assert root["factor_registry_version"] == wide["factor_registry_version"]
    assert MockEquityDataAdapter.demo().snapshot() == original.snapshot()
    assert _container.DEFAULT_RUN_ARTIFACT_ROOT == tmp_path / "root-runs"
    for path in (
        "root-strategy.sqlite3",
        "root-research.sqlite3",
        "root-assistant.sqlite3",
        "ideas/strategy.sqlite3",
        "ideas/research.sqlite3",
        "ideas/assistant.sqlite3",
    ):
        assert (tmp_path / path).is_file(), path


@pytest.mark.parametrize("name", sorted(path.name for path in IDEAS.glob("*.yaml")))
def test_each_graph_idea_selects_real_targets_without_changing_its_document(name: str) -> None:
    adapter = ideas_adapter()
    authoring = StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version="ideas",
        dataset_snapshot_id=lambda: adapter.snapshot().snapshot_id,
        field_catalog=adapter,
    )
    compiled = authoring.compile(CompileRequest((IDEAS / name).read_text(), SourceFormat.YAML))
    assert compiled.spec is not None
    portfolio = PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="ideas",
    )
    preview = portfolio.preview(PortfolioPreviewRequest(compiled.spec, ENVIRONMENT))
    assert preview.engine.compatible
    assert preview.tape.frames
    assert all(frame.targets for frame in preview.tape.frames)
    if name == "top_trading_value.yaml":
        assert all(len(frame.targets) == 20 for frame in preview.tape.frames)
