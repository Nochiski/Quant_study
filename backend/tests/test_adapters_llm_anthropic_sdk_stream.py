"""`SdkMessagesClient`를 실제 SDK 스트림 경로에 태우는 회귀 테스트 (A-05).

## 왜 따로 있는가

`test_adapters_llm_anthropic.py`의 대본 fixture는 `AnthropicMessagesClient` **자리에 통째로**
꽂힌다. 그래서 루프·상한·이벤트 번역은 전부 검증되지만 `_client.py`는 한 줄도 실행되지 않고,
SDK의 스트림 누적기도 지나지 않는다.

그 공백에서 실제로 사고가 났다. `output_format=None`을 명시했더니 SDK가 "구조화 출력 요청"으로
읽어(`is_given(None)`은 참, 센티널은 `omit`) 모든 텍스트 블록에서 `TypeAdapter(None)
.validate_json`이 터졌다. 타입은 맞고 런타임 의미만 달랐으므로 pyright도 42건의 대본 테스트도
전부 초록이었다. 네트워크 없이 재현되는 버그가 live 전까지 숨어 있었다.

## 어떻게 네트워크를 안 쓰는가

`httpx2.MockTransport`로 SSE 본문을 직접 돌려준다. 그 위는 전부 진짜다 — 진짜
`anthropic.Anthropic`, 진짜 `messages.stream`, 진짜 누적기, 진짜 `SdkMessagesClient`,
진짜 `AnthropicLlmAdapter`. 가짜는 소켓뿐이다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

pytest.importorskip(
    "anthropic",
    reason="공급자 SDK는 optional extra `llm`이다. 미설치 환경에서는 이 모듈을 건너뛴다.",
)

import anthropic  # noqa: E402  # reason: 위 importorskip 뒤에야 안전하게 import할 수 있다
import httpx2  # noqa: E402  # reason: 위와 같음 (anthropic이 끌고 오는 전송 계층)

from strategy_workbench.adapters.outbound.llm_anthropic import (  # noqa: E402  # reason: 위와 같음
    _client as client_module,
)
from strategy_workbench.adapters.outbound.llm_anthropic._client import (  # noqa: E402  # reason: 위와 같음
    AUTH_HEADER,
    DEFAULT_BASE_URL,
    SdkMessagesClient,
    sdk_client_factory,
)
from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import (  # noqa: E402  # reason: 위와 같음
    AnthropicLlmAdapter,
    AnthropicMessagesClient,
)
from strategy_workbench.domain.assistant.facade.models import (  # noqa: E402  # reason: 위와 같음
    Done,
    Failure,
    TextDelta,
    ToolResult,
    Usage,
)

from .test_adapters_llm_anthropic import (  # noqa: E402  # reason: 위와 같음
    SECRET,
    make_profile,
    make_request,
)

_MESSAGE_ID = "msg_sdk_stream_1"
_MODEL = "claude-opus-5"
# monkeypatch가 `anthropic.Anthropic`을 팩토리로 바꾸기 전에 기본 클래스를 붙잡아 둔다.
_BASE_ANTHROPIC = anthropic.Anthropic


def _sse(events: list[tuple[str, dict[str, object]]]) -> bytes:
    """Anthropic SSE 본문. 실제 응답과 같은 `event:`/`data:` 한 쌍씩이다."""
    lines: list[str] = []
    for name, payload in events:
        lines.append(f"event: {name}")
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _text_turn_body(*chunks: str) -> bytes:
    """텍스트 블록 하나로 끝나는 한 턴. P0가 터졌던 바로 그 모양이다."""
    events: list[tuple[str, dict[str, object]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": _MESSAGE_ID,
                    "type": "message",
                    "role": "assistant",
                    "model": _MODEL,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 120, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
    ]
    events.extend(
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": chunk},
            },
        )
        for chunk in chunks
    )
    events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 34},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return _sse(events)


def _client_factory(body: bytes) -> tuple[object, list[httpx2.Request]]:
    """SSE 본문 하나를 돌려주는 진짜 SDK 클라이언트 팩토리."""
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    def build(secret: str, base_url: str | None) -> AnthropicMessagesClient:
        return SdkMessagesClient(
            anthropic.Anthropic(
                api_key=secret,
                base_url=base_url,
                max_retries=0,
                http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle)),
            )
        )

    return build, seen


def _run(body: bytes) -> tuple[list[object], list[httpx2.Request]]:
    build, seen = _client_factory(body)
    adapter = AnthropicLlmAdapter(client_factory=build)  # pyright: ignore[reportArgumentType]  # reason: 로컬 팩토리는 구조만 맞춘다
    events: Iterator[object] = adapter.stream_turn(
        SECRET,
        make_profile(model=_MODEL),
        make_request(),
        lambda call: ToolResult(call_id=call.call_id, ok=True, content="{}"),
        lambda: False,
    )
    return list(events), seen


def test_a_real_sdk_stream_of_text_reaches_the_turn_as_deltas_and_a_done() -> None:
    """P0 회귀: `output_format=None`이 돌아오면 여기서 `ValidationError`로 터진다."""
    events, _ = _run(_text_turn_body("모멘", "텀 전략"))

    assert events == [
        TextDelta(text="모멘"),
        TextDelta(text="텀 전략"),
        Usage(input_tokens=120, output_tokens=34),
        Done(stop_reason="end_turn"),
    ]
    assert not any(isinstance(event, Failure) for event in events)


def test_the_request_body_carries_the_tools_thinking_and_cache_breakpoints() -> None:
    """`_client.py`가 실제로 보내는 본문. 대본 fixture는 여기까지 내려가지 않는다."""
    _, seen = _run(_text_turn_body("답"))

    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert body["model"] == _MODEL
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "high"}
    # `output_format`은 본문에 아예 없어야 한다 — 있으면 P0가 돌아온 것이다.
    assert "output_format" not in body
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all(tool.get("cache_control") is None for tool in body["tools"][:-1])
    assert [message.get("cache_control") for message in body["messages"]] == [None]
    declared = [tool for tool in body["tools"] if "type" not in tool]
    assert all(tool["strict"] is True for tool in declared)


# -- SDK 환경 변수 폴백 차단 -------------------------------------------------------------------


def test_an_env_base_url_cannot_redirect_the_key_to_another_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ANTHROPIC_BASE_URL`이 프로파일 키를 남의 호스트로 보내면 안 된다.

    프로파일이 base_url을 말하지 않을 때 SDK에 `None`을 넘기면 SDK가 이 환경 변수를 읽는다.
    그 호스트는 spec D6의 base_url 검사를 **한 번도 지나지 않았고**, 화면에는 정상으로 보인다.
    키 누설은 회수 경로가 없다.
    """
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example.com")
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=_text_turn_body("답")
        )

    def build(secret: str, base_url: str | None) -> AnthropicMessagesClient:
        client = anthropic.Anthropic(
            api_key=secret,
            base_url=base_url if base_url is not None else DEFAULT_BASE_URL,
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle)),
        )
        return SdkMessagesClient(client)

    adapter = AnthropicLlmAdapter(client_factory=build)  # pyright: ignore[reportArgumentType]  # reason: 로컬 팩토리는 구조만 맞춘다
    list(
        adapter.stream_turn(
            SECRET,
            make_profile(model=_MODEL, base_url=None),
            make_request(),
            lambda call: ToolResult(call_id=call.call_id, ok=True, content="{}"),
            lambda: False,
        )
    )

    assert len(seen) == 1
    assert str(seen[0].url).startswith(DEFAULT_BASE_URL)
    assert "evil.example.com" not in str(seen[0].url)


