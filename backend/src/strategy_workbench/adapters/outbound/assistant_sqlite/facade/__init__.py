"""Declared dependencies for adapters.outbound.assistant_sqlite.

`application.assistant_chat`의 포트 Protocol·예외와 세션 값 타입(`ChatSession`·`DocumentRef`),
`domain.assistant`의 도메인 값 타입(`ProviderProfile`·`Turn`·`ChatEvent`)만 본다. 이 어댑터는
정책을 다시 판단하지 않고 행 ↔ 값 타입 변환과 sequence 부여만 한다.
"""

DEPENDS_ON: tuple[str, ...] = (
    "application.assistant_chat",
    "domain.assistant",
)
