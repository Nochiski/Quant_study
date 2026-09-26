"""Declared dependencies for adapters.outbound.llm_anthropic; exports use named modules."""

# `application.assistant_chat`은 선언하지 않는다. 이 노드는 그 패키지를 import하지 않는다 —
# `LlmProviderPort`는 Protocol이라 구조만 맞으면 되고, 실제로 쓰는 타입은 전부 `domain.assistant`
# 것이다. 선언은 그래프가 사실을 말하게 하는 것이 목적이므로, import하지 않는 노드를 적으면
# 영향 범위를 읽는 사람이 없는 간선을 본다. 포트 시그니처 변경은
# `tests/test_adapters_llm_anthropic.py`의 포트 적합성 테스트가 pyright로 잡는다.
DEPENDS_ON: tuple[str, ...] = ("domain.assistant",)
