from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    ResolvedBacktestRunSpec,
    StrategyProvenance,
    StrategySource,
)
from strategy_workbench.domain.portfolio.facade.construction import TargetTape

from .backtest_data import BacktestDataset

ProgressCallback = Callable[[float, str], None]
"""(실행기 작업 안의 완료 비율 0~1, 설명). run 막대의 구간 배치와 단계(`RunStage`)는 유스케이스가
정한다 — 실행기는 단계 이름을 짓지 않는다(DR-B-03)."""
CancellationCheck = Callable[[], bool]


@dataclass(frozen=True)
class BacktestExecutionRequest:
    run_id: str
    spec: ResolvedBacktestRunSpec
    target_tape: TargetTape
    dataset: BacktestDataset
    strategy_provenance: StrategyProvenance
    # 기본값을 두지 않는다 — 빠뜨린 호출자가 manifest 출처를 조용히 비우지 않게(#497).
    strategy_source: StrategySource | None
    lineage_strategy_id: str | None


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
