from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import ClassVar, cast

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.timestamp import datetime_text
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestRunNotFoundError,
    BacktestRunSummary,
)
from strategy_workbench.application.strategy_design.facade.ports import Page, PageRequest
from strategy_workbench.domain.backtest.facade.runs import (
    RUN_FAILURE_CODES,
    BacktestRunSpec,
    BacktestRunState,
    RunFailureCode,
    RunStatus,
    StrategyProvenance,
    StrategySourceKind,
)

from ._errors import ResearchStorageError
from ._request_codec import decode_request, encode_request
from ._schema import SCHEMA_VERSION, migrate_schema

_TERMINAL = (RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value)
# 앞 둘(run_id·created_at)은 접수 때만 쓰는 식별 칸이다. 상태 전이(`update`)는 그 뒤만 바꾼다.
_SUMMARY_COLUMNS = """
    run_id, created_at, status, progress, stage, message, updated_at, error, error_code,
    artifact_sha256, strategy_kind, spec_hash, schema_version, strategy_id, revision, source_hash
"""


class SQLiteBacktestRunRepository:
    """실행 기록 SQLite adapter. 경로가 `None`이면 프로세스 안에서만 사는 in-memory DB다."""

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __init__(self, path: str | Path | None = None) -> None:
        self._database = SqliteDatabase(
            path, label="research database", error=ResearchStorageError, prepare=migrate_schema
        )

    @property
    def database_path(self) -> Path | None:
        return self._database.database_path

    def close(self) -> None:
        self._database.close()

    def add(self, summary: BacktestRunSummary, request: BacktestRunSpec) -> None:
        run, provenance = summary.run, summary.strategy_provenance
        with self._database.transaction(write=True) as connection:
            connection.execute(
                f"""
                INSERT INTO backtest_runs ({_SUMMARY_COLUMNS}, request_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    _time_text(run.created_at, "created_at", run.run_id),
                    *_state_values(run),
                    provenance.kind.value,
                    provenance.spec_hash,
                    provenance.schema_version,
                    provenance.strategy_id,
                    provenance.revision,
                    provenance.source_hash,
                    encode_request(request),
                ),
            )

    def update(self, state: BacktestRunState) -> None:
        with self._database.transaction(write=True) as connection:
            updated = connection.execute(
                """
                UPDATE backtest_runs
                SET status = ?, progress = ?, stage = ?, message = ?, updated_at = ?,
                    error = ?, error_code = ?, artifact_sha256 = ?
                WHERE run_id = ?
                """,
                (*_state_values(state), state.run_id),
            )
            if updated.rowcount != 1:
                raise BacktestRunNotFoundError(state.run_id)

    def get(self, run_id: str) -> BacktestRunSummary:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise BacktestRunNotFoundError(run_id)
        return _summary(row)

    def request(self, run_id: str) -> BacktestRunSpec:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT request_json FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise BacktestRunNotFoundError(run_id)
        return decode_request(row["request_json"], run_id=run_id)

    def list(
        self, page: PageRequest, *, strategy_id: str | None = None
    ) -> Page[BacktestRunSummary]:
        where = "WHERE ? IS NULL OR strategy_id = ?"
        with self._database.transaction(write=False) as connection:
            (total,) = connection.execute(
                f"SELECT COUNT(*) FROM backtest_runs {where}", (strategy_id, strategy_id)
            ).fetchone()
            rows = connection.execute(
                f"""
                SELECT {_SUMMARY_COLUMNS} FROM backtest_runs {where}
                ORDER BY accepted_order DESC LIMIT ? OFFSET ?
                """,
                (strategy_id, strategy_id, page.limit, page.offset),
            ).fetchall()
        return Page(
            items=tuple(_summary(row) for row in rows),
            total=total,
            offset=page.offset,
            limit=page.limit,
        )

    def unfinished(self) -> tuple[BacktestRunState, ...]:
        with self._database.transaction(write=False) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SUMMARY_COLUMNS} FROM backtest_runs
                WHERE status NOT IN (?, ?, ?) ORDER BY accepted_order
                """,
                _TERMINAL,
            ).fetchall()
        return tuple(_summary(row).run for row in rows)


def _state_values(state: BacktestRunState) -> tuple[object, ...]:
    """상태 전이가 바꾸는 칸. 순서는 `_SUMMARY_COLUMNS` 의 `status` 부터 `artifact_sha256` 까지."""
    return (
        state.status.value,
        float(state.progress),
        state.stage,
        state.message,
        _time_text(state.updated_at, "updated_at", state.run_id),
        state.error,
        state.error_code,
        state.artifact_sha256,
    )


def _summary(row: sqlite3.Row) -> BacktestRunSummary:
    run_id = row["run_id"]
    try:
        error_code = row["error_code"]
        if error_code is not None and error_code not in RUN_FAILURE_CODES:
            raise ValueError(f"unknown error_code={error_code!r}")
        return BacktestRunSummary(
            run=BacktestRunState(
                run_id=run_id,
                status=RunStatus(row["status"]),
                progress=row["progress"],
                stage=row["stage"],
                message=row["message"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                error=row["error"],
                error_code=cast(RunFailureCode | None, error_code),
                artifact_sha256=row["artifact_sha256"],
            ),
            strategy_provenance=StrategyProvenance(
                kind=StrategySourceKind(row["strategy_kind"]),
                spec_hash=row["spec_hash"],
                schema_version=row["schema_version"],
                strategy_id=row["strategy_id"],
                revision=row["revision"],
                source_hash=row["source_hash"],
            ),
        )
    except (TypeError, ValueError) as error:
        raise ResearchStorageError(
            f"stored backtest run failed integrity validation — run_id={run_id}: {error}"
        ) from error


def _time_text(value: datetime, field: str, run_id: str) -> str:
    return datetime_text(value, field=f"backtest run {field} (run_id={run_id})")
