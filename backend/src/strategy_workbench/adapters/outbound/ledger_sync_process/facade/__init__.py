"""Declared dependencies for adapters.outbound.ledger_sync_process.

`application.ledger_refresh`의 `LedgerSyncPort`를 원장 동기화 CLI(`python -m ledger_sync`)의 UTF-8
subprocess로 구현한다. backend 는 database 패키지를 import 하지 않는다 — 명령·기계용 출력
(`plan --json`·`--progress-file`·`_sync/last_run.json`·`_sync/lock`)으로만 잇는다.
"""

DEPENDS_ON: tuple[str, ...] = ("application.ledger_refresh",)
