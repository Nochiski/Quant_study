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
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import StrategyRepositoryPort


@dataclass(frozen=True)
class BackendContainer:
    equity_data: EquityDataPort
    equity_workspace: EquityWorkspaceService
    strategy_repository: StrategyRepositoryPort
    strategy_design: StrategyDesignService


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
    )
