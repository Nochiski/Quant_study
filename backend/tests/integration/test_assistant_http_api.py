"""`/api/v1/assistant/*` 계약 (설계 spec D6, WORKFLOW A-04).

앱은 **실제 bootstrap 그래프**로 세운다(`build_http_app`). 가짜로 바꾸는 것은 공급자 adapter
하나뿐이고, 저장소·비밀 파일·컴파일러는 전부 진짜다. 그래야 라우트가 아니라 배선이 틀렸을
때도 이 테스트가 깨진다.

마지막 두 건(A-05)은 가짜를 한 겹 더 벗긴다. 진짜 `AnthropicLlmAdapter`를 쓰고 SDK
클라이언트만 대본으로 바꿔, bootstrap 레지스트리에 등록된 그 클래스가 HTTP 왕복에서
"설치 필요"가 아니라 probe 경로로 가는지 본다.

비밀은 이 파일 전체에서 `_API_KEY` 하나만 쓴다. 마지막 테스트가 그 문자열이 어떤 응답 본문과
로그에도 없음을 단언하므로, 새 라우트를 추가하면서 키를 응답에 흘리면 여기서 걸린다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from strategy_workbench.application.assistant_chat.facade.turns import AssistantTurnRunner
from strategy_workbench.bootstrap.facade.container import (
    PROVIDER_ADAPTER_FACTORIES,
    AssistantSettings,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    Failure,
    FailureCode,
    ProbeFailure,
    ProbeResult,
    ProviderKind,
    ProviderProfile,
    SequencedEvent,
    TextDelta,
    ToolCall,
    ToolResult,
    Turn,
    TurnRequest,
    TurnStatus,
)

_API_KEY = "sk-secret-workbench-ABCD1234"
_ASSISTANT = "/api/v1/assistant"
_DOCUMENT_REF = {"draft_id": "draft-1", "strategy_id": None, "revision": None}
_CONTEXT = {"source_text": "schema_version: '1.1'\n", "source_format": "yaml", "diagnostics": []}


class _GatedProvider:
    """대본대로 이벤트를 흘리되, 중간에서 테스트가 열어 줄 때까지 멈추는 가짜 공급자.

    턴이 RUNNING으로 남아 있는 구간을 결정적으로 만들기 위해 필요하다. 진짜 스레드에서 도는
    턴을 `time.sleep`으로 따라잡으려 하면 느리고 잘 깨진다.
    """

    kind = ProviderKind.ANTHROPIC

    def __init__(
        self,
        *,
        before: Sequence[ChatEvent] = (),
        after: Sequence[ChatEvent] = (),
        gate: threading.Event | None = None,
        probe_result: ProbeResult | None = None,
    ) -> None:
        self._before = tuple(before)
        self._after = tuple(after)
        self._gate = gate
        self._probe_result = probe_result or ProbeResult(ok=True, latency_ms=3)
        self.probed_secrets: list[str] = []
        self.streamed_secrets: list[str] = []
        self.reached_gate = threading.Event()

    def set_probe_result(self, result: ProbeResult) -> None:
        """다음 `probe`부터 돌려줄 결과. 한 클라이언트 안에서 실패와 성공을 다 거둘 때 쓴다."""
        self._probe_result = result

    def release(self) -> None:
        """gate에 잡힌 턴 스레드를 풀어 준다. 멱등이다.

        teardown이 이걸 부르지 않으면, 단언이 먼저 실패한 테스트에서 공급자 스레드가 gate
        타임아웃(10초)까지 붙잡혀 있어 서버 종료가 그만큼 늦어진다. 실패한 테스트일수록
        빨리 끝나야 다음 테스트의 신호가 읽힌다.
        """
        if self._gate is not None:
            self._gate.set()

    def default_model(self) -> str:
        return "fake-model-1"

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        self.probed_secrets.append(secret)
        return self._probe_result

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        self.streamed_secrets.append(secret)
        yield from self._before
        if self._gate is not None:
            self.reached_gate.set()
            self._gate.wait(timeout=10.0)
        if cancelled():
            yield Failure(code=FailureCode.CANCELLED, message="사용자가 턴을 취소했습니다")
            return
        yield from self._after


def _app(tmp_path: Path, provider: _GatedProvider | None) -> FastAPI:
    """실제 컨테이너로 앱을 세운다. 비밀 파일은 임시 디렉터리에 둔다."""
    factories = {} if provider is None else {ProviderKind.ANTHROPIC: lambda: provider}
    return build_http_app(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories=factories,
        )
    )


def _client(tmp_path: Path, provider: _GatedProvider | None) -> TestClient:
    return TestClient(_app(tmp_path, provider))


@contextmanager
def _live_client(tmp_path: Path, provider: _GatedProvider) -> Iterator[httpx.Client]:
    """진짜 서버에 붙은 클라이언트.

    **SSE를 검증하려면 `TestClient`로는 부족하다.** `TestClient`도 httpx의 `ASGITransport`도
    응답 본문을 끝까지 모은 뒤에야 돌려준다(`ASGITransport.handle_async_request`가 `body_parts`를
    쌓고 앱이 끝난 다음 `Response`를 만든다). 그래서 "진행 중인 턴의 프레임이 먼저 도착한다"는
    성질을 볼 수 없고, 스트림을 열어 둔 채 gate를 푸는 순서도 성립하지 않는다 — 열기가 끝나지
    않으므로 `set()`에 닿지 못하고 공급자 쪽 `wait` 타임아웃이 풀 때까지 멈춘다.
    """
    config = uvicorn.Config(_app(tmp_path, provider), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="assistant-test-server", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15.0
        while not server.started:
            if time.monotonic() > deadline:
                raise AssertionError("the test server did not start within 15s")
            time.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0) as client:
            yield client
    finally:
        # 공급자 스레드를 먼저 푼다. 단언이 실패해 gate가 닫힌 채로 빠져나오면 그 스레드가
        # 자기 타임아웃까지 서버를 붙잡는다.
        provider.release()
        server.should_exit = True
        thread.join(timeout=15.0)


def _create_profile(client: httpx.Client, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": "anthropic", "label": "내 Claude", "secret": _API_KEY}
    body.update(overrides)
    response = client.post(f"{_ASSISTANT}/providers", json=body)
    return {"status": response.status_code, "json": response.json()}


def _start_session(client: httpx.Client) -> str:
    response = client.post(f"{_ASSISTANT}/sessions", json={"document_ref": _DOCUMENT_REF})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _read_frames(lines: Iterator[str], *, until_id: int) -> list[dict[str, Any]]:
    """`id`가 `until_id`인 프레임까지 읽어 payload 목록으로 돌려준다."""
    frames: list[dict[str, Any]] = []
    current: int | None = None
    for line in lines:
        if line.startswith("id: "):
            current = int(line.removeprefix("id: "))
        elif line.startswith("data: "):
            frames.append(json.loads(line.removeprefix("data: ")))
            if current == until_id:
                return frames
    return frames


# -- 공급자 프로파일 --------------------------------------------------------------------------


def test_a_failed_probe_rejects_the_profile_with_only_an_enumerated_reason(
    tmp_path: Path,
) -> None:
    provider = _GatedProvider(
        probe_result=ProbeResult(ok=False, failure=ProbeFailure.AUTH, latency_ms=11)
    )
    client = _client(tmp_path, provider)

    created = _create_profile(client)

    assert created["status"] == 422
    detail = created["json"]["detail"]
    assert detail["code"] == "assistant.probe_failed"
    assert detail["failure"] == ProbeFailure.AUTH.value
    assert client.get(f"{_ASSISTANT}/providers").json()["profiles"] == []


def test_a_probed_profile_is_stored_and_shows_only_the_key_tail(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())

    created = _create_profile(client)

    assert created["status"] == 201, created["json"]
    assert created["json"]["secret_tail"] == _API_KEY[-4:]
    assert created["json"]["active"] is True
    assert created["json"]["model"] == "fake-model-1"
    listed = client.get(f"{_ASSISTANT}/providers").json()
    assert [item["secret_tail"] for item in listed["profiles"]] == [_API_KEY[-4:]]


def test_an_uninstalled_provider_kind_is_visible_but_unusable(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())

    created = _create_profile(client, kind="openai")

    assert created["status"] == 422
    assert created["json"]["detail"]["code"] == "assistant.provider_not_installed"
    kinds = {item["kind"]: item for item in client.get(f"{_ASSISTANT}/providers").json()["kinds"]}
    assert kinds["openai"]["installed"] is False
    assert kinds["anthropic"]["installed"] is True


def test_a_plain_http_base_url_is_rejected_before_the_provider_sees_the_key(
    tmp_path: Path,
) -> None:
    provider = _GatedProvider()
    client = _client(tmp_path, provider)

    created = _create_profile(client, base_url="http://proxy.example.com")

    assert created["status"] == 422
    assert created["json"]["detail"]["code"] == "assistant.base_url_rejected"
    assert provider.probed_secrets == []


def test_activating_moves_the_active_flag_and_deleting_drops_the_key(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())
    first = _create_profile(client)["json"]
    second = _create_profile(client, label="두 번째")["json"]

    activated = client.post(f"{_ASSISTANT}/providers/{second['profile_id']}/activate")

    assert activated.status_code == 200
    assert activated.json()["active"] is True
    assert client.delete(f"{_ASSISTANT}/providers/{second['profile_id']}").status_code == 204
    remaining = client.get(f"{_ASSISTANT}/providers").json()["profiles"]
    assert [item["profile_id"] for item in remaining] == [first["profile_id"]]
    assert remaining[0]["active"] is True


def test_testing_a_profile_returns_the_probe_result_as_a_value(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())
    profile = _create_profile(client)["json"]

    response = client.post(f"{_ASSISTANT}/providers/{profile['profile_id']}/test")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["latency_ms"] == 3


def test_an_unknown_profile_is_a_404(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())

    response = client.post(f"{_ASSISTANT}/providers/missing/activate")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "assistant.provider.not_found"


# -- 세션과 턴 --------------------------------------------------------------------------------


def test_a_session_needs_an_active_provider(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())

    response = client.post(f"{_ASSISTANT}/sessions", json={"document_ref": _DOCUMENT_REF})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "assistant.no_active_provider"


def test_a_document_ref_naming_both_a_strategy_and_a_draft_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())
    _create_profile(client)

    response = client.post(
        f"{_ASSISTANT}/sessions",
        json={"document_ref": {"draft_id": "draft-1", "strategy_id": "strategy-1"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "assistant.document_ref_invalid"


def test_a_turn_streams_its_events_in_order_and_the_stream_closes(tmp_path: Path) -> None:
    gate = threading.Event()
    provider = _GatedProvider(
        before=[TextDelta(text="먼저")],
        after=[TextDelta(text="나중"), Done(stop_reason="end_turn")],
        gate=gate,
    )
    with _live_client(tmp_path, provider) as client:
        _create_profile(client)
        session_id = _start_session(client)

        accepted = client.post(
            f"{_ASSISTANT}/sessions/{session_id}/turns",
            json={"text": "모멘텀 전략을 제안해 줘", "context": _CONTEXT},
        )

        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["accepted_sequence"] == -1
        assert accepted.json()["status"] == TurnStatus.RUNNING.value
        assert provider.reached_gate.wait(timeout=5.0)
        with client.stream(
            "GET", f"{_ASSISTANT}/sessions/{session_id}/events?after_sequence=-1"
        ) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            lines = stream.iter_lines()
            # 공급자는 아직 gate에 잡혀 있다 — 이 프레임은 **진행 중인 턴**에서 온 것이다.
            live = _read_frames(lines, until_id=0)
            assert [item["event"]["type"] for item in live] == ["text_delta"]
            running = client.get(f"{_ASSISTANT}/sessions/{session_id}").json()
            assert running["turns"][0]["status"] == TurnStatus.RUNNING.value
            gate.set()
            payloads = live + _read_frames(lines, until_id=2)
        assert [item["event"]["type"] for item in payloads] == [
            "text_delta",
            "text_delta",
            "done",
        ]
        assert [item["sequence"] for item in payloads] == [0, 1, 2]
        history = client.get(f"{_ASSISTANT}/sessions/{session_id}").json()
        assert [turn["status"] for turn in history["turns"]] == [TurnStatus.COMPLETED.value]
        assert [message["role"] for message in history["messages"]] == ["user", "assistant"]
        assert len(history["events"]) == 3


def test_the_stream_keeps_going_past_done_until_the_turn_settles(tmp_path: Path) -> None:
    """`Done`은 공급자 스트림이 끝났다는 표시일 뿐 턴 종료 판정이 아니다(spec D3).

    도구가 세운 종료 사유는 손에 든 이벤트를 내보낸 뒤에 적용되므로 `Done` 뒤에 `Failure`가
    온다. 스트림이 `Done`을 보고 닫으면 그 턴이 왜 실패했는지가 화면에 영영 안 나온다.
    """
    gate = threading.Event()
    provider = _GatedProvider(
        after=[
            Done(stop_reason="end_turn"),
            Failure(code=FailureCode.PROVIDER, message="공급자 호출이 실패했습니다"),
        ],
        gate=gate,
    )
    with _live_client(tmp_path, provider) as client:
        _create_profile(client)
        session_id = _start_session(client)
        client.post(
            f"{_ASSISTANT}/sessions/{session_id}/turns",
            json={"text": "Done 뒤에도 읽는다", "context": _CONTEXT},
        )
        assert provider.reached_gate.wait(timeout=5.0)

        with client.stream("GET", f"{_ASSISTANT}/sessions/{session_id}/events") as stream:
            lines = stream.iter_lines()
            gate.set()
            payloads = _read_frames(lines, until_id=1)

        assert [item["event"]["type"] for item in payloads] == ["done", "failure"]
        history = _wait_for_terminal_turn(client, session_id)
        assert history["turns"][0]["status"] == TurnStatus.FAILED.value


def test_the_stream_resumes_after_the_last_event_id_the_client_applied(tmp_path: Path) -> None:
    gate = threading.Event()
    provider = _GatedProvider(
        before=[TextDelta(text="먼저")],
        after=[TextDelta(text="나중"), Done(stop_reason="end_turn")],
        gate=gate,
    )
    with _live_client(tmp_path, provider) as client:
        _create_profile(client)
        session_id = _start_session(client)
        client.post(
            f"{_ASSISTANT}/sessions/{session_id}/turns",
            json={"text": "이어서", "context": _CONTEXT},
        )
        assert provider.reached_gate.wait(timeout=5.0)

        with client.stream(
            "GET",
            f"{_ASSISTANT}/sessions/{session_id}/events",
            headers={"Last-Event-ID": "0"},
        ) as stream:
            lines = stream.iter_lines()
            gate.set()
            payloads = _read_frames(lines, until_id=2)

    # sequence 0은 헤더가 가리킨 지점이라 다시 오지 않는다. 쿼리(`after_sequence` 기본 -1)보다
    # 헤더가 이긴다는 것이 이 단언의 핵심이다.
    assert [item["sequence"] for item in payloads] == [1, 2]


def test_a_second_turn_while_one_runs_is_refused_with_the_running_turn_id(
    tmp_path: Path,
) -> None:
    gate = threading.Event()
    provider = _GatedProvider(after=[Done(stop_reason="end_turn")], gate=gate)
    client = _client(tmp_path, provider)
    _create_profile(client)
    session_id = _start_session(client)
    first = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "첫 턴", "context": _CONTEXT},
    ).json()
    assert provider.reached_gate.wait(timeout=5.0)

    second = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "둘째 턴", "context": _CONTEXT},
    )

    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "assistant.turn_in_progress"
    assert second.json()["detail"]["turn_id"] == first["turn_id"]
    gate.set()


def test_opening_the_stream_without_a_running_turn_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())
    _create_profile(client)
    session_id = _start_session(client)

    response = client.get(f"{_ASSISTANT}/sessions/{session_id}/events")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assistant.no_running_turn"


def test_streaming_an_unknown_session_is_a_404(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())
    _create_profile(client)

    response = client.get(f"{_ASSISTANT}/sessions/missing/events")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "assistant.session.not_found"


def test_cancelling_a_running_turn_records_the_cancellation(tmp_path: Path) -> None:
    gate = threading.Event()
    provider = _GatedProvider(after=[Done(stop_reason="end_turn")], gate=gate)
    client = _client(tmp_path, provider)
    _create_profile(client)
    session_id = _start_session(client)
    turn = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "취소할 턴", "context": _CONTEXT},
    ).json()
    assert provider.reached_gate.wait(timeout=5.0)

    cancelled = client.post(f"{_ASSISTANT}/sessions/{session_id}/turns/{turn['turn_id']}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == TurnStatus.CANCELLED.value
    gate.set()
    history = _wait_for_terminal_turn(client, session_id)
    assert history["turns"][0]["status"] == TurnStatus.CANCELLED.value
    # 공급자도, 서비스도 취소를 알린다. 둘 다 이벤트로 남지만 턴 상태가 되는 것은 먼저
    # 확정된 하나뿐이다(spec D3 Failure 우선순위). 개수가 아니라 사유가 계약이다.
    codes = {item["event"]["code"] for item in history["events"]}
    assert [item["event"]["type"] for item in history["events"]] != []
    assert codes == {FailureCode.CANCELLED.value}


def test_cancelling_an_already_finished_turn_reports_its_final_state(tmp_path: Path) -> None:
    """저장소가 종료된 턴의 상태 변경을 거부하므로(A-03), 늦은 취소는 쓰지 않고 읽기만 한다.

    사용자가 취소를 누르는 순간과 턴이 끝나는 순간은 언제든 겹친다. 여기서 500이 나면 화면은
    "취소 실패"를 보여 주는데 정작 턴은 멀쩡히 끝나 있다.
    """
    client = _client(tmp_path, _GatedProvider(after=[Done(stop_reason="end_turn")]))
    _create_profile(client)
    session_id = _start_session(client)
    turn = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "곧 끝나는 턴", "context": _CONTEXT},
    ).json()
    _wait_for_terminal_turn(client, session_id)

    late = client.post(f"{_ASSISTANT}/sessions/{session_id}/turns/{turn['turn_id']}/cancel")

    assert late.status_code == 200
    assert late.json()["status"] == TurnStatus.COMPLETED.value
    assert client.get(f"{_ASSISTANT}/sessions/{session_id}/events").status_code == 409


def test_cancelling_a_turn_that_belongs_to_another_session_is_a_404(tmp_path: Path) -> None:
    gate = threading.Event()
    provider = _GatedProvider(after=[Done(stop_reason="end_turn")], gate=gate)
    client = _client(tmp_path, provider)
    _create_profile(client)
    session_id = _start_session(client)
    other_session_id = _start_session(client)
    turn = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "턴", "context": _CONTEXT},
    ).json()
    assert provider.reached_gate.wait(timeout=5.0)

    response = client.post(
        f"{_ASSISTANT}/sessions/{other_session_id}/turns/{turn['turn_id']}/cancel"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "assistant.turn.not_found"
    gate.set()


def test_the_history_reads_turns_before_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """네 조회는 한 트랜잭션이 아니다. 그 창의 오차 방향을 "바쁘다 쪽"으로 고정한다.

    이벤트를 먼저 읽으면 "턴은 FAILED인데 그 실패 이벤트는 목록에 없는" 응답이 나가고, spec D7의
    복구 규칙이 이 응답 하나만 보기 때문에 화면은 이유 없이 멈춘 턴을 그린다. 턴을 먼저 읽으면
    창의 방향이 "턴은 아직 RUNNING인데 이벤트가 더 와 있다"가 되고, 프론트 리듀서는 sequence
    기준 멱등이라 여분 이벤트를 그대로 흡수한다.

    턴 조회 도중에 이벤트를 하나 더 저장해 그 순서를 밖에서 관측한다.
    """
    client = _client(tmp_path, _GatedProvider(after=[Done(stop_reason="end_turn")]))
    _create_profile(client)
    session_id = _start_session(client)
    turn = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "이력 순서", "context": _CONTEXT},
    ).json()
    _wait_for_terminal_turn(client, session_id)
    reads: list[str] = []
    original_turns = AssistantTurnRunner.turns
    original_events = AssistantTurnRunner.events

    def spy_turns(runner: AssistantTurnRunner, session: str) -> tuple[Turn, ...]:
        reads.append("turns")
        result = original_turns(runner, session)
        # 두 조회 사이에 러너가 마지막 이벤트를 저장하는 상황을 재현한다.
        runner._sessions.append_events(  # pyright: ignore[reportPrivateUsage]  # reason: 두 조회 사이의 창을 밖에서 열 방법이 이것뿐이다
            turn["turn_id"],
            (Failure(code=FailureCode.PROVIDER, message="늦게 도착한 종료 사유"),),
        )
        return result

    def spy_events(
        runner: AssistantTurnRunner, session: str, *, after_sequence: int = -1
    ) -> tuple[SequencedEvent, ...]:
        reads.append("events")
        return original_events(runner, session, after_sequence=after_sequence)

    monkeypatch.setattr(AssistantTurnRunner, "turns", spy_turns)
    monkeypatch.setattr(AssistantTurnRunner, "events", spy_events)

    history = client.get(f"{_ASSISTANT}/sessions/{session_id}").json()

    assert reads == ["turns", "events"]
    assert [item["event"]["type"] for item in history["events"]][-1] == "failure"


def test_sessions_are_listed_for_one_document(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())
    _create_profile(client)
    session_id = _start_session(client)

    listed = client.get(f"{_ASSISTANT}/sessions", params={"draft_id": "draft-1"})

    assert [item["session_id"] for item in listed.json()] == [session_id]
    assert client.get(f"{_ASSISTANT}/sessions", params={"draft_id": "other"}).json() == []


# -- 비밀 누설 --------------------------------------------------------------------------------


def test_no_response_body_or_log_line_carries_the_api_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """키 문자열이 응답·로그 어디에도 없어야 한다(spec D1/D2).

    프로파일을 만들고 연결을 테스트하고 턴을 한 번 돌린 뒤, 그 사이 오간 모든 응답 본문과
    캡처된 로그를 한 덩어리로 검사한다. 새 라우트가 키를 흘리면 여기서 걸린다.
    """
    caplog.set_level(logging.DEBUG)
    provider = _GatedProvider(after=[Done(stop_reason="end_turn")])
    client = _client(tmp_path, provider)
    bodies: list[str] = []

    bodies.append(client.post(f"{_ASSISTANT}/providers", json=_profile_body()).text)
    profile_id = client.get(f"{_ASSISTANT}/providers").json()["profiles"][0]["profile_id"]
    bodies.append(client.get(f"{_ASSISTANT}/providers").text)
    bodies.append(client.post(f"{_ASSISTANT}/providers/{profile_id}/test").text)
    session_id = _start_session(client)
    bodies.append(
        client.post(
            f"{_ASSISTANT}/sessions/{session_id}/turns",
            json={"text": "키가 새는지 본다", "context": _CONTEXT},
        ).text
    )
    _wait_for_terminal_turn(client, session_id)
    bodies.append(client.get(f"{_ASSISTANT}/sessions/{session_id}").text)
    bodies.append(json.dumps(client.get("/openapi.json").json(), ensure_ascii=False))

    # 가짜 공급자는 키를 실제로 받았다 — 즉 "아무 데도 없다"가 "아무 데도 안 갔다"의 결과가 아니다.
    assert provider.probed_secrets == [_API_KEY, _API_KEY]
    assert provider.streamed_secrets == [_API_KEY]
    assert all(_API_KEY not in body for body in bodies)
    assert _API_KEY not in caplog.text


# spec D6이 적은 코드와 이 PR이 더한 두 개. 라우트가 코드를 새로 만들고 계약에 넣지 않으면
# 아래 테스트가 깨진다 — 생성 SDK의 판별 유니언은 코드로 갈라지므로, 계약에 없는 코드는
# 프론트에서 어느 갈래에도 맞지 않고 "알 수 없는 오류"로 떨어진다.
_DECLARED_ASSISTANT_CODES = frozenset(
    {
        "assistant.provider_not_installed",
        "assistant.no_active_provider",
        "assistant.probe_failed",
        "assistant.base_url_rejected",
        "assistant.provider_secret_missing",
        "assistant.document_ref_invalid",
        "assistant.turn_in_progress",
        "assistant.no_running_turn",
        "assistant.session.not_found",
        "assistant.turn.not_found",
        "assistant.provider.not_found",
    }
)


def _openapi_assistant_codes(client: httpx.Client) -> set[str]:
    """OpenAPI의 `Assistant*Detail` 스키마가 선언한 `code` 값 전부."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    declared: set[str] = set()
    for name, schema in schemas.items():
        if not (name.startswith("Assistant") and name.endswith("Detail")):
            continue
        code = schema.get("properties", {}).get("code", {})
        declared.update(code.get("enum", []) or [])
        if "const" in code:
            declared.add(code["const"])
    return declared


