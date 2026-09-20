"""`/api/v1/assistant/*` 왕복을 **진짜 OpenAI adapter**로 한 번 (WORKFLOW A-06).

`test_assistant_http_api.py`는 공급자 자리에 가짜를 꽂아 라우트·SSE 프레이밍·배선을 본다. 여기서
가짜로 바꾸는 것은 한 칸 더 안쪽, **SDK 경계**(`OpenAiResponsesClient`)뿐이다. 프로파일 생성의
`probe`부터 저장된 이벤트까지 `OpenAiLlmAdapter`·`_turn.py`·`_payload.py`가 실제로 도는 경로를
지나간다. adapter 단위 테스트가 초록인데 배선이 틀려 화면에 아무것도 오지 않는 사고를 여기서
잡는다.

**SSE를 열지 않고 이력으로 읽는다.** `TestClient.stream`은 이 저장소의 SSE 라우트에 대해 응답을
끝까지 버퍼링한다 — 스트림이 열리는 시점이 턴이 끝난 뒤라, 턴을 붙잡아 두고 프레임을 읽으려는
테스트는 붙잡아 둔 시간만큼 그대로 기다린다(`test_assistant_http_api.py`의 SSE 테스트 세 건이
각각 11초인 이유이며, 그 gate는 `set()`이 아니라 timeout으로 풀린다). SSE 프레이밍·재개는 그쪽이
이미 보고 있고, 프레임은 여기서 읽는 이력과 같은 `SequencedEvent`를 같은 변환기로 옮긴 것이다.
그래서 이 파일은 adapter 경로만 본다.

비밀은 `_API_KEY` 하나만 쓴다. 왕복이 끝난 뒤 그 문자열이 어떤 응답 본문에도 없음을 같이
단언한다 — 실제 adapter는 키를 들고 SDK를 부르므로 새는 경로가 가짜보다 길다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, TypeAlias

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip(
    "openai",
    reason="공급자 SDK는 optional extra `llm`이다. 미설치 환경에서는 이 모듈을 건너뛴다.",
)

import httpx2  # noqa: E402  # reason: SDK가 끌고 오는 전송 계층이라 importorskip 뒤에야 있다
import openai  # noqa: E402  # reason: 위와 같음

from strategy_workbench.adapters.outbound.llm_openai.facade.provider import (  # noqa: E402  # reason: importorskip 이후 import
    DEFAULT_MODEL,
    OpenAiLlmAdapter,
    OpenAiResponsesClient,
)
from strategy_workbench.bootstrap.facade.container import (  # noqa: E402  # reason: importorskip 이후 import
    PROVIDER_ADAPTER_FACTORIES,
    AssistantSettings,
)
from strategy_workbench.bootstrap.facade.http import (  # noqa: E402  # reason: importorskip 이후 import
    build_http_app,
)
from strategy_workbench.domain.assistant.facade.models import (  # noqa: E402  # reason: importorskip 이후 import
    ProviderKind,
    TurnStatus,
)

from ..openai_stream_script import (  # noqa: E402  # reason: importorskip 이후 import
    final_event,
    response_of,
    text_delta,
)

# 헬퍼가 받는 HTTP 클라이언트. 두 종류가 섞이는 이유는 `test_assistant_http_api.py`와 같다 —
# starlette 1.6의 `TestClient`는 설치된 전송 계층에 따라 `httpx` 또는 `httpx2`를 상속하고, 둘은
# 서로의 하위 타입이 아니다. 한쪽으로만 적으면 extra 설치 여부에 따라 타입 검사가 갈린다.
AssistantClient: TypeAlias = TestClient | httpx.Client

_API_KEY = "sk-proj-secret-workbench-ABCD1234"
_ASSISTANT = "/api/v1/assistant"
_DOCUMENT_REF = {"draft_id": "draft-1", "strategy_id": None, "revision": None}
_CONTEXT = {"source_text": "schema_version: '1.1'\n", "source_format": "yaml", "diagnostics": []}
_SETTLE_TIMEOUT_SECONDS = 5.0


class _ScriptedStream:
    """SDK가 흘렸을 이벤트 열. adapter가 `with ... as stream`으로 받는 자리다."""

    def __init__(
        self, events: Sequence[Any]
    ) -> None:  # reason: SDK 스트림 이벤트 union을 그대로 받는다
        self._events = tuple(events)

    def __iter__(self) -> Iterator[Any]:  # reason: 위와 같음
        yield from self._events

    def __enter__(self) -> _ScriptedStream:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


class _ScriptedResponsesClient:
    """`OpenAiResponsesClient` 자리의 SDK 경계 가짜. 실제 adapter가 이것을 부른다."""

    def __init__(
        self,
        *,
        events: Sequence[Any] = (),  # reason: 위와 같음
        stream_error: Exception | None = None,
    ) -> None:
        self._events = tuple(events)
        self._stream_error = stream_error

    def stream_response(
        self, **_: Any
    ) -> _ScriptedStream:  # reason: 인자를 보지 않는 가짜라 전부 받는다
        if self._stream_error is not None:
            raise self._stream_error
        return _ScriptedStream(self._events)

    def create(  # reason: 반환은 SDK `Response`이고 이 파일은 그 타입을 쓰지 않는다
        self, *, input: str, max_output_tokens: int, model: str, store: bool
    ) -> Any:
        return response_of(model=model)


class _RecordingFactory:
    """호출마다 받은 비밀·base_url을 기록한다. 배선이 키를 실제로 전달하는지 여기서 본다."""

    def __init__(self, client: _ScriptedResponsesClient) -> None:
        self._client = client
        self.seen: list[tuple[str, str | None]] = []

    def __call__(self, secret: str, base_url: str | None) -> OpenAiResponsesClient:
        self.seen.append((secret, base_url))
        return self._client


def _client(tmp_path: Path, factory: _RecordingFactory) -> TestClient:
    """실제 컨테이너로 앱을 세우고 공급자 자리에 진짜 adapter를 꽂는다."""
    adapter = OpenAiLlmAdapter(client_factory=factory)
    app = build_http_app(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={ProviderKind.OPENAI: lambda: adapter},
        )
    )
    return TestClient(app)


def _create_profile(client: AssistantClient) -> dict[str, Any]:
    response = client.post(
        f"{_ASSISTANT}/providers",
        json={"kind": "openai", "label": "내 Codex", "secret": _API_KEY},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _start_session(client: AssistantClient) -> str:
    response = client.post(f"{_ASSISTANT}/sessions", json={"document_ref": _DOCUMENT_REF})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _settled_history(client: AssistantClient, session_id: str) -> dict[str, Any]:
    """턴이 종료 상태가 될 때까지 이력을 다시 읽는다.

    턴은 러너의 스레드에서 돌기 때문에 202 응답 시점에는 아직 이벤트가 없다. 고정 시간
    `sleep`으로 기다리면 느린 기계에서 깨지므로 종료 상태를 조건으로 건다.
    """
    deadline = time.monotonic() + _SETTLE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        history = client.get(f"{_ASSISTANT}/sessions/{session_id}").json()
        turns = history["turns"]
        if turns and turns[0]["status"] != TurnStatus.RUNNING.value:
            return history
        time.sleep(0.01)
    raise AssertionError(
        f"턴이 끝나지 않았다 — session_id={session_id} timeout={_SETTLE_TIMEOUT_SECONDS}s"
    )


def test_the_openai_adapter_is_registered_in_the_default_bootstrap_registry() -> None:
    """레지스트리가 비어 있으면 아래 왕복 테스트가 가짜만 검증하게 된다."""
    factory = PROVIDER_ADAPTER_FACTORIES[ProviderKind.OPENAI]

    provider = factory()

    assert isinstance(provider, OpenAiLlmAdapter)
    assert provider.kind is ProviderKind.OPENAI
    assert provider.default_model() == DEFAULT_MODEL


def test_a_turn_runs_end_to_end_through_the_real_openai_adapter(tmp_path: Path) -> None:
    sdk = _ScriptedResponsesClient(
        events=[
            text_delta("모멘텀"),
            text_delta(" 전략"),
            final_event(response_of(input_tokens=120, output_tokens=42)),
        ]
    )
    factory = _RecordingFactory(sdk)
    client = _client(tmp_path, factory)

    profile = _create_profile(client)
    session_id = _start_session(client)
    accepted = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "모멘텀 전략을 제안해 줘", "context": _CONTEXT},
    )

    # "설치 필요"(422 `assistant.provider_not_installed`)가 아니라 probe를 거쳐 201이 나와야
    # 레지스트리 배선이 살아 있는 것이다.
    assert profile["model"] == DEFAULT_MODEL
    assert profile["secret_tail"] == _API_KEY[-4:]
    kinds = {item["kind"]: item for item in client.get(f"{_ASSISTANT}/providers").json()["kinds"]}
    assert kinds["openai"]["installed"] is True
    assert accepted.status_code == 202, accepted.text
    history = _settled_history(client, session_id)
    events = [item["event"] for item in history["events"]]
    assert [event["type"] for event in events] == ["text_delta", "text_delta", "usage", "done"]
    assert [event["text"] for event in events[:2]] == ["모멘텀", " 전략"]
    assert events[2] == {"type": "usage", "input_tokens": 120, "output_tokens": 42}
    assert [item["sequence"] for item in history["events"]] == [0, 1, 2, 3]
    assert [turn["status"] for turn in history["turns"]] == [TurnStatus.COMPLETED.value]
    assert [message["role"] for message in history["messages"]] == ["user", "assistant"]
    # probe 한 번, 턴 한 번. 두 호출 모두 비밀을 받고 어느 쪽도 보관하지 않는다.
    assert factory.seen == [(_API_KEY, None), (_API_KEY, None)]
    assert _API_KEY not in json.dumps(history, ensure_ascii=False)


def test_a_provider_auth_error_reaches_the_client_as_a_failure_event_without_the_key(
    tmp_path: Path,
) -> None:
    """공급자 인증 오류 본문은 키 조각을 담는다. adapter의 매핑이 HTTP 끝까지 유지되는지 본다."""
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    sdk = _ScriptedResponsesClient(
        stream_error=openai.AuthenticationError(
            f"Incorrect API key provided: {_API_KEY}",
            response=httpx2.Response(401, request=request),
            body=None,
        )
    )
    client = _client(tmp_path, _RecordingFactory(sdk))
    _create_profile(client)
    session_id = _start_session(client)

    accepted = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "모멘텀 전략을 제안해 줘", "context": _CONTEXT},
    )

    assert accepted.status_code == 202, accepted.text
    history = _settled_history(client, session_id)
    failures = [item["event"] for item in history["events"] if item["event"]["type"] == "failure"]
    assert [failure["code"] for failure in failures] == ["auth"]
    assert [turn["status"] for turn in history["turns"]] == [TurnStatus.FAILED.value]
    body = json.dumps(history, ensure_ascii=False)
    assert _API_KEY not in body
    assert "Incorrect API key provided" not in body
