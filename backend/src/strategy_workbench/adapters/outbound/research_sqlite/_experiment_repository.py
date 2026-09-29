from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import TypeAdapter

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.timestamp import datetime_text
from strategy_workbench.application.experiment_run.facade.ports import (
    ExperimentRecord,
    ExperimentSelection,
    TrialAttempt,
)
from strategy_workbench.domain.experiment.facade.design import ExperimentNotFoundError

from ._errors import ResearchStorageError
from ._schema import migrate_schema

_T = TypeVar("_T")
# 설계 JSON 은 기록에서 칸 셋(식별·시각)을 뺀 것이다. 그 셋은 칸으로 둔다(취소 시각은 바뀐다).
_COLUMNS = {"experiment_id", "created_at", "cancelled_at"}
_SELECT = "experiment_id, created_at, cancelled_at, design_json"
_RECORD = TypeAdapter(ExperimentRecord)
_ATTEMPT = TypeAdapter(TrialAttempt)
_SELECTION = TypeAdapter(ExperimentSelection)


class SQLiteExperimentRepository:
    """실험 기록 SQLite adapter. 실행 기록과 같은 research DB 파일을 쓴다(spec D3).

    경로가 `None`이면 프로세스 안에서만 사는 in-memory DB다. 설계 JSON 은 요청 본문과 같은
    dataclass 검증(pydantic `TypeAdapter`)으로 되읽어 `__post_init__` 불변식을 다시 탄다.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._database = SqliteDatabase(
            path, label="research database", error=ResearchStorageError, prepare=migrate_schema
        )

    def close(self) -> None:
        self._database.close()

    def add(self, record: ExperimentRecord) -> None:
        with self._database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO experiments (experiment_id, created_at, design_json) VALUES (?, ?, ?)",
                (
                    record.experiment_id,
                    _time_text(record.created_at, "created_at", record.experiment_id),
                    _RECORD.dump_json(record, exclude=_COLUMNS).decode(),
                ),
            )

    def get(self, experiment_id: str) -> ExperimentRecord:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                f"SELECT {_SELECT} FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise _not_found(experiment_id)
        return _record(row)

    def list(self, *, after: str | None, limit: int) -> tuple[ExperimentRecord, ...]:
        with self._database.transaction(write=False) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SELECT} FROM experiments
                WHERE ? IS NULL OR experiment_order < (
                    SELECT experiment_order FROM experiments WHERE experiment_id = ?
                )
                ORDER BY experiment_order DESC LIMIT ?
                """,
                (after, after, limit),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def cancel(self, experiment_id: str, *, cancelled_at: datetime) -> None:
        with self._database.transaction(write=True) as connection:
            updated = connection.execute(
                "UPDATE experiments SET cancelled_at = ? WHERE experiment_id = ?",
                (_time_text(cancelled_at, "cancelled_at", experiment_id), experiment_id),
            )
            if updated.rowcount != 1:
                raise _not_found(experiment_id)

    def add_attempt(self, attempt: TrialAttempt) -> None:
        with self._database.transaction(write=True) as connection:
            inserted = connection.execute(
                """
                INSERT INTO experiment_attempts
                    (experiment_order, trial_index, attempt, created_at, run_id, error_code, error)
                SELECT experiment_order, ?, ?, ?, ?, ?, ? FROM experiments WHERE experiment_id = ?
                """,
                (
                    attempt.trial_index,
                    attempt.attempt,
                    _time_text(attempt.created_at, "attempt created_at", attempt.experiment_id),
                    attempt.run_id,
                    attempt.error_code,
                    attempt.error,
                    attempt.experiment_id,
                ),
            )
            if inserted.rowcount != 1:
                raise _not_found(attempt.experiment_id)

    def attempts(self, experiment_id: str) -> tuple[TrialAttempt, ...]:
        with self._database.transaction(write=False) as connection:
            rows = connection.execute(
                """
                SELECT a.trial_index, a.attempt, a.created_at, a.run_id, a.error_code, a.error
                FROM experiment_attempts AS a JOIN experiments USING (experiment_order)
                WHERE experiment_id = ? ORDER BY a.trial_index, a.attempt
                """,
                (experiment_id,),
            ).fetchall()
        return tuple(
            _decode(_ATTEMPT, experiment_id, {**dict(row), "experiment_id": experiment_id})
            for row in rows
        )

    def add_selection(self, selection: ExperimentSelection) -> None:
        with self._database.transaction(write=True) as connection:
            inserted = connection.execute(
                """
                INSERT INTO experiment_selections (experiment_order, selection_json)
                SELECT experiment_order, ? FROM experiments WHERE experiment_id = ?
                """,
                (_SELECTION.dump_json(selection).decode(), selection.experiment_id),
            )
            if inserted.rowcount != 1:
                raise _not_found(selection.experiment_id)

    def selections(self, experiment_id: str) -> tuple[ExperimentSelection, ...]:
        with self._database.transaction(write=False) as connection:
            rows = connection.execute(
                """
                SELECT selection_json
                FROM experiment_selections JOIN experiments USING (experiment_order)
                WHERE experiment_id = ? ORDER BY selection_order
                """,
                (experiment_id,),
            ).fetchall()
        return tuple(
            _decode(_SELECTION, experiment_id, json.loads(row["selection_json"])) for row in rows
        )


def _record(row: sqlite3.Row) -> ExperimentRecord:
    return _decode(
        _RECORD,
        row["experiment_id"],
        {**json.loads(row["design_json"]), **{key: row[key] for key in _COLUMNS}},
    )


def _decode(adapter: TypeAdapter[_T], experiment_id: str, value: dict[str, Any]) -> _T:
    try:
        return adapter.validate_python(value)
    except (TypeError, ValueError) as error:
        # pydantic `ValidationError` 도 `ValueError` 다.
        raise ResearchStorageError(
            f"stored experiment failed integrity validation — experiment_id={experiment_id}: "
            f"{error}"
        ) from error


def _not_found(experiment_id: str) -> ExperimentNotFoundError:
    return ExperimentNotFoundError(
        "experiment.not_found", f"실험이 없습니다: experiment_id={experiment_id}"
    )


def _time_text(value: datetime, field: str, experiment_id: str) -> str:
    return datetime_text(value, field=f"experiment {field} (experiment_id={experiment_id})")