def test_the_openapi_contract_declares_every_assistant_error_code(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())

    assert _openapi_assistant_codes(client) == set(_DECLARED_ASSISTANT_CODES)


def test_every_code_the_routes_actually_emit_is_in_the_contract(tmp_path: Path) -> None:
    """실제 응답에서 코드를 거둬 계약과 대조한다. 선언만 맞추고 라우트가 딴 말을 하면 걸린다."""
    provider = _GatedProvider(
        probe_result=ProbeResult(ok=False, failure=ProbeFailure.AUTH, latency_ms=1)
    )
    client = _client(tmp_path, provider)
    emitted: set[str] = set()

    # 프로파일이 없는 상태에서 나오는 거절들.
    emitted.add(_code(client.post(f"{_ASSISTANT}/providers", json=_profile_body(kind="openai"))))
    emitted.add(_code(client.post(f"{_ASSISTANT}/providers", json=_profile_body())))
    emitted.add(
        _code(
            client.post(
                f"{_ASSISTANT}/providers", json=_profile_body(base_url="http://proxy.example.com")
            )
        )
    )
    emitted.add(_code(client.post(f"{_ASSISTANT}/providers/absent/activate")))
    emitted.add(_code(client.post(f"{_ASSISTANT}/sessions", json={"document_ref": _DOCUMENT_REF})))
    emitted.add(_code(client.get(f"{_ASSISTANT}/sessions")))
    # 프로파일을 만든 뒤에만 닿는 거절들.
    provider.set_probe_result(ProbeResult(ok=True, latency_ms=1))
    _create_profile(client)
    session_id = _start_session(client)
    emitted.add(_code(client.get(f"{_ASSISTANT}/sessions/{session_id}/events")))
    emitted.add(_code(client.get(f"{_ASSISTANT}/sessions/absent")))
    emitted.add(_code(client.post(f"{_ASSISTANT}/sessions/{session_id}/turns/absent/cancel")))

    assert emitted <= _openapi_assistant_codes(client)
    assert "assistant.document_ref_invalid" in emitted
    assert "assistant.probe_failed" in emitted


