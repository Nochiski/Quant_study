"""OpenAI SDK 클라이언트를 만들고 실제로 호출하는 모듈 (설계 spec D4).

`openai`를 import하는 유일한 파일은 아니다 — `_payload.py`·`_turn.py`·`_failures.py`도 SDK 타입을
쓴다(architecture 게이트는 노드 디렉터리 전체를 허용한다). 여기만 다른 점은 **클라이언트를 만들고
네트워크로 나가는 호출을 거는 곳**이 여기뿐이라는 것이다.

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

## 응답을 공급자에 저장하지 않는다

`store`는 **주지 않으면 참**이고, 그러면 응답이 공급자 서버에 최소 30일 남는다. 요청에 실리는
것은 runtime schema 요약·데이터 필드 카탈로그·팩터 카탈로그(spec D3)와 사용자의 전략 YAML 원문·
대화다. 비밀은 아니지만 사용자 저작물이다.

`store=False`를 명시한다. 이 adapter는 대화 상태를 서버에 맡기지 않고 `input`에 누적하므로
(`_turn.py` "대화 상태를 서버에 맡기지 않는다") 저장의 이득이 없다. `include`에
`reasoning.encrypted_content`를 넣는 것도 SDK가 **stateless**(`store=false`·ZDR) 맥락으로 설명하는
기제다. 저장을 켜 두면 이득 없이 보존 비용만 치른다.

## 서버 환경이 사용자 호출을 바꾸지 못하게 한다

**SDK는 인자를 주지 않으면 환경 변수를 읽는다.** 읽는 것은 일곱이다 — `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, `OPENAI_CUSTOM_HEADERS`,
`OPENAI_ADMIN_KEY`, `OPENAI_WEBHOOK_SECRET`. 앞의 다섯은 **누가 어디로 무엇을 들고 호출하는가**를
바꾼다. 그 값은 프로파일이 정해야 하고, 운영 셸에 남아 있던 변수가 정해서는 안 된다.

우리가 닫는 것과 그 이유:

- `api_key`: 언제나 프로파일 비밀을 명시한다. 비면 SDK가 `OPENAI_API_KEY`를 읽어, 사용자가 지운
  프로파일로도 호출이 성립하고 화면의 꼬리 4자리가 실제로 쓰인 키와 달라진다.
- `base_url`: 프로파일이 말하지 않으면 `DEFAULT_BASE_URL`을 명시한다. `None`을 넘기면 SDK가
  `OPENAI_BASE_URL`을 읽어, 사용자의 키가 application의 base_url 규칙(spec D6: https만, 루프백·
  사설 대역 금지)을 한 번도 통과하지 않은 호스트로 나간다.
- `default_headers`: `Authorization`을 직접 넣는다. 주지 않으면 SDK가 `OPENAI_CUSTOM_HEADERS`를
  파싱해 그 자리에 넣고, 그 매핑은 **`api_key`로 만든 인증 헤더를 덮는다.** 즉 프로파일 비밀이
  아예 나가지 않는다. 빈 dict는 듣지 않는다 — SDK가 "명시된 Authorization이 있는가"로 갈리므로
  값이 실제로 들어 있어야 env 쪽 줄이 걸러진다.
- `OpenAI-Organization`·`OpenAI-Project`: `omit` 센티널로 **지운다.** `organization=None`만으로는
  모자라다 — SDK는 두 헤더를 `Omit()`으로 두고 나서 `**self._custom_headers`를 **그 뒤에** 병합하고,
  env에서 파싱된 같은 이름의 줄이 거기 들어 있으면 그것이 이긴다. 두 헤더는 요청이 **어느 조직·
  프로젝트로 과금되고 접근되는지**를 정하므로 자격 증명과 같은 급이다. 명시 헤더는 env 쪽보다
  뒤에 병합되므로(`{**parsed, **explicit}`) `omit`이 이긴다.

`OPENAI_CUSTOM_HEADERS`의 형식은 SDK 소스로 확인했다 — 줄바꿈으로 나누고 첫 `:`에서 이름과 값을
가르며, 이름은 대소문자를 구분하지 않고 `authorization`과 비교된다.

**남겨 둔 것**: 위 세 이름이 아닌 임의 env 헤더(`X-Evil: 1` 같은)는 여전히 요청에 붙는다. 그걸
막으려면 SDK의 private(`_custom_headers`)을 비워야 해서, 자격 증명과 과금 귀속이 닫힌 선에서
멈췄다. `OPENAI_ADMIN_KEY`·`OPENAI_WEBHOOK_SECRET`은 이 경로가 쓰지 않는 표면이다. 환경 변수
전수와 차단 여부는 spec D6의 표가 정본이다.

값이 SDK 기본과 어긋나면 테스트가 깨진다(`test_the_default_base_url_matches_the_sdk`). 환경 변수
쪽은 **실제로 나가는 요청 헤더**로 단언한다 — `auth_headers` 속성은 위 덮어쓰기를 보지 못한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Protocol, cast

import openai
from openai import omit
from openai.types.responses import (
    Response,
    ResponseIncludable,
    ResponseInputItemParam,
    ResponseStreamEvent,
    ToolParam,
)
from openai.types.shared_params import Reasoning

if TYPE_CHECKING:
    # `httpx2`는 SDK가 끌고 오는 전송 계층이고 우리 의존성 선언에는 없다. 여기서 쓰는 곳이
    # `http_client` 인자의 타입 하나뿐이라(`from __future__ import annotations`로 문자열이다)
    # 런타임 import를 만들지 않는다 — extra 없이 이 모듈을 읽는 경로가 생겨도 깨지지 않는다.
    import httpx2

__all__ = [
    "DEFAULT_BASE_URL",
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
# 프로파일이 base_url을 말하지 않을 때 쓰는 값. SDK 기본과 같아야 하며 그 일치는 테스트가 본다.
# 여기 적는 이유는 위 "base_url은 프로파일만 정한다"에 있다 — `None`을 넘기면 환경 변수가 이긴다.
DEFAULT_BASE_URL = "https://api.openai.com/v1"


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
        store: bool,
        tools: list[ToolParam],
    ) -> OpenAiResponseStream: ...

    def create(
        self,
        *,
        input: str,
        max_output_tokens: int,
        model: str,
        store: bool,
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
        store: bool,
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
            store=store,
            tools=tools,
        )

    def create(self, *, input: str, max_output_tokens: int, model: str, store: bool) -> Response:
        return self._client.responses.create(
            input=input,
            max_output_tokens=max_output_tokens,
            model=model,
            store=store,
        )


def sdk_client_factory(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    http_client: httpx2.Client | None = None,
) -> OpenAiClientFactory:
    """실제 SDK 클라이언트를 만드는 팩토리. 프로덕션 배선의 기본값이다.

    Args:
        timeout_seconds: HTTP 타임아웃.
        max_retries: SDK 재시도 횟수.
        http_client: 전송 계층. 프로덕션은 주지 않는다. 테스트가 여기에
            `httpx2.MockTransport`를 꽂아 **실제로 나가는 요청 헤더**를 본다 — 환경 변수가 인증
            헤더를 덮는지는 속성이 아니라 wire에서만 보이기 때문이다(모듈 docstring).
    """

    def build(secret: str, base_url: str | None) -> OpenAiResponsesClient:
        client = openai.OpenAI(
            api_key=secret,
            base_url=base_url if base_url is not None else DEFAULT_BASE_URL,
            timeout=timeout_seconds,
            max_retries=max_retries,
            # 이 자리를 비우면 SDK가 `OPENAI_CUSTOM_HEADERS`를 파싱해 넣고, 그 매핑이 인증 헤더와
            # 과금 귀속 헤더를 덮는다. 빈 dict로는 막히지 않는다(모듈 docstring "서버 환경이 …").
            #
            # reason: `default_headers`의 선언 타입은 `Mapping[str, str]`이지만 SDK 자신이 같은
            # 병합 경로에 `Omit()`을 넣어 헤더를 지운다(`_client.py`의 `default_headers` 속성).
            # 지우는 표현이 `omit` 하나뿐이라 여기서만 타입을 넓힌다.
            default_headers=cast(
                "Mapping[str, str]",
                {
                    "Authorization": f"Bearer {secret}",
                    "OpenAI-Organization": omit,
                    "OpenAI-Project": omit,
                },
            ),
            http_client=http_client,
        )
        # 생성자는 `None`을 "환경 변수를 읽어라"로 읽는다. 만든 뒤에 되돌려야 두 값이 빠진다.
        # 위 `omit`과 둘 다 필요하다 — 이쪽은 `OPENAI_ORG_ID`/`OPENAI_PROJECT_ID`를, 저쪽은
        # `OPENAI_CUSTOM_HEADERS`의 같은 이름 줄을 막는다.
        client.organization = None
        client.project = None
        return SdkResponsesClient(client)

    return build
