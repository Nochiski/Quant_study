"""연구 DB 판본 2 — 시도 원장·계열 합치기·봉인 원장 차단 기록(검증 랩 spec D2·D11, V1-05)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.research_sqlite._schema import V1_SCHEMA_OBJECTS
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    TrialLineageAlreadyMergedError,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunNotFoundError,
    BacktestRunSpec,
    BacktestRunState,
    BacktestRunSummary,
    RunKind,
    RunStatus,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.backtest.facade.trials import BlockedTrialAttempt

_AT = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)


def _add(repository: SQLiteBacktestRunRepository, run_id: str, lineage_id: str | None) -> None:
    repository.add(
        BacktestRunSummary(
            BacktestRunState(run_id, RunStatus.QUEUED, 0.0, "queued", "Run accepted", _AT, _AT),
            StrategyProvenance(StrategySourceKind.INLINE_DRAFT, "a" * 64, "1.2"),
            RunKind.SINGLE,
        ),
        BacktestRunSpec(),
        lineage_id=lineage_id,
        trial_key=run_id[-1] * 64,
    )


def test_a_version_1_file_is_upgraded_and_its_old_runs_stay_outside_every_lineage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research.sqlite3"
    with sqlite3.connect(path) as connection:
        for _object_type, _name, statement in V1_SCHEMA_OBJECTS:
            connection.execute(statement)
        connection.execute("PRAGMA application_id = 1398231635")
        connection.execute("PRAGMA user_version = 1")

    repository = SQLiteBacktestRunRepository(path)
    _add(repository, "run-1", "s-1")

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (5,)
    assert [entry.run_id for entry in repository.trial_ledger("s-1").entries] == ["run-1"]


def test_merges_follow_the_surviving_lineage_and_keep_every_member(tmp_path: Path) -> None:
    repository = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")
    for run_id, lineage_id in (("run-a", "a"), ("run-b", "b"), ("run-c", "c"), ("run-x", None)):
        _add(repository, run_id, lineage_id)
    blocked = BlockedTrialAttempt(_AT, "a", "e" * 64, "f" * 64, date(2019, 6, 3))
    repository.record_blocked_attempt(blocked)

    repository.merge_lineages("a", "b", merged_at=_AT)
    repository.merge_lineages("b", "c", merged_at=_AT)
    records = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3").trial_ledger("a")

    assert (records.lineage_id, records.merged_lineage_ids) == ("c", ("a", "b"))
    assert [entry.run_id for entry in records.entries] == ["run-a", "run-b", "run-c"]
    assert records.blocked == (blocked,)
    with pytest.raises(TrialLineageAlreadyMergedError, match="lineage_id=c"):
        repository.merge_lineages("a", "c", merged_at=_AT)


def test_merging_an_already_merged_lineage_again_links_the_surviving_lineages(
    tmp_path: Path,
) -> None:
    repository = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")
    for run_id, lineage_id in (("run-a", "a"), ("run-b", "b"), ("run-c", "c")):
        _add(repository, run_id, lineage_id)

    repository.merge_lineages("a", "b", merged_at=_AT)
    # `a` 는 이미 `b` 에 합쳐졌다. 다시 합치면 남은 계열 `b` 가 `c` 로 이어진다(`a` 를 두 번
    # 적지 않아 UNIQUE 위반이 없다).
    repository.merge_lineages("a", "c", merged_at=_AT)

    for asked in ("a", "b", "c"):
        records = repository.trial_ledger(asked)
        assert (records.lineage_id, records.merged_lineage_ids) == ("c", ("a", "b"))
    assert [entry.run_id for entry in records.entries] == ["run-a", "run-b", "run-c"]


def test_a_trial_result_for_an_unknown_run_is_refused(tmp_path: Path) -> None:
    repository = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")

    with pytest.raises(BacktestRunNotFoundError):
        repository.record_trial_result(
            "missing", session_sharpe=0.1, metric_registry_version="metric-registry-v4"
        )
