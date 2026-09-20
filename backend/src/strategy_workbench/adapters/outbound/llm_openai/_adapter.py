"""`LlmProviderPort`의 OpenAI 구현 (설계 spec D4).

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

from ._client import OpenAiClientFactory, sdk_client_factory
from ._failures import probe_failure_for
from ._payload import DEFAULT_MODEL
from ._turn import stream_turn

__all__ = ["PROBE_MAX_OUTPUT_TOKENS", "OpenAiLlmAdapter"]

logger = logging.getLogger(__name__)

# 연결 테스트 한 번의 출력 상한.
#
# API가 받는 최솟값은 16이지만 16으로 부르지 않는다. 추론 모델은 본문을 쓰기 전에 추론 토큰을
# 먼저 쓰고, 상한이 추론분보다 작으면 공급자가 400으로 거절하는 경우가 있다. 그러면 "키가
# 틀렸다"와 "상한이 너무 낮다"를 구분할 수 없게 되고, 연결 테스트가 존재하는 이유가 사라진다.
# 64는 그 여유를 두면서도 한 번의 확인으로 끝나는 크기다. 실제 모델별 최솟값은 A-07 live
# smoke에서 확인한다.
PROBE_MAX_OUTPUT_TOKENS = 64
_PROBE_PROMPT = "ping"


class OpenAiLlmAdapter:
    """OpenAI(Codex) 공급자. `LlmProviderPort`를 구현한다."""

    kind = ProviderKind.OPENAI

    def __init__(
        self,
        *,
        client_factory: OpenAiClientFactory | None = None,
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

        응답이 `incomplete`로 와도 성공이다. 추론 모델은 상한을 사고에 다 쓰고 본문 없이 끝나는
        일이 흔한데, 그것은 키·모델·네트워크가 모두 살아 있다는 뜻이다. 확인하려는 것은 응답의
        내용이 아니라 호출이 성립하는가뿐이다.

        실패 사유만 고르고 문장은 고르지 않는다. `ProbeResult.message`는 사유에서 유도되며,
        SDK 예외 본문에는 키 조각이 섞여 있을 수 있어 여기 들어올 자리를 두지 않았다(spec D2).
        """
        client = self._client_factory(secret, base_url)
        started = self._monotonic()
        try:
            client.create(
                input=_PROBE_PROMPT,
                max_output_tokens=PROBE_MAX_OUTPUT_TOKENS,
                model=model,
            )
        except Exception as error:
            failure = probe_failure_for(error)
            logger.warning(
                "openai probe failed — model=%s has_base_url=%s failure=%s error_type=%s",
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