def test_the_event_stream_response_declares_its_frame_schema(tmp_path: Path) -> None:
    """SSE payload에 스키마가 없으면 생성 SDK가 `unknown`을 만들고, B-02가 손으로 캐스팅한다."""
    client = _client(tmp_path, _GatedProvider())

    document = client.get("/openapi.json").json()
    operation = document["paths"][f"{_ASSISTANT}/sessions/{{session_id}}/events"]["get"]
    content = operation["responses"]["200"]["content"]["text/event-stream"]

    assert content["schema"]["$ref"].endswith("/AssistantEventEnvelopeView")
    envelope = document["components"]["schemas"]["AssistantEventEnvelopeView"]
    assert set(envelope["required"]) >= {"sequence", "turn_id", "event"}


def test_the_openapi_contract_marks_the_provider_secret_write_only(tmp_path: Path) -> None:
    client = _client(tmp_path, _GatedProvider())

    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert schemas["CreateProviderProfileRequest"]["properties"]["secret"]["writeOnly"] is True
    assert "secret" not in schemas["ProviderProfileView"]["properties"]
    assert set(schemas["ProviderProfileView"]["required"]) >= {"profile_id", "secret_tail"}


def _profile_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": "anthropic", "label": "내 Claude", "secret": _API_KEY}
    body.update(overrides)
    return body


