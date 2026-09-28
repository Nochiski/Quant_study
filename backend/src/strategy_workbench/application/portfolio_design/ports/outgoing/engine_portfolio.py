from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.strategy.facade.specification import StrategySpec

from ..._models import EngineCompatibility


class EnginePortfolioPort(Protocol):
    # 참여율이 실행 설정으로 옮겨가면서(1.2) 능력 판정이 전략만으로는 끝나지 않는다.
    def assess(self, spec: StrategySpec, environment: RunEnvironment) -> EngineCompatibility: ...
