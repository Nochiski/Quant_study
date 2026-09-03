from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.equity_workspace.facade.ports import EquityDataPort
from strategy_workbench.application.equity_workspace.facade.workspace import (
    EquityWorkspaceService,
)
from strategy_workbench.application.factor_research.facade.research import (
    FactorResearchService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import StrategyRepositoryPort
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry


@dataclass(frozen=True)
class BackendContainer:
    equity_data: EquityDataPort
    equity_workspace: EquityWorkspaceService
    strategy_repository: StrategyRepositoryPort
    strategy_design: StrategyDesignService
    factor_research: FactorResearchService


def build_container(*, equity_adapter: str = "mock") -> BackendContainer:
    """Build one explicit dependency graph; unknown adapters fail instead of falling back."""
    if equity_adapter != "mock":
        raise ValueError(
            f"unsupported equity adapter — equity_adapter={equity_adapter!r} available=('mock',)"
        )
    equity_data = MockEquityDataAdapter.demo()
    strategy_repository = InMemoryStrategyRepository()
    return BackendContainer(
        equity_data=equity_data,
        equity_workspace=EquityWorkspaceService(equity_data),
        strategy_repository=strategy_repository,
        strategy_design=StrategyDesignService(
            strategy_repository,
            new_id=lambda: str(uuid4()),
        ),
        factor_research=FactorResearchService(build_default_factor_registry(), equity_data),
    )
