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
"""(실행기 작업 안의 완료 비율 0~1, 단계 이름, 설명). run 막대의 구간 배치는 유스케이스가 정한다."""
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


class EquityWipedOutError(RuntimeError):
    """세션 종료 자산이 0 이하가 되어 실행기가 멈췄다(자본 잠식). 서버 오류가 아니라 전략 결과다."""


class BacktestExecutorPort(Protocol):
    def execute(
        self,
        request: BacktestExecutionRequest,
        *,
        progress: ProgressCallback,
        cancelled: CancellationCheck,
    ) -> BacktestRunResult: ...
