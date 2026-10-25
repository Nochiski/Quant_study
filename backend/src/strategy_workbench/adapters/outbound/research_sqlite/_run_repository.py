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
    RunHeadline,
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
from strategy_workbench.domain.backtest.facade.trials import (
    BlockedTrialAttempt,
    TrialLedgerEntry,
    lineage_root,
    resolve_lineage,
)

from ._errors import ResearchStorageError
from ._request_codec import RUN_END_PATH, RUN_START_PATH, decode_request, encode_request
from ._schema import SCHEMA_VERSION, migrate_schema

_TERMINAL = tuple(status.value for status in RunStatus if status.is_terminal)
# 앞 둘(run_id·created_at)은 접수 때만 쓰는 식별 칸이다. 상태 전이(`update`)는 그 뒤만 바꾼다.
_SUMMARY_COLUMNS = """
    run_id, created_at, status, progress, stage, message, updated_at, error, error_code,
    artifact_sha256, strategy_kind, spec_hash, schema_version, strategy_id, revision, source_hash
"""
# run 행과 시작 시각(곁 표 `backtest_run_starts`). 읽기는 모두 이 관계를 쓴다.
_RUN_ROWS = """(
    SELECT backtest_runs.*, backtest_run_starts.started_at,
        backtest_run_headlines.accepted_order IS NOT NULL AS has_headline,
        backtest_run_headlines.total_return AS headline_total_return,
        backtest_run_headlines.sharpe AS headline_sharpe,
        backtest_run_headlines.max_drawdown AS headline_max_drawdown
    FROM backtest_runs
    LEFT JOIN backtest_run_starts USING (accepted_order)
    LEFT JOIN backtest_run_headlines USING (accepted_order)
)"""
_STATE_COLUMNS = f"{_SUMMARY_COLUMNS}, started_at"
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
    FROM {_RUN_ROWS} AS backtest_runs
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
_READ_COLUMNS = (
    f"{_STATE_COLUMNS}, kind, experiment_id, experiment_paused, has_headline, "
    "headline_total_return, headline_sharpe, headline_max_drawdown"
)


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
            _write_state(connection, state)

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
        self,
        completed: BacktestRunState,
        *,
        session_sharpe: float | None,
        metric_registry_version: str,
        headline: RunHeadline | None = None,
    ) -> None:
        if completed.status is not RunStatus.COMPLETED:
            raise ValueError(
                "trial result is recorded only with the completed state — "
                f"run_id={completed.run_id} status={completed.status}"
            )
        run_id = completed.run_id
        # 완료 전이·대표 샤프·머리 지표는 한 트랜잭션이다. 완료가 아닌 run 에 샤프·머리 지표가 남지
        # 않는다(#581 리뷰).
        with self._database.transaction(write=True) as connection:
            _write_state(connection, completed)
            updated = connection.execute(
                """
                UPDATE trial_ledger SET session_sharpe = ?, metric_registry_version = ?
                WHERE accepted_order = (SELECT accepted_order FROM backtest_runs WHERE run_id = ?)
                """,
                (session_sharpe, metric_registry_version, run_id),
            )
            if updated.rowcount != 1:
                raise BacktestRunNotFoundError(run_id)
            if headline is not None:
                # 머리 지표는 완료 기록과 같은 트랜잭션에 한 번 적는다. 다시 적으면 처음 값을 둔다.
                connection.execute(
                    """
                    INSERT OR IGNORE INTO backtest_run_headlines
                        (accepted_order, total_return, sharpe, max_drawdown)
                    SELECT accepted_order, ?, ?, ? FROM backtest_runs WHERE run_id = ?
                    """,
                    (headline.total_return, headline.sharpe, headline.max_drawdown, run_id),
                )

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
            resolved = resolve_lineage(merges, lineage_id)
            root, merged = resolved.root, resolved.merged
            members = (root, *merged)
            where = f"lineage_id IN ({', '.join('?' * len(members))})"
            entries = connection.execute(
                f"""
                SELECT run_id, trial_key, status, created_at, updated_at, session_sharpe,
                    metric_registry_version, error_code, started_at,
                    json_extract(request_json, ?) AS run_start,
                    json_extract(request_json, ?) AS run_end
                FROM trial_ledger JOIN {_RUN_ROWS} USING (accepted_order)
                WHERE {where} ORDER BY accepted_order
                """,
                (RUN_START_PATH, RUN_END_PATH, *members),
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
                        error_code=row["error_code"],
                        started_at=_optional_time(row["started_at"]),
                        start=_optional_date(row["run_start"]),
                        end=_optional_date(row["run_end"]),
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
            source_root, target_root = (
                lineage_root(merges, source_id),
                lineage_root(merges, target_id),
            )
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
                SELECT {_STATE_COLUMNS} FROM {_RUN_ROWS}
                WHERE status NOT IN ({", ".join("?" for _ in _TERMINAL)}) ORDER BY accepted_order
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
                f"SELECT {_STATE_COLUMNS} FROM {_RUN_ROWS} "
                f"WHERE run_id IN ({', '.join('?' * len(ids))})",
                ids,
            ).fetchall()
        return {row["run_id"]: _state(row) for row in rows}


def _write_state(connection: sqlite3.Connection, state: BacktestRunState) -> None:
    """상태 전이 하나를 열린 쓰기 트랜잭션에 적는다(`update`·`record_trial_result` 가 함께 쓴다)."""
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
    # 시작 시각은 한 번만 적는다 — 처음 `running` 이 된 전이가 정본이다.
    if state.started_at is not None:
        connection.execute(
            """
            INSERT OR IGNORE INTO backtest_run_starts (accepted_order, started_at)
            SELECT accepted_order, ? FROM backtest_runs WHERE run_id = ?
            """,
            (_time_text(state.started_at, "started_at", state.run_id), state.run_id),
        )


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
            # 거르지 않는다 — 상태가 바뀔 때만 저장해 `RunStage` 어휘 안이다(그 정의의 주석)
            stage=row["stage"],
            message=row["message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            error=row["error"],
            error_code=cast(RunFailureCode | None, error_code),
            artifact_sha256=row["artifact_sha256"],
            started_at=_optional_time(row["started_at"]),
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
            headline=(
                RunHeadline(
                    total_return=row["headline_total_return"],
                    sharpe=row["headline_sharpe"],
                    max_drawdown=row["headline_max_drawdown"],
                )
                if row["has_headline"]
                else None
            ),
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


def _optional_time(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def _optional_date(text: str | None) -> date | None:
    return None if text is None else date.fromisoformat(text)


def _time_text(value: datetime, field: str, run_id: str) -> str:
    return datetime_text(value, field=f"backtest run {field} (run_id={run_id})")
