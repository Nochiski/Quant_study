"""run 하나를 어딘가에서 돌리고 진행·종결을 알리는 전송 포트(run-worker spec D8·D10·D12·D13).

계산 자체는 `_computation.py` 의 `RunComputation.compute` 하나가 소유하고, 이 포트는 그 계산을
어디서 돌릴지(같은 프로세스의 스레드, worker 프로세스)만 가린다. 상태 전이·완료와 취소의 선형화·
저장은 서비스가 `on_finished` 안에서 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
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
class RunFootprint:
    """계산이 읽은 입력의 크기. 메모리 추정식을 다시 맞출 근거로 로그에만 남긴다(spec D8).

    추정식은 세션 수 하나로 예약치를 정한다. 다른 모양의 전략(필드·팩터·종목이 많은)에서 실측
    최대 RSS 와 어긋나면 이 값들로 원인을 가른다.
    """

    # 원시 관측을 읽은 세션 수(측정 구간 + 팩터 워밍업).
    sessions: int
    # 읽은 원천 필드 수.
    fields: int
    # 전략의 팩터 수.
    factors: int
    # 원시 관측 행 수 — (세션, 종목) 쌍, 곧 유니버스 행이다.
    observation_rows: int


@dataclass(frozen=True)
class RunCommitted:
    """결과를 산출물 저장소에 커밋했다. 시도 원장에 적을 요약 값을 함께 싣는다.

    커밋만 끝났고 아직 보이지 않는다 — 완료 전이는 서비스가 취소와 선형화한 뒤 정한다.
    `footprint` 는 로그용이고 상태·원장에 싣지 않는다.
    """

    sha256: str
    session_sharpe: float | None
    metric_registry_version: str
    headline: RunHeadline
    footprint: RunFootprint | None = None


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


@dataclass(frozen=True)
class RunMemory:
    """메모리 가드가 한 번 판정할 때 읽는 전송의 측정값(spec D8). 판정 규칙은 서비스가 소유한다."""

    # 지금 새로 쓸 수 있는 물리 메모리(바이트). 읽지 못한 전송은 0 을 준다 — 가드가 run 을 하나씩만
    # 띄운다(보수 쪽).
    available_bytes: int
    # 도는 run_id → 그 run 을 계산하는 worker 의 지금 RSS(바이트). worker 를 아직 손에 넣지 않았거나
    # 잴 수 없는 run 은 없다 — 가드는 그 run 의 예약치를 다 남은 몫으로 센다.
    rss_bytes: Mapping[str, int]
    # 새 run 을 띄우면 전송이 그 run 몫 밖에서 곧 더 쓸 메모리(바이트, 다시 채울 예비 worker).
    overhead_bytes: int


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

    def memory(self) -> RunMemory | None:
        """메모리 가드가 읽을 측정값. 서비스 잠금 안에서 불린다 — 파이프·프로세스를 기다리지 않는다.

        None 이면 이 전송은 run 마다 메모리를 따로 잴 수 없어(같은 프로세스의 스레드) 가드를 쓰지
        않는다.
        """
        ...

    def set_memory_hold(self, held: bool) -> None:
        """메모리 가드가 run 을 대기시키고 있는가. 서비스 잠금 안에서 배정할 때마다 불린다.

        참인 동안 전송은 run 몫이 아닌 메모리를 새로 쓰지 않는다(예비 worker 다시 채우기를 미룬다).
        거짓으로 돌아오면 미룬 것을 채운다. 막히지 않는다.
        """
        ...

    def shutdown(self) -> None:
        """서버가 끝날 때 이 전송이 띄운 것을 정리한다."""
        ...
