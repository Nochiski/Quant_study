from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.strategy.facade.specification import StrategySpec


class StrategyNotFoundError(LookupError):
    pass


class StrategyRevisionConflictError(RuntimeError):
    pass


class StrategyRepositoryPort(Protocol):
    def add(self, spec: StrategySpec) -> None: ...

    def get(self, strategy_id: str, revision: int | None = None) -> StrategySpec: ...

    def append(self, spec: StrategySpec, *, expected_revision: int) -> None: ...
