"""Declared dependencies for adapters.outbound.llm_scripted.

`domain.assistant`의 이벤트·도구 이름만 본다. `LlmProviderPort`는 구조적 Protocol이라 구현이
import하지 않아도 성립하며, 그 계약을 실제로 확인하는 곳은 이 adapter를 주입하는 bootstrap이다.
공급자 SDK를 쓰지 않는 테스트 전용 adapter이므로 SDK import 게이트와도 무관하다.
`domain.strategy`는 문서가 없는 결과 세션에서 제안을 시도하는 대본이 버전 상수
`CURRENT_SCHEMA_VERSION` 하나를 읽으려고 본다(리터럴을 적지 않는다, SoT 규칙).
"""

DEPENDS_ON: tuple[str, ...] = ("domain.assistant", "domain.strategy")
