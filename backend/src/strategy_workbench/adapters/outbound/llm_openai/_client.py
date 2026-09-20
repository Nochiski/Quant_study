"""OpenAI SDK와 닿는 유일한 경계 (설계 spec D4).

## 왜 Protocol을 한 겹 두는가

도구 루프(`_turn.py`)는 네트워크 없이 검증되어야 한다. 실제 호출을 하는 테스트는 비결정적이고
느리며 키를 요구한다(live smoke는 A-07의 `RUN_LLM_LIVE=1` 한 건뿐이다). 그래서 루프는 SDK
객체가 아니라 여기 선언한 좁은 Protocol에만 기대고, 테스트는 스트림 이벤트 스크립트를 주입한다.

Protocol이 쓰는 타입은 SDK의 실제 타입 그대로다(`ResponseInputItemParam`, `ToolParam`,
`ResponseStreamEvent`, `Response`, …). 우리 형식으로 한 번 더 옮기면 SDK 모양이 바뀌었을 때 타입
검사가 잡아 주지 못한다. 테스트 픽스처도 같은 이유로 SDK 타입을 그대로 만들어 쓴다.

## 왜 `responses.create(stream=True)`이고 `responses.stream(...)`이 아닌가

SDK에는 이벤트를 누적해 주는 헬퍼 `responses.stream(...)`도 있다. 쓰지 않는 이유는 두 가지다.

- 헬퍼는 자기 상태 기계로 스냅샷을 만들어 주는데, 우리가 필요한 것은 원시 이벤트(텍스트 델타,
  reasoning 요약 델타, `web_search_call` 완료, 종료 이벤트)뿐이라 누적 계층이 더해 주는 것이 없다.
- 종료 이벤트(`response.completed`/`incomplete`/`failed`)가 실어 오는 `Response` 객체가 상한
  집행에 필요한 전부다(`status`, `incomplete_details`, `usage`, `output`). 원시 스트림에서 그대로
  읽는 쪽이 경로가 짧다.

## 비밀

클라이언트는 호출마다 `secret`으로 새로 만들고 보관하지 않는다(`LlmProviderPort` 계약). 그래서
팩토리는 `(secret, base_url) -> client` 형태이지 미리 만들어 둔 클라이언트가 아니다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Protocol

import openai
from openai.types.responses import (
    Response,
    ResponseIncludable,
    ResponseInputItemParam,
    ResponseStreamEvent,
    ToolParam,
)
from openai.types.shared_params import Reasoning

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_SECONDS",
    "OpenAiClientFactory",
    "OpenAiResponseStream",
    "OpenAiResponsesClient",
    "SdkResponsesClient",
    "sdk_client_factory",
]

# 턴당 벽시계 타임아웃은 `AssistantTurnRunner`가 이벤트 사이에서만 본다. 공급자가 아무 이벤트도
# 내지 않은 채 멈춘 소켓은 러너가 깨우지 못하므로(그 모듈 "타임아웃의 범위") HTTP 타임아웃을
# 여기서 건다. 턴 기본 타임아웃(300초)보다 짧게 잡아 러너의 유예보다 먼저 끊기게 한다.
DEFAULT_TIMEOUT_SECONDS = 120.0
# SDK 기본값(2)을 그대로 쓴다. 429·5xx는 SDK가 지수 백오프로 재시도하고, 그래도 실패하면 예외가
# 올라와 `_failures.py`가 `FailureCode`로 옮긴다.
DEFAULT_MAX_RETRIES = 2


class OpenAiResponseStream(Protocol):
    """`openai.Stream[ResponseStreamEvent]`가 구조적으로 만족하는 최소 표면.

    `contextlib.AbstractContextManager`를 쓰지 않는 이유는 그쪽 `__exit__`가 `bool | None`을
    돌려주는 반면 SDK는 `None`만 돌려줘 구조적 검사가 어긋나기 때문이다.
    """

    def __iter__(self) -> Iterator[ResponseStreamEvent]: ...

    def __enter__(self) -> OpenAiResponseStream: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


class OpenAiResponsesClient(Protocol):
    """`openai.OpenAI().responses`가 구조적으로 만족하는 최소 표면.

    adapter가 실제로 부르는 인자만 적는다. SDK가 인자를 더 받는 것은 이 Protocol과 어긋나지
    않지만, 여기 없는 인자를 adapter가 쓰기 시작하면 타입 검사가 먼저 막는다.
    """

    def stream_response(
        self,
        *,
        include: list[ResponseIncludable],
        input: list[ResponseInputItemParam],
        instructions: str,
        max_output_tokens: int,
        model: str,
        reasoning: Reasoning,
        tools: list[ToolParam],
    ) -> OpenAiResponseStream: ...

    def create(
        self,
        *,
        input: str,
        max_output_tokens: int,
        model: str,
    ) -> Response: ...


# `(secret, base_url) -> client`. 비밀을 붙잡아 두지 않으려고 호출마다 새로 만든다.
OpenAiClientFactory = Callable[[str, str | None], OpenAiResponsesClient]


class SdkResponsesClient:
    """`OpenAiResponsesClient`의 실제 SDK 구현.

    `client.responses`를 그대로 돌려주지 않고 한 겹 두는 이유는 `create`의 오버로드 때문이다.
    SDK의 `responses.create`는 `stream` 인자의 리터럴 값으로 반환 타입이 갈리는 오버로드라
    (`Response` vs `Stream[ResponseStreamEvent]`), Protocol 하나로는 두 쓰임을 같이 적을 수 없다.
    여기서 스트리밍 호출과 단발 호출을 다른 이름의 메서드로 갈라 각자 타입을 확정한다.
    """

    def __init__(self, client: openai.OpenAI) -> None:
        self._client = client

    def stream_response(
        self,
        *,
        include: list[ResponseIncludable],
        input: list[ResponseInputItemParam],
        instructions: str,
        max_output_tokens: int,
        model: str,
        reasoning: Reasoning,
        tools: list[ToolParam],
    ) -> OpenAiResponseStream:
        return self._client.responses.create(
            stream=True,
            include=include,
            input=input,
            instructions=instructions,
            max_output_tokens=max_output_tokens,
            model=model,
            reasoning=reasoning,
            tools=tools,
        )

    def create(self, *, input: str, max_output_tokens: int, model: str) -> Response:
        return self._client.responses.create(
            input=input,
            max_output_tokens=max_output_tokens,
            model=model,
        )


def sdk_client_factory(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> OpenAiClientFactory:
    """실제 SDK 클라이언트를 만드는 팩토리. 프로덕션 배선의 기본값이다."""

    def build(secret: str, base_url: str | None) -> OpenAiResponsesClient:
        return SdkResponsesClient(
            openai.OpenAI(
                api_key=secret,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
        )

    return build
