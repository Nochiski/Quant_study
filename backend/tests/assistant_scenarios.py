"""가짜 공급자 시나리오 3개와 그 SSE 프레임 골든 (WORKFLOW A-07).

## 이 파일이 만드는 것

B-05 e2e의 MSW가 그대로 재생할 **SSE 프레임 배열**이다. 형식은 A-04가 정한
`AssistantEventEnvelopeView`(`sequence`·`turn_id`·payload `type`)이며, 프레임을 손으로 적지 않고
**실제 HTTP 응답에서 받아 적는다** — `GET /sessions/{id}`의 `events`가 SSE `data:` payload와 같은
dataclass를 싣기 때문이다. 손으로 적으면 계약이 바뀌었을 때 fixture만 옛 모양으로 남고, 그
fixture로 통과한 frontend가 진짜 서버에서 깨진다.

## 왜 턴 id를 바꿔 적는가

bootstrap은 턴·세션 id를 `uuid4`로 만든다. 그대로 두면 재생성할 때마다 골든이 통째로 바뀌어
diff가 쓸모없어진다. 그래서 프레임에 처음 나타난 순서대로 `turn-1`, `turn-2`로 바꾼다. MSW가
재생할 때도 이 고정된 id를 쓰면 되고, 프론트는 턴 id를 불투명한 문자열로만 다룬다.

## 갱신 절차

시나리오나 이벤트 계약을 고치면 `backend/`에서 아래를 실행하고 diff를 읽는다.

    uv run python tools/export_assistant_scenarios.py

빠뜨리면 `tests/application/test_assistant_scenarios.py`가 재생성 명령을 띄우며 실패한다.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.container import AssistantSettings
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.assistant.facade.models import (
    Done,
    ProviderKind,
    SearchActivity,
    Source,
    TextDelta,
    ToolCall,
    Usage,
)
from strategy_workbench.domain.assistant.facade.tools import (
    PROPOSE_STRATEGY,
    READ_CURRENT_STRATEGY,
)

from .application._assistant_fakes import ScriptedProvider, ScriptStep, ToolStep

__all__ = ["SCENARIOS", "SCENARIO_DIR", "REGENERATE_COMMAND", "Scenario", "build_scenario_frames"]

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = _BACKEND_ROOT / "tests" / "fixtures" / "assistant" / "scenarios"
REGENERATE_COMMAND = "uv run python tools/export_assistant_scenarios.py"

_ASSISTANT = "/api/v1/assistant"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_POLL_TIMEOUT_SECONDS = 10.0

# 제안이 통과하는 원문과 실패하는 원문. 실제 compile을 거치므로 통과본은 진짜 문서여야 한다.
_CURRENT_DOCUMENT = (
    _BACKEND_ROOT / "tests" / "fixtures" / "strategy_documents" / "quality_momentum.yaml"
).read_text(encoding="utf-8")
_BROKEN_DOCUMENT = "schema_version: '1.1'\ntitle: 깨진 제안\n"


@dataclass(frozen=True)
class Scenario:
    """가짜 공급자 대본 하나. `user_text`는 프론트가 재생할 때 보여 줄 질문이다."""

    user_text: str
    script: tuple[ScriptStep, ...]


def _propose(source_text: str, call_id: str) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name=PROPOSE_STRATEGY,
        arguments={
            "title": "KRX 12-1 모멘텀",
            "summary": "12개월 모멘텀에서 최근 1개월을 뺀 순위로 상위 종목을 담는다.",
            "rationale": (
                "모멘텀 프리미엄은 KRX에서도 관측된다 (https://example.com/krx-momentum)."
            ),
            "sources": [{"title": "KRX 모멘텀 리뷰", "url": "https://example.com/krx-momentum"}],
            "source_text": source_text,
        },
    )


SCENARIOS: Mapping[str, Scenario] = {
    # 도구도 검색도 없이 답변만. 사이드바의 가장 흔한 턴이고, 제안 카드가 없는 화면을 만든다.
    "simple_answer": Scenario(
        user_text="모멘텀 전략이 뭔지 간단히 설명해 줘",
        script=(
            TextDelta(text="모멘텀 전략은 "),
            TextDelta(text="최근 많이 오른 종목을 사는 전략입니다."),
            Usage(input_tokens=1840, output_tokens=120),
            Done(stop_reason="end_turn"),
        ),
    ),
    # 도구 한 번 뒤 제안 성공. 제안 카드와 "문서에 적용" 경로를 태우는 대본이다.
    "tool_then_proposal": Scenario(
        user_text="지금 문서를 읽고 더 나은 전략을 제안해 줘",
        script=(
            ToolStep(ToolCall(call_id="call-read", name=READ_CURRENT_STRATEGY, arguments={})),
            Usage(input_tokens=2100, output_tokens=180),
            ToolStep(_propose(_CURRENT_DOCUMENT, "call-propose")),
            TextDelta(text="퀄리티 필터를 더한 안을 올렸습니다."),
            Usage(input_tokens=3400, output_tokens=920),
            Done(stop_reason="end_turn"),
        ),
    ),
    # 검색 뒤 제안이 3회 연속 검증에 실패해 턴이 끝난다. 검색 활동 칩과 실패 표시를 같이 태운다.
    "search_then_failure": Scenario(
        user_text="최근 자료를 찾아보고 전략을 제안해 줘",
        script=(
            SearchActivity(
                query="KRX 모멘텀 팩터 2026",
                sources=(
                    Source(title="KRX 모멘텀 리뷰", url="https://example.com/krx-momentum"),
                    Source(title="팩터 성과 보고", url="https://example.com/factor-review"),
                ),
            ),
            Usage(input_tokens=2600, output_tokens=240),
            ToolStep(_propose(_BROKEN_DOCUMENT, "call-propose-1")),
            ToolStep(_propose(_BROKEN_DOCUMENT, "call-propose-2")),
            ToolStep(_propose(_BROKEN_DOCUMENT, "call-propose-3")),
            Done(stop_reason="end_turn"),
        ),
    ),
}


def _client(workspace: Path, script: Sequence[ScriptStep]) -> TestClient:
    """실제 bootstrap 그래프. 가짜로 바꾸는 것은 공급자 adapter 하나뿐이다."""
    provider = ScriptedProvider(script=script, kind=ProviderKind.ANTHROPIC)
    app = build_http_app(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=workspace / "secrets.json",
            provider_factories={ProviderKind.ANTHROPIC: lambda: provider},
        )
    )
    return TestClient(app)


def _run_scenario(scenario: Scenario, workspace: Path) -> list[dict[str, Any]]:
    client = _client(workspace, scenario.script)
    created = client.post(
        f"{_ASSISTANT}/providers",
        json={"kind": "anthropic", "label": "시나리오", "secret": "sk-scenario-fixture"},
    )
    if created.status_code != 201:
        raise RuntimeError(f"provider profile rejected — status={created.status_code}")
    session = client.post(
        f"{_ASSISTANT}/sessions",
        json={"document_ref": {"strategy_id": None, "revision": None, "draft_id": "scenario"}},
    )
    session_id = session.json()["session_id"]
    started = client.post(
        f"{_ASSISTANT}/sessions/{session_id}/turns",
        json={
            "text": scenario.user_text,
            "context": {
                "source_text": _CURRENT_DOCUMENT,
                "source_format": "yaml",
                "diagnostics": [],
            },
        },
    )
    if started.status_code != 202:
        raise RuntimeError(f"turn rejected — status={started.status_code}")
    return _normalised_frames(_await_history(client, session_id)["events"], session_id)


def _await_history(client: TestClient, session_id: str) -> dict[str, Any]:
    """턴이 종료 상태가 될 때까지 이력을 다시 읽는다. 턴은 진짜 스레드에서 돈다."""
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        history = client.get(f"{_ASSISTANT}/sessions/{session_id}").json()
        turns = history["turns"]
        if turns and all(turn["status"] in _TERMINAL_STATUSES for turn in turns):
            return history
        time.sleep(0.02)
    raise RuntimeError(
        f"scenario turn never settled — session_id={session_id} "
        f"timeout_seconds={_POLL_TIMEOUT_SECONDS}"
    )


def _normalised_frames(frames: Sequence[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    """`uuid4` 세션·턴 id를 고정 별칭으로 바꾼다. 그 밖의 값은 손대지 않는다.

    필드만 바꾸지 않고 직렬화한 텍스트 전체에서 치환하는 이유는 id가 **문장 안에도** 들어가기
    때문이다. `Failure.message`는 진단에 쓰라고 `session_id=…`를 담는다(error-messages 규칙).
    그 한 곳을 놓치면 골든이 실행마다 바뀌어 diff가 쓸모없어진다.
    """
    aliases = {
        turn_id: f"turn-{index}"
        for index, turn_id in enumerate(dict.fromkeys(frame["turn_id"] for frame in frames), 1)
    }
    text = json.dumps(list(frames), ensure_ascii=False)
    text = text.replace(session_id, "session-1")
    for turn_id, alias in aliases.items():
        text = text.replace(turn_id, alias)
    normalised = json.loads(text)
    if not isinstance(normalised, list):
        raise RuntimeError(f"scenario frames must stay a list — got {type(normalised).__name__}")
    return normalised


def build_scenario_frames() -> Mapping[str, str]:
    """파일 이름 → 내용. 재생성 스크립트와 골든 테스트가 같은 함수를 쓴다."""
    documents: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="assistant-scenarios-") as directory:
        workspace = Path(directory)
        for name, scenario in SCENARIOS.items():
            frames = _run_scenario(scenario, workspace)
            documents[f"{name}.json"] = (
                json.dumps(frames, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
            )
    return documents