def _code(response: httpx.Response) -> str:
    """응답 본문의 `detail.code`. 코드 없는 본문이면 테스트가 여기서 멈춘다."""
    payload = response.json()
    detail = payload["detail"]
    if not isinstance(detail, dict) or "code" not in detail:
        raise AssertionError(f"response carries no coded detail — payload={payload!r}")
    return str(detail["code"])


def _wait_for_terminal_turn(client: httpx.Client, session_id: str) -> dict[str, Any]:
    """턴이 종료 상태로 기록될 때까지 이력을 다시 읽는다.

    턴은 진짜 스레드에서 돌고 종료 기록은 스트림이 끝난 뒤에 남는다. 고정된 `sleep`으로
    맞추면 느린 기계에서 깨지므로 짧은 폴링으로 기다린다.
    """
    deadline = datetime.now(UTC).timestamp() + 10.0
    while datetime.now(UTC).timestamp() < deadline:
        history = client.get(f"{_ASSISTANT}/sessions/{session_id}").json()
        statuses = {turn["status"] for turn in history["turns"]}
        if statuses and TurnStatus.RUNNING.value not in statuses:
            return history
    raise AssertionError(f"turn never reached a terminal state — session_id={session_id}")


# -- 등록된 진짜 adapter (A-05) ---------------------------------------------------------------


