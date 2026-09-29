"""Outgoing port: 기반 요청을 검사하고 trial 을 실행·조회·취소한다 (검증 랩 spec D6).

구현은 bootstrap 이 `BacktestRunService` 를 감싸 주입한다(`assistant_chat` 의 `BacktestResultPort`
선례). 그래서 `experiment_run` 은 `backtest_run` 유스케이스에 의존하지 않는다. 요청 해소·계열·접수
판정과 슬롯·대기열·같은 입력 잇기는 감싼 실행 서비스가 소유한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import BacktestRunSpec, RunStatus
from strategy_workbench.domain.backtest.facade.trials import TrialLedger


class TrialRunRejectedError(RuntimeError):
    """실행 서비스가 trial 실행 요청을 접수하지 않았다. `code` 는 실행 접수 거절 코드다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AdmittedRun:
    """실행 접수 판정을 통과한 기반 요청과 그 계열 원장."""

    # 실행 서비스가 전략·실행 설정·파라미터 값을 해소한 실행 spec.
    run: BacktestRunSpec
    ledger: TrialLedger


class TrialRunPort(Protocol):
    def admit(self, request: BacktestRunSpec) -> AdmittedRun:
        """실행 시작과 같은 판정(preflight 포함)으로 검사하되 접수하지 않는다.

        거절은 감싼 실행 서비스의 접수 오류 그대로 올라가고, inbound 가 실행 접수와 같은 코드로
        옮긴다. 실행 요청이 아니므로 봉인 원장에 남기지 않는다.
        """
        ...

    def start(self, request: BacktestRunSpec, *, trial_key: str) -> str:
        """실행을 접수하고 run_id 를 돌려준다. `trial_key` 로 계열 원장에 적는다.

        Raises:
            TrialRunRejectedError: 실행 서비스가 요청을 접수하지 않았다.
        """
        ...

    def status(self, run_id: str) -> RunStatus: ...

    def cancel(self, run_id: str) -> None:
        """끝난 실행이면 아무것도 하지 않는다."""
        ...
