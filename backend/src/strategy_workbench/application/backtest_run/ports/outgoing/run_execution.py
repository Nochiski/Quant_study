"""run 하나를 어딘가에서 돌리고 진행·종결을 알리는 전송 포트(run-worker spec D10·D12·D13).

계산 자체는 `_computation.py` 의 `RunComputation.compute` 하나가 소유하고, 이 포트는 그 계산을
어디서 돌릴지(같은 프로세스의 스레드, worker 프로세스)만 가린다. 상태 전이·완료와 취소의 선형화·
저장은 서비스가 `on_finished` 안에서 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import (
    ResolvedBacktestRunSpec,
    RunFailureCode,
    RunStage,
    StrategyProvenance,
    StrategySource,
)

from .trial_ledger import RunHeadline


@dataclass(frozen=True)
class RunJob:
    """접수한 run 하나의 계산 입력. 서비스가 만들고 전송은 그대로 계산에 넘긴다."""

    run_id: str
    spec: ResolvedBacktestRunSpec
    provenance: StrategyProvenance
    strategy_source: StrategySource | None
    lineage_strategy_id: str | None


@dataclass(frozen=True)
class RunCommitted:
    """결과를 산출물 저장소에 커밋했다. 시도 원장에 적을 요약 값을 함께 싣는다.

    커밋만 끝났고 아직 보이지 않는다 — 완료 전이는 서비스가 취소와 선형화한 뒤 정한다.
    """

    sha256: str
    session_sharpe: float | None
    metric_registry_version: str
    headline: RunHeadline


@dataclass(frozen=True)
class RunFailed:
    """계산이 실패했다. `error_code`·`error` 는 상태에 싣는 값이다(`error` 는 서버 경로를 가렸다).

    `exception` 은 같은 프로세스에서 계산한 전송만 싣는 원래 예외다. 서버 로그의 stack trace 용이고
    상태에는 싣지 않는다. 다른 프로세스의 전송은 코드와 문장만 보낸다.
    """

    error_code: RunFailureCode
    error: str
    exception: BaseException | None = None


@dataclass(frozen=True)
class RunCancelled:
    """계산이 취소를 보고 멈췄다."""


RunOutcome = RunCommitted | RunFailed | RunCancelled


class RunListener(Protocol):
    """전송이 run 하나의 진행·종결을 알리는 곳. 전송의 감독 스레드가 서비스 잠금 밖에서 부른다.

    잠금 순서는 서비스 → 전송이다. 서비스는 자기 잠금을 쥔 채 `start`·`cancel` 을 부르므로,
    전송은 자기 잠금(예: 예비 worker 장부)을 쥔 채 listener 를 부르지 않는다. listener 는
    `start` 를 부른 스레드에서 동기로 불리지 않는다 — 불리면 서비스가 실행 수를 늘리기 전에
    줄인다.
    """

    def on_progress(self, stage: RunStage, fraction: float, message: str) -> None:
        """run 막대 위치(0~1)와 단계.

        취소가 이겼으면 `RunCancelledError` 를 낸다. 같은 프로세스 전송은 그 예외로 계산을 멈추고
        (협조적 취소), 다른 프로세스 전송의 감독 스레드는 삼키고 종결까지 읽는다.
        """
        ...

    def on_finished(self, outcome: RunOutcome) -> None:
        """종결을 한 번 알린다."""
        ...


class RunHandle(Protocol):
    def cancel(self) -> None:
        """취소를 요청한다. 막히지 않는다 — 멈춘 결과는 `on_finished` 로 온다."""
        ...


class RunExecutionPort(Protocol):
    def start(self, job: RunJob, listener: RunListener) -> RunHandle:
        """run 을 돌리기 시작하고 바로 돌아온다. 서비스 잠금 안에서 불려도 막히지 않는다.

        띄우지 못하면 예외(`RuntimeError`·`MemoryError` 등)를 올리고 `listener` 를 부르지 않는다.
        """
        ...

    def shutdown(self) -> None:
        """서버가 끝날 때 이 전송이 띄운 것을 정리한다."""
        ...
