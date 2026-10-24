"""Declared dependencies for adapters.outbound.secrets_local.

`application.assistant_chat`의 공급자 포트(`ProviderSecretStore`·`ProviderProfileRepository`)와 그
예외, `application.broker_connection`의 키움 모의 자격증명 포트를 구현한다(S06 ②-2).
`domain.assistant`는 내 문서 `auto_trading/.env`에 공급자 프로파일을 키와 한 묶음으로 두면서
`ProviderProfile`·`ProviderKind` 값 타입을 읽으려고 본다(2026-10-24 사용자 결정). 정책은 다시
판단하지 않고 줄 ↔ 값 타입 변환만 한다.
"""

DEPENDS_ON: tuple[str, ...] = (
    "application.assistant_chat",
    "application.broker_connection",
    "domain.assistant",
)
