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
    RunKind,
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
# 실행 종류와 쓰는 실험(검증 랩 V5-03, #382)은 저장하지 않고 같은 파일에서 읽는다. 실험 attempt 가
# 가리키면 실험 trial, 워크포워드 창 선택이 가리키면 검증 창 실행이다(둘 다면 attempt 가 먼저).
# 역조회는 비상관 집계라 문장당 한 번 만들어진다(run_id 에 인덱스가 없어 상관 서브쿼리면 run 수 ×
# attempt 수로 커진다, #386 리뷰 P2-1). 거절된 attempt·고를 칸 없는 창은 run_id 가 NULL 이라 뺀다.
_RUNS = f"""(
    SELECT backtest_runs.*,
        CASE
            WHEN trials.owner IS NOT NULL THEN '{RunKind.EXPERIMENT_TRIAL.value}'
            WHEN picks.owner IS NOT NULL THEN '{RunKind.WALK_FORWARD_VALIDATION.value}'
            ELSE '{RunKind.SINGLE.value}'
        END AS kind,
        experiments.experiment_id AS experiment_id,
        COALESCE(experiment_controls.paused, 0) AS experiment_paused
    FROM backtest_runs
    LEFT JOIN (
        SELECT run_id, MIN(experiment_order) AS owner FROM experiment_attempts
        WHERE run_id IS NOT NULL GROUP BY run_id
    ) AS trials USING (run_id)
    LEFT JOIN (
        SELECT run_id, MIN(experiment_order) AS owner FROM experiment_window_picks
        WHERE run_id IS NOT NULL GROUP BY run_id
    ) AS picks USING (run_id)
    LEFT JOIN experiments
        ON experiments.experiment_order = COALESCE(trials.owner, picks.owner)
    LEFT JOIN experiment_controls
        ON experiment_controls.experiment_order = experiments.experiment_order
)"""
_READ_COLUMNS = f"{_SUMMARY_COLUMNS}, kind, experiment_id, experiment_paused"


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
        run: BacktestRunState,
        provenance: StrategyProvenance,
        request: BacktestRunSpec,
        *,
        lineage_id: str | None,
        trial_key: str,
    ) -> None:
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
                f"SELECT {_READ_COLUMNS} FROM {_RUNS} WHERE run_id = ?", (run_id,)
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
        self, page: PageRequest, *, strategy_id: str | None = None, kind: RunKind | None = None
    ) -> Page[BacktestRunSummary]:
        where = "WHERE (? IS NULL OR strategy_id = ?) AND (? IS NULL OR kind = ?)"
        filters = (strategy_id, strategy_id, kind, kind)
        with self._database.transaction(write=False) as connection:
            count = connection.execute(f"SELECT COUNT(*) FROM {_RUNS} {where}", filters)
            (total,) = count.fetchone()
            rows = connection.execute(
                f"""
                SELECT {_READ_COLUMNS} FROM {_RUNS} {where}
                ORDER BY accepted_order DESC LIMIT ? OFFSET ?
                """,
                (*filters, page.limit, page.offset),
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
        return tuple(_state(row) for row in rows)

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
        return {row["run_id"]: _state(row) for row in rows}


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


def _state(row: sqlite3.Row) -> BacktestRunState:
    run_id = row["run_id"]
    try:
        error_code = row["error_code"]
        if error_code is not None and error_code not in RUN_FAILURE_CODES:
            raise ValueError(f"unknown error_code={error_code!r}")
        return BacktestRunState(
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
        )
    except (TypeError, ValueError) as error:
        raise ResearchStorageError(
            f"stored backtest run failed integrity validation — run_id={run_id}: {error}"
        ) from error


def _summary(row: sqlite3.Row) -> BacktestRunSummary:
    run = _state(row)
    try:
        return BacktestRunSummary(
            run=run,
            strategy_provenance=StrategyProvenance(
                kind=StrategySourceKind(row["strategy_kind"]),
                spec_hash=row["spec_hash"],
                schema_version=row["schema_version"],
                strategy_id=row["strategy_id"],
                revision=row["revision"],
                source_hash=row["source_hash"],
            ),
            kind=RunKind(row["kind"]),
            experiment_id=row["experiment_id"],
            experiment_paused=bool(row["experiment_paused"]),
        )
    except (TypeError, ValueError) as error:
        raise ResearchStorageError(
            f"stored backtest run failed integrity validation — run_id={run.run_id}: {error}"
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
