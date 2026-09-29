"""Outgoing port: trial 을 실행하고 그 실행을 조회·취소한다 (검증 랩 spec D6).

구현은 bootstrap 이 `BacktestRunService` 와 전략 저장소를 감싸 주입한다(`assistant_chat` 의
`BacktestResultPort` 선례). 그래서 `experiment_run` 은 `backtest_run` 유스케이스에 의존하지 않는다.
실행 접수·슬롯·대기열·같은 입력 잇기는 감싼 실행 서비스가 소유한다.
"""

from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    RunStatus,
    SavedRevisionReference,
)
from strategy_workbench.domain.backtest.facade.trials import TrialLedger
from strategy_workbench.domain.strategy.facade.specification import StrategySpec


class TrialRunRejectedError(RuntimeError):
    """실행 서비스가 trial 실행 요청을 접수하지 않았다. 문장은 실행 서비스의 거절 문장이다."""


class TrialRunPort(Protocol):
    def validate(self, request: BacktestRunSpec) -> None:
        """실행 접수와 같은 판정(저장 리비전·해시·동결·계열·실행 설정·봉인·파라미터)으로 검사한다.

        거절은 감싼 실행 서비스의 접수 오류 그대로 올라가고, inbound 가 실행 접수와 같은 코드로
        옮긴다. 실행 요청이 아니므로 봉인 원장에 남기지 않는다.
        """
        ...

    def strategy(self, source: SavedRevisionReference) -> StrategySpec:
        """`validate` 를 통과한 저장 리비전의 전략."""
        ...

    def trial_ledger(self, lineage_id: str) -> TrialLedger: ...

    def start(self, request: BacktestRunSpec) -> str:
        """실행을 접수하고 run_id 를 돌려준다.

        Raises:
            TrialRunRejectedError: 실행 서비스가 요청을 접수하지 않았다.
        """
        ...

    def status(self, run_id: str) -> RunStatus: ...

    def cancel(self, run_id: str) -> None:
        """끝난 실행이면 아무것도 하지 않는다."""
        ...
