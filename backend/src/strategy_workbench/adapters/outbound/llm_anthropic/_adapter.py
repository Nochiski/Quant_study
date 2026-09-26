"""`LlmProviderPort`의 Anthropic 구현 (설계 spec D4).

어댑터 자체는 얇다. 도구 루프는 `_turn.py`, 요청 조립은 `_payload.py`, 예외 매핑은
`_failures.py`가 갖는다. 여기 있는 것은 포트 계약과 그 세 모듈을 잇는 배선, 그리고 연결
테스트(`probe`)뿐이다.

비밀은 보관하지 않는다. `stream_turn`·`probe`가 호출마다 받은 `secret`으로 클라이언트를 만들고
호출이 끝나면 버린다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator

from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ProbeResult,
    ProviderKind,
    ProviderProfile,
    ToolCall,
    ToolResult,
    TurnRequest,
)

from ._client import AnthropicClientFactory, sdk_client_factory
from ._failures import probe_failure_for
from ._payload import DEFAULT_MODEL
from ._turn import stream_turn

__all__ = ["PROBE_MAX_TOKENS", "AnthropicLlmAdapter"]

logger = logging.getLogger(__name__)

# 연결 테스트 한 번의 출력 상한.
#
# 작을수록 싸지만 너무 작으면 위험하다. `claude-opus-5`는 thinking이 기본으로 켜져 있어, 상한이
# 모자라면 400이 날 수 있다. 그러면 `BadRequestError` → `ProbeFailure.UNKNOWN`으로 떨어져
# **올바른 키가 틀린 키처럼 보인다**. 응답이 잘려 끝나는 것(`stop_reason == "max_tokens"`)은
# 문제가 아니다 — probe는 요청이 받아들여졌는지만 본다.
#
# `thinking: {"type": "disabled"}`로 막지 않는 이유는 모델이 사용자 설정값이기 때문이다. Opus 5는
# effort `high` 이하에서만 disabled를 받고, Fable 5.1 계열은 어떤 effort에서도 400이다. 즉
# disabled는 멀쩡한 설정을 실패로 만들 수 있다. 넉넉한 상한이 공급자 모델에 중립적이다.
# 실측 확인은 A-07 live smoke 몫이다.
PROBE_MAX_TOKENS = 64
_PROBE_PROMPT = "ping"


class AnthropicLlmAdapter:
    """Anthropic(Claude) 공급자. `LlmProviderPort`를 구현한다."""

    kind = ProviderKind.ANTHROPIC

    def __init__(
        self,
        *,
        client_factory: AnthropicClientFactory | None = None,
        default_model: str = DEFAULT_MODEL,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_factory = client_factory or sdk_client_factory()
        self._default_model = default_model
        self._monotonic = monotonic

    def default_model(self) -> str:
        return self._default_model

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        """최소 토큰 요청 한 번으로 키·모델·네트워크를 확인한다.

        실패 사유만 고르고 문장은 고르지 않는다. `ProbeResult.message`는 사유에서 유도되며,
        SDK 예외 본문에는 키 조각이 섞여 있을 수 있어 여기 들어올 자리를 두지 않았다(spec D2).
        """
        client = self._client_factory(secret, base_url)
        started = self._monotonic()
        try:
            client.create(
                max_tokens=PROBE_MAX_TOKENS,
                messages=[{"role": "user", "content": _PROBE_PROMPT}],
                model=model,
            )
        except Exception as error:
            failure = probe_failure_for(error)
            logger.warning(
                "anthropic probe failed — model=%s has_base_url=%s failure=%s error_type=%s",
                model,
                base_url is not None,
                failure.value,
                type(error).__name__,
            )
            return ProbeResult(ok=False, failure=failure)
        return ProbeResult(ok=True, latency_ms=self._elapsed_ms(started))

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        client = self._client_factory(secret, profile.base_url)
        return stream_turn(client, request, execute_tool, cancelled, model=profile.model)

    def _elapsed_ms(self, started: float) -> int:
        return max(0, round((self._monotonic() - started) * 1000))
