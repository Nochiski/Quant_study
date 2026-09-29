"""연구 기록 DB의 스키마 (검증 랩 spec D3).

전략 revision DB와 **다른 파일**이다. 저 쪽은 revision이 immutable이라 UPDATE/DELETE를 트리거로
막지만, 여기 run 은 상태가 바뀐다. 실험도 이 파일에 둔다.

## 버전

| 버전 | 바뀐 것 |
|---|---|
| 1 | 최초(V1-03): `backtest_runs` |
| 2 | 시도 원장(V1-05): `trial_ledger`·`lineage_merges`·`sealed_window_blocks` |
| 3 | 실험(V3-03): `experiments`·`experiment_attempts`·`experiment_selections` |

v1 에서 올린 파일의 기존 run 은 원장 행이 없어 어느 계열의 시도로도 세지 않는다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from strategy_workbench.adapters.outbound.sqlite_store.facade.schema import (
    SchemaContract,
    SchemaUpgrade,
    ensure_schema,
)

from ._errors import ResearchStorageError

SCHEMA_VERSION = 3

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


# `accepted_order` 로 run 한 줄에 원장 한 줄이다(rowid 별칭이라 자동 인덱스가 없다). 계열이 없는
# run(저장한 적 없는 초안)도 적고 N 에서 뺀다. 세션 샤프·판본은 완료 때 채운다.
_V2_ADDED_OBJECTS: tuple[tuple[str, str, str], ...] = (
    (
        "table",
        "trial_ledger",
        """
        CREATE TABLE trial_ledger (
            accepted_order INTEGER PRIMARY KEY REFERENCES backtest_runs (accepted_order),
            lineage_id TEXT COLLATE BINARY,
            trial_key TEXT NOT NULL CHECK (length(trial_key) = 64),
            session_sharpe REAL,
            metric_registry_version TEXT
        )
        """,
    ),
    (
        "index",
        "trial_ledger_by_lineage",
        "CREATE INDEX trial_ledger_by_lineage ON trial_ledger (lineage_id, accepted_order)",
    ),
    # 계열 합치기는 되돌릴 수 없고 한 계열은 한 번만 합쳐진다(합친 쪽은 남은 계열로 따라간다).
    (
        "table",
        "lineage_merges",
        """
        CREATE TABLE lineage_merges (
            merge_order INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL COLLATE BINARY,
            target_id TEXT NOT NULL COLLATE BINARY CHECK (target_id <> source_id),
            merged_at TEXT NOT NULL
        )
        """,
    ),
    (
        "index",
        "lineage_merges_source",
        "CREATE UNIQUE INDEX lineage_merges_source ON lineage_merges (source_id)",
    ),
    # 봉인 원장의 "차단한 시도"(spec D11). 거절된 요청이라 run 이 없다.
    (
        "table",
        "sealed_window_blocks",
        """
        CREATE TABLE sealed_window_blocks (
            blocked_order INTEGER PRIMARY KEY,
            blocked_at TEXT NOT NULL,
            lineage_id TEXT COLLATE BINARY,
            trial_key TEXT NOT NULL CHECK (length(trial_key) = 64),
            spec_hash TEXT NOT NULL CHECK (length(spec_hash) = 64),
            start TEXT NOT NULL
        )
        """,
    ),
    (
        "index",
        "sealed_window_blocks_by_lineage",
        "CREATE INDEX sealed_window_blocks_by_lineage "
        "ON sealed_window_blocks (lineage_id, blocked_order)",
    ),
)

V2_SCHEMA_OBJECTS = V1_SCHEMA_OBJECTS + _V2_ADDED_OBJECTS

# 실험 설계(기반 요청·분할·해소된 그리드·창)는 만든 뒤 바뀌지 않아 JSON 한 칸에 둔다.
# trial 은 설계에서 다시 펴므로 행이 없다. attempt 는 실행에 배정되면 run_id, 접수가 거절되면
# 거절 코드와 문장만 싣고, 실행 상태는 적지 않는다(실행 기록에서 파생). run_id 는 같은 파일의
# `backtest_runs` 를 가리키지만 외래 키로 묶지 않는다 — 실행 기록은 실행 유스케이스의 저장소다.
_V3_ADDED_OBJECTS: tuple[tuple[str, str, str], ...] = (
    (
        "table",
        "experiments",
        """
        CREATE TABLE experiments (
            experiment_order INTEGER PRIMARY KEY,
            experiment_id TEXT NOT NULL COLLATE BINARY CHECK (length(trim(experiment_id)) >= 1),
            created_at TEXT NOT NULL,
            cancelled_at TEXT,
            design_json TEXT NOT NULL
        )
        """,
    ),
    (
        "index",
        "experiments_experiment_id",
        "CREATE UNIQUE INDEX experiments_experiment_id ON experiments (experiment_id)",
    ),
    (
        "table",
        "experiment_attempts",
        """
        CREATE TABLE experiment_attempts (
            attempt_order INTEGER PRIMARY KEY,
            experiment_order INTEGER NOT NULL REFERENCES experiments (experiment_order),
            trial_index INTEGER NOT NULL CHECK (
                typeof(trial_index) = 'integer' AND trial_index >= 0
            ),
            attempt INTEGER NOT NULL CHECK (typeof(attempt) = 'integer' AND attempt >= 1),
            created_at TEXT NOT NULL,
            run_id TEXT COLLATE BINARY,
            error_code TEXT,
            error TEXT,
            CHECK ((run_id IS NULL) <> (error_code IS NULL)),
            CHECK ((error_code IS NULL) = (error IS NULL))
        )
        """,
    ),
    (
        "index",
        "experiment_attempts_by_trial",
        "CREATE UNIQUE INDEX experiment_attempts_by_trial "
        "ON experiment_attempts (experiment_order, trial_index, attempt)",
    ),
    # 후보 선택 기록(spec D9)은 되돌릴 수 없고 쌓이기만 한다.
    (
        "table",
        "experiment_selections",
        """
        CREATE TABLE experiment_selections (
            selection_order INTEGER PRIMARY KEY,
            experiment_order INTEGER NOT NULL REFERENCES experiments (experiment_order),
            selection_json TEXT NOT NULL
        )
        """,
    ),
    (
        "index",
        "experiment_selections_by_experiment",
        "CREATE INDEX experiment_selections_by_experiment "
        "ON experiment_selections (experiment_order, selection_order)",
    ),
)

V3_SCHEMA_OBJECTS = V2_SCHEMA_OBJECTS + _V3_ADDED_OBJECTS


def _added(objects: tuple[tuple[str, str, str], ...]) -> Callable[[sqlite3.Connection], None]:
    def apply(connection: sqlite3.Connection) -> None:
        for _object_type, _name, statement in objects:
            connection.execute(statement)

    return apply


def migrate_schema(connection: sqlite3.Connection) -> None:
    ensure_schema(
        connection,
        SchemaContract(
            label="research",
            application_id=_APPLICATION_ID,
            version=SCHEMA_VERSION,
            objects=V3_SCHEMA_OBJECTS,
            error=ResearchStorageError,
            upgrades=(
                SchemaUpgrade(1, V1_SCHEMA_OBJECTS, _added(_V2_ADDED_OBJECTS)),
                SchemaUpgrade(2, V2_SCHEMA_OBJECTS, _added(_V3_ADDED_OBJECTS)),
            ),
        ),
    )
