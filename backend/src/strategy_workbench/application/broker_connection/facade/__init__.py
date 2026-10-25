"""Declared dependencies for application.broker_connection.

사용자 PC의 키움 모의 키 저장·연결 확인(S06 ②-2). 비밀 저장과 키움 호출은 자기 outgoing 포트로만
본다. 주문 실행(`application.offline_execution`)과는 서로 import하지 않는다.
"""

DEPENDS_ON: tuple[str, ...] = ()
