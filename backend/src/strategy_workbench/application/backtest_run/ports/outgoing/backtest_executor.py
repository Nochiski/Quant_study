from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    StrategyProvenance,
)
from strategy_workbench.domain.portfolio.facade.construction import TargetTape

from .backtest_data import BacktestDataset

ProgressCallback = Callable[[float, str, str], None]
CancellationCheck = Callable[[], bool]


@dataclass(frozen=True)
class BacktestExecutionRequest:
    run_id: str
    spec: BacktestRunSpec  # resolved: `strategy` is always set here
    target_tape: TargetTape
    dataset: BacktestDataset
    strategy_provenance: StrategyProvenance


class RunCancelledError(RuntimeError):
    pass


class BacktestExecutorPort(Protocol):
    def execute(
        self,
        request: BacktestExecutionRequest,
        *,
        progress: ProgressCallback,
        cancelled: CancellationCheck,
    ) -> BacktestRunResult: ...
