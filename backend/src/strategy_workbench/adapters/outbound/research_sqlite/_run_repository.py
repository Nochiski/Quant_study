from __future__ import annotations

import sqlite3
from collections.abc import Collection
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar, cast

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.timestamp import datetime_text
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestRunNotFoundError,
    BacktestRunSummary,
    TrialLedgerRecords,
    TrialLineageAlreadyMergedError,
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
from strategy_workbench.domain.backtest.facade.trials import BlockedTrialAttempt, TrialLedgerEntry

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

    def add(
        self,
        summary: BacktestRunSummary,
        request: BacktestRunSpec,
        *,
        lineage_id: str | None,
        trial_key: str,
    ) -> None:
        run, provenance = summary.run, summary.strategy_provenance
        with self._database.transaction(write=True) as connection:
            inserted = connection.execute(
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
            connection.execute(
                "INSERT INTO trial_ledger (accepted_order, lineage_id, trial_key) VALUES (?, ?, ?)",
                (inserted.lastrowid, lineage_id, trial_key),
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

    def record_trial_result(
        self, run_id: str, *, session_sharpe: float | None, metric_registry_version: str
    ) -> None:
        with self._database.transaction(write=True) as connection:
            updated = connection.execute(
                """
                UPDATE trial_ledger SET session_sharpe = ?, metric_registry_version = ?
                WHERE accepted_order = (SELECT accepted_order FROM backtest_runs WHERE run_id = ?)
                """,
                (session_sharpe, metric_registry_version, run_id),
            )
            if updated.rowcount != 1:
                raise BacktestRunNotFoundError(run_id)

    def record_blocked_attempt(self, attempt: BlockedTrialAttempt) -> None:
        with self._database.transaction(write=True) as connection:
            connection.execute(
                """
                INSERT INTO sealed_window_blocks
                    (blocked_at, lineage_id, trial_key, spec_hash, start)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime_text(attempt.blocked_at, field="sealed window block blocked_at"),
                    attempt.lineage_id,
                    attempt.trial_key,
                    attempt.spec_hash,
                    attempt.start.isoformat(),
                ),
            )

    def trial_ledger(self, lineage_id: str) -> TrialLedgerRecords:
        with self._database.transaction(write=False) as connection:
            merges = _merges(connection)
            root = _root(merges, lineage_id)
            merged = tuple(source for source in merges if _root(merges, source) == root)
            members = (root, *merged)
            where = f"lineage_id IN ({', '.join('?' * len(members))})"
            entries = connection.execute(
                f"""
                SELECT run_id, trial_key, status, created_at, updated_at, session_sharpe,
                    metric_registry_version
                FROM trial_ledger JOIN backtest_runs USING (accepted_order)
                WHERE {where} ORDER BY accepted_order
                """,
                members,
            ).fetchall()
            blocked = connection.execute(
                f"""
                SELECT blocked_at, lineage_id, trial_key, spec_hash, start
                FROM sealed_window_blocks WHERE {where} ORDER BY blocked_order
                """,
                members,
            ).fetchall()
        try:
            return TrialLedgerRecords(
                lineage_id=root,
                merged_lineage_ids=merged,
                entries=tuple(
                    TrialLedgerEntry(
                        run_id=row["run_id"],
                        trial_key=row["trial_key"],
                        status=RunStatus(row["status"]),
                        created_at=datetime.fromisoformat(row["created_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]),
                        session_sharpe=row["session_sharpe"],
                        metric_registry_version=row["metric_registry_version"],
                    )
                    for row in entries
                ),
                blocked=tuple(
                    BlockedTrialAttempt(
                        blocked_at=datetime.fromisoformat(row["blocked_at"]),
                        lineage_id=row["lineage_id"],
                        trial_key=row["trial_key"],
                        spec_hash=row["spec_hash"],
                        start=date.fromisoformat(row["start"]),
                    )
                    for row in blocked
                ),
            )
        except (TypeError, ValueError) as error:
            raise ResearchStorageError(
                f"stored trial ledger failed integrity validation — lineage_id={root}: {error}"
            ) from error

    def merge_lineages(self, source_id: str, target_id: str, *, merged_at: datetime) -> None:
        with self._database.transaction(write=True) as connection:
            merges = _merges(connection)
            source_root, target_root = _root(merges, source_id), _root(merges, target_id)
            if source_root == target_root:
                raise TrialLineageAlreadyMergedError(
                    "lineages are already one — "
                    f"source_id={source_id} target_id={target_id} lineage_id={target_root}"
                )
            # 이미 다른 계열에 합쳐진 계열이면 남은 계열끼리 잇는다(한 계열은 한 번만 합쳐진다).
            connection.execute(
                "INSERT INTO lineage_merges (source_id, target_id, merged_at) VALUES (?, ?, ?)",
                (source_root, target_root, datetime_text(merged_at, field="lineage merged_at")),
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

    def states(self, run_ids: Collection[str]) -> dict[str, BacktestRunState]:
        ids = list(run_ids)
        if not ids:
            return {}
        with self._database.transaction(write=False) as connection:
            rows = connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM backtest_runs "
                f"WHERE run_id IN ({', '.join('?' * len(ids))})",
                ids,
            ).fetchall()
        return {row["run_id"]: _summary(row).run for row in rows}


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


def _merges(connection: sqlite3.Connection) -> dict[str, str]:
    """합친 계열 → 합쳐 들어간 계열(합친 순)."""
    rows = connection.execute(
        "SELECT source_id, target_id FROM lineage_merges ORDER BY merge_order"
    ).fetchall()
    return {row["source_id"]: row["target_id"] for row in rows}


def _root(merges: dict[str, str], lineage_id: str) -> str:
    """합치기를 따라가 남은 계열. 합칠 때마다 남은 계열끼리 잇으므로 고리가 없다."""
    while lineage_id in merges:
        lineage_id = merges[lineage_id]
    return lineage_id


def _time_text(value: datetime, field: str, run_id: str) -> str:
    return datetime_text(value, field=f"backtest run {field} (run_id={run_id})")
