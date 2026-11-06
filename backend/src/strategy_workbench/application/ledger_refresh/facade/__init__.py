"""Declared dependencies for application.ledger_refresh.

홈 "DB 새로고침": 원장 동기화(받기 → 정리하기 → 확인하기)를 돌리고 끝나면 앱에 반영한다. 동기화
도구는 자기 outgoing `LedgerSyncPort`로만 부르고(database 패키지를 import 하지 않는다), 진행 중
작업 수와 앱 반영은 `ActiveWorkPort`·`ApplyDataPort`로 composition root 가 준다.
"""

DEPENDS_ON: tuple[str, ...] = ()