def test_the_anthropic_adapter_is_registered_in_the_default_bootstrap_registry() -> None:
    """레지스트리가 비어 있으면 아래 왕복 테스트가 가짜만 검증하게 된다."""
    pytest.importorskip("anthropic", reason="공급자 SDK는 optional extra `llm`이다")
    from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import (
        DEFAULT_MODEL,
        AnthropicLlmAdapter,
    )

    factory = PROVIDER_ADAPTER_FACTORIES[ProviderKind.ANTHROPIC]

    provider = factory()

    assert isinstance(provider, AnthropicLlmAdapter)
    assert provider.kind is ProviderKind.ANTHROPIC
    assert provider.default_model() == DEFAULT_MODEL


def test_creating_an_anthropic_profile_reaches_the_real_adapters_probe(tmp_path: Path) -> None:
    """등록된 adapter 클래스를 그대로 쓰고 SDK 클라이언트만 대본으로 바꾼다.

    "설치 필요"(422 `assistant.provider_not_installed`)가 아니라 probe를 거쳐 201이 나와야
    레지스트리 배선이 살아 있는 것이다. 네트워크는 타지 않는다.
    """
    pytest.importorskip("anthropic", reason="공급자 SDK는 optional extra `llm`이다")
    from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import (
        DEFAULT_MODEL,
        AnthropicLlmAdapter,
    )

    from ..anthropic_stream_script import RecordingClientFactory, ScriptedMessagesClient

    sdk = ScriptedMessagesClient()
    factory = RecordingClientFactory(sdk)
    app = build_http_app(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={
                ProviderKind.ANTHROPIC: lambda: AnthropicLlmAdapter(client_factory=factory)
            },
        )
    )
    client = TestClient(app)

    created = _create_profile(client)

    assert created["status"] == 201, created["json"]
    assert created["json"]["model"] == DEFAULT_MODEL
    assert created["json"]["secret_tail"] == _API_KEY[-4:]
    # probe가 실제로 SDK 경계를 한 번 두드렸고, 비밀은 그 호출에만 쓰였다.
    assert factory.seen == [(_API_KEY, None)]
    assert [model for _, model in sdk.create_payloads] == [DEFAULT_MODEL]
    kinds = {item["kind"]: item for item in client.get(f"{_ASSISTANT}/providers").json()["kinds"]}
    assert kinds["anthropic"]["installed"] is True
