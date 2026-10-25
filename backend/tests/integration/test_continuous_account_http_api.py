"""실제 mock 조립의 명시 연속 실행·기존 실험 불변·영속 재조회 계약."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.continuous_pipeline.facade.preparation import (
    ContinuousPortfolioPipelineAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import BacktestDataPort
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunResult,
    BacktestRunService,
)
from strategy_workbench.application.continuous_account.facade.evaluation import (
    ContinuousAccountService,
    FrozenExperimentInput,
    FrozenWindowChoice,
)
from strategy_workbench.application.continuous_account.facade.ports import (
    ContinuousEvaluationStorePort,
    ContinuousExecutorPort,
    ContinuousExperimentSourcePort,
    ContinuousPipelinePort,
    PreparedContinuousSignals,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from tests.integration.test_experiment_http_api import _experiment, _wait_until_finished


@dataclass(frozen=True)
class CompletedExperiment:
    client: TestClient
    root: Path
    experiment_id: str
    strategy_id: str


def client_at(root: Path) -> TestClient:
    return TestClient(
        build_http_app(
            strategy_repository_path=root / "strategy.sqlite3",
            research_db_path=root / "research.sqlite3",
            continuous_db_path=root / "continuous.sqlite3",
            artifact_root=root / "artifacts",
        )
    )


@pytest.fixture(scope="module")
def finished_experiment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CompletedExperiment]:
    root = tmp_path_factory.mktemp("continuous-http")
    with client_at(root) as client:
        request = _experiment(client)
        # 기반 실험과 혼합 실행 모두 같은 초기 자본과 비용을 명시한다.
        request["run"]["initial_cash"] = 1_000_000
        created = client.post("/api/v1/experiments", json=request)
        assert created.status_code == 202, created.text
        experiment_id = created.json()["record"]["experiment_id"]
        finished = _wait_until_finished(client, experiment_id)
        assert finished["status"] == "completed"
        yield CompletedExperiment(
            client, root, experiment_id, request["run"]["strategy_source"]["strategy_id"]
        )


def test_ready_queries_do_not_read_completed_artifacts(
    finished_experiment: CompletedExperiment, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = finished_experiment
    reads: list[str] = []

    def unexpected_read(self: BacktestRunService, run_id: str) -> object:
        reads.append(run_id)
        raise AssertionError("readiness must not read full backtest artifacts")

    monkeypatch.setattr(BacktestRunService, "result", unexpected_read)
    for _ in range(3):
        response = fixture.client.get(
            f"/api/v1/experiments/{fixture.experiment_id}/continuous-account"
        )
        assert response.status_code == 200, response.text
        assert response.json()["readiness"]["ready"] is True
    assert reads == []


def test_actual_continuous_execution_is_durable_and_preserves_old_results_and_trials(
    finished_experiment: CompletedExperiment, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = finished_experiment
    client = fixture.client
    path = f"/api/v1/experiments/{fixture.experiment_id}"
    trials_path = f"/api/v1/strategies/{fixture.strategy_id}/trials"
    before_trials = client.get(trials_path).json()
    before_walk = client.get(f"{path}/walk-forward").json()
    selected_runs = {window["pick"]["run_id"] for window in before_walk["windows"]}
    before_results = {
        run_id: client.get(f"/api/v1/backtests/{run_id}/result").json() for run_id in selected_runs
    }
    source_reads: list[str] = []
    prepared_ids: list[str] = []
    original = BacktestRunService.result
    original_prepare = ContinuousPortfolioPipelineAdapter.prepare

    def checked_result(self: BacktestRunService, run_id: str) -> BacktestRunResult:
        source_reads.append(run_id)
        return original(self, run_id)

    def checked_prepare(
        self: ContinuousPortfolioPipelineAdapter,
        source: FrozenExperimentInput,
        choice: FrozenWindowChoice,
        environment: RunEnvironment,
        *,
        progress: Callable[[float, str], None],
    ) -> PreparedContinuousSignals:
        prepared = original_prepare(self, source, choice, environment, progress=progress)
        prepared_ids.append(prepared.candidate.candidate_id)
        return prepared

    monkeypatch.setattr(BacktestRunService, "result", checked_result)
    monkeypatch.setattr(ContinuousPortfolioPipelineAdapter, "prepare", checked_prepare)
    evaluation_id = str(uuid4())
    response = client.post(f"{path}/continuous-account", json={"client_request_id": evaluation_id})
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["status"] == "completed", state
    assert state["progress"] == 1.0
    assert state["completed_at"] is not None
    assert state["evaluation_id"] == evaluation_id
    # 각 창의 선택된 학습·검증 결과를 명시 실행에서 한 번씩 검증한다.
    assert len(source_reads) == 2 * len(before_walk["windows"])
    assert len(source_reads) == len(set(source_reads))
    result = state["result"]
    summary = result["summary"]
    manifest = result["manifest"]
    snapshots = result["artifacts"]["snapshots"]
    assert summary["initial_cash"] == 1_000_000
    assert summary["snapshot_count"] == len(snapshots)
    assert summary["final_equity"] == snapshots[-1]["equity"]
    assert summary["final_cash"] == snapshots[-1]["cash"]
    assert summary["pnl"] == pytest.approx(summary["final_equity"] - summary["initial_cash"])
    assert summary["total_return"] == pytest.approx(
        summary["final_equity"] / summary["initial_cash"] - 1
    )
    assert result["curve"] == [
        {"session": snapshot["session"], "equity": snapshot["equity"]} for snapshot in snapshots
    ]
    assert result["artifacts"]["fills"]
    assert result["total_fees"] == pytest.approx(
        sum(fill["fee"] for fill in result["artifacts"]["fills"])
    )
    assert {metric["metric_id"] for metric in result["metrics"]} >= {"sharpe", "max_drawdown"}
    assert manifest["evaluation_basis"] == "continuous_account"
    assert manifest["core"] == "python"
    assert manifest["annualization_days"] == 252
    assert manifest["selection_time_policy"] == "assumed_train_end_close"
    assert len(manifest["evaluation_fingerprint"]) == 64
    assert manifest["frame_sources"]
    candidate_ids = {candidate["candidate_id"] for candidate in manifest["candidates"]}
    assert len(prepared_ids) == len(candidate_ids)
    assert set(prepared_ids) == candidate_ids
    for source in manifest["frame_sources"]:
        assert source["candidate_id"] in candidate_ids
        assert source["signal_as_of"] < source["execution_on"]
        assert source["selection_window_index"] <= source["execution_window_index"]
    assert client.get(f"/api/v1/continuous-accounts/{evaluation_id}").json() == state
    assert client.get(f"{path}/continuous-account").json()["evaluation"] == state
    source_reads.clear()
    prepared_ids.clear()
    replay = client.post(f"{path}/continuous-account", json={"client_request_id": evaluation_id})
    assert replay.json() == state
    assert source_reads == []
    assert prepared_ids == []
    assert client.get(trials_path).json() == before_trials
    assert client.get(f"{path}/walk-forward").json() == before_walk
    for run_id, before in before_results.items():
        assert client.get(f"/api/v1/backtests/{run_id}/result").json() == before
    # 새 컨테이너와 SQLite 연결에서 완료 결과를 재해석하거나 재실행하지 않는다.
    with client_at(fixture.root) as reopened:
        assert reopened.get(f"/api/v1/continuous-accounts/{evaluation_id}").json() == state
        assert (
            reopened.post(
                f"{path}/continuous-account", json={"client_request_id": evaluation_id}
            ).json()
            == state
        )
        conflict = reopened.post(
            "/api/v1/experiments/another/continuous-account",
            json={"client_request_id": evaluation_id},
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "continuous.request_conflict"


def test_unknown_entities_and_invalid_uuid_are_explicit(
    finished_experiment: CompletedExperiment,
) -> None:
    client = finished_experiment.client
    for path in (
        "/api/v1/experiments/unknown/continuous-account",
        "/api/v1/continuous-accounts/unknown",
    ):
        response = client.get(path)
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "continuous.not_found"
    missing = client.post(
        "/api/v1/experiments/unknown/continuous-account", json={"client_request_id": str(uuid4())}
    )
    assert missing.status_code == 404, missing.text
    invalid = client.post(
        f"/api/v1/experiments/{finished_experiment.experiment_id}/continuous-account",
        json={"client_request_id": "not-a-uuid"},
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["detail"]["code"] == "continuous.request_invalid"
    missing_body = client.post(
        f"/api/v1/experiments/{finished_experiment.experiment_id}/continuous-account", json={}
    )
    assert missing_body.status_code == 422, missing_body.text
    assert missing_body.json()["detail"]["code"] == "continuous.request_invalid"


def test_corrupt_completed_payload_is_a_typed_410_without_local_paths(
    finished_experiment: CompletedExperiment, tmp_path: Path
) -> None:
    fixture = finished_experiment
    seeded = fixture.client.post(
        f"/api/v1/experiments/{fixture.experiment_id}/continuous-account",
        json={"client_request_id": str(uuid4())},
    )
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["status"] == "completed", seeded.text
    seeded_id = seeded.json()["evaluation_id"]
    # 테스트가 만든 원장의 백업만 손상시켜 다른 통합 사례의 완료 결과를 보존한다.
    copied = tmp_path / "copied.sqlite3"
    with sqlite3.connect(fixture.root / "continuous.sqlite3") as source:
        with sqlite3.connect(copied) as destination:
            source.backup(destination)
    with sqlite3.connect(copied) as connection:
        row = connection.execute(
            "SELECT evaluation_id, payload FROM continuous_evaluations WHERE evaluation_id = ?",
            (seeded_id,),
        ).fetchone()
        assert row is not None
        evaluation_id, encoded = row
        payload = json.loads(encoded)
        payload["result"]["total_fees"] += 1
        connection.execute(
            "UPDATE continuous_evaluations SET payload = ? WHERE evaluation_id = ?",
            (json.dumps(payload), evaluation_id),
        )
    with TestClient(build_http_app(continuous_db_path=copied)) as client:
        responses = (
            client.get(f"/api/v1/continuous-accounts/{evaluation_id}"),
            client.post(
                f"/api/v1/experiments/{fixture.experiment_id}/continuous-account",
                json={"client_request_id": evaluation_id},
            ),
        )
        for response in responses:
            assert response.status_code == 410, response.text
            assert response.json()["detail"]["code"] == "continuous.result_unreadable"
            assert str(tmp_path) not in response.text
            assert str(fixture.root) not in response.text


def test_no_cell_and_capacity_experiments_are_rejected_before_continuous_execution(
    finished_experiment: CompletedExperiment,
) -> None:
    fixture = finished_experiment
    client = fixture.client
    request = _experiment(client)
    # 유효한 목표는 만들되 참여 한도를 0주로 내려 현금 곡선·샤프 미정의 창을 재현한다.
    request["run"]["environment"]["participation_rate"] = 1e-12
    created = client.post("/api/v1/experiments", json=request)
    assert created.status_code == 202, created.text
    no_cell_id = created.json()["record"]["experiment_id"]
    _wait_until_finished(client, no_cell_id)
    path = f"/api/v1/experiments/{no_cell_id}/continuous-account"
    latest = client.get(path)
    assert latest.status_code == 200, latest.text
    assert latest.json()["readiness"]["code"] == "continuous.no_cell"
    assert latest.json()["evaluation"] is None
    rejected = client.post(path, json={"client_request_id": str(uuid4())})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "continuous.no_cell"
    base_run = client.get(f"/api/v1/experiments/{fixture.experiment_id}").json()["record"]["run"]
    capacity = client.post(
        "/api/v1/experiments/capacity", json={"run": base_run, "initial_cash": [1e6, 2e6, 3e6]}
    )
    assert capacity.status_code == 202, capacity.text
    capacity_id = capacity.json()["record"]["experiment_id"]
    refused = client.post(
        f"/api/v1/experiments/{capacity_id}/continuous-account",
        json={"client_request_id": str(uuid4())},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "continuous.unsupported"
    client.post(f"/api/v1/experiments/{capacity_id}/cancel")
    _wait_until_finished(client, capacity_id)


def test_unfinished_experiment_is_rejected_without_creating_an_evaluation() -> None:
    with TestClient(build_http_app(trial_hold_seconds=2)) as client:
        request = _experiment(client)
        created = client.post("/api/v1/experiments", json=request)
        assert created.status_code == 202, created.text
        experiment_id = created.json()["record"]["experiment_id"]
        client.patch(f"/api/v1/experiments/{experiment_id}/controls", json={"paused": True})
        path = f"/api/v1/experiments/{experiment_id}/continuous-account"
        latest = client.get(path)
        assert latest.json()["readiness"]["code"] == "continuous.unfinished"
        assert latest.json()["evaluation"] is None
        rejected = client.post(path, json={"client_request_id": str(uuid4())})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "continuous.unfinished"
        client.post(f"/api/v1/experiments/{experiment_id}/cancel")
        _wait_until_finished(client, experiment_id)


def test_nonmock_mode_is_blocked_before_source_or_data_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ContinuousAccountService.__init__

    def nonmock_init(
        self: ContinuousAccountService,
        source: ContinuousExperimentSourcePort,
        pipeline: ContinuousPipelinePort,
        data: BacktestDataPort,
        executor: ContinuousExecutorPort,
        store: ContinuousEvaluationStorePort,
        *,
        data_mode: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        # 실제 어댑터는 mock으로 조립하고 기능의 mode guard만 주입해 실자료를 열지 않는다.
        original(self, source, pipeline, data, executor, store, data_mode="duckdb", now=now)

    monkeypatch.setattr(ContinuousAccountService, "__init__", nonmock_init)
    with TestClient(build_http_app()) as client:
        path = "/api/v1/experiments/never-loaded/continuous-account"
        latest = client.get(path)
        assert latest.status_code == 200, latest.text
        assert latest.json()["readiness"]["code"] == "continuous.mock_only"
        response = client.post(path, json={"client_request_id": str(uuid4())})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "continuous.mock_only"
