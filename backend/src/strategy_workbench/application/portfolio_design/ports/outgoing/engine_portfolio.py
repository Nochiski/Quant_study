from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.strategy.facade.specification import StrategySpec

from ..._models import EngineCompatibility


class EnginePortfolioPort(Protocol):
    def assess(self, spec: StrategySpec) -> EngineCompatibility: ...
