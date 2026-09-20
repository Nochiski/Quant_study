"""Anthropic SDK와 닿는 유일한 경계 (설계 spec D4).

## 왜 Protocol을 한 겹 두는가

도구 루프(`_turn.py`)는 네트워크 없이 검증되어야 한다. 실제 호출을 하는 테스트는 비결정적이고
느리며 키를 요구한다(live smoke는 A-07의 `RUN_LLM_LIVE=1` 한 건뿐이다). 그래서 루프는 SDK
객체가 아니라 여기 선언한 좁은 Protocol에만 기대고, 테스트는 스트림 이벤트 스크립트를 주입한다.

Protocol이 쓰는 타입은 SDK의 실제 타입 그대로다(`MessageParam`, `ToolUnionParam`, `Message`, …).
우리 형식으로 한 번 더 옮기면 SDK 모양이 바뀌었을 때 타입 검사가 잡아 주지 못한다. 테스트
픽스처도 같은 이유로 SDK 타입을 그대로 만들어 쓴다.

## 비밀

클라이언트는 호출마다 `secret`으로 새로 만들고 보관하지 않는다(`LlmProviderPort` 계약). 그래서
팩토리는 `(secret, base_url) -> client` 형태이지 미리 만들어 둔 클라이언트가 아니다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from types import TracebackType
from typing import Protocol, TypeAlias

import anthropic
from anthropic.lib.streaming import ParsedMessageStreamEvent
from anthropic.types import (
    Message,
    MessageParam,
    OutputConfigParam,
    ParsedMessage,
    TextBlockParam,
    ThinkingConfigParam,
    ToolUnionParam,
)

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_SECONDS",
    "AnthropicClientFactory",
    "AnthropicMessageStream",
    "AnthropicMessagesClient",
    "AnthropicStreamManager",
    "SdkMessagesClient",
    "StreamEvent",
    "TurnMessage",
    "sdk_client_factory",
]

# `messages.stream`이 실제로 흘리는 이벤트·메시지 타입. `output_format` 없이 부르므로 파라미터는
# 언제나 `None`이다. `ParsedMessage`는 `Message`의 하위 타입이라 `.content`·`.stop_reason`·
# `.usage`를 읽는 코드는 그대로다.
#
# `StreamEvent`가 문자열인 이유: `ParsedMessageStreamEvent`는 `Annotated[Union[...], ...]` 별칭이라
# 파이썬 3.11 런타임에서 `[None]` 첨자를 받지 못한다(`TypeError: ... is not a generic class`).
# 문자열 별칭은 타입 검사기만 읽으므로 import 시점에 터지지 않는다.
StreamEvent: TypeAlias = "ParsedMessageStreamEvent[None]"
TurnMessage: TypeAlias = ParsedMessage[None]

# 턴당 벽시계 타임아웃은 `AssistantTurnRunner`가 이벤트 사이에서만 본다. 공급자가 아무 이벤트도
# 내지 않은 채 멈춘 소켓은 러너가 깨우지 못하므로(그 모듈 "타임아웃의 범위") HTTP 타임아웃을
# 여기서 건다. 턴 기본 타임아웃(300초)보다 짧게 잡아 러너의 유예보다 먼저 끊기게 한다.
DEFAULT_TIMEOUT_SECONDS = 120.0
# SDK 기본값(2)을 그대로 쓴다. 429·5xx는 SDK가 지수 백오프로 재시도하고, 그래도 실패하면 예외가
# 올라와 `_failures.py`가 `FailureCode`로 옮긴다.
DEFAULT_MAX_RETRIES = 2


class AnthropicMessageStream(Protocol):
    """`anthropic.lib.streaming.MessageStream`이 구조적으로 만족하는 최소 표면."""

    def __iter__(self) -> Iterator[StreamEvent]: ...

    def get_final_message(self) -> TurnMessage: ...


class AnthropicStreamManager(Protocol):
    """`MessageStreamManager`가 구조적으로 만족하는 최소 표면.

    `contextlib.AbstractContextManager`를 쓰지 않는 이유는 그쪽 `__exit__`가 `bool | None`을
    돌려주는 반면 SDK는 `None`만 돌려줘 구조적 검사가 어긋나기 때문이다.
    """

    def __enter__(self) -> AnthropicMessageStream: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


class AnthropicMessagesClient(Protocol):
    """`anthropic.Anthropic().messages`가 구조적으로 만족하는 최소 표면.

    adapter가 실제로 부르는 인자만 적는다. SDK가 인자를 더 받는 것은 이 Protocol과 어긋나지
    않지만, 여기 없는 인자를 adapter가 쓰기 시작하면 타입 검사가 먼저 막는다.
    """

    def stream(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: str,
        output_config: OutputConfigParam,
        system: Iterable[TextBlockParam],
        thinking: ThinkingConfigParam,
        tools: Iterable[ToolUnionParam],
    ) -> AnthropicStreamManager: ...

    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: str,
    ) -> Message: ...


# `(secret, base_url) -> client`. 비밀을 붙잡아 두지 않으려고 호출마다 새로 만든다.
AnthropicClientFactory = Callable[[str, str | None], AnthropicMessagesClient]


class SdkMessagesClient:
    """`AnthropicMessagesClient`의 실제 SDK 구현.

    `client.messages`를 그대로 돌려주지 않고 한 겹 두는 이유는 타입 하나 때문이다. SDK의
    `messages.stream`은 `output_format` 인자로 결정되는 제네릭(`MessageStreamManager[T]`)을
    돌려주는데, 우리는 그 인자를 쓰지 않아 `T`가 풀리지 않는다. 여기서 우리가 실제로 보내는
    인자만으로 한 번 부르면 `T`가 `None`으로 확정되고, 위 Protocol과 구조가 맞는다.
    """

    def __init__(self, client: anthropic.Anthropic) -> None:
        self._client = client

    def stream(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: str,
        output_config: OutputConfigParam,
        system: Iterable[TextBlockParam],
        thinking: ThinkingConfigParam,
        tools: Iterable[ToolUnionParam],
    ) -> AnthropicStreamManager:
        return self._client.messages.stream(
            max_tokens=max_tokens,
            messages=messages,
            model=model,
            output_config=output_config,
            output_format=None,
            system=system,
            thinking=thinking,
            tools=tools,
        )

    def create(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: str,
    ) -> Message:
        return self._client.messages.create(
            max_tokens=max_tokens,
            messages=messages,
            model=model,
        )


def sdk_client_factory(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AnthropicClientFactory:
    """실제 SDK 클라이언트를 만드는 팩토리. 프로덕션 배선의 기본값이다."""

    def build(secret: str, base_url: str | None) -> AnthropicMessagesClient:
        return SdkMessagesClient(
            anthropic.Anthropic(
                api_key=secret,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
        )

    return build
