"""임시 DB의 독립 연결로 멱등 claim·재시작·엄격한 저장 계약을 검증한다."""

import json
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from strategy_workbench.adapters.outbound.continuous_account_sqlite.facade.store import (
    ContinuousAccountSqliteStore,
    ContinuousAccountStoreError,
)
from strategy_workbench.application.continuous_account.facade.evaluation import (
    ContinuousAccountManifest,
    ContinuousAccountNotFoundError,
    ContinuousAccountRejectedError,
    ContinuousAccountResult,
    ContinuousAccountState,
    ContinuousAccountStatus,
    ContinuousCurvePoint,
    ContinuousScheduleProvenance,
)
from strategy_workbench.bootstrap import _http
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.domain.backtest.facade.account_report import AccountReportSummary
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment, environment_hash
from strategy_workbench.domain.backtest.facade.runs import (
    ExecutionCore,
    RawArtifactBundle,
    RawSnapshot,
)
from strategy_workbench.domain.canonical.facade.encoding import canonical_json_spec_hash
from strategy_workbench.domain.experiment.facade.design import WalkForwardWindow
from strategy_workbench.domain.experiment.facade.selection_schedule import (
    SelectionSchedule,
    WindowSelection,
)

NOW = datetime(2026, 10, 17, tzinfo=UTC)


def running(
    evaluation_id: str = "request-a", experiment_id: str = "experiment-a"
) -> ContinuousAccountState:
    return ContinuousAccountState(
        evaluation_id, experiment_id, ContinuousAccountStatus.RUNNING, 0.0, "preparing", NOW
    )


def completed(state: ContinuousAccountState) -> ContinuousAccountState:
    first, last = date(2026, 10, 5), date(2026, 10, 6)
    environment = RunEnvironment(start=first, end=last, universe_id="krx.common-stock")
    schedule = SelectionSchedule(
        selections=(
            WindowSelection(
                window_index=0,
                window=WalkForwardWindow(date(2026, 10, 1), date(2026, 10, 2), first, last),
                candidate_id="candidate",
                known_as_of=date(2026, 10, 2),
            ),
        ),
        sessions=(first, last),
    )
    manifest = ContinuousAccountManifest(
        evaluation_id=state.evaluation_id,
        experiment_id=state.experiment_id,
        created_at=state.created_at,
        engine_version="fixture",
        data_snapshot_id="snapshot",
        factor_evaluation_version="factor-v1",
        environment=environment,
        environment_hash=environment_hash(environment),
        schedule=ContinuousScheduleProvenance.from_schedule(schedule),
        scheduled_tape_hash="tape",
        strategy_id="strategy",
        revision=1,
        source="source",
        choices=(),
        candidates=(),
        initial_cash=1000.0,
        core=ExecutionCore.PYTHON,
        annualization_days=252,
        frame_sources=(),
        metric_registry_version="fixture",
    )
    summary = AccountReportSummary(1000.0, first, last, 100.0, 1100.0, 100.0, 0.1, 2)
    result = ContinuousAccountResult(
        manifest,
        summary,
        (ContinuousCurvePoint(first, 1000.0), ContinuousCurvePoint(last, 1100.0)),
        RawArtifactBundle(
            (
                RawSnapshot(first, 1000.0, 1000.0, 0.0, 0.0),
                RawSnapshot(last, 100.0, 1100.0, 1000.0, 1000.0),
            ),
            (),
            (),
            (),
            (),
            (),
        ),
        0.0,
        0.0,
        (),
        metric_definitions=(),
        metrics=(),
    )
    return replace(
        state,
        status=ContinuousAccountStatus.COMPLETED,
        progress=1.0,
        stage="completed",
        completed_at=NOW + timedelta(seconds=1),
        result=result,
    )


def test_first_query_does_not_create_an_evaluation_and_missing_get_is_explicit() -> None:
    store = ContinuousAccountSqliteStore(None)
    assert store.latest("unused") is None
    with pytest.raises(ContinuousAccountNotFoundError):
        store.get("unused")
    state = running()
    assert store.claim(state)
    assert store.latest(state.experiment_id) == state


