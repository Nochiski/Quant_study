"""어시스턴트 유스케이스 테스트용 가짜 구현.

공급자 SDK 없이도 계약이 성립하는지 확인하는 것이 Phase A 앞단의 목표이므로, 여기 가짜들이
유일한 "바깥"이다. `ScriptedProvider`는 미리 적어 둔 이벤트 열을 흘리고 `ToolStep`을 만나면
application이 넘긴 `execute_tool` 콜백을 실제로 호출한다.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from strategy_workbench.application.assistant_chat.facade.ports import (
    ProviderProfileNotFoundError,
    ProviderSecretMissingError,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ProbeResult,
    ProviderKind,
    ProviderProfile,
    ToolCall,
    ToolResult,
    TurnRequest,
)


class InMemoryProviderProfileRepository:
    """`ProviderProfileRepository` 인메모리 구현. 활성 프로파일은 언제나 최대 한 개다."""

    def __init__(self) -> None:
        self._profiles: list[ProviderProfile] = []

    def list(self) -> tuple[ProviderProfile, ...]:
        return tuple(self._profiles)

    def get(self, profile_id: str) -> ProviderProfile:
        for profile in self._profiles:
            if profile.profile_id == profile_id:
                return profile
        raise ProviderProfileNotFoundError(profile_id)

    def add(self, profile: ProviderProfile) -> None:
        self._profiles.append(profile)

    def delete(self, profile_id: str) -> None:
        self.get(profile_id)
        self._profiles = [item for item in self._profiles if item.profile_id != profile_id]

    def set_active(self, profile_id: str) -> None:
        self.get(profile_id)
        self._profiles = [
            dataclasses.replace(item, active=item.profile_id == profile_id)
            for item in self._profiles
        ]


class InMemoryProviderSecretStore:
    """`ProviderSecretStore` 인메모리 구현. `delete`는 멱등이다."""

    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def get(self, profile_id: str) -> str:
        try:
            return self.secrets[profile_id]
        except KeyError as error:
            raise ProviderSecretMissingError(profile_id) from error

    def put(self, profile_id: str, secret: str) -> None:
        self.secrets[profile_id] = secret

    def delete(self, profile_id: str) -> None:
        self.secrets.pop(profile_id, None)


@dataclass(frozen=True)
class ToolStep:
    """공급자가 도구를 부르는 지점. `execute_tool` 콜백을 실제로 호출한다."""

    call: ToolCall


@dataclass(frozen=True)
class RaiseStep:
    """공급자 쪽 예외(SDK 오류·네트워크 끊김)를 흉내 내는 지점."""

    error: Exception


ScriptStep = ChatEvent | ToolStep | RaiseStep


class ScriptedProvider:
    """`LlmProviderPort` 가짜 구현. 스크립트를 순서대로 흘린다."""

    def __init__(
        self,
        *,
        script: Sequence[ScriptStep] = (),
        probe_result: ProbeResult | None = None,
        kind: ProviderKind = ProviderKind.ANTHROPIC,
        model: str = "fake-model-1",
    ) -> None:
        self.kind = kind
        self._script = tuple(script)
        self._probe_result = probe_result or ProbeResult(ok=True, message="연결 확인", latency_ms=7)
        self._model = model
        self.tool_results: list[ToolResult] = []
        self.requests: list[TurnRequest] = []
        self.probe_calls: list[tuple[str, str, str | None]] = []

    def default_model(self) -> str:
        return self._model

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        self.probe_calls.append((secret, model, base_url))
        return self._probe_result

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        self.requests.append(request)
        for step in self._script:
            if isinstance(step, RaiseStep):
                raise step.error
            if isinstance(step, ToolStep):
                yield step.call
                self.tool_results.append(execute_tool(step.call))
                continue
            yield step


def sequential_ids(prefix: str) -> Callable[[], str]:
    """결정적인 id 생성기. 테스트가 id를 미리 알 수 있게 한다."""
    counter = iter(range(1, 1000))
    return lambda: f"{prefix}-{next(counter)}"
