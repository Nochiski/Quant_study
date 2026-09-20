"""`LlmProviderPort`의 대본 구현 (테스트 전용, WORKFLOW B-05).

## 기본은 꺼져 있다

이 adapter는 bootstrap이 `STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER=1`을 읽었을 때만 레지스트리에
들어간다(`bootstrap/_http.py`). 켜지지 않은 프로세스에서는 이 모듈이 import될 뿐 아무 것도 하지
않는다. 실서비스 경로와 섞이면 "연결 테스트는 통과하는데 답이 대본"인 상태를 사용자가 구분할 수
없으므로, 켜는 통로를 환경 변수 하나로 좁혀 둔다.

## 무엇을 흉내 내고 무엇을 흉내 내지 않는가

흉내 내는 것은 **공급자 루프의 관찰 가능한 모양**뿐이다: 이벤트를 하나씩 시간차로 흘리고, 도구
호출에 `execute_tool` 콜백을 실제로 부르고, 취소 신호를 보면 멈춘다. 라운드·검색·토큰 상한
집행(spec D3에서 adapter의 몫)은 흉내 내지 않는다 — 대본은 상한에 닿지 않는 길이다.

`probe`는 언제나 성공한다. 키를 검사할 상대가 없기 때문이며, 그래서 이 adapter가 켜진 프로세스의
"연결 테스트 통과"는 키가 옳다는 뜻이 아니다.

## 이벤트 사이 지연

조각마다 `_STEP_DELAY_SECONDS`만큼 쉰다. 지연이 0이면 턴이 시작 응답보다 먼저 끝나 화면이 스트림을
한 번도 열지 못하고, 스트리밍·취소·재연결 경로가 e2e에서 통째로 빠진다. 턴은 러너가 만든 별도
스레드에서 돌므로 여기서 자는 것이 요청 스레드를 막지 않는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ChatRole,
    ProbeResult,
    ProviderKind,
    ProviderProfile,
    ToolCall,
    ToolResult,
    TurnRequest,
)

from ._scenarios import scenario_for

__all__ = ["DEFAULT_SCRIPTED_MODEL", "ScriptedLlmProvider"]

DEFAULT_SCRIPTED_MODEL = "scripted-fake-1"

# 사람이 스트리밍으로 읽는 속도에 가깝고, 시나리오 하나가 몇 초 안에 끝나는 값.
_STEP_DELAY_SECONDS = 0.2
_PROBE_LATENCY_MS = 7


class ScriptedLlmProvider:
    """대본대로 이벤트를 흘리는 가짜 공급자. 네트워크도 SDK도 쓰지 않는다."""

    def __init__(
        self,
        kind: ProviderKind,
        *,
        model: str = DEFAULT_SCRIPTED_MODEL,
        step_delay_seconds: float = _STEP_DELAY_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.kind = kind
        self._model = model
        self._step_delay_seconds = step_delay_seconds
        self._sleep = sleep

    def default_model(self) -> str:
        return self._model

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        """언제나 성공. 키를 검사할 상대가 없다(모듈 docstring)."""
        return ProbeResult(ok=True, latency_ms=_PROBE_LATENCY_MS)

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        """마지막 사용자 질문으로 시나리오를 고르고 그 대본을 흘린다."""
        for event in scenario_for(_last_user_text(request))(execute_tool):
            if cancelled():
                return
            self._sleep(self._step_delay_seconds)
            yield event


def _last_user_text(request: TurnRequest) -> str:
    """이번 턴의 질문. 이력에 assistant 답이 섞여 있으므로 뒤에서부터 찾는다."""
    for message in reversed(request.messages):
        if message.role is ChatRole.USER:
            return message.text
    return ""