def test_reopen_preserves_completed_result_and_duplicate_request_identity(tmp_path: Path) -> None:
    path = tmp_path / "continuous.sqlite3"
    store = ContinuousAccountSqliteStore(path)
    state = running()
    store.claim(state)
    done = completed(state)
    store.save(done)
    store.close()
    reopened = ContinuousAccountSqliteStore(path)
    assert reopened.get(state.evaluation_id) == done
    assert not reopened.claim(state)
    assert reopened.get(state.evaluation_id) == done
    reopened.save(done)
    with pytest.raises(ContinuousAccountStoreError, match="immutable"):
        reopened.save(replace(done, stage="changed"))


def test_duplicate_claim_precedes_global_busy_and_cross_experiment_reuse(tmp_path: Path) -> None:
    store = ContinuousAccountSqliteStore(tmp_path / "store.sqlite3")
    state = running()
    assert store.claim(state)
    assert not store.claim(state)
    with pytest.raises(ContinuousAccountRejectedError) as conflict:
        store.claim(replace(state, experiment_id="different"))
    assert conflict.value.code == "continuous.request_conflict"
    with pytest.raises(ContinuousAccountRejectedError) as busy:
        store.claim(running("new"))
    assert busy.value.code == "continuous.busy"


@pytest.mark.parametrize("same_request", [False, True])
def test_independent_connections_claim_atomically(tmp_path: Path, same_request: bool) -> None:
    path = tmp_path / "race.sqlite3"
    stores = (ContinuousAccountSqliteStore(path), ContinuousAccountSqliteStore(path))
    barrier = Barrier(2)

    def claim(index: int) -> str:
        barrier.wait()
        try:
            inserted = stores[index].claim(running("same" if same_request else str(index)))
            return "inserted" if inserted else "existing"
        except ContinuousAccountRejectedError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(claim, (0, 1)))
    assert outcomes == (
        ["existing", "inserted"] if same_request else ["continuous.busy", "inserted"]
    )


def test_restart_preserves_actual_progress_and_closes_only_running(tmp_path: Path) -> None:
    path = tmp_path / "restart.sqlite3"
    store = ContinuousAccountSqliteStore(path)
    first = running()
    store.claim(first)
    store.save(completed(first))
    active = replace(running("request-b"), progress=0.43, stage="engine")
    store.claim(active)
    store.close()
    reopened = ContinuousAccountSqliteStore(path)
    ended = NOW + timedelta(minutes=2)
    reopened.interrupt_running(ended)
    interrupted = reopened.get(active.evaluation_id)
    assert interrupted.status is ContinuousAccountStatus.INTERRUPTED
    assert interrupted.progress == 0.43
    assert interrupted.created_at == active.created_at
    assert interrupted.completed_at == ended
    assert interrupted.error_code == "continuous.interrupted"
    assert reopened.get(first.evaluation_id).status is ContinuousAccountStatus.COMPLETED
    assert reopened.claim(running("request-c"))


@pytest.mark.parametrize("progress", [float("nan"), float("inf"), -0.1, 1.1])
def test_nonfinite_or_out_of_range_progress_never_enters_storage(progress: float) -> None:
    store = ContinuousAccountSqliteStore(None)
    with pytest.raises(ValueError):
        store.claim(replace(running(), progress=progress))
    assert store.latest("experiment-a") is None


def test_state_transition_rejects_regression_changed_identity_and_missing_result() -> None:
    store = ContinuousAccountSqliteStore(None)
    active = replace(running(), progress=0.5)
    store.claim(active)
    for invalid in (
        replace(active, progress=0.1),
        replace(active, experiment_id="other"),
    ):
        with pytest.raises(ContinuousAccountStoreError):
            store.save(invalid)
    assert store.get(active.evaluation_id) == active
    with pytest.raises(ValueError):
        store.save(
            replace(
                active, status=ContinuousAccountStatus.COMPLETED, completed_at=NOW, progress=1.0
            )
        )
    with pytest.raises(ValueError):
        store.save(replace(active, completed_at=NOW))


