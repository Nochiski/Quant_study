"""Anthropic SDK 클라이언트를 만들고 부르는 유일한 모듈 (설계 spec D4).

## 왜 Protocol을 한 겹 두는가

도구 루프(`_turn.py`)는 네트워크 없이 검증되어야 한다. 실제 호출을 하는 테스트는 비결정적이고
느리며 키를 요구한다(live smoke는 A-07의 `RUN_LLM_LIVE=1` 한 건뿐이다). 그래서 루프는 SDK
객체가 아니라 여기 선언한 좁은 Protocol에만 기대고, 테스트는 스트림 이벤트 스크립트를 주입한다.

Protocol이 쓰는 타입은 SDK의 실제 타입 그대로다(`MessageParam`, `ToolUnionParam`, `Message`, …).
우리 형식으로 한 번 더 옮기면 SDK 모양이 바뀌었을 때 타입 검사가 잡아 주지 못한다. 테스트
픽스처도 같은 이유로 SDK 타입을 그대로 만들어 쓴다.

**타입이 맞는다고 런타임 의미까지 맞는 것은 아니다.** `output_format=None`을 명시했다가 모든
텍스트 블록에서 `ValidationError`가 났던 적이 있다 — SDK가 "인자 없음"으로 보는 센티널은
`None`이 아니라 `omit`이고, `is_given(None)`은 참이다. 그래서 이 모듈은 타입 검사만이 아니라
실제 SDK 스트림을 지나는 테스트로도 고정한다(`test_adapters_llm_anthropic_sdk_stream.py`).
쓰지 않는 인자는 `None`으로 넘기지 말고 **아예 빼라**.

같은 이유로 `anthropic`을 import하는 모듈이 여기 하나뿐인 것은 아니다(`_payload`·`_turn`·
`_failures`도 SDK 타입을 쓴다). 여기만 하는 일은 **클라이언트를 만들고 실제로 호출하는 것**이다.

## 비밀

클라이언트는 호출마다 `secret`으로 새로 만들고 보관하지 않는다(`LlmProviderPort` 계약). 그래서
팩토리는 `(secret, base_url) -> client` 형태이지 미리 만들어 둔 클라이언트가 아니다.

## SDK의 환경 변수 폴백을 막는다

`base_url`과 `api_key`는 **언제나 명시한다**. SDK는 둘 다 인자가 없으면 `ANTHROPIC_BASE_URL`·
`ANTHROPIC_API_KEY`를 읽는데, 그러면 사용자가 등록하지도 않은 호스트로 프로파일의 키가 나간다
(spec D6의 base_url 검사를 **한 번도 지나지 않은** 호스트다). 화면은 정상으로 보인다.

프로파일이 base_url을 말하지 않으면 `None`을 그대로 넘기지 말고 `DEFAULT_BASE_URL`을 넘겨
환경 변수가 끼어들 자리를 없앤다. `api_key`도 프로파일 비밀만 쓰고 환경 변수 폴백이 없다.

**인증 헤더도 못 박는다.** `api_key`를 명시해도 `ANTHROPIC_CUSTOM_HEADERS`가 남는다. SDK는 그
값을 `"이름: 값"` 줄 단위로 파싱해 `default_headers`에 넣고, `default_headers`는 인증 헤더보다
**뒤에** 합쳐지므로 `X-Api-Key`를 통째로 덮어쓸 수 있다. 실측으로 확인했다 — 그 환경 변수만
있으면 우리 요청이 남의 키로 나간다. 그래서 `X-Api-Key`를 프로파일 비밀로 명시하고
`Authorization`은 `omit`으로 지운다(명시한 `default_headers`가 환경 변수 파싱분을 이긴다).

**인증과 무관한 헤더가 주입되는 것까지는 막지 않는다. 그것이 의도다.** 같은 환경 변수로
`anthropic-beta` 같은 줄이나 사내 프록시가 요구하는 헤더를 더할 수 있고, 운영자가 자기 배포에
그런 헤더를 얹는 것은 정당한 용도다. SDK private(`_custom_headers`)을 비워 막으면 그 쓰임까지
같이 닫힌다.

안전한 이유는 두 가지가 남아 있기 때문이다.

- **목적지가 고정된다.** `base_url`은 프로파일이 정하고 없으면 우리가 공급자 기본을 명시하므로,
  주입된 헤더가 요청을 다른 호스트로 보낼 수 없다. 비밀이 새려면 목적지가 바뀌어야 한다.
- **신원이 고정된다.** 인증 헤더는 팩토리가 프로파일 비밀로 못 박고 쓰지 않는 쪽은 지우므로,
  주입된 헤더가 "누구로 호출하는가"를 바꿀 수 없다.

즉 남은 구멍으로 할 수 있는 것은 우리 요청에 부가 정보를 덧붙이는 것뿐이고, 비밀을 다른 곳으로
보내거나 신원을 갈아 끼우는 경로는 닫혀 있다. OpenAI adapter도 같은 선에서 멈췄고, 두 공급자의
환경 변수 전수와 차단 여부는 spec D6의 표가 정본이다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from types import TracebackType
from typing import Protocol, TypeAlias, cast

import anthropic
from anthropic._types import Omit, omit
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
    "AUTH_HEADER",
    "DEFAULT_BASE_URL",
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

# 프로파일이 base_url을 말하지 않을 때 쓰는 호스트. SDK 기본값과 같은 값이지만 **우리가
# 명시해야** `ANTHROPIC_BASE_URL`이 끼어들지 못한다(모듈 docstring). 테스트가 이 상수와 SDK
# 기본값이 같은지 고정하므로, SDK가 기본 호스트를 바꾸면 조용히 어긋나지 않고 빨개진다.
DEFAULT_BASE_URL = "https://api.anthropic.com"

# SDK가 API 키를 싣는 헤더 이름(`Anthropic._api_key_auth`). 상수로 두는 이유는 이 이름이 틀리면
# 덮어쓰기를 막지 못하면서 막은 것처럼 보이기 때문이다 — 테스트가 실제 요청 헤더로 확인한다.
AUTH_HEADER = "X-Api-Key"
# 키를 명시해도 `ANTHROPIC_CUSTOM_HEADERS`는 `Authorization`을 주입할 수 있다. 우리는 이 헤더를
# 쓰지 않으므로 지운다.
_BEARER_HEADER = "Authorization"


class AnthropicMessageStream(Protocol):
    """`anthropic.lib.streaming.MessageStream`이 구조적으로 만족하는 최소 표면."""

    def __iter__(self) -> Iterator[StreamEvent]: ...

    def get_final_message(self) -> TurnMessage:
        """스트림을 **끝까지 읽고** 누적된 메시지를 돌려준다."""
        ...

    @property
    def current_message_snapshot(self) -> TurnMessage:
        """지금까지 누적된 메시지. 남은 이벤트를 읽지 않는다.

        취소 경로가 이것을 쓴다. `get_final_message()`는 스트림을 끝까지 읽으므로, 취소하고
        나서 부르면 멈추려던 응답을 오히려 전부 받아 온다.
        """
        ...


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

    `client.messages`(SDK의 `Messages` 리소스)를 그대로 돌려주면 위 Protocol과 구조가 맞지
    않는다. `Messages.stream`이 돌려주는 `MessageStreamManager[T]`의 `T`가 호출부 없이는 풀리지
    않기 때문이다. 여기서 우리가 실제로 보내는 인자만으로 한 번 부르면 `T`가 `None`으로
    확정된다. 덧붙여 이 클래스가 클라이언트 수명(`secret`·`base_url`·타임아웃)의 주인이다.
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
        # `output_format`은 **넘기지 않는다**. `None`을 명시하면 SDK가 "구조화 출력 요청"으로
        # 읽어(`is_given(None)`이 참) 모든 텍스트 블록에서 `TypeAdapter(None).validate_json`이
        # 터진다. 센티널은 `omit`이고 그건 인자를 생략할 때의 기본값이다.
        return self._client.messages.stream(
            max_tokens=max_tokens,
            messages=messages,
            model=model,
            output_config=output_config,
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


def _pinned_auth_headers(secret: str) -> Mapping[str, str]:
    """환경 변수가 건드리지 못하게 못 박는 인증 헤더.

    `Authorization`을 `omit`으로 지운다. SDK 생성자의 주석은 `Mapping[str, str]`이라 `Omit`을
    받지 않는 것처럼 보이지만, 같은 SDK의 `default_headers` **property**는
    `dict[str, str | Omit]`이고 런타임은 `Omit`을 "이 헤더를 보내지 않는다"로 처리한다(실측으로
    확인했고, 실제 요청 헤더를 보는 테스트가 그 동작을 고정한다). 생성자 주석이 property보다
    좁은 것이 원인이라 여기 한 곳에서만 좁힌다.
    """
    headers: dict[str, str | Omit] = {AUTH_HEADER: secret, _BEARER_HEADER: omit}
    # reason: SDK 생성자 주석이 자기 property 타입보다 좁다(위 docstring).
    return cast(Mapping[str, str], headers)


def sdk_client_factory(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AnthropicClientFactory:
    """실제 SDK 클라이언트를 만드는 팩토리. 프로덕션 배선의 기본값이다."""

    def build(secret: str, base_url: str | None) -> AnthropicMessagesClient:
        return SdkMessagesClient(
            anthropic.Anthropic(
                # 프로파일 비밀만 쓴다. 인자를 비우면 SDK가 `ANTHROPIC_API_KEY`를 읽는다.
                api_key=secret,
                # `None`을 넘기면 SDK가 `ANTHROPIC_BASE_URL`을 읽어, spec D6 검사를 지나지
                # 않은 호스트로 키가 나간다. 미지정은 기본 호스트를 **명시**한다.
                base_url=base_url if base_url is not None else DEFAULT_BASE_URL,
                # `api_key`를 명시해도 `ANTHROPIC_CUSTOM_HEADERS`가 인증 헤더를 덮어쓸 수 있다
                # (모듈 docstring). 명시한 `default_headers`가 그 파싱분을 이긴다.
                default_headers=_pinned_auth_headers(secret),
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
        )

    return build
