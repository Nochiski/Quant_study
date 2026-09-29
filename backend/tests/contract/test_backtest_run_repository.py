"""실행 기록 SQLite adapter 계약(검증 랩 spec D3, V1-03).

파일 claim·판본 대조 규칙은 공용 `sqlite_store` 가 소유한다. 여기서는 연구 기록 파일이 그 규칙을
따르는지와, run 레코드·요청이 파일을 다시 열어도 같은 값으로 돌아오는지를 본다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    ResearchStorageError,
    SQLiteBacktestRunRepository,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunNotFoundError,
    BacktestRunSummary,
    RunKind,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import PageRequest
from strategy_workbench.domain.analytics.facade.metrics import MetricScope
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    BacktestRunState,
    InlineDraft,
    MetricWindow,
    RunStatus,
    SavedRevisionReference,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.strategy.facade.specification import StrategyIdentity

_AT = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
_ENVIRONMENT = RunEnvironment(start=date(2021, 1, 4), end=date(2021, 6, 30), universe_id="u")
# canonical 표기에서 빠지는 저장 정체성이 따로 돌아오는지 보려고 기본값이 아닌 id·revision 을 쓴다.
_SPEC = replace(
    StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "unused").template(),
    identity=StrategyIdentity("draft-7", 3),
)


# 원장 행은 `test_trial_ledger_repository.py` 가 본다. 여기서는 run 기록만 본다.
_NO_LINEAGE = {"lineage_id": None, "trial_key": "d" * 64}


def _summary(run_id: str, *, strategy_id: str | None = None) -> BacktestRunSummary:
    return BacktestRunSummary(
        run=BacktestRunState(
            run_id=run_id,
            status=RunStatus.QUEUED,
            progress=0.0,
            stage="queued",
            message="Run accepted",
            created_at=_AT,
            updated_at=_AT,
        ),
        strategy_provenance=StrategyProvenance(
            kind=(
                StrategySourceKind.INLINE_DRAFT
                if strategy_id is None
                else StrategySourceKind.SAVED_REVISION
            ),
            spec_hash="a" * 64,
            schema_version="1.2",
            strategy_id=strategy_id,
            revision=None if strategy_id is None else 1,
        ),
        kind=RunKind.SINGLE,
    )


_REQUESTS = {
    "saved_revision": BacktestRunSpec(
        strategy_source=SavedRevisionReference("s-1", 1, "a" * 64, "saved_revision"),
        environment=_ENVIRONMENT,
        benchmark_security_id="005930",
        metric_windows=(
            MetricWindow(MetricScope.OUT_OF_SAMPLE, date(2021, 3, 2), date(2021, 6, 30), "OOS"),
        ),
        # 원본 요청의 파라미터 값(검증 랩 V3-02). int·float·bool 이 섞여 있다.
        parameter_values={"n": 20, "w": 1.0, "flag": True},
    ),
    "inline_draft": BacktestRunSpec(
        strategy_source=InlineDraft(_SPEC, "inline_draft", source_hash="b" * 64),
        environment=replace(_ENVIRONMENT, fee_bps=7.5),
    ),
    "legacy_strategy": BacktestRunSpec(strategy=_SPEC, environment=_ENVIRONMENT),
}


@pytest.mark.parametrize("kind", sorted(_REQUESTS))
def test_the_accepted_request_survives_reopening_the_file(kind: str, tmp_path: Path) -> None:
    path = tmp_path / "research.sqlite3"
    SQLiteBacktestRunRepository(path).add(_summary("run-1"), _REQUESTS[kind], **_NO_LINEAGE)

    assert SQLiteBacktestRunRepository(path).request("run-1") == _REQUESTS[kind]


def test_parameter_values_keep_their_types_through_the_file(tmp_path: Path) -> None:
    """`20 == 20.0 == True` 라 요청 `==` 로는 타입이 바뀐 것을 못 본다. 값마다 타입을 대조한다."""
    path = tmp_path / "research.sqlite3"
    SQLiteBacktestRunRepository(path).add(
        _summary("run-1"), _REQUESTS["saved_revision"], **_NO_LINEAGE
    )

    restored = SQLiteBacktestRunRepository(path).request("run-1").parameter_values
    assert {key: (type(value), value) for key, value in restored.items()} == {
        "n": (int, 20),
        "w": (float, 1.0),
        "flag": (bool, True),
    }


def test_states_list_newest_first_filter_by_strategy_and_report_unfinished(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research.sqlite3"
    repository = SQLiteBacktestRunRepository(path)
    for run_id, strategy_id in (("run-1", "s-1"), ("run-2", None), ("run-3", "s-1")):
        repository.add(
            _summary(run_id, strategy_id=strategy_id), _REQUESTS["saved_revision"], **_NO_LINEAGE
        )
    completed = replace(
        _summary("run-3").run,
        status=RunStatus.COMPLETED,
        progress=1.0,
        stage="completed",
        updated_at=datetime(2026, 9, 29, 9, 5, tzinfo=UTC),
        artifact_sha256="c" * 64,
    )
    repository.update(completed)

    reopened = SQLiteBacktestRunRepository(path)
    page = reopened.list(PageRequest(offset=0, limit=2))
    assert [item.run.run_id for item in page.items] == ["run-3", "run-2"]
    assert page.total == 3
    assert page.items[0].run == completed
    assert (
        page.items[0].strategy_provenance
        == _summary("run-3", strategy_id="s-1").strategy_provenance
    )
    filtered = reopened.list(PageRequest(), strategy_id="s-1")
    assert [item.run.run_id for item in filtered.items] == ["run-3", "run-1"]
    assert filtered.total == 2
    assert [state.run_id for state in reopened.unfinished()] == ["run-1", "run-2"]
    # 여러 run 의 상태를 한 번에 읽는다(V3-04). 모르는 run 은 빠진다.
    assert reopened.states(["run-3", "run-1", "missing"]) == {
        "run-3": completed,
        "run-1": _summary("run-1").run,
    }
    assert reopened.states([]) == {}


def test_a_run_an_experiment_attempt_points_at_is_an_experiment_trial(tmp_path: Path) -> None:
    # 실행 종류(검증 랩 V5-03)는 같은 파일의 실험 attempt 가 run 을 가리키는지로 정한다.
    path = tmp_path / "research.sqlite3"
    repository = SQLiteBacktestRunRepository(path)
    for run_id in ("run-1", "run-2"):
        repository.add(_summary(run_id), _REQUESTS["saved_revision"], **_NO_LINEAGE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO experiments (experiment_id, created_at, design_json) VALUES (?, ?, ?)",
            ("e-1", _AT.isoformat(), "{}"),
        )
        connection.execute(
            "INSERT INTO experiment_attempts "
            "(experiment_order, trial_index, attempt, created_at, run_id) VALUES (1, 0, 1, ?, ?)",
            (_AT.isoformat(), "run-2"),
        )

    assert [(item.run.run_id, item.kind) for item in repository.list(PageRequest()).items] == [
        ("run-2", RunKind.EXPERIMENT),
        ("run-1", RunKind.SINGLE),
    ]
    for kind, run_ids in ((RunKind.EXPERIMENT, ["run-2"]), (RunKind.SINGLE, ["run-1"])):
        page = repository.list(PageRequest(), kind=kind)
        assert ([item.run.run_id for item in page.items], page.total) == (run_ids, 1)
    assert repository.get("run-2").kind is RunKind.EXPERIMENT


def test_an_unknown_run_is_not_found(tmp_path: Path) -> None:
    repository = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")

    with pytest.raises(BacktestRunNotFoundError):
        repository.get("missing")
    with pytest.raises(BacktestRunNotFoundError):
        repository.request("missing")
    with pytest.raises(BacktestRunNotFoundError):
        repository.update(_summary("missing").run)


def test_a_stored_failure_code_outside_the_vocabulary_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "research.sqlite3"
    SQLiteBacktestRunRepository(path).add(
        _summary("run-1"), _REQUESTS["saved_revision"], **_NO_LINEAGE
    )
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE backtest_runs SET error_code = 'backtest.run.gone'")

    with pytest.raises(ResearchStorageError, match="run_id=run-1"):
        SQLiteBacktestRunRepository(path).get("run-1")


def test_an_empty_file_is_claimed_once(tmp_path: Path) -> None:
    path = tmp_path / "research.sqlite3"
    path.touch()

    SQLiteBacktestRunRepository(path)
    SQLiteBacktestRunRepository(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone() == (0x53575253,)
        assert connection.execute("PRAGMA user_version").fetchone() == (5,)


@pytest.mark.parametrize(
    ("prepare", "match"),
    [
        # 다른 앱(전략 DB 의 "SWRK")이 claim 한 파일
        ("PRAGMA application_id = 1398231627", "another application"),
        # 아무도 claim 하지 않았지만 비어 있지 않은 파일
        ("CREATE TABLE someone_elses_data (x)", "refusing to claim"),
    ],
)
def test_a_file_owned_by_someone_else_is_refused(prepare: str, match: str, tmp_path: Path) -> None:
    path = tmp_path / "research.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(prepare)

    with pytest.raises(ResearchStorageError, match=match):
        SQLiteBacktestRunRepository(path)


@pytest.mark.parametrize(
    ("tamper", "match"),
    [
        ("PRAGMA user_version = 6", "newer than this server"),
        ("CREATE TABLE stray (x)", "does not match version 5"),
        ("DROP TABLE experiment_controls", "does not match version 5"),
        ("DROP TABLE experiment_window_picks", "does not match version 5"),
    ],
)
def test_a_future_or_edited_schema_is_refused(tamper: str, match: str, tmp_path: Path) -> None:
    path = tmp_path / "research.sqlite3"
    SQLiteBacktestRunRepository(path)
    with sqlite3.connect(path) as connection:
        connection.execute(tamper)

    with pytest.raises(ResearchStorageError, match=match):
        SQLiteBacktestRunRepository(path)
