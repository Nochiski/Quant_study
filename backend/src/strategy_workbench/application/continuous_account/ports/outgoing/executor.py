"""혼합 전략 신원을 단일 전략 manifest로 바꾸지 않는 실행 포트."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.application.backtest_run.facade.ports import BacktestDataset
from strategy_workbench.domain.analytics.facade.metrics import MetricDefinition, MetricValue
from strategy_workbench.domain.backtest.facade.runs import (
    DataWarning,
    ExecutionCore,
    RawArtifactBundle,
)
from strategy_workbench.domain.portfolio.facade.scheduled_construction import ScheduledTargetTape
from strategy_workbench.domain.strategy.facade.specification import StrategySpec

from ..._models import ContinuousAccountManifest


@dataclass(frozen=True)
class ContinuousExecutionRequest:
    manifest: ContinuousAccountManifest
    tape: ScheduledTargetTape
    dataset: BacktestDataset
    strategies: tuple[StrategySpec, ...]
    core: ExecutionCore


@dataclass(frozen=True)
class ContinuousExecutionOutput:
    artifacts: RawArtifactBundle
    engine_version: str
    warnings: tuple[DataWarning, ...]
    metric_definitions: tuple[MetricDefinition, ...]
    metrics: tuple[MetricValue, ...]


class ContinuousExecutorPort(Protocol):
    @property
    def engine_version(self) -> str: ...

    @property
    def metric_registry_version(self) -> str: ...

    def execute(
        self, request: ContinuousExecutionRequest, *, progress: Callable[[float, str], None]
    ) -> ContinuousExecutionOutput: ...
