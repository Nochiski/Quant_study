from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.equity_workspace.facade.ports import EquityDataPort
from strategy_workbench.application.equity_workspace.facade.workspace import (
    EquityWorkspaceService,
)


@dataclass(frozen=True)
class BackendContainer:
    equity_data: EquityDataPort
    equity_workspace: EquityWorkspaceService


def build_container(*, equity_adapter: str = "mock") -> BackendContainer:
    """Build one explicit dependency graph; unknown adapters fail instead of falling back."""
    if equity_adapter != "mock":
        raise ValueError(
            "unsupported equity adapter — "
            f"equity_adapter={equity_adapter!r} available=('mock',)"
        )
    equity_data = MockEquityDataAdapter.demo()
    return BackendContainer(
        equity_data=equity_data,
        equity_workspace=EquityWorkspaceService(equity_data),
    )
