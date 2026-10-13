"""결과 설명 세션의 HTTP 계약 (결과 설명 spec R3·R5·R7).

앱은 실제 bootstrap 그래프로 세우고 공급자만 대본으로 바꾼다. 백테스트도 진짜로 돌린다 — 결과를
읽는 포트는 bootstrap이 실행 레지스트리를 감싼 것이라, 가짜 결과로는 그 배선이 검사 밖으로 나간다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.container import AssistantSettings
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    ProbeResult,
    ProviderKind,
    ProviderProfile,
    TextDelta,
    ToolCall,
    ToolResult,
    TurnRequest,
)
from strategy_workbench.domain.assistant.facade.tools import (
    READ_BACKTEST_RESULT,
    RESULT_EXPLAIN_TOOLS,
)

from ..backtest_run_wait import wait_for_terminal_state

_ASSISTANT = "/api/v1/assistant"
_STRATEGY_CONTEXT = {"source_text": "schema_version: '1.2'\n", "source_format": "yaml"}


class _ResultReadingProvider:
    """결과 도구를 한 번 부르고 짧게 답하는 공급자. 받은 요청과 도구 결과를 남긴다."""

    kind = ProviderKind.ANTHROPIC

    def __init__(self) -> None:
        self.requests: list[TurnRequest] = []
        self.tool_results: list[ToolResult] = []

    def default_model(self) -> str:
        return "fake-model-1"

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        return ProbeResult(ok=True, latency_ms=1)

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        self.requests.append(request)
        call = ToolCall(call_id="call-read", name=READ_BACKTEST_RESULT, arguments={})
        yield call
        self.tool_results.append(execute_tool(call))
        yield TextDelta(text="좋은 편입니다.")
        yield Done(stop_reason="end_turn")


@pytest.fixture
def provider() -> _ResultReadingProvider:
    return _ResultReadingProvider()


@pytest.fixture
def client(tmp_path: Path, provider: _ResultReadingProvider) -> TestClient:
    app = build_http_app(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            provider_factories={ProviderKind.ANTHROPIC: lambda: provider},
        )
    )
    test_client = TestClient(app)
    created = test_client.post(
        f"{_ASSISTANT}/providers",
        json={"kind": "anthropic", "label": "Claude", "secret": "sk-result-0001"},
    )
    assert created.status_code == 201, created.text
    return test_client


def _completed_run(client: TestClient) -> str:
    spec = client.get("/api/v1/strategies/template").json()
    # schema 1.2 문서는 기간·유니버스를 담지 않는다(lang2 P2-03). 실행 설정은 요청 본문이 싣는다.
    environment = {"start": "2026-01-02", "end": "2026-02-20", "universe_id": "krx.common-stock"}
    accepted = client.post(
        "/api/v1/backtests",
        json={
            "strategy": spec,
            "environment": environment,
            "core": "python",
            "benchmark_security_id": "sec-005930-1",
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["run_id"]
    assert wait_for_terminal_state(client, run_id, timeout_s=60.0)["status"] == "completed"
    return run_id


def _settled_history(client: TestClient, session_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        history = client.get(f"{_ASSISTANT}/sessions/{session_id}").json()
        if history["turns"] and history["turns"][-1]["status"] != "running":
            return history
        time.sleep(0.02)
    raise AssertionError(f"assistant turn did not settle — session_id={session_id}")


def _code(response: Any) -> str:
    assert response.status_code == 422, response.text
    return str(response.json()["detail"]["code"])


def test_a_completed_run_is_explained_from_the_server_summary(
    client: TestClient, provider: _ResultReadingProvider
) -> None:
    run_id = _completed_run(client)

    created = client.post(f"{_ASSISTANT}/sessions", json={"document_ref": {"run_id": run_id}})
    assert created.status_code == 201, created.text
    session = created.json()
    assert session["document_ref"]["run_id"] == run_id
    listed = client.get(f"{_ASSISTANT}/sessions", params={"run_id": run_id}).json()
    assert [item["session_id"] for item in listed] == [session["session_id"]]

    started = client.post(
        f"{_ASSISTANT}/sessions/{session['session_id']}/turns", json={"text": "이 결과 좋은 거야?"}
    )
    assert started.status_code == 202, started.text
    history = _settled_history(client, session["session_id"])

    assert history["turns"][-1]["status"] == "completed"
    assert provider.requests[0].tools == RESULT_EXPLAIN_TOOLS
    assert provider.requests[0].research == frozenset()
    summary = json.loads(provider.tool_results[0].content)
    assert summary["run"]["run_id"] == run_id
    assert summary["capital"]["benchmark_security_id"] == "sec-005930-1"
    assert {metric["metric_id"] for metric in summary["metrics"]} >= {"total_return", "sharpe"}
    assert [message["text"] for message in history["messages"]] == [
        "이 결과 좋은 거야?",
        "좋은 편입니다.",
    ]


def test_a_session_for_an_unknown_run_is_refused(client: TestClient) -> None:
    response = client.post(f"{_ASSISTANT}/sessions", json={"document_ref": {"run_id": "run-gone"}})

    assert _code(response) == "assistant.result_unavailable"
    assert "run-gone" in response.json()["detail"]["message"]


def test_a_result_turn_carrying_document_context_is_refused(client: TestClient) -> None:
    run_id = _completed_run(client)
    session_id = client.post(
        f"{_ASSISTANT}/sessions", json={"document_ref": {"run_id": run_id}}
    ).json()["session_id"]

    response = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={"text": "좋아?", "context": _STRATEGY_CONTEXT},
    )

    assert _code(response) == "assistant.turn_context_mismatch"


def test_a_strategy_turn_without_document_context_is_refused(client: TestClient) -> None:
    session_id = client.post(
        f"{_ASSISTANT}/sessions", json={"document_ref": {"draft_id": "draft-1"}}
    ).json()["session_id"]

    response = client.post(f"{_ASSISTANT}/sessions/{session_id}/turns", json={"text": "만들어 줘"})

    assert _code(response) == "assistant.turn_context_mismatch"


def test_a_run_reference_that_also_names_a_draft_is_rejected(client: TestClient) -> None:
    response = client.post(
        f"{_ASSISTANT}/sessions",
        json={"document_ref": {"run_id": "run-1", "draft_id": "draft-1"}},
    )

    assert _code(response) == "assistant.document_ref_invalid"
