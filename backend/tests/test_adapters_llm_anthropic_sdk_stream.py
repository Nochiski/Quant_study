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

from strategy_workbench.adapters.outbound.llm_anthropic._client import (  # noqa: E402  # reason: 위와 같음
    SdkMessagesClient,
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