def test_the_production_factory_pins_the_host_and_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """프로덕션 배선(`sdk_client_factory`)이 환경 변수를 읽지 않는다.

    위 테스트는 로컬 팩토리를 쓰므로 진짜 배선이 같은 규칙을 지키는지는 따로 본다.
    """
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ENV-KEY-NOT-THE-PROFILE")

    client = sdk_client_factory()(SECRET, None)

    assert isinstance(client, SdkMessagesClient)
    sdk = client._client  # pyright: ignore[reportPrivateUsage]  # reason: 배선을 밖에서 볼 창구가 없다
    assert str(sdk.base_url).rstrip("/") == DEFAULT_BASE_URL
    assert sdk.api_key == SECRET


def test_an_explicit_profile_base_url_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example.com")

    client = sdk_client_factory()(SECRET, "https://gateway.example.test")

    assert isinstance(client, SdkMessagesClient)
    sdk = client._client  # pyright: ignore[reportPrivateUsage]  # reason: 위와 같음
    assert str(sdk.base_url).rstrip("/") == "https://gateway.example.test"


def test_the_pinned_host_matches_the_sdk_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """상수가 SDK 기본값에서 조용히 갈라지지 않게 고정한다.

    SDK가 기본 호스트를 바꾸면 우리 상수가 옛 호스트를 가리키게 되는데, 그건 "환경 변수를
    막는다"와 별개의 사고다. 여기가 빨개져야 알아챈다.
    """
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    assert str(anthropic.Anthropic(api_key="sk-unused").base_url).rstrip("/") == DEFAULT_BASE_URL


