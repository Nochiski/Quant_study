from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    SQLiteStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.runs import BacktestRunService
from strategy_workbench.application.equity_workspace.facade.ports import EquityDataPort
from strategy_workbench.application.equity_workspace.facade.workspace import (
    EquityWorkspaceService,
)
from strategy_workbench.application.factor_research.facade.research import (
    FactorResearchService,
)
from strategy_workbench.application.portfolio_design.facade.design import PortfolioDesignService
from strategy_workbench.application.portfolio_design.facade.trace import StrategyTraceService
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    StrategyAuthoringService,
    StrategyDocumentService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import StrategyRepositoryPort
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry


@dataclass(frozen=True)
class BackendContainer:
    equity_data: EquityDataPort
    equity_workspace: EquityWorkspaceService
    strategy_repository: StrategyRepositoryPort
    strategy_design: StrategyDesignService
    strategy_authoring: StrategyAuthoringService
    strategy_documents: StrategyDocumentService
    factor_research: FactorResearchService
    portfolio_design: PortfolioDesignService
    strategy_traces: StrategyTraceService
    backtest_runs: BacktestRunService


def build_container(
    *,
    equity_adapter: str = "mock",
    artifact_root: Path | None = None,
    strategy_repository_path: str | Path | None = None,
) -> BackendContainer:
    """Build one dependency graph; ``None`` selects isolated in-memory SQLite for tests.

    The HTTP runtime supplies a durable file path explicitly. This keeps test application
    factories isolated while both environments exercise the same persistent adapter contract.
    """
    if equity_adapter != "mock":
        raise ValueError(
            f"unsupported equity adapter — equity_adapter={equity_adapter!r} available=('mock',)"
        )
    equity_data = MockEquityDataAdapter.demo()
    engine_portfolio = BacktestEnginePortfolioAdapter()
    strategy_repository = SQLiteStrategyRepository(strategy_repository_path)
    factor_registry = build_default_factor_registry()
    portfolio_design = PortfolioDesignService(
        equity_data,
        engine_portfolio,
        factor_metadata=equity_data,
        factor_registry_version=factor_registry.version,
    )
    metric_registry = build_default_metric_registry()
    strategy_authoring = StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version=factor_registry.version,
        dataset_snapshot_id=lambda: equity_data.snapshot().snapshot_id,
    )
    run_artifact_root = artifact_root or (
        Path(__file__).resolve().parents[3] / ".local" / "backtest-runs"
    )
    strategy_traces = StrategyTraceService(portfolio_design, strategy_repository)
    return BackendContainer(
        equity_data=equity_data,
        equity_workspace=EquityWorkspaceService(equity_data),
        strategy_repository=strategy_repository,
        strategy_design=StrategyDesignService(
            strategy_repository,
            new_id=lambda: str(uuid4()),
        ),
        strategy_authoring=strategy_authoring,
        strategy_documents=StrategyDocumentService(
            strategy_authoring, strategy_repository, new_id=lambda: str(uuid4())
        ),
        factor_research=FactorResearchService(
            factor_registry,
            metadata_source=equity_data,
            observation_source=equity_data,
        ),
        portfolio_design=portfolio_design,
        strategy_traces=strategy_traces,
        backtest_runs=BacktestRunService(
            portfolio_design,
            strategy_repository,
            equity_data,
            BacktestEngineExecutorAdapter(metric_registry),
            LocalArtifactStore(run_artifact_root),
            new_id=lambda: str(uuid4()),
        ),
    )
