"""Outgoing port: 한 LLM 공급자와의 대화 (설계 spec D3/D4).

adapter가 아는 것은 "도구 정의 목록을 모델에 주고, 모델이 부른 도구를 콜백으로 실행해 결과를
돌려주는 루프"뿐이다. 어떤 도구가 있고 무엇을 하는지는 `application/assistant_chat`이 정한다.
그래서 새 도구를 추가해도 adapter는 바뀌지 않는다.

웹 검색은 `TurnRequest.research`로 요청만 하고, 켜는 방법은 adapter가 자기 공급자 방식으로
정한다(v1은 공급자 내장 서버 도구).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Protocol

from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ProbeResult,
    ProviderKind,
    ProviderProfile,
    ToolCall,
    ToolResult,
    TurnRequest,
)

__all__ = ["LlmProviderPort"]


class LlmProviderPort(Protocol):
    """한 공급자 SDK를 감싼 adapter. 비밀은 호출마다 넘겨받고 보관하지 않는다."""

    kind: ProviderKind

    def default_model(self) -> str:
        """프로파일을 만들 때 제안할 기본 모델. 사용자가 바꿀 수 있다."""
        ...

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        """최소 토큰 요청 한 번으로 키·모델·네트워크를 확인한다. 실패 사유를 구분해 돌려준다."""
        ...

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        """한 턴을 흘린다. 모델이 도구를 부르면 `execute_tool`로 실행하고 결과를 이어서 보낸다.

        `cancelled`가 참을 돌려주면 가능한 빨리 스트림을 끝낸다. 공급자 오류는 예외로 올리고,
        `Failure` 이벤트로의 변환은 application이 한다.
        """
        ...