def _headers_from_a_production_call(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """프로덕션 팩토리로 한 번 호출하고 **실제 요청 헤더**를 돌려준다.

    팩토리가 무엇을 넘겼는지가 아니라 소켓에 실린 것을 본다. SDK가 `default_headers`를 인증
    헤더보다 뒤에 합치므로, "넘겼다"와 "실제로 그 값이 나갔다" 사이에 실제로 차이가 생긴다.
    전송 계층만 가짜다 — 클라이언트 생성은 프로덕션 코드가 한다.

    전송을 바꿔 끼울 때 `anthropic.Anthropic`의 **하위 클래스를 만들면 안 된다.** SDK는
    `type(client) in (Anthropic, AsyncAnthropic)`일 때만 자격 증명 auto-discovery 체인을
    돌리므로, 하위 클래스로는 그 체인이 gate와 무관하게 절대 돌지 않는다. 그러면 gate가 바뀌어도
    이 경로의 테스트는 전부 초록이다(1차 리뷰가 SDK gate 돌연변이로 확인했다). 그래서 기본 클래스
    인스턴스를 돌려주는 팩토리로 바꿔 끼운다.
    """
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=_text_turn_body("답")
        )

    def build_base_client(**kwargs: object) -> anthropic.Anthropic:
        return _BASE_ANTHROPIC(
            **kwargs,  # pyright: ignore[reportArgumentType]  # reason: 프로덕션 인자를 그대로 넘긴다
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle)),
        )

    monkeypatch.setattr(client_module.anthropic, "Anthropic", build_base_client)
    client = sdk_client_factory()(SECRET, None)
    with client.stream(
        max_tokens=16,
        messages=[{"role": "user", "content": "x"}],
        model=_MODEL,
        output_config={"effort": "high"},
        system=[],
        thinking={"type": "adaptive"},
        tools=[],
    ) as stream:
        list(stream)

    assert len(seen) == 1
    return {name.lower(): value for name, value in seen[0].headers.items()}


