"""진행 중 턴의 owner (설계 spec D3).

`backtest_run`의 `BacktestRunService`와 같은 구조다: 작업을 스레드에서 돌리고, 결과를 이벤트로
저장소에 append하며, 클라이언트는 sequence를 들고 폴링·재개한다. 그래서 클라이언트가 스트림
연결을 끊어도 턴은 상한 안에서 계속 돌고 이벤트는 남는다. 멈추려면 취소를 불러야 한다.

## 단일 워커 전제

진행 중 턴의 레지스트리(`dict` + `RLock`)와 취소 신호(`threading.Event`)는 **이 프로세스 안에만**
있다. 이 앱이 로컬 단일 사용자 도구이고 backend가 uvicorn 단일 워커로 돈다는 전제다. 워커를
늘리면 다른 워커가 시작한 턴은 취소할 수 없고 `state()`가 레지스트리 대신 저장소만 보게 된다.
그때는 레지스트리를 프로세스 밖(DB 행 + 취소 플래그)으로 옮겨야 한다.

## Failure 우선순위

한 턴에서 **턴 상태가 되는 Failure는 먼저 확정된 하나뿐이다**(spec D3). 뒤이어 들어오는 Failure는
이벤트로 저장되지만 상태를 바꾸지 않는다. 러너의 타임아웃은 `TIMEOUT`을 **먼저 확정한 뒤** 취소
신호를 보내므로, 그 신호를 받은 adapter가 내는 `CANCELLED`는 이벤트로만 남는다. 이 규칙이 없으면
같은 턴이 "취소됨"으로도 "시간 초과"로도 보이고, 어느 쪽이 원인인지 이력에서 알 수 없다.

라운드·검색·토큰 상한은 adapter가 집행하므로 `TOOL_ROUNDS_EXCEEDED`·`TOKEN_BUDGET_EXCEEDED`도
공급자가 내는 Failure로 도착한다. 러너는 그것을 상태로 옮길 뿐 따로 세지 않는다.

## 타임아웃의 범위

턴당 벽시계 타임아웃은 **이벤트 사이**에서 본다. 공급자가 아무 이벤트도 내지 않은 채 멈춰 있으면
여기서 깨우지 못하고, 그 경우는 adapter가 건 HTTP 타임아웃이 막는다. 상한을 넘겨 도는 턴을
잘라 내는 것이 목적이지 응답 없는 소켓을 감시하는 것이 목적이 아니다.

취소 신호를 보낸 뒤에는 유예 시간만큼 더 읽어 adapter가 마무리로 내는 이벤트를 저장한다. 그
유예마저 지나면 스트림을 닫는다. `cancelled`를 무시하는 adapter가 스레드를 영원히 붙잡지 못하게
하는 상한이다.
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

__all__ = ["AssistantTurnRunner", "TurnInProgressError", "TurnThread", "TurnThreadFactory"]

logger = logging.getLogger(__name__)


class TurnInProgressError(RuntimeError):
    """세션에 이미 RUNNING 턴이 있다. 한 세션의 턴은 한 번에 하나다."""

    def __init__(self, session_id: str, turn_id: str) -> None:
        super().__init__(
            "a turn is already running for this session — "
            f"session_id={session_id!r} running_turn_id={turn_id!r}; cancel it first"
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
    """레지스트리 한 칸. 취소 신호, 마지막으로 알려진 턴, 먼저 확정된 종료 사유."""

    turn: Turn
    cancel: threading.Event
    decided: TurnStatus | None = None


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
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chat = chat_service
        self._sessions = sessions
        self._now = now
        self._new_id = new_id
        self._thread_factory = thread_factory
        self._timeout_seconds = timeout_seconds
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._running: dict[str, _Running] = {}

    # -- 명령 ---------------------------------------------------------------------------------

    def start(self, session_id: str, text: str, context: TurnContext) -> Turn:
        """턴 하나를 시작한다. 이미 도는 턴이 있으면 `TurnInProgressError`.

        `chat_service.send`는 호출 스레드에서 부른다. 세션 없음·활성 프로파일 없음 같은 거절을
        HTTP 응답으로 바로 돌려주려면 스레드 안에서 터지면 안 되기 때문이다.
        """
        with self._lock:
            existing = self._running_turn_for(session_id)
            if existing is not None:
                raise TurnInProgressError(session_id, existing.turn_id)
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
        """취소 신호를 세우고 턴을 CANCELLED로 기록한다. 이미 끝난 턴이면 그대로 돌려준다."""
        with self._lock:
            entry = self._running.get(turn_id)
            if entry is None:
                return self._sessions.get_turn(turn_id)
            entry.cancel.set()
            if entry.decided is not None:
                # 이미 확정된 사유(예: 러너 타임아웃)가 있으면 취소가 덮지 않는다.
                return entry.turn
            entry.decided = TurnStatus.CANCELLED
            cancelled = _with_status(entry.turn, TurnStatus.CANCELLED, finished_at=None)
            entry.turn = cancelled
            return self._sessions.update_turn(cancelled)

    # -- 조회 ---------------------------------------------------------------------------------

    def state(self, turn_id: str) -> Turn:
        """진행 중이면 레지스트리가, 아니면 저장소가 답한다(재시작 이후에도 조회된다)."""
        with self._lock:
            entry = self._running.get(turn_id)
            if entry is not None:
                return entry.turn
        return self._sessions.get_turn(turn_id)

    def events(self, session_id: str, *, after_sequence: int = -1) -> tuple[SequencedEvent, ...]:
        """세션 이벤트를 sequence 순으로. 모르는 세션은 빈 목록이 아니라 예외로 답한다.

        빈 목록으로 답하면 클라이언트가 오타 난 세션 id로 영원히 폴링하면서 "아직 아무 일도 없다"로
        읽는다.
        """
        self._sessions.get(session_id)
        return self._sessions.events(session_id, after_sequence=after_sequence)

    # -- 스레드 본체 --------------------------------------------------------------------------

    def _drain(self, turn_id: str, stream: Iterator[ChatEvent]) -> None:
        deadline = self._monotonic() + self._timeout_seconds
        grace_deadline: float | None = None
        try:
            for event in stream:
                # 이벤트마다 즉시 append한다. 배치로 모으면 화면이 턴이 끝날 때까지 비어 있다.
                self._sessions.append_events(turn_id, (event,))
                if isinstance(event, Failure):
                    self._decide(turn_id, _status_for(event.code))
                now = self._monotonic()
                if grace_deadline is not None:
                    if now >= grace_deadline:
                        break
                    continue
                if now >= deadline:
                    grace_deadline = now + self._timeout_seconds
                    self._expire(turn_id)
        except Exception:
            logger.exception("assistant turn crashed — turn_id=%s", turn_id)
            self._finish(turn_id, TurnStatus.FAILED)
            return
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        self._finish(turn_id, TurnStatus.COMPLETED)

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

    def _decide(self, turn_id: str, status: TurnStatus) -> None:
        """턴의 종료 사유를 확정한다. 먼저 확정된 것이 남고 뒤엣것은 이벤트로만 남는다."""
        with self._lock:
            entry = self._running.get(turn_id)
            if entry is None or entry.decided is not None:
                return
            entry.decided = status

    def _finish(self, turn_id: str, status: TurnStatus) -> None:
        with self._lock:
            entry = self._running.pop(turn_id, None)
            if entry is None:
                return
            final = entry.decided or status
            self._sessions.update_turn(_with_status(entry.turn, final, finished_at=self._now()))

    def _running_turn_for(self, session_id: str) -> Turn | None:
        for entry in self._running.values():
            if entry.turn.session_id == session_id and entry.turn.status is TurnStatus.RUNNING:
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
