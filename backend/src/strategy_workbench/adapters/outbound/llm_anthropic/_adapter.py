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

# 연결 테스트 한 번의 출력 상한. 키·모델·네트워크가 살아 있는지만 보면 되므로 가장 작은 값에
# 가깝게 잡는다. 0으로 두면 모델에 따라 400이 나 "키가 틀렸다"와 구분이 안 된다.
PROBE_MAX_TOKENS = 16
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
