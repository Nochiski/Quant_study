from __future__ import annotations

from pathlib import Path

from strategy_workbench.adapters.inbound.signal_research_cli.facade.command import run_cli
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import EquityDuckdbAdapter
from strategy_workbench.adapters.outbound.equity_duckdb.facade.signal_labels import (
    DuckdbSignalLabelAdapter,
)
from strategy_workbench.adapters.outbound.research_snapshot.facade.guard import (
    ResearchSnapshotGuard,
)
from strategy_workbench.adapters.outbound.signal_pipeline.facade.provider import (
    PortfolioSignalPipelineAdapter,
)
from strategy_workbench.application.portfolio_design.facade.design import PortfolioDesignService
from strategy_workbench.application.signal_research.facade.research import SignalResearchService
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry


def build_signal_research(preserved_equity_root: Path, repo_root: Path) -> SignalResearchService:
    guard = ResearchSnapshotGuard(preserved_equity_root, repo_root)
    # 어댑터가 파일을 읽기 전에도 보존 root의 완료·무결성·현재 코드 일치를 확인한다.
    guard.verify()
    equity = EquityDuckdbAdapter(preserved_equity_root)
    pipeline = PortfolioDesignService(
        equity,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=equity,
        factor_registry_version=build_default_factor_registry().version,
    )
    return SignalResearchService(
        guard, PortfolioSignalPipelineAdapter(pipeline), DuckdbSignalLabelAdapter(equity)
    )


def main(argv: list[str] | None = None) -> int:
    return run_cli(build_signal_research, argv)
