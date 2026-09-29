"""Declared dependencies for adapters.outbound.sqlite_store.

저장 규칙(파일 claim·application_id·DDL manifest·연결 수명)만 가진다. 어떤 port도 구현하지 않고
어떤 값 타입도 모르므로 다른 노드에 의존하지 않는다.
"""

DEPENDS_ON: tuple[str, ...] = ()