def test_foreign_sqlite_file_is_rejected_without_changing_its_owner(tmp_path: Path) -> None:
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA application_id = 123")
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(ContinuousAccountStoreError, match="another application"):
        ContinuousAccountSqliteStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 123
        assert connection.execute("SELECT * FROM unrelated").fetchall() == []


@pytest.mark.parametrize("mutation", ["unknown", "nan", "derived_hash"])
def test_corrupt_json_is_not_silently_repaired(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / "corrupt.sqlite3"
    store = ContinuousAccountSqliteStore(path)
    state = running()
    store.claim(state)
    store.save(completed(state))
    with sqlite3.connect(path) as connection:
        payload = json.loads(
            connection.execute("SELECT payload FROM continuous_evaluations").fetchone()[0]
        )
        if mutation == "unknown":
            payload["invented"] = True
        elif mutation == "nan":
            payload["result"]["total_fees"] = float("nan")
        else:
            payload["result"]["manifest"]["schedule"]["schedule_hash"] = "forged"
        encoded = json.dumps(payload)
        # digest를 함께 바꾸더라도 JSON·파생 신원의 의미 검증은 따로 통과해야 한다.
        connection.execute(
            "UPDATE continuous_evaluations SET payload = ?, payload_sha256 = ?",
            (encoded, canonical_json_spec_hash(encoded)),
        )
    with pytest.raises(ContinuousAccountStoreError, match="decode"):
        store.get(state.evaluation_id)


@pytest.mark.parametrize("mutation", ["equity", "fee", "replacement"])
def test_payload_digest_detects_valid_json_numeric_changes_and_payload_replacement(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "bytes.sqlite3"
    store = ContinuousAccountSqliteStore(path)
    first = running()
    store.claim(first)
    store.save(completed(first))
    other = running("other-request")
    store.claim(other)
    store.save(completed(other))
    with sqlite3.connect(path) as connection:
        first_payload = connection.execute(
            "SELECT payload FROM continuous_evaluations WHERE evaluation_id = ?",
            (first.evaluation_id,),
        ).fetchone()[0]
        data = json.loads(first_payload)
        if mutation == "equity":
            data["result"]["summary"]["final_equity"] += 1
        elif mutation == "fee":
            data["result"]["total_fees"] += 1
        if mutation == "replacement":
            encoded = connection.execute(
                "SELECT payload FROM continuous_evaluations WHERE evaluation_id = ?",
                (other.evaluation_id,),
            ).fetchone()[0]
        else:
            encoded = json.dumps(data)
        connection.execute(
            "UPDATE continuous_evaluations SET payload = ? WHERE evaluation_id = ?",
            (encoded, first.evaluation_id),
        )
    with pytest.raises(ContinuousAccountStoreError, match="digest"):
        store.get(first.evaluation_id)


def test_container_recovers_only_its_explicit_continuous_database(tmp_path: Path) -> None:
    path = tmp_path / "continuous.sqlite3"
    store = ContinuousAccountSqliteStore(path)
    active = replace(running(), created_at=datetime.now(UTC) - timedelta(minutes=1), progress=0.37)
    store.claim(active)
    store.close()
    container = build_container(continuous_db_path=path)
    recovered = container.continuous_accounts.get(active.evaluation_id)
    assert recovered.status is ContinuousAccountStatus.INTERRUPTED
    assert recovered.progress == 0.37
    assert recovered.completed_at is not None
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    independent = build_container()
    with pytest.raises(ContinuousAccountNotFoundError):
        independent.continuous_accounts.get(active.evaluation_id)


def test_runtime_continuous_path_uses_an_independent_environment_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "custom-continuous.sqlite3"
    monkeypatch.setenv(_http.CONTINUOUS_DB_PATH_ENV, str(configured))
    assert _http.runtime_continuous_db_path() == configured
