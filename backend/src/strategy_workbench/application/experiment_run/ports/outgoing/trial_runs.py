"""Outgoing port: 기반 요청을 검사하고 trial 을 실행·조회·취소한다 (검증 랩 spec D6).

구현은 bootstrap 이 `BacktestRunService` 를 감싸 주입한다(`assistant_chat` 의 `BacktestResultPort`
선례). 그래서 `experiment_run` 은 `backtest_run` 유스케이스에 의존하지 않는다. 요청 해소·계열·접수
판정과 슬롯·대기열·같은 입력 잇기는 감싼 실행 서비스가 소유한다.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import (
    AdmissionRejectionCode,
    BacktestRunResult,
    BacktestRunSpec,
    BacktestRunState,
)
from strategy_workbench.domain.backtest.facade.trials import TrialLedger


class TrialRunRejectedError(RuntimeError):
    """실행 서비스가 요청을 접수하지 않았다. `code` 는 실행 접수 거절 코드다.

    감싼 실행 서비스의 원래 거절은 `__cause__` 로 남는다 — 실험 만들기·재시도의 inbound 가 실행
    시작과 같은 HTTP 거절로 옮긴다.
    """

    def __init__(self, code: AdmissionRejectionCode, message: str) -> None:
        super().__init__(message)
        self.code: AdmissionRejectionCode = code


class TrialResultUnreadableError(RuntimeError):
    """완료된 실행의 결과 파일을 읽을 수 없다(지워졌거나 기록한 해시·지금 모델과 다르다)."""


@dataclass(frozen=True)
class RunSlotUsage:
    """동시 실행 슬롯 사용량(spec D6). 실험 목록 화면이 trial 수로 추정하지 않게 싣는다."""

    total: int
    running: int


@dataclass(frozen=True)
class AdmittedRun:
    """실행 접수 판정을 통과한 기반 요청과 그 계열 원장."""

    # 실행 서비스가 전략·실행 설정·파라미터 값을 해소한 실행 spec.
    run: BacktestRunSpec
    ledger: TrialLedger


class TrialRunPort(Protocol):
    def admit(self, request: BacktestRunSpec) -> AdmittedRun:
        """실행 시작과 같은 판정(preflight 포함)으로 검사하되 접수하지 않는다. 실행 요청이 아니므로
        봉인 원장에 남기지 않는다.

        Raises:
            TrialRunRejectedError: 실행 서비스가 요청을 접수하지 않는다.
        """
        ...

    def start(self, request: BacktestRunSpec, *, trial_key: str, owner: str) -> str:
        """실행을 접수하고 run_id 를 돌려준다. `trial_key` 로 계열 원장에 적는다.

        `owner` 는 실험 id 다. 실행 서비스가 대기 순서를 실험끼리 번갈아 정하고, 같은 입력을 이은
        run 은 소유자가 모두 취소해야 멈춘다.

        Raises:
            TrialRunRejectedError: 실행 서비스가 요청을 접수하지 않았다.
        """
        ...

    def result(self, run_id: str) -> BacktestRunResult:
        """완료된 실행의 결과(이어 붙인 검증 곡선).

        Raises:
            TrialResultUnreadableError: 결과 파일을 읽을 수 없다.
        """
        ...

    def trial_ledger(self, request: BacktestRunSpec) -> TrialLedger:
        """기반 요청이 속한 계열의 원장. 계열은 실행 서비스가 실행 접수와 같은 규칙으로 정한다.

        창 고르기는 실행마다 원장에 적힌 세션 샤프를 학습 점수로 읽고, 후보 선택은 N·시도 대표
        샤프로 DSR 스냅숏을 낸다.
        """
        ...

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        """양끝을 포함한 거래 세션(워크포워드 엠바고·검증 곡선 기준점)."""
        ...

    def states(self, run_ids: Collection[str]) -> Mapping[str, BacktestRunState]:
        """여러 실행의 상태를 한 번에 읽는다."""
        ...

    def slot_usage(self) -> RunSlotUsage:
        """지금 슬롯 수와 도는 실행 수."""
        ...

    def schedule(self, owner: str, *, paused: bool, priority: int) -> None:
        """실험(`owner`)의 대기 실행을 멈추거나 풀고, 한 차례에 배정할 수를 우선순위로 정한다."""
        ...

    def cancel(self, run_id: str, *, owner: str) -> None:
        """`owner` 가 실행에서 빠진다. 다른 소유자가 남았거나 끝난 실행이면 멈추지 않는다."""
        ...
