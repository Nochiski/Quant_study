"""`/api/v1/assistant/*` 라우트 (설계 spec D6).

이 모듈이 하는 일은 셋뿐이다.

1. 요청 본문·쿼리를 유스케이스 값으로 옮기고 결과를 wire 모델로 되돌린다.
2. 유스케이스 예외를 코드 있는 HTTP 오류로 옮긴다(`detail={"code", "message", ...}` 관례).
3. 저장된 이벤트를 SSE로 흘린다.

**정책을 다시 판단하지 않는다.** base_url 규칙·probe 필수·활성 승계·턴 중복 거부는 전부
`application/assistant_chat`이 정하고, 여기서는 그 거절을 상태 코드로 번역만 한다.

비밀은 요청 방향으로만 흐른다. 이 파일에는 `secret`을 읽어 응답·로그·예외 메시지에 넣는 경로가
없다. 키 꼬리 4자리는 `ProviderProfileService.summaries()`가 만들어 주고, 평문은 inbound 계층에
올라오지 않는다(spec D1).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import asdict
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from strategy_workbench.application.assistant_chat.facade.chat import (
    AssistantChatService,
    ChatSession,
    DocumentRef,
    NoActiveProviderError,
    TurnContext,
)
from strategy_workbench.application.assistant_chat.facade.ports import (
    ChatSessionNotFoundError,
    ProviderProfileNotFoundError,
    ProviderSecretMissingError,
    TurnNotFoundError,
)
from strategy_workbench.application.assistant_chat.facade.profiles import (
    ProviderBaseUrlRejectedError,
    ProviderNotInstalledError,
    ProviderProbeFailedError,
    ProviderProfileService,
)
from strategy_workbench.application.assistant_chat.facade.turns import (
    AssistantTurnRunner,
    TurnInProgressError,
)
from strategy_workbench.domain.assistant.facade.models import SequencedEvent, Turn

from ._assistant_contract import (
    ASSISTANT_EVENT_NAME,
    Assistant409Response,
    Assistant422Response,
    AssistantEventEnvelopeView,
    AssistantNotFoundResponse,
    CreateProviderProfileRequest,
    CreateSessionRequest,
    DocumentRefView,
    ProbeResultView,
    ProviderProfileView,
    ProvidersView,
    SessionHistoryView,
    SessionView,
    StartTurnRequest,
    TurnAcceptedView,
    TurnContextPayload,
    TurnView,
    event_envelope_view,
    message_view,
    probe_result_view,
    profile_view,
    providers_view,
    turn_view,
)

__all__ = [
    "ASSISTANT_KEEPALIVE_SECONDS",
    "ASSISTANT_POLL_SECONDS",
    "register_assistant_routes",
]

ASSISTANT_PREFIX = "/api/v1/assistant"

# 열린 스트림이 조용해도 15초마다 주석 한 줄을 보낸다(spec D6). 중간의 프록시·브라우저가 아무
# 바이트도 오지 않는 연결을 끊어 버리면, 클라이언트는 턴이 끝난 것으로 착각하지 않고 재연결을
# 반복하게 된다. 주석 프레임은 SSE 파서가 무시하므로 이벤트 번호를 건드리지 않는다.
ASSISTANT_KEEPALIVE_SECONDS = 15.0
# 저장소를 다시 읽는 간격. 백테스트 스트림과 같은 폴링 구조다(spec D3: 러너는 저장소가 정본).
ASSISTANT_POLL_SECONDS = 0.05


class TurnEventSource(Protocol):
    """스트림이 실제로 필요로 하는 읽기 두 개.

    러너 전체가 아니라 이 좁은 계약을 받는 이유는, 스트림이 턴을 시작하거나 취소할 수 있으면
    안 되기 때문이다(GET이 상태를 바꾸지 않는다는 CQS). 테스트도 러너 전체를 조립하지 않고
    이 두 조회만 흉내 내면 된다.

    닫는 조건으로 턴 **상태**가 아니라 `is_settled`를 쓴다. 취소는 스레드가 신호를 읽기 전에
    턴을 CANCELLED로 기록하므로, 상태만 보고 닫으면 취소 직후 adapter가 내는 마지막 이벤트를
    클라이언트가 못 받는다.
    """

    def is_settled(self, turn_id: str) -> bool: ...

    def events(
        self, session_id: str, *, after_sequence: int = -1
    ) -> tuple[SequencedEvent, ...]: ...


def register_assistant_routes(
    app: FastAPI,
    *,
    profiles: ProviderProfileService,
    chat: AssistantChatService,
    turns: AssistantTurnRunner,
    keepalive_seconds: float = ASSISTANT_KEEPALIVE_SECONDS,
    poll_seconds: float = ASSISTANT_POLL_SECONDS,
) -> None:
    """어시스턴트 라우트를 `app`에 등록한다. 서비스는 bootstrap이 조립해 넘긴다."""

    @app.get(
        f"{ASSISTANT_PREFIX}/providers",
        operation_id="listAssistantProviders",
        tags=["assistant"],
    )
    def list_assistant_providers() -> ProvidersView:
        """가용 공급자 종류와 등록된 프로파일. 키 자리에는 꼬리 4자리만 있다."""
        return providers_view(profiles.available_kinds(), profiles.summaries())

    @app.post(
        f"{ASSISTANT_PREFIX}/providers",
        operation_id="createAssistantProvider",
        status_code=status.HTTP_201_CREATED,
        tags=["assistant"],
        responses={422: {"model": Assistant422Response, **_UNPROCESSABLE_DESCRIPTION}},
    )
    def create_assistant_provider(request: CreateProviderProfileRequest) -> ProviderProfileView:
        """연결 테스트를 통과한 프로파일만 저장된다(spec D6)."""
        try:
            created = profiles.create(
                kind=request.kind,
                label=request.label,
                secret=request.secret,
                model=request.model,
                base_url=request.base_url,
            )
        except (
            ProviderNotInstalledError,
            ProviderBaseUrlRejectedError,
            ProviderProbeFailedError,
        ) as error:
            raise _unprocessable(error) from error
        return profile_view(profiles.summary(created.profile_id))

    @app.post(
        f"{ASSISTANT_PREFIX}/providers/{{profile_id}}/test",
        operation_id="testAssistantProvider",
        tags=["assistant"],
        responses={
            404: {"model": AssistantNotFoundResponse, **_NOT_FOUND_DESCRIPTION},
            422: {"model": Assistant422Response, **_UNPROCESSABLE_DESCRIPTION},
        },
    )
    def test_assistant_provider(profile_id: str) -> ProbeResultView:
        """저장된 키로 연결을 다시 확인한다. 실패는 예외가 아니라 결과 값으로 돌려준다."""
        try:
            return probe_result_view(profiles.test(profile_id))
        except ProviderProfileNotFoundError as error:
            raise _provider_not_found(error) from error
        except (ProviderNotInstalledError, ProviderSecretMissingError) as error:
            raise _unprocessable(error) from error

    @app.post(
        f"{ASSISTANT_PREFIX}/providers/{{profile_id}}/activate",
        operation_id="activateAssistantProvider",
        tags=["assistant"],
        responses={404: {"model": AssistantNotFoundResponse, **_NOT_FOUND_DESCRIPTION}},
    )
    def activate_assistant_provider(profile_id: str) -> ProviderProfileView:
        try:
            profiles.activate(profile_id)
        except ProviderProfileNotFoundError as error:
            raise _provider_not_found(error) from error
        return profile_view(profiles.summary(profile_id))

    @app.delete(
        f"{ASSISTANT_PREFIX}/providers/{{profile_id}}",
        operation_id="deleteAssistantProvider",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["assistant"],
        responses={404: {"model": AssistantNotFoundResponse, **_NOT_FOUND_DESCRIPTION}},
    )
    def delete_assistant_provider(profile_id: str) -> None:
        """프로파일과 그 키를 함께 지운다. 활성이었으면 application이 승계를 정한다."""
        try:
            profiles.delete(profile_id)
        except ProviderProfileNotFoundError as error:
            raise _provider_not_found(error) from error

    @app.post(
        f"{ASSISTANT_PREFIX}/sessions",
        operation_id="createAssistantSession",
        status_code=status.HTTP_201_CREATED,
        tags=["assistant"],
        responses={422: {"model": Assistant422Response, **_UNPROCESSABLE_DESCRIPTION}},
    )
    def create_assistant_session(request: CreateSessionRequest) -> SessionView:
        document_ref = _document_ref(request.document_ref)
        try:
            session = chat.create_session(document_ref, title=request.title)
        except NoActiveProviderError as error:
            raise _unprocessable(error) from error
        return _session_view(session)

    @app.get(
        f"{ASSISTANT_PREFIX}/sessions",
        operation_id="listAssistantSessions",
        tags=["assistant"],
        responses={422: {"model": Assistant422Response, **_UNPROCESSABLE_DESCRIPTION}},
    )
    def list_assistant_sessions(
        strategy_id: str | None = Query(default=None, min_length=1),
        revision: int | None = Query(default=None, ge=1),
        draft_id: str | None = Query(default=None, min_length=1),
    ) -> tuple[SessionView, ...]:
        """문서 하나의 세션 목록.

        `document_ref`를 한 덩어리 문자열로 받지 않고 필드 셋으로 받는 이유는, 그래야 생성
        SDK가 타입을 그대로 만들고 서버도 다시 parse하지 않기 때문이다.
        """
        view = DocumentRefView(strategy_id=strategy_id, revision=revision, draft_id=draft_id)
        return tuple(
            _session_view(session) for session in chat.list_for_document(_document_ref(view))
        )

    @app.get(
        f"{ASSISTANT_PREFIX}/sessions/{{session_id}}",
        operation_id="getAssistantSession",
        tags=["assistant"],
        responses={404: {"model": AssistantNotFoundResponse, **_NOT_FOUND_DESCRIPTION}},
    )
    def get_assistant_session(session_id: str) -> SessionHistoryView:
        """메시지·턴·이벤트 이력 전부. 사이드바가 새로 열릴 때 한 번에 복구한다."""
        try:
            session = chat.get(session_id)
            stored_events = turns.events(session_id)
            history_turns = turns.turns(session_id)
            messages = chat.messages(session_id)
        except ChatSessionNotFoundError as error:
            raise _session_not_found(error) from error
        return SessionHistoryView(
            session=_session_view(session),
            messages=tuple(message_view(message) for message in messages),
            turns=tuple(turn_view(turn) for turn in history_turns),
            events=tuple(event_envelope_view(stored) for stored in stored_events),
        )

    @app.post(
        f"{ASSISTANT_PREFIX}/sessions/{{session_id}}/turns",
        operation_id="startAssistantTurn",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["assistant"],
        responses={
            404: {"model": AssistantNotFoundResponse, **_NOT_FOUND_DESCRIPTION},
            409: {"model": Assistant409Response, **_TURN_CONFLICT_DESCRIPTION},
            422: {"model": Assistant422Response, **_UNPROCESSABLE_DESCRIPTION},
        },
    )
    def start_assistant_turn(session_id: str, request: StartTurnRequest) -> TurnAcceptedView:
        """턴을 시작하고 즉시 202로 답한다. 이벤트는 SSE로 따로 읽는다."""
        try:
            turn = turns.start(session_id, request.text, _turn_context(request.context))
        except ChatSessionNotFoundError as error:
            raise _session_not_found(error) from error
        except TurnInProgressError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "assistant.turn_in_progress",
                    "message": str(error),
                    "turn_id": error.turn_id,
                },
            ) from error
        except (
            NoActiveProviderError,
            ProviderNotInstalledError,
            ProviderSecretMissingError,
        ) as error:
            raise _unprocessable(error) from error
        return TurnAcceptedView(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            status=turn.status,
            accepted_sequence=turn.accepted_sequence,
            started_at=turn.started_at,
        )

    @app.post(
        f"{ASSISTANT_PREFIX}/sessions/{{session_id}}/turns/{{turn_id}}/cancel",
        operation_id="cancelAssistantTurn",
        tags=["assistant"],
        responses={404: {"model": AssistantNotFoundResponse, **_NOT_FOUND_DESCRIPTION}},
    )
    def cancel_assistant_turn(session_id: str, turn_id: str) -> TurnView:
        """취소 신호를 세운다. 이미 끝난 턴이면 그 상태를 그대로 돌려준다."""
        return turn_view(_cancel(turns, session_id, turn_id))

    @app.get(
        f"{ASSISTANT_PREFIX}/sessions/{{session_id}}/events",
        operation_id="streamAssistantEvents",
        response_class=StreamingResponse,
        tags=["assistant"],
        responses={
            200: {"content": {"text/event-stream": {}}},
            404: {"model": AssistantNotFoundResponse, **_NOT_FOUND_DESCRIPTION},
            409: {"model": Assistant409Response, **_TURN_CONFLICT_DESCRIPTION},
        },
    )
    def stream_assistant_events(
        session_id: str,
        after_sequence: int = Query(default=-1, ge=-1),
        last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=-1)] = None,
    ) -> StreamingResponse:
        """진행 중 턴의 이벤트를 SSE로 흘린다.

        재개 위치는 `Last-Event-ID` 헤더가 있으면 그것, 없으면 `after_sequence` 쿼리다(spec D6).
        헤더가 이기는 이유는 그 값이 **클라이언트가 실제로 반영한 마지막 번호**이기 때문이다.
        쿼리는 최초 연결 때 적은 값이라 재연결 시점에는 이미 낡아 있다.

        진행 중 턴이 없으면 열지 않고 409로 거절한다. 스트림을 열어 두면 클라이언트는 "곧 뭔가
        오겠지"로 읽고 기다리지만, 이력에 이미 결말이 적힌 턴이라 아무것도 오지 않는다.
        """
        try:
            chat.get(session_id)
        except ChatSessionNotFoundError as error:
            raise _session_not_found(error) from error
        running = turns.occupied_turn(session_id)
        if running is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "assistant.no_running_turn",
                    "message": (
                        "이 세션에는 진행 중인 턴이 없습니다 — "
                        f"session_id={session_id}; 이력 조회로 결과를 읽으세요"
                    ),
                },
            )
        resume_from = last_event_id if last_event_id is not None else after_sequence
        return StreamingResponse(
            _event_stream(
                turns,
                session_id=session_id,
                turn_id=running.turn_id,
                after_sequence=resume_from,
                keepalive_seconds=keepalive_seconds,
                poll_seconds=poll_seconds,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


def _event_stream(
    turns: TurnEventSource,
    *,
    session_id: str,
    turn_id: str,
    after_sequence: int,
    keepalive_seconds: float,
    poll_seconds: float,
) -> Iterator[str]:
    """저장된 이벤트를 sequence 순으로 흘리고 턴이 끝나면 닫는다.

    **종료 여부를 이벤트보다 먼저 읽는다.** 반대로 읽으면 두 조회 사이에 저장된 마지막
    이벤트가 영원히 전송되지 않는다(이벤트를 읽은 뒤 종료를 보면 그대로 루프를 빠져나가므로).
    먼저 읽어 두면 "종료를 본 뒤 읽은 이벤트 목록"에는 그 턴의 이벤트가 전부 들어 있다 —
    러너가 슬롯을 푸는 것은 모든 append가 끝난 다음이기 때문이다.
    """
    sequence = after_sequence
    last_frame_at = time.monotonic()
    while True:
        settled = turns.is_settled(turn_id)
        for stored in turns.events(session_id, after_sequence=sequence):
            if stored.turn_id != turn_id:
                # 이 스트림은 한 턴의 것이다. 같은 세션의 앞선 턴 이벤트가 재개 범위에 걸려도
                # 다시 보내지 않는다(클라이언트는 이력으로 이미 갖고 있다).
                sequence = stored.sequence
                continue
            sequence = stored.sequence
            yield _frame(event_envelope_view(stored))
            last_frame_at = time.monotonic()
        if settled:
            return
        now = time.monotonic()
        if now - last_frame_at >= keepalive_seconds:
            yield ": keepalive\n\n"
            last_frame_at = now
        time.sleep(poll_seconds)


def _frame(envelope: AssistantEventEnvelopeView) -> str:
    body = json.dumps(
        jsonable_encoder(asdict(envelope)),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {envelope.sequence}\nevent: {ASSISTANT_EVENT_NAME}\ndata: {body}\n\n"


def _cancel(turns: AssistantTurnRunner, session_id: str, turn_id: str) -> Turn:
    """턴이 이 세션의 것인지 먼저 확인하고 취소한다."""
    try:
        state = turns.state(turn_id)
    except TurnNotFoundError as error:
        raise _turn_not_found(turn_id) from error
    if state.session_id != session_id:
        raise _turn_not_found(turn_id)
    return turns.cancel(turn_id)


def _document_ref(view: DocumentRefView) -> DocumentRef:
    """wire 값을 유스케이스 값으로. "정확히 하나" 규칙은 application이 판정한다."""
    try:
        return DocumentRef(
            strategy_id=view.strategy_id, revision=view.revision, draft_id=view.draft_id
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "assistant.document_ref_invalid", "message": str(error)},
        ) from error


def _document_ref_view(reference: DocumentRef) -> DocumentRefView:
    return DocumentRefView(
        strategy_id=reference.strategy_id,
        revision=reference.revision,
        draft_id=reference.draft_id,
    )


def _session_view(session: ChatSession) -> SessionView:
    return SessionView(
        session_id=session.session_id,
        document_ref=_document_ref_view(session.document_ref),
        provider_profile_id=session.provider_profile_id,
        created_at=session.created_at,
        title=session.title,
    )


def _turn_context(payload: TurnContextPayload) -> TurnContext:
    return TurnContext(
        source_text=payload.source_text,
        source_format=payload.source_format,
        environment=payload.environment,
        diagnostics=tuple(payload.diagnostics),
    )


def _unprocessable(
    error: ProviderNotInstalledError
    | ProviderBaseUrlRejectedError
    | ProviderProbeFailedError
    | ProviderSecretMissingError
    | NoActiveProviderError,
) -> HTTPException:
    """거절 하나를 코드 있는 422로. 어느 갈래도 비밀을 message에 넣지 않는다.

    `ProviderProbeFailedError`만 `str(error)` 대신 고정 문구를 쓴다. 나머지 예외 문자열은
    우리가 쓴 것이라 열거값·식별자뿐이지만, probe 실패는 공급자 응답에서 유래한 사유라
    `ProbeFailure` 표가 정한 문장만 내보낸다(spec D2).
    """
    if isinstance(error, ProviderProbeFailedError):
        detail: dict[str, object] = {
            "code": "assistant.probe_failed",
            "message": error.result.message,
            "failure": _probe_failure_value(error),
        }
    elif isinstance(error, ProviderBaseUrlRejectedError):
        detail = {"code": "assistant.base_url_rejected", "message": str(error)}
    elif isinstance(error, ProviderNotInstalledError):
        detail = {"code": "assistant.provider_not_installed", "message": str(error)}
    elif isinstance(error, ProviderSecretMissingError):
        detail = {"code": "assistant.provider_secret_missing", "message": str(error)}
    else:
        detail = {"code": "assistant.no_active_provider", "message": str(error)}
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _probe_failure_value(error: ProviderProbeFailedError) -> str:
    failure = error.result.failure
    # `ProbeResult`가 "실패인데 사유 없음"을 생성자에서 막으므로 여기는 방어선이다.
    return failure.value if failure is not None else "unknown"


def _session_not_found(error: ChatSessionNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "assistant.session.not_found", "message": str(error)},
    )


def _turn_not_found(turn_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "assistant.turn.not_found",
            "message": f"unknown assistant turn — turn_id={turn_id!r}",
        },
    )


def _provider_not_found(error: ProviderProfileNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "assistant.provider.not_found", "message": str(error)},
    )


_UNPROCESSABLE_DESCRIPTION: dict[str, Any] = {
    "description": "A coded assistant rejection or a malformed request envelope",
}
_TURN_CONFLICT_DESCRIPTION: dict[str, Any] = {
    "description": "A turn is already running, or no turn is running to stream",
}
_NOT_FOUND_DESCRIPTION: dict[str, Any] = {
    "description": "The session, turn or provider profile does not exist",
}