def test_custom_header_env_cannot_replace_the_profile_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ANTHROPIC_CUSTOM_HEADERS`가 인증 헤더를 덮어쓰면 안 된다.

    `api_key`를 명시해도 이 환경 변수는 남는다. SDK가 값을 `"이름: 값"` 줄 단위로 파싱해
    `default_headers`에 넣고, 그것이 인증 헤더보다 **뒤에** 합쳐지기 때문이다. 막지 않으면
    우리 요청이 남의 키로 나간다 — 실측으로 확인한 동작이다.
    """
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS",
        f"{AUTH_HEADER}: sk-ant-ATTACKER" + chr(10) + "Authorization: Bearer sk-ant-ATTACKER-2",
    )
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-ENV-TOKEN")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ENV-KEY")

    headers = _headers_from_a_production_call(monkeypatch)

    assert headers[AUTH_HEADER.lower()] == SECRET
    # 우리는 bearer를 쓰지 않는다. 주입된 것이 남아 있으면 안 된다.
    assert "authorization" not in headers


def _sdk_credential_env_names() -> tuple[str, ...]:
    """SDK가 자격 증명·목적지를 정하려고 읽는 환경 변수 전수.

    로그(`ANTHROPIC_LOG`)나 프록시 변수처럼 자격 증명·목적지와 무관한 것은 범위 밖이다.

    목록을 손으로 적지 않고 **SDK의 상수에서 읽는다.** 상수가 있는 곳은 SDK private 모듈
    (`anthropic.lib.credentials._constants`)이다. 모듈 이름이 바뀌면 ImportError로 빨개지므로
    조용히 넘어가지는 않는다 — 그때 새 위치를 찾아 고치면 된다. 손으로 적으면 SDK가 변수를 하나 더
    읽기 시작해도 기준선이 그대로라 아무것도 빨개지지 않는다 — 그 침묵이 NB-9이 지적한 위험이다.

    `_constants.py` 밖에서 읽히는 둘은 여기서 더한다. `ANTHROPIC_CUSTOM_HEADERS`는 클라이언트
    생성 경로가, `ANTHROPIC_WEBHOOK_SIGNING_KEY`는 webhook 검증이 읽는다. 둘 다 `ENV_*` 상수가
    아니라 자동으로 딸려오지 않는다.
    """
    from anthropic.lib.credentials import _constants

    discovered = tuple(
        value
        for name, value in sorted(vars(_constants).items())
        if name.startswith("ENV_") and isinstance(value, str)
    )
    # 목록이 비면 아래 기준선이 "아무것도 안 심은" 테스트로 조용히 바뀐다.
    assert discovered, "SDK에서 자격 증명 환경 변수를 하나도 찾지 못했다"
    return (*discovered, "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_WEBHOOK_SIGNING_KEY")


# 체인 앞단에서 곧바로 끝나게 만드는 둘. 이 둘이 있으면 체인이 돌더라도 나머지 변수는 읽히지 않는다.
_CHAIN_SHORT_CIRCUIT_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@pytest.mark.parametrize(
    "include_direct_keys",
    [True, False],
    ids=["every_credential_env", "discovery_chain_env_only"],
)
def test_the_only_credential_on_the_wire_is_the_profile_secret(
    monkeypatch: pytest.MonkeyPatch,
    include_direct_keys: bool,
) -> None:
    """SDK가 읽는 자격 증명 환경 변수를 심고도 프로파일 비밀 하나만 나가는지 본다 (감사 NB-9).

    SDK는 `credentials`·`api_key`·`auth_token`이 모두 없고 클라이언트가 기본 클래스일 때만
    자격 증명 auto-discovery 체인을 돌린다. 프로덕션은 `api_key`를 명시하므로 오늘은 체인이
    돌지 않고, 이 테스트가 지키는 것이 그 gate다.

    두 경우가 필요하다.

    - `every_credential_env`: 전부 심는다. 무엇이 새로 읽히든 헤더에는 프로파일 비밀만 있어야 한다.
      다만 체인이 돌더라도 `ANTHROPIC_API_KEY`가 있으면 체인이 앞단에서 끝나 나머지 변수를 읽지
      않으므로, 이 경우만으로는 gate 변화를 보지 못한다.
    - `discovery_chain_env_only`: 앞단에서 끝내는 둘을 **빼고** 체인 변수만 심는다. gate가
      명시 `api_key`와 무관하게 체인을 돌리기 시작하면 체인이 심어 둔 프로파일·설정 디렉터리를
      읽으러 가서 클라이언트 생성이나 헤더 단언에서 빨개진다. SDK gate에서 `api_key`·`auth_token`
      조건을 지우는 돌연변이, 기본 클래스 조건까지 지우는 돌연변이 둘 다 이 경우가 잡는다.

    심는 값이 전부 `sk-ant-`로 시작하는 이유는 마지막 단언 때문이다. 헤더에 그 접두가 붙은 값이
    프로파일 비밀 말고 또 있으면 주입된 것이 새어 나간 것이다.
    """
    planted = _sdk_credential_env_names()
    for name in planted:
        if include_direct_keys or name not in _CHAIN_SHORT_CIRCUIT_ENV:
            monkeypatch.setenv(name, f"sk-ant-ENV-{name}")
        else:
            monkeypatch.delenv(name, raising=False)
    # 목적지를 바꾸는 둘은 형식이 정해져 있어 위 접두를 쓸 수 없다.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example.com")
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS",
        f"{AUTH_HEADER}: sk-ant-ENV-CUSTOM-HEADER"
        + chr(10)
        + "Authorization: Bearer sk-ant-ENV-BEARER",
    )

    headers = _headers_from_a_production_call(monkeypatch)

    assert headers[AUTH_HEADER.lower()] == SECRET
    assert "authorization" not in headers
    credentials = [value for name, value in headers.items() if "sk-ant-" in value]
    assert credentials == [SECRET]


def test_the_credential_env_baseline_covers_every_credential_name_the_sdk_reads() -> None:
    """기준선이 자격 증명·목적지 변수의 전수인지 고정한다.

    SDK가 `ENV_*` 상수를 더하면 위 테스트가 자동으로 그것을 심지만, 상수 **밖에서** 읽는 변수가
    늘면 여기서 걸리지 않는다. 그래서 오늘 아는 전수(14종)를 수로 못 박아 둔다. SDK를 올릴 때
    이 수가 바뀌면 무엇이 늘었는지 확인하고 갱신하라는 신호다.
    """
    names = _sdk_credential_env_names()

    assert len(names) == len(set(names))
    assert len(names) == 14, f"SDK가 읽는 환경 변수 수가 바뀌었다 — names={names}"
    assert {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"} <= set(names)
