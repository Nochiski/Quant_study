"""별도 SQLite 파일에 멱등 요청과 전역 실행 claim을 한 트랜잭션으로 보존한다."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.schema import (
    SchemaContract,
    ensure_schema,
)
from strategy_workbench.application.continuous_account.facade.evaluation import (
    ContinuousAccountNotFoundError,
    ContinuousAccountRejectedError,
    ContinuousAccountState,
    ContinuousAccountStatus,
    ContinuousAccountUnreadableError,
)
from strategy_workbench.domain.canonical.facade.encoding import (
    canonical_json_spec_hash,
    canonical_payload_json,
)


class ContinuousAccountStoreError(ContinuousAccountUnreadableError):
    """스키마·상태·직렬화 계약 위반. 잘못된 원장을 정상 결과로 대체하지 않는다."""


_STATE = TypeAdapter(ContinuousAccountState)
_OBJECTS = (
    (
        "table",
        "continuous_evaluations",
        "CREATE TABLE continuous_evaluations (evaluation_id TEXT PRIMARY KEY, "
        "experiment_id TEXT NOT NULL, status TEXT NOT NULL "
        "CHECK (status IN ('running', 'completed', 'failed', 'interrupted')), "
        "created_at TEXT NOT NULL, payload TEXT NOT NULL, "
        "payload_sha256 TEXT NOT NULL) WITHOUT ROWID",
    ),
    (
        "index",
        "continuous_one_running",
        "CREATE UNIQUE INDEX continuous_one_running ON continuous_evaluations (status) "
        "WHERE status = 'running'",
    ),
    (
        "index",
        "continuous_experiment_history",
        "CREATE INDEX continuous_experiment_history ON continuous_evaluations "
        "(experiment_id, created_at, evaluation_id)",
    ),
)


def _prepare(connection: sqlite3.Connection) -> None:
    ensure_schema(
        connection,
        SchemaContract(
            label="continuous_account",
            application_id=0x4D484341,
            version=1,
            objects=_OBJECTS,
            error=ContinuousAccountStoreError,
        ),
    )


def _validate_state(state: ContinuousAccountState) -> None:
    context = f"evaluation_id={state.evaluation_id} status={state.status}"
    if not state.evaluation_id or not state.experiment_id:
        raise ContinuousAccountStoreError(f"continuous state requires identifiers: {context}")
    if not math.isfinite(state.progress) or not 0 <= state.progress <= 1:
        raise ContinuousAccountStoreError(
            f"continuous progress is invalid: {context} progress={state.progress}"
        )
    if state.created_at.tzinfo is None or state.created_at.utcoffset() is None:
        raise ContinuousAccountStoreError(f"continuous created_at requires timezone: {context}")
    if state.completed_at is not None and (
        state.completed_at.tzinfo is None
        or state.completed_at.utcoffset() is None
        or state.completed_at < state.created_at
    ):
        raise ContinuousAccountStoreError(f"continuous completed_at is invalid: {context}")
    if state.status is ContinuousAccountStatus.RUNNING:
        if any(
            value is not None
            for value in (state.completed_at, state.error_code, state.error_detail, state.result)
        ):
            raise ContinuousAccountStoreError(
                f"running continuous state carries terminal facts: {context}"
            )
    elif state.completed_at is None:
        raise ContinuousAccountStoreError(
            f"terminal continuous state lacks completed_at: {context}"
        )
    elif state.status is ContinuousAccountStatus.COMPLETED:
        if (
            state.progress != 1
            or state.result is None
            or state.error_code is not None
            or state.error_detail is not None
        ):
            raise ContinuousAccountStoreError(
                f"completed continuous state is inconsistent: {context}"
            )
        manifest = state.result.manifest
        if (
            manifest.evaluation_id != state.evaluation_id
            or manifest.experiment_id != state.experiment_id
            or manifest.created_at != state.created_at
        ):
            raise ContinuousAccountStoreError(
                f"continuous manifest identity differs from state: {context}"
            )
    elif state.result is not None or not state.error_code or not state.error_detail:
        raise ContinuousAccountStoreError(f"failed continuous state is inconsistent: {context}")


def _encode(state: ContinuousAccountState) -> str:
    _validate_state(state)
    try:
        # Pydantic이 생략하는 init=False 파생 필드도 함께 보존한다.
        return canonical_payload_json(asdict(state))
    except (ValueError, TypeError) as error:
        raise ContinuousAccountStoreError(
            f"cannot encode continuous state: evaluation_id={state.evaluation_id} {error}"
        ) from error


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: key={key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: value={value}")


def _decode(row: sqlite3.Row) -> ContinuousAccountState:
    try:
        if not isinstance(row["payload"], str) or not isinstance(row["payload_sha256"], str):
            raise ValueError("continuous payload and digest must be text")
        # 우발적인 바이트 손상 검출이며 DB 소유자의 악의적 재서명까지 인증하지 않는다.
        if canonical_json_spec_hash(row["payload"]) != row["payload_sha256"]:
            raise ValueError("continuous payload digest differs from stored bytes")
        raw = json.loads(
            row["payload"],
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
        state = _STATE.validate_json(row["payload"], strict=True)
        _validate_state(state)
        # 생성자에서 다시 파생되는 일정 hash·상태와 미지 필드도 저장 사실과 일치해야 한다.
        if json.loads(_encode(state)) != raw:
            raise ValueError("continuous JSON contains unknown or inconsistent derived fields")
        if (
            state.evaluation_id != row["evaluation_id"]
            or state.experiment_id != row["experiment_id"]
            or state.status.value != row["status"]
            or _utc(state.created_at) != row["created_at"]
        ):
            raise ValueError("continuous row index differs from payload identity")
        return state
    except (ValueError, TypeError, KeyError) as error:
        raise ContinuousAccountStoreError(
            f"cannot decode continuous state: evaluation_id={row['evaluation_id']} {error}"
        ) from error


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class ContinuousAccountSqliteStore:
    """None 경로는 격리된 메모리 DB이며 파일 모드도 같은 스키마·정책을 사용한다."""

    def __init__(self, path: str | Path | None) -> None:
        self._database = SqliteDatabase(
            path, label="continuous_account", error=ContinuousAccountStoreError, prepare=_prepare
        )

    @property
    def database_path(self) -> Path | None:
        return self._database.database_path

    def close(self) -> None:
        self._database.close()

    def claim(self, state: ContinuousAccountState) -> bool:
        payload = _encode(state)
        if state.status is not ContinuousAccountStatus.RUNNING:
            raise ContinuousAccountStoreError(
                f"claim requires running state: evaluation_id={state.evaluation_id}"
            )
        with self._database.transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM continuous_evaluations WHERE evaluation_id = ?",
                (state.evaluation_id,),
            ).fetchone()
            if existing is not None:
                saved = _decode(existing)
                if saved.experiment_id != state.experiment_id:
                    raise ContinuousAccountRejectedError(
                        "continuous.request_conflict",
                        "이 요청 ID는 다른 실험에서 이미 사용했습니다.",
                    )
                return False
            if connection.execute(
                "SELECT 1 FROM continuous_evaluations WHERE status = 'running'"
            ).fetchone():
                raise ContinuousAccountRejectedError(
                    "continuous.busy",
                    "다른 연속 계좌 평가가 실행 중입니다. 완료 뒤 다시 실행하세요.",
                )
            connection.execute(
                "INSERT INTO continuous_evaluations VALUES (?, ?, ?, ?, ?, ?)",
                (
                    state.evaluation_id,
                    state.experiment_id,
                    state.status.value,
                    _utc(state.created_at),
                    payload,
                    canonical_json_spec_hash(payload),
                ),
            )
            return True

    def save(self, state: ContinuousAccountState) -> None:
        payload = _encode(state)
        with self._database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM continuous_evaluations WHERE evaluation_id = ?",
                (state.evaluation_id,),
            ).fetchone()
            if row is None:
                raise ContinuousAccountNotFoundError(state.evaluation_id)
            previous = _decode(row)
            if (
                previous.experiment_id != state.experiment_id
                or previous.created_at != state.created_at
            ):
                raise ContinuousAccountStoreError(
                    f"continuous state identity changed: evaluation_id={state.evaluation_id}"
                )
            if previous.status is not ContinuousAccountStatus.RUNNING:
                if _encode(previous) == payload:
                    return
                raise ContinuousAccountStoreError(
                    f"continuous terminal state is immutable: evaluation_id={state.evaluation_id}"
                )
            if state.progress < previous.progress:
                raise ContinuousAccountStoreError(
                    f"continuous progress decreased: evaluation_id={state.evaluation_id}"
                )
            connection.execute(
                "UPDATE continuous_evaluations SET status = ?, payload = ?, payload_sha256 = ? "
                "WHERE evaluation_id = ?",
                (
                    state.status.value,
                    payload,
                    canonical_json_spec_hash(payload),
                    state.evaluation_id,
                ),
            )

    def get(self, evaluation_id: str) -> ContinuousAccountState:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM continuous_evaluations WHERE evaluation_id = ?", (evaluation_id,)
            ).fetchone()
            if row is None:
                raise ContinuousAccountNotFoundError(evaluation_id)
            return _decode(row)

    def latest(self, experiment_id: str) -> ContinuousAccountState | None:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM continuous_evaluations WHERE experiment_id = ? "
                "ORDER BY created_at DESC, evaluation_id DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
            return None if row is None else _decode(row)

    def interrupt_running(self, completed_at: datetime) -> None:
        with self._database.transaction(write=True) as connection:
            rows = connection.execute(
                "SELECT * FROM continuous_evaluations WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                previous = _decode(row)
                interrupted = replace(
                    previous,
                    status=ContinuousAccountStatus.INTERRUPTED,
                    completed_at=completed_at,
                    stage="interrupted",
                    error_code="continuous.interrupted",
                    error_detail=(
                        "서버가 다시 시작되어 이전 연속 계좌 평가가 중단됐습니다. 새로 실행하세요."
                    ),
                )
                payload = _encode(interrupted)
                connection.execute(
                    "UPDATE continuous_evaluations SET status = ?, payload = ?, payload_sha256 = ? "
                    "WHERE evaluation_id = ?",
                    (
                        interrupted.status.value,
                        payload,
                        canonical_json_spec_hash(payload),
                        interrupted.evaluation_id,
                    ),
                )
