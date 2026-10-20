"""Declared dependencies for adapters.outbound.kiwoom_mock.

S05 `BrokerPort`를 키움 모의 REST로 구현한다. 누적 사실의 전이 판정은 `domain.order_execution`의
`classify_event`를 그대로 쓰고, 참조 원장 파일은 `sqlite_store`의 연결·DDL 규칙을 재사용한다.
"""

DEPENDS_ON: tuple[str, ...] = (
    "domain.order_execution",
    "application.offline_execution",
    "adapters.outbound.sqlite_store",
)
