"""진행 중 턴의 owner (설계 spec D3).

`backtest_run`의 `BacktestRunService`와 같은 구조다: 작업을 스레드에서 돌리고, 결과를 이벤트로
저장소에 append하며, 클라이언트는 sequence를 들고 폴링·재개한다. 그래서 클라이언트가 스트림
연결을 끊어도 턴은 상한 안에서 계속 돌고 이벤트는 남는다. 멈추려면 취소를 불러야 한다.

## 단일 워커 전제

진행 중 턴의 레지스트리(`dict` + `RLock`)와 취소 신호(`threading.Event`)는 **이 프로세스 안에만**
있다. 이 앱이 로컬 단일 사용자 도구이고 backend가 uvicorn 단일 워커로 돈다는 전제다. 워커를
늘리면 다른 워커가 시작한 턴은 취소할 수 없고 `state()`가 레지스트리 대신 저장소만 보게 된다.
그때는 레지스트리를 프로세스 밖(DB 행 + 취소 플래그)으로 옮겨야 한다.

## 세션 슬롯은 스레드가 끝날 때까지 잡혀 있다

레지스트리에 항목이 있으면 그 세션은 점유 중이다. **취소했다는 사실은 슬롯을 풀지 않는다.** 취소는
`threading.Event`를 세우는 best-effort 신호일 뿐이고, 러너 스레드는 공급자가 다음 이벤트를 낼
때까지 그 신호를 읽지 못한다. 그 사이에 다음 턴을 받아 주면 한 세션에 두 스레드가 겹쳐서
`ChatMessage` 이력이 `[user, user, assistant]`처럼 역할 교대가 깨진 순서로 저장되고(공급자 요청
자체가 망가진다), 새 턴의 `accepted_sequence`보다 큰 sequence를 이전 턴이 계속 쓴다. 그래서
슬롯은 `_finish`가 항목을 뺄 때만 풀린다.

## Failure 우선순위

한 턴에서 **턴 상태가 되는 Failure는 먼저 확정된 하나뿐이다**(spec D3). 뒤이어 들어오는 Failure는
이벤트로 저장되지만 상태를 바꾸지 않는다. 러너의 타임아웃은 `TIMEOUT`을 **먼저 확정한 뒤** 취소
신호를 보내므로, 그 신호를 받은 adapter가 내는 `CANCELLED`는 이벤트로만 남는다. 이 규칙이 없으면
같은 턴이 "취소됨"으로도 "시간 초과"로도 보이고, 어느 쪽이 원인인지 이력에서 알 수 없다.

라운드·검색·토큰 상한은 adapter가 집행하므로 `TOOL_ROUNDS_EXCEEDED`·`TOKEN_BUDGET_EXCEEDED`도
공급자가 내는 Failure로 도착한다. 러너는 그것을 상태로 옮길 뿐 따로 세지 않는다.

## 어떤 경로로 끝나도 슬롯은 풀리고 턴은 종료 상태가 된다

`_drain`은 소비·크래시 기록·스트림 닫기·종료 기록을 분리해, `_finish`가 **정확히 한 번** 돌게
한다. `_finish`는 저장소 예외를 삼키고 레지스트리 pop을 먼저 한다. 종료를 기록하지 못하는 것과
세션이 영원히 잠기는 것 중에서는 전자가 훨씬 낫다. 잠기면 사용자는 사이드바에서 아무 질문도 보낼
수 없고 프로세스를 재시작해야 한다.

크래시로 끝난 턴에도 `Failure` 이벤트를 best-effort로 남긴다. 화면은 이벤트 열만 보고 턴의 끝을
알기 때문에, 종료 이벤트 없이 스트림이 닫히면 사용자는 "멈춘 채 끝난" 턴을 본다.

## 타임아웃의 범위

턴당 벽시계 타임아웃은 **이벤트 사이**에서 본다. 공급자가 아무 이벤트도 내지 않은 채 멈춰 있으면
여기서 깨우지 못하고, 그 경우는 adapter가 건 HTTP 타임아웃이 막는다. 상한을 넘겨 도는 턴을
잘라 내는 것이 목적이지 응답 없는 소켓을 감시하는 것이 목적이 아니다.

상한을 넘기면 `TIMEOUT`을 확정하고 취소 신호를 보낸 뒤 **유예(`grace_seconds`, 기본 10초)** 동안만
더 읽어 adapter가 마무리로 내는 이벤트를 저장한다. 유예가 지나면 스트림을 닫는다. 즉 한 턴의 실제
벽시계 상한은 `timeout_seconds + grace_seconds`다. 유예를 턴 상한과 같은 값으로 두면 spec D3이
말하는 300초가 실제로는 600초가 되므로 따로 둔다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from strategy_workbench.domain.assistant.facade.models import (
    DEFAULT_TURN_TIMEOUT_SECONDS,
    ChatEvent,
    Failure,
    FailureCode,
    SequencedEvent,
    Turn,
    TurnStatus,
)

from ._chat import AssistantChatService
from ._models import TurnContext
from .ports.outgoing.chat_sessions import ChatSessionRepository

__all__ = [
    "DEFAULT_TURN_GRACE_SECONDS",
    "AssistantTurnRunner",
    "TurnInProgressError",
    "TurnThread",
    "TurnThreadFactory",
]

logger = logging.getLogger(__name__)

# 취소 신호를 보낸 뒤 adapter가 마무리 이벤트를 낼 시간. 턴 상한(`DEFAULT_TURN_TIMEOUT_SECONDS`)과
# 달리 `TurnRequest`로 나가지 않는 러너 내부 정책이라 여기가 owner다(spec D9: 값과 집행의 분리).
DEFAULT_TURN_GRACE_SECONDS = 10.0


class TurnInProgressError(RuntimeError):
    """세션에 이미 도는 턴이 있다. 한 세션의 턴은 한 번에 하나다.

    취소한 직후에도 스레드가 아직 돌고 있으면 이 예외가 난다. 취소는 신호일 뿐 종료가 아니다.
    """

    def __init__(self, session_id: str, turn_id: str) -> None:
        super().__init__(
            "a turn is already running for this session — "
            f"session_id={session_id!r} running_turn_id={turn_id!r}; "
            "cancel it and wait for it to finish"
        )
        self.session_id = session_id
        self.turn_id = turn_id


class TurnThread(Protocol):
    """러너가 쓰는 스레드의 최소 계약. 테스트는 즉시·수동 실행 구현을 넣는다."""

    def start(self) -> None: ...


class TurnThreadFactory(Protocol):
    def __call__(self, *, target: Callable[[], None], name: str, daemon: bool) -> TurnThread: ...


def _default_thread_factory(*, target: Callable[[], None], name: str, daemon: bool) -> TurnThread:
    return threading.Thread(target=target, name=name, daemon=daemon)


@dataclass
class _Running:
    """레지스트리 한 칸. 항목이 있다는 것 자체가 "그 세션은 점유 중"이라는 뜻이다."""

    turn: Turn
    cancel: threading.Event
    decided: TurnStatus | None = None

    def view(self) -> Turn:
        """바깥에 보여 줄 턴. 확정된 종료 사유가 있으면 그것을 반영한다."""
        if self.decided is None:
            return self.turn
        return _with_status(self.turn, self.decided, finished_at=None)


class AssistantTurnRunner:
    def __init__(
        self,
        chat_service: AssistantChatService,
        sessions: ChatSessionRepository,
        *,
        now: Callable[[], datetime],
        new_id: Callable[[], str],
        thread_factory: TurnThreadFactory = _default_thread_factory,
        timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
        grace_seconds: float = DEFAULT_TURN_GRACE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chat = chat_service
        self._sessions = sessions
        self._now = now
        self._new_id = new_id
        self._thread_factory = thread_factory
        self._timeout_seconds = timeout_seconds
        self._grace_seconds = grace_seconds
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._running: dict[str, _Running] = {}

    # -- 명령 ---------------------------------------------------------------------------------

    def start(self, session_id: str, text: str, context: TurnContext) -> Turn:
        """턴 하나를 시작한다. 그 세션에 도는 턴이 있으면 `TurnInProgressError`.

        `chat_service.send`는 호출 스레드에서 부른다. 세션 없음·활성 프로파일 없음 같은 거절을
        HTTP 응답으로 바로 돌려주려면 스레드 안에서 터지면 안 되기 때문이다.
        """
        with self._lock:
            occupied = self._occupied_turn(session_id)
            if occupied is not None:
                raise TurnInProgressError(session_id, occupied.turn_id)
            accepted = self._sessions.last_sequence(session_id)
            turn = Turn(
                turn_id=self._new_id(),
                session_id=session_id,
                status=TurnStatus.RUNNING,
                accepted_sequence=accepted,
                started_at=self._now(),
                finished_at=None,
            )
            cancel = threading.Event()
            stream = self._chat.send(session_id, text, context, cancelled=cancel.is_set)
            self._sessions.create_turn(turn)
            self._running[turn.turn_id] = _Running(turn=turn, cancel=cancel)
        thread = self._thread_factory(
            target=lambda: self._drain(turn.turn_id, stream),
            name=f"assistant-turn-{turn.turn_id}",
            daemon=True,
        )
        thread.start()
        return turn

    def cancel(self, turn_id: str) -> Turn:
        """취소 신호를 세우고 턴을 CANCELLED로 기록한다.

        슬롯은 여기서 풀리지 않는다. 스레드가 신호를 읽고 `_finish`에 닿아야 다음 턴을 받는다.
        """
        with self._lock:
            entry = self._running.get(turn_id)
            if entry is None:
                return self._sessions.get_turn(turn_id)
            entry.cancel.set()
            if entry.decided is not None:
                # 이미 확정된 사유(예: 러너 타임아웃)가 있으면 취소가 덮지 않는다.
                return entry.view()
            entry.decided = TurnStatus.CANCELLED
            cancelled = _with_status(entry.turn, TurnStatus.CANCELLED, finished_at=None)
            entry.turn = cancelled
            return self._sessions.update_turn(cancelled)

    # -- 조회 ---------------------------------------------------------------------------------

    def state(self, turn_id: str) -> Turn:
        """도는 중이면 레지스트리가, 아니면 저장소가 답한다(재시작 이후에도 조회된다)."""
        with self._lock:
            entry = self._running.get(turn_id)
            if entry is not None:
                return entry.view()
        return self._sessions.get_turn(turn_id)

    def occupied_turn(self, session_id: str) -> Turn | None:
        """이 세션의 슬롯을 잡고 있는 턴, 없으면 `None`.

        판정 기준은 `start`와 같은 **레지스트리 존재**다. 취소 신호를 받은 턴도 스레드가
        `_finish`에 닿기 전까지는 여기에 잡히고, 그동안 adapter가 마무리 이벤트를 더 낼 수
        있다. 이벤트 스트림을 여는 쪽이 물어보는 것이 바로 그 질문이다 — "이 세션에 이벤트가
        더 나올 턴이 있는가".

        저장소는 보지 않는다. 저장소의 RUNNING 행은 이전 프로세스가 남긴 것일 수 있고, 그
        턴은 더 이상 돌지 않는다.
        """
        with self._lock:
            return self._occupied_turn(session_id)

    def is_settled(self, turn_id: str) -> bool:
        """이 턴에 이벤트가 더 붙을 수 있는지. 레지스트리에서 빠졌으면 끝났다.

        종료 **상태**로는 이 질문에 답할 수 없다. 취소는 스레드가 신호를 읽기 전에 턴을
        CANCELLED로 기록하므로, 상태만 보고 스트림을 닫으면 adapter가 마무리로 내는 이벤트를
        클라이언트가 못 받는다. 슬롯이 풀리는 시점(`_finish`)은 모든 append 다음이다.
        """
        with self._lock:
            return turn_id not in self._running

    def turns(self, session_id: str) -> tuple[Turn, ...]:
        """세션의 턴 이력. 슬롯을 잡고 있는 턴은 레지스트리의 최신 상태로 덮어 준다.

        저장소 행의 상태는 `_finish`에서 마지막으로 갱신되므로, 러너가 먼저 확정한 사유(취소·
        타임아웃)는 그 사이 이력 조회에 아직 보이지 않는다. `view()`가 그 확정을 반영한다.
        """
        stored = self._sessions.turns(session_id)
        with self._lock:
            live = {turn_id: entry.view() for turn_id, entry in self._running.items()}
        return tuple(live.get(turn.turn_id, turn) for turn in stored)

    def events(self, session_id: str, *, after_sequence: int = -1) -> tuple[SequencedEvent, ...]:
        """세션 이벤트를 sequence 순으로. 모르는 세션은 빈 목록이 아니라 예외로 답한다.

        빈 목록으로 답하면 클라이언트가 오타 난 세션 id로 영원히 폴링하면서 "아직 아무 일도 없다"로
        읽는다.
        """
        self._sessions.get(session_id)
        return self._sessions.events(session_id, after_sequence=after_sequence)

    # -- 스레드 본체 --------------------------------------------------------------------------

    def _drain(self, turn_id: str, stream: Iterator[ChatEvent]) -> None:
        """워커 스레드의 전부. 어떤 경로로 끝나도 `_finish`가 정확히 한 번 돈다."""
        status = TurnStatus.COMPLETED
        try:
            self._consume(turn_id, stream)
        except Exception as error:
            status = TurnStatus.FAILED
            self._record_crash(turn_id, error)
        finally:
            self._close(turn_id, stream)
            self._finish(turn_id, status)

    def _consume(self, turn_id: str, stream: Iterator[ChatEvent]) -> None:
        deadline = self._monotonic() + self._timeout_seconds
        grace_deadline: float | None = None
        for event in stream:
            # 이벤트마다 즉시 append한다. 배치로 모으면 화면이 턴이 끝날 때까지 비어 있다.
            self._sessions.append_events(turn_id, (event,))
            if isinstance(event, Failure):
                self._decide(turn_id, _status_for(event.code))
            now = self._monotonic()
            if grace_deadline is not None:
                if now >= grace_deadline:
                    return
                continue
            if now >= deadline:
                grace_deadline = now + self._grace_seconds
                self._expire(turn_id)

    def _expire(self, turn_id: str) -> None:
        """`TIMEOUT`을 먼저 확정하고 나서 취소 신호를 보낸다(spec D3 Failure 우선순위)."""
        self._sessions.append_events(
            turn_id,
            (
                Failure(
                    code=FailureCode.TIMEOUT,
                    message=(
                        "턴이 제한 시간을 넘겨 종료했습니다 — "
                        f"turn_id={turn_id} timeout_seconds={self._timeout_seconds}"
                    ),
                ),
            ),
        )
        self._decide(turn_id, TurnStatus.FAILED)
        with self._lock:
            entry = self._running.get(turn_id)
            if entry is not None:
                entry.cancel.set()

    def _record_crash(self, turn_id: str, error: Exception) -> None:
        """예상 못 한 예외도 종료 이벤트를 남긴다. 로그·이벤트 모두 예외 타입 이름까지만.

        코드는 `INTERNAL`이다. 여기서 잡히는 것은 저장소 호출이나 러너 자신의 예외이지 공급자
        오류가 아니다. `PROVIDER`로 찍으면 이력과 화면이 원인을 공급자 탓으로 잘못 가리킨다.
        """
        logger.warning(
            "assistant turn crashed — turn_id=%s error_type=%s",
            turn_id,
            type(error).__name__,
        )
        self._decide(turn_id, TurnStatus.FAILED)
        try:
            self._sessions.append_events(
                turn_id,
                (
                    Failure(
                        code=FailureCode.INTERNAL,
                        message=(
                            "턴 처리 중 내부 오류로 종료했습니다 — "
                            f"turn_id={turn_id} error_type={type(error).__name__}"
                        ),
                    ),
                ),
            )
        except Exception as append_error:
            logger.warning(
                "could not record the crash event — turn_id=%s error_type=%s",
                turn_id,
                type(append_error).__name__,
            )

    def _close(self, turn_id: str, stream: Iterator[ChatEvent]) -> None:
        """제너레이터를 닫는다. 닫는 과정의 예외가 종료 기록을 건너뛰게 두지 않는다.

        `close()`는 `send` 제너레이터의 `finally`를 돌리므로 부분 assistant 메시지 저장이 그 안에서
        일어난다. 저장소가 그 순간 흔들리면 예외가 여기로 나온다.
        """
        close = getattr(stream, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as error:
            logger.warning(
                "closing the turn stream failed — turn_id=%s error_type=%s",
                turn_id,
                type(error).__name__,
            )

    def _decide(self, turn_id: str, status: TurnStatus) -> None:
        """턴의 종료 사유를 확정한다. 먼저 확정된 것이 남고 뒤엣것은 이벤트로만 남는다."""
        with self._lock:
            entry = self._running.get(turn_id)
            if entry is None or entry.decided is not None:
                return
            entry.decided = status

    def _finish(self, turn_id: str, status: TurnStatus) -> None:
        """슬롯을 풀고 종료 상태를 기록한다. 기록이 실패해도 슬롯은 이미 풀려 있다."""
        with self._lock:
            entry = self._running.pop(turn_id, None)
        if entry is None:
            return
        final = entry.decided or status
        try:
            self._sessions.update_turn(_with_status(entry.turn, final, finished_at=self._now()))
        except Exception as error:
            logger.warning(
                "could not persist the final turn state — turn_id=%s status=%s error_type=%s",
                turn_id,
                final.value,
                type(error).__name__,
            )

    def _occupied_turn(self, session_id: str) -> Turn | None:
        """그 세션의 슬롯을 잡고 있는 턴. 취소 신호를 받았어도 스레드가 돌면 여전히 점유 중이다."""
        for entry in self._running.values():
            if entry.turn.session_id == session_id:
                return entry.turn
        return None


def _status_for(code: FailureCode) -> TurnStatus:
    """Failure 하나를 턴 상태로 옮긴다. 취소만 FAILED가 아니다."""
    return TurnStatus.CANCELLED if code is FailureCode.CANCELLED else TurnStatus.FAILED


def _with_status(turn: Turn, status: TurnStatus, *, finished_at: datetime | None) -> Turn:
    return Turn(
        turn_id=turn.turn_id,
        session_id=turn.session_id,
        status=status,
        accepted_sequence=turn.accepted_sequence,
        started_at=turn.started_at,
        finished_at=finished_at if finished_at is not None else turn.finished_at,
    )
