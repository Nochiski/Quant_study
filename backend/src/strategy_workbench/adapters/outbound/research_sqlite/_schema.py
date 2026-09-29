"""연구 기록 DB의 스키마 (검증 랩 spec D3).

전략 revision DB와 **다른 파일**이다. 저 쪽은 revision이 immutable이라 UPDATE/DELETE를 트리거로
막지만, 여기 run 은 상태가 바뀐다. 시도 원장·실험·봉인 원장(V1-05 이후)도 이 파일에 더한다.

## 버전

| 버전 | 바뀐 것 |
|---|---|
| 1 | 최초(V1-03): `backtest_runs` |
"""

from __future__ import annotations

import sqlite3

from strategy_workbench.adapters.outbound.sqlite_store.facade.schema import (
    SchemaContract,
    ensure_schema,
)

from ._errors import ResearchStorageError

SCHEMA_VERSION = 1

# ASCII-ish "SWRS". 전략 DB("SWRK")·어시스턴트 DB("SWAI")와 달라야 세 파일을 서로 열지 않는다.
_APPLICATION_ID = 0x53575253

# `accepted_order` 는 rowid 별칭이라 넣을 때 비워 두면 접수 순서대로 커진다(행을 지우지 않는다).
# 상태·실패 코드 어휘는 domain(`RunStatus`·`RunFailureCode`)이 소유하고 읽을 때 검사한다 — DDL 에
# 두 번째 목록을 두면 코드가 늘 때마다 파일 판본을 올려야 한다.
V1_SCHEMA_OBJECTS: tuple[tuple[str, str, str], ...] = (
    (
        "table",
        "backtest_runs",
        """
        CREATE TABLE backtest_runs (
            accepted_order INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL COLLATE BINARY CHECK (length(trim(run_id)) >= 1),
            status TEXT NOT NULL,
            progress REAL NOT NULL CHECK (
                typeof(progress) = 'real' AND progress BETWEEN 0 AND 1
            ),
            stage TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT,
            error_code TEXT,
            artifact_sha256 TEXT CHECK (
                artifact_sha256 IS NULL OR length(artifact_sha256) = 64
            ),
            strategy_kind TEXT NOT NULL,
            spec_hash TEXT NOT NULL CHECK (length(spec_hash) = 64),
            schema_version TEXT NOT NULL,
            strategy_id TEXT COLLATE BINARY,
            revision INTEGER CHECK (
                revision IS NULL OR (typeof(revision) = 'integer' AND revision >= 1)
            ),
            source_hash TEXT,
            request_json TEXT NOT NULL
        )
        """,
    ),
    # UNIQUE 제약 대신 이름 있는 인덱스로 둔다. 제약이 만드는 `sqlite_autoindex_*` 는 DDL 이 없어
    # manifest 선언으로 적을 수 없다.
    (
        "index",
        "backtest_runs_run_id",
        "CREATE UNIQUE INDEX backtest_runs_run_id ON backtest_runs (run_id)",
    ),
    (
        "index",
        "backtest_runs_by_strategy",
        "CREATE INDEX backtest_runs_by_strategy ON backtest_runs (strategy_id, accepted_order)",
    ),
)


def migrate_schema(connection: sqlite3.Connection) -> None:
    ensure_schema(
        connection,
        SchemaContract(
            label="research",
            application_id=_APPLICATION_ID,
            version=SCHEMA_VERSION,
            objects=V1_SCHEMA_OBJECTS,
            error=ResearchStorageError,
        ),
    )
