from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._backtest_contract import Backtest422Response
from strategy_workbench.adapters.inbound.http_api.facade.api import create_app
from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    ArtifactCommit,
    BacktestDataPort,
)
from strategy_workbench.application.backtest_run.facade.runs import BacktestRunService
from strategy_workbench.application.portfolio_design.facade.design import (
    EngineCapabilityIssue,
    EngineCompatibility,
    EngineRequirementSummary,
    PortfolioDesignService,
    RawObservationUnavailableError,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.bootstrap.facade.container import BackendContainer, build_container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult
from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry
from tests.backtest_run_wait import wait_for_terminal_state


class _CommitBarrierStore:
    """Test adapter that pauses one real local commit at the cancellation race boundary."""

    def __init__(self, delegate: LocalArtifactStore, blocked_run_id: str) -> None:
        self._delegate = delegate
        self._blocked_run_id = blocked_run_id
        self.entered = Event()
        self.release = Event()
        self.discarded: list[str] = []

    def commit(self, result: BacktestRunResult) -> ArtifactCommit:
        if result.manifest.run_id == self._blocked_run_id:
            self.entered.set()
            if not self.release.wait(timeout=30):
                raise TimeoutError("artifact commit test barrier was not released")
        return self._delegate.commit(result)

    def discard(self, run_id: str) -> None:
        self.discarded.append(run_id)
        self._delegate.discard(run_id)


class _RawLoadBarrierPort:
    """tape 단계의 원시 관측 로딩 안에서 멈추는 테스트용 관측 포트.

    실제 mock 어댑터에 위임하되, 로딩 진입 시점에 `entered` 를 올리고 `release` 까지 기다린다.
    해제 뒤 어댑터의 checkpoint 가 취소 플래그를 보므로 "로딩 도중 취소" 경로를 그대로 탄다.
    `failure` 가 있으면 로딩 대신 그 예외를 던진다(데이터 부재 경로).
    """

    def __init__(
        self,
        delegate: MockEquityDataAdapter,
        *,
        failure: Exception | None = None,
    ) -> None:
        self._delegate = delegate
        self._failure = failure
        self.entered = Event()
        self.release = Event()
        self.loaded = False

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        return self.load_raw_observations_cancellable(query, checkpoint=lambda: None)

    def load_raw_observations_cancellable(
        self,
        query: RawObservationQuery,
        *,
        checkpoint: Callable[[], None],
    ) -> RawObservationSet:
        self.entered.set()
        if not self.release.wait(timeout=30):
            raise TimeoutError("raw observation test barrier was not released")
        if self._failure is not None:
            raise self._failure
        result = self._delegate.load_raw_observations_cancellable(query, checkpoint=checkpoint)
        self.loaded = True
        return result


def _backtests_with_raw_load_barrier(
    container: BackendContainer,
    tmp_path: Path,
    run_id: str,
    *,
    failure: Exception | None = None,
) -> tuple[BacktestRunService, _RawLoadBarrierPort]:
    adapter = cast(MockEquityDataAdapter, container.equity_data)
    barrier = _RawLoadBarrierPort(adapter, failure=failure)
    portfolio_design = PortfolioDesignService(
        barrier,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version=build_default_factor_registry().version,
    )
    backtests = BacktestRunService(
        portfolio_design,
        container.strategy_repository,
        cast(BacktestDataPort, container.equity_data),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path / "artifacts"),
        new_id=lambda: run_id,
    )
    return backtests, barrier


def _app_with_backtests(container: BackendContainer, backtests: BacktestRunService) -> FastAPI:
    return create_app(
        strategy_design=container.strategy_design,
        strategy_authoring=container.strategy_authoring,
        strategy_documents=container.strategy_documents,
        strategy_drafts=container.strategy_drafts,
        equity_workspace=container.equity_workspace,
        factor_research=container.factor_research,
        portfolio_design=container.portfolio_design,
        strategy_traces=container.strategy_traces,
        backtest_runs=backtests,
        # TestClient는 브라우저가 아니라 CORS를 거치지 않는다 — 허용할 origin이 없다.
        allowed_origins=(),
    )


def _environment(**overrides: Any) -> dict[str, Any]:
    """실행 설정은 1.2 부터 요청 본문이 싣는다(P2-03)."""
    return {
        "start": "2026-01-02",
        "end": "2026-02-20",
        "universe_id": "krx.common-stock",
        **overrides,
    }


def _run_body(client: TestClient, core: str = "rust") -> dict[str, Any]:
    spec = client.get("/api/v1/strategies/template").json()
    spec["portfolio"].update(
        {
            "selection_count": 2,
            "rebalance": "every_n_sessions",
            "rebalance_every_n_sessions": 5,
        }
    )
    spec["risk"].update({"max_name_weight": 0.6, "max_sector_weight": 1.0})
    return {
        "strategy": spec,
        "core": core,
        "environment": _environment(),
        "benchmark_security_id": "005930",
        "metric_windows": [
            {
                "scope": "out_of_sample",
                "start": "2026-02-02",
                "end": "2026-02-20",
                "label": "OOS",
            }
        ],
    }


def _wait(client: TestClient, run_id: str) -> dict[str, Any]:
    return wait_for_terminal_state(client, run_id)


def _execute(client: TestClient, core: str) -> dict[str, Any]:
    accepted = client.post("/api/v1/backtests", json=_run_body(client, core))
    assert accepted.status_code == 202
    run_id = accepted.json()["run"]["run_id"]
    state = _wait(client, run_id)
    assert state["status"] == "completed", state
    response = client.get(f"/api/v1/backtests/{run_id}/result")
    assert response.status_code == 200
    return response.json()


def test_backtest_lifecycle_exposes_progress_result_manifest_and_raw_artifacts() -> None:
    client = TestClient(build_http_app())
    accepted = client.post("/api/v1/backtests", json=_run_body(client))
    assert accepted.status_code == 202
    run_id = accepted.json()["run"]["run_id"]

    accepted_request = client.get(f"/api/v1/backtests/{run_id}/request")
    assert accepted_request.status_code == 200
    assert accepted_request.json() == {
        **_run_body(client),
        # 응답은 해소된 실행 설정을 전부 채워 돌려준다(요청은 기본값을 생략했다).
        "environment": {
            "market": "KRX",
            "frequency": "daily",
            "start": "2026-01-02",
            "end": "2026-02-20",
            "universe_id": "krx.common-stock",
            "timing": "next_open",
            "participation_rate": 0.1,
            "fee_bps": 15.0,
            "slippage_bps": 10.0,
            "missing": "drop",
        },
        "annualization_days": 252,
        "initial_cash": 100_000_000.0,
        "strategy_source": None,
    }

    not_ready = client.get(f"/api/v1/backtests/{run_id}/result")
    assert not_ready.status_code in {200, 409}
    state = _wait(client, run_id)

    assert state["status"] == "completed", state
    assert len(state["artifact_sha256"]) == 64
    assert state["artifact_uri"].startswith("file:///")
    result = client.get(f"/api/v1/backtests/{run_id}/result").json()
    assert result["manifest"]["engine_core"] == "rust"
    assert len(result["manifest"]["run_fingerprint"]) == 64
    assert result["manifest"]["run_spec"]["strategy"] == _run_body(client)["strategy"]
    assert result["manifest"]["run_spec"]["benchmark_security_id"] == "005930"
    assert result["manifest"]["metric_registry_version"] == "metric-registry-v1"
    assert {item["code"] for item in result["manifest"]["warnings"]} == {
        "corporate_action_feed_empty",
        "mock_equity_data",
    }
    assert len(result["metric_definitions"]) == 21
    assert len(result["metrics"]) == 42
    assert {item["scope"] for item in result["metrics"]} == {
        "full",
        "out_of_sample",
    }
    assert result["series"]["equity"]
    assert result["series"]["drawdown"]
    assert result["series"]["monthly_returns"]
    assert result["series"]["rolling_sharpe"]
    assert result["artifacts"]["snapshots"]
    assert result["artifacts"]["orders"]
    assert result["artifacts"]["fills"]

    events = client.get(f"/api/v1/backtests/{run_id}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert '"status":"completed"' in events.text


def test_cancel_accepted_during_artifact_commit_wins_and_exact_request_replays(
    tmp_path: Path,
) -> None:
    first_run_id = "run-cancel-during-artifact"
    replay_run_id = "run-byte-exact-replay"
    run_ids = iter((first_run_id, replay_run_id))
    container = build_container(artifact_root=tmp_path / "unused")
    artifact_root = tmp_path / "artifacts"
    barrier = _CommitBarrierStore(LocalArtifactStore(artifact_root), first_run_id)
    backtests = BacktestRunService(
        container.portfolio_design,
        container.strategy_repository,
        cast(BacktestDataPort, container.equity_data),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        barrier,
        new_id=lambda: next(run_ids),
    )
    client = TestClient(_app_with_backtests(container, backtests))
    submitted = _run_body(client, "python")
    accepted = client.post("/api/v1/backtests", json=submitted)
    assert accepted.status_code == 202
    assert accepted.json()["run"]["run_id"] == first_run_id
    assert barrier.entered.wait(timeout=30), "run never entered artifact commit"

    accepted_request = client.get(f"/api/v1/backtests/{first_run_id}/request")
    assert accepted_request.status_code == 200
    try:
        cancellation = client.post(f"/api/v1/backtests/{first_run_id}/cancel")
        assert cancellation.status_code == 200
        assert cancellation.json()["status"] == "cancel_requested"
    finally:
        barrier.release.set()

    state = _wait(client, first_run_id)
    assert state["status"] == "cancelled"
    assert state["artifact_uri"] is None
    assert state["artifact_sha256"] is None
    assert barrier.discarded == [first_run_id]
    assert not (artifact_root / first_run_id).exists()
    not_ready = client.get(f"/api/v1/backtests/{first_run_id}/result")
    assert not_ready.status_code == 409
    assert not_ready.json()["detail"]["code"] == "backtest.result.not_ready"
    events = client.get(f"/api/v1/backtests/{first_run_id}/events").text
    assert '"status":"cancel_requested"' in events
    assert '"status":"cancelled"' in events
    assert '"status":"completed"' not in events

    # Reuse the server-serialized accepted request bytes, not the original client draft.
    replay = client.post(
        "/api/v1/backtests",
        content=accepted_request.content,
        headers={"content-type": "application/json"},
    )
    assert replay.status_code == 202
    assert replay.json()["run"]["run_id"] == replay_run_id
    replay_request = client.get(f"/api/v1/backtests/{replay_run_id}/request")
    assert replay_request.status_code == 200
    assert replay_request.content == accepted_request.content
    replay_state = _wait(client, replay_run_id)
    assert replay_state["status"] == "completed", replay_state
    replay_result = client.get(f"/api/v1/backtests/{replay_run_id}/result")
    assert replay_result.status_code == 200
    # 접수된 요청은 그대로 다시 제출할 수 있는 원본이라 `environment` 가 None 으로 남고,
    # 매니페스트의 run spec 은 브리지로 해소한 실행 설정을 담는다(P2-01).
    manifest_run_spec = replay_result.json()["manifest"]["run_spec"]
    assert manifest_run_spec["environment"] == replay_result.json()["manifest"]["environment"]
    assert {k: v for k, v in manifest_run_spec.items() if k != "environment"} == {
        k: v for k, v in accepted_request.json().items() if k != "environment"
    }


def test_start_accepts_the_run_before_raw_observations_are_loaded(tmp_path: Path) -> None:
    """이슈 #158: 시작 요청은 관측 로딩·TargetTape 계산을 기다리지 않고 202 `queued` 를 돌려준다.

    누군가 start() 에 preview/run_pipeline 을 되돌려 넣으면 POST 가 배리어에 막혀 이 테스트가
    30초 타임아웃으로 실패한다.
    """
    run_id = "run-accepted-before-tape"
    container = build_container(artifact_root=tmp_path / "unused")
    backtests, barrier = _backtests_with_raw_load_barrier(container, tmp_path, run_id)
    client = TestClient(_app_with_backtests(container, backtests))

    accepted = client.post("/api/v1/backtests", json=_run_body(client, "python"))

    try:
        assert accepted.status_code == 202
        assert accepted.json()["run"]["run_id"] == run_id
        assert accepted.json()["run"]["status"] == "queued"
        assert barrier.entered.wait(timeout=30), "run never entered raw observation loading"
        assert barrier.loaded is False
        # 배리어는 tape 단계의 `_update` 뒤(run_pipeline 안)에서 잡히므로 여기서는 반드시 tape 다.
        in_tape = client.get(f"/api/v1/backtests/{run_id}").json()
        assert in_tape["status"] == "running"
        assert in_tape["stage"] == "tape"
        not_ready = client.get(f"/api/v1/backtests/{run_id}/result")
        assert not_ready.status_code == 409
    finally:
        barrier.release.set()
    state = _wait(client, run_id)
    assert state["status"] == "completed", state
    assert barrier.loaded is True
    events = client.get(f"/api/v1/backtests/{run_id}/events").text
    assert '"stage":"tape"' in events
    assert '"stage":"data"' in events
    assert events.index('"stage":"tape"') < events.index('"stage":"data"')


def test_cancel_during_raw_observation_loading_ends_cancelled_without_a_tape(
    tmp_path: Path,
) -> None:
    """이슈 #158: 관측 로딩 도중 취소하면 어댑터 checkpoint 가 멈추고 `cancelled` 로 끝난다."""
    run_id = "run-cancel-during-tape"
    container = build_container(artifact_root=tmp_path / "unused")
    backtests, barrier = _backtests_with_raw_load_barrier(container, tmp_path, run_id)
    client = TestClient(_app_with_backtests(container, backtests))

    accepted = client.post("/api/v1/backtests", json=_run_body(client, "python"))
    assert accepted.status_code == 202
    assert barrier.entered.wait(timeout=30), "run never entered raw observation loading"
    try:
        cancellation = client.post(f"/api/v1/backtests/{run_id}/cancel")
        assert cancellation.status_code == 200
        assert cancellation.json()["status"] == "cancel_requested"
    finally:
        barrier.release.set()

    state = _wait(client, run_id)
    assert state["status"] == "cancelled", state
    assert state["error"] is None
    assert state["error_code"] is None
    assert state["artifact_uri"] is None
    # 취소는 mock 어댑터의 로딩 checkpoint 에서 관측되므로 로딩이 끝까지 가지 않는다.
    assert barrier.loaded is False
    events = client.get(f"/api/v1/backtests/{run_id}/events").text
    assert '"status":"cancel_requested"' in events
    assert '"status":"cancelled"' in events
    assert '"status":"completed"' not in events
    assert '"stage":"data"' not in events


class _ReportingBarrierPort:
    """진행 보고 능력만으로 로딩하는 테스트 포트(리뷰 P3-4).

    배리어에서 멈췄다 풀리면 checkpoint 없이 진행을 먼저 보고한다. 그래서 취소가 진행 콜백의
    `_update` 에서 처음 관측되는 경로를 탄다. 관측은 mock 어댑터에 위임한다.
    """

    def __init__(self, delegate: MockEquityDataAdapter) -> None:
        self._delegate = delegate
        self.entered = Event()
        self.release = Event()
        self.loaded = False

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        return self._delegate.load_raw_observations(query)

    def load_raw_observations_reporting(
        self,
        query: RawObservationQuery,
        *,
        checkpoint: Callable[[], None],
        progress: Callable[[float], None],
    ) -> RawObservationSet:
        self.entered.set()
        if not self.release.wait(timeout=30):
            raise TimeoutError("reporting raw load test barrier was not released")
        progress(0.6)
        result = self._delegate.load_raw_observations(query)
        self.loaded = True
        progress(1.0)
        return result


def test_cancel_first_observed_by_a_progress_callback_ends_cancelled(tmp_path: Path) -> None:
    """이슈 #162 리뷰 P3-4: 진행 콜백이 취소의 첫 관측 지점이어도 `failed` 가 아니라 `cancelled`.

    진행 보고 `_update` 가 던지는 `RunCancelledError` 를 어댑터·평가기·파이프라인이 삼키거나
    다른 예외로 바꾸면 이 테스트가 잡는다. 취소 수락 뒤에는 `running` 이벤트가 끼지 않는다.
    """
    run_id = "run-cancel-in-progress-callback"
    container = build_container(artifact_root=tmp_path / "unused")
    adapter = cast(MockEquityDataAdapter, container.equity_data)
    port = _ReportingBarrierPort(adapter)
    backtests = BacktestRunService(
        PortfolioDesignService(
            port,
            BacktestEnginePortfolioAdapter(),
            factor_metadata=adapter,
            factor_registry_version=build_default_factor_registry().version,
        ),
        container.strategy_repository,
        cast(BacktestDataPort, container.equity_data),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path / "artifacts"),
        new_id=lambda: run_id,
    )
    client = TestClient(_app_with_backtests(container, backtests))

    accepted = client.post("/api/v1/backtests", json=_run_body(client, "python"))
    assert accepted.status_code == 202
    assert port.entered.wait(timeout=30), "run never entered the reporting raw load"
    try:
        cancellation = client.post(f"/api/v1/backtests/{run_id}/cancel")
        assert cancellation.json()["status"] == "cancel_requested"
    finally:
        port.release.set()

    state = _wait(client, run_id)
    assert state["status"] == "cancelled", state
    assert state["error"] is None
    assert state["error_code"] is None
    assert port.loaded is False
    events = [
        json.loads(line.removeprefix("data: "))
        for line in client.get(f"/api/v1/backtests/{run_id}/events").text.splitlines()
        if line.startswith("data: ")
    ]
    statuses = [event["status"] for event in events]
    requested = statuses.index("cancel_requested")
    assert "running" not in statuses[requested:], statuses
    assert statuses[-1] == "cancelled"


def test_data_failure_in_the_tape_stage_is_coded_and_hides_server_paths(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """이슈 #158: 데이터 의존 실패는 run `failed` + `error_code` 로 오고, 절대 경로는 가린다.

    원문과 stack trace 는 서버 로그(ERROR)에 남는다 — HTTP 500 이 없으므로 유일한 진단 채널이다.
    """
    caplog.set_level("ERROR", logger="strategy_workbench.application.backtest_run._service")
    run_id = "run-data-unavailable"
    container = build_container(artifact_root=tmp_path / "unused")
    failure = RawObservationUnavailableError(
        DataLoadStatus.NO_DATA,
        "no members in universe — universe_id=krx.common-stok "
        "root=C:\\Users\\someone\\quant-ledger\\data\\equity",
    )
    backtests, barrier = _backtests_with_raw_load_barrier(
        container, tmp_path, run_id, failure=failure
    )
    client = TestClient(_app_with_backtests(container, backtests))

    accepted = client.post("/api/v1/backtests", json=_run_body(client, "python"))
    barrier.release.set()  # 단언보다 먼저 해제 — 실패해도 run 스레드가 30초를 태우지 않게
    assert accepted.status_code == 202, accepted.text

    state = _wait(client, run_id)
    assert state["status"] == "failed", state
    assert state["error_code"] == "portfolio.data.unavailable"
    assert "universe_id=krx.common-stok" in state["error"]
    assert "status=no_data" in state["error"]
    assert "Users" not in state["error"] and "quant-ledger" not in state["error"]
    assert "root=<path>" in state["error"]
    failure_logs = [r for r in caplog.records if "backtest run failed" in r.getMessage()]
    assert len(failure_logs) == 1, caplog.text
    assert failure_logs[0].exc_info is not None
    assert failure_logs[0].exc_info[0] is RawObservationUnavailableError
    assert "quant-ledger" in caplog.text  # 원문 경로는 로그에만 남는다


def test_failure_that_races_a_cancel_keeps_its_reason(tmp_path: Path) -> None:
    """tape 단계 실패와 취소가 겹쳐도 사유(`error`·`error_code`)는 버리지 않는다."""
    run_id = "run-failure-races-cancel"
    container = build_container(artifact_root=tmp_path / "unused")
    failure = RawObservationUnavailableError(DataLoadStatus.NO_DATA, "no members in universe")
    backtests, barrier = _backtests_with_raw_load_barrier(
        container, tmp_path, run_id, failure=failure
    )
    client = TestClient(_app_with_backtests(container, backtests))

    accepted = client.post("/api/v1/backtests", json=_run_body(client, "python"))
    assert accepted.status_code == 202, accepted.text
    assert barrier.entered.wait(timeout=30), "run never entered raw observation loading"
    try:
        assert client.post(f"/api/v1/backtests/{run_id}/cancel").status_code == 200
    finally:
        barrier.release.set()

    state = _wait(client, run_id)
    assert state["status"] == "cancelled", state
    assert state["error_code"] == "portfolio.data.unavailable"
    assert "no members in universe" in state["error"]


class _RejectingEngine:
    """엔진이 구현하지 못하는 스펙으로 판정하는 포트 — preflight 반환값 소비 분기를 고정한다."""

    def assess(self, spec: object, environment: object) -> EngineCompatibility:
        return EngineCompatibility(
            compatible=False,
            requirements=EngineRequirementSummary("EverySession", (), (), ("unsupported",)),
            issues=(
                EngineCapabilityIssue("feature", "unsupported", "not_implemented", "no kernel"),
            ),
        )

    def to_target_action(self, frame: object, *, max_participation: object = None) -> object:
        raise AssertionError((frame, max_participation))  # pragma: no cover


def test_engine_incompatible_strategy_is_rejected_at_start_with_the_issue_list(
    tmp_path: Path,
) -> None:
    """preflight 의 `compatible=False` 는 202 가 아니라 422 `backtest.run.invalid` 로 거부된다."""
    container = build_container(artifact_root=tmp_path / "unused")
    adapter = cast(MockEquityDataAdapter, container.equity_data)
    portfolio_design = PortfolioDesignService(
        adapter,
        _RejectingEngine(),
        factor_metadata=adapter,
        factor_registry_version=build_default_factor_registry().version,
    )
    backtests = BacktestRunService(
        portfolio_design,
        container.strategy_repository,
        cast(BacktestDataPort, container.equity_data),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path / "artifacts"),
        new_id=lambda: "must-not-be-accepted",
    )
    client = TestClient(_app_with_backtests(container, backtests))

    response = client.post("/api/v1/backtests", json=_run_body(client, "python"))

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "backtest.run.invalid"
    assert "feature.unsupported=not_implemented (no kernel)" in detail["message"]
    assert client.get("/api/v1/backtests/must-not-be-accepted").status_code == 404


def test_tape_hash_that_differs_from_the_accepted_provenance_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start() 의 provenance 해시와 컴파일된 tape 해시가 갈리면 run 은 500 대신 `failed` 로 끝난다.

    두 값은 같은 `strategy_spec_hash` 의 출력이라 정상 경로에서는 갈릴 수 없다 — 접수 쪽 계산만
    바꿔 분기를 강제로 연다.
    """
    monkeypatch.setattr(
        "strategy_workbench.application.backtest_run._service.strategy_spec_hash",
        lambda spec: "0" * 64,
    )
    run_id = "run-tape-hash-mismatch"
    container = build_container(artifact_root=tmp_path / "unused")
    backtests, barrier = _backtests_with_raw_load_barrier(container, tmp_path, run_id)
    client = TestClient(_app_with_backtests(container, backtests))

    accepted = client.post("/api/v1/backtests", json=_run_body(client, "python"))
    barrier.release.set()  # 단언보다 먼저 해제 — 실패해도 run 스레드가 30초를 태우지 않게
    assert accepted.status_code == 202, accepted.text

    state = _wait(client, run_id)
    assert state["status"] == "failed", state
    assert state["error_code"] == "backtest.run.internal"
    assert "differs from the accepted strategy provenance" in state["error"]
    assert "provenance='" + "0" * 64 + "'" in state["error"]
    assert client.get(f"/api/v1/backtests/{run_id}/result").status_code == 409


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("can't start new thread"), MemoryError("cannot allocate thread stack")],
)
def test_run_thread_start_failure_ends_the_run_failed_instead_of_stuck_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    """스레드 기동 실패(상한 RuntimeError·메모리 압박 MemoryError)는 500 이되 run 은 `failed`."""

    class _UnstartableThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise failure

    monkeypatch.setattr(
        "strategy_workbench.application.backtest_run._service.Thread", _UnstartableThread
    )
    run_id = "run-thread-unstartable"
    container = build_container(artifact_root=tmp_path / "unused")
    backtests, _barrier = _backtests_with_raw_load_barrier(container, tmp_path, run_id)
    client = TestClient(_app_with_backtests(container, backtests), raise_server_exceptions=False)

    response = client.post("/api/v1/backtests", json=_run_body(client, "python"))

    assert response.status_code == 500
    state = client.get(f"/api/v1/backtests/{run_id}").json()
    assert state["status"] == "failed", state
    assert state["error_code"] == "backtest.run.internal"
    assert state["error"] == f"{type(failure).__name__}: {failure}"
    assert client.post(f"/api/v1/backtests/{run_id}/cancel").json()["status"] == "failed"


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        # 키가 붙은 값은 모양·루트와 무관하게 전부 가린다(접미형 키·값 끝 문장부호 포함).
        (r"root=C:\Users\someone\quant-ledger\data\equity end", "root=<path> end"),
        ("root=/home/ledger/quant-ledger/data/equity detail", "root=<path> detail"),
        ("root=/app/quant-ledger/data/equity", "root=<path>"),
        ("root=/c/Users/sangmok/quant-ledger", "root=<path>"),
        ("path=/workspace/quant-ledger/data x", "path=<path> x"),
        ("equity_root=/x/y, retry later", "equity_root=<path>, retry later"),
        ("manifest=/x/MANIFEST.json.", "manifest=<path>."),
        (r"root=\\fileserver\quant\ledger\equity", "root=<path>"),
        # 모양만으로 경로인 것: 드라이브·UNC·file://·확장자 있는 POSIX 파일·계정 루트 아래 디렉터리.
        ("path C:/Users/a/b.parquet end", "path <path> end"),
        (r"UNC \\fileserver\share\x.parquet", "UNC <path>"),
        ("source file:///C:/Users/sangmok/data/x.parquet", "source <path>"),
        ("source FILE:///C:/Users/sangmok/secret", "source <path>"),
        ("(/tmp/foo/part0.parquet)", "(<path>)"),
        ("(/tmp/foo)", "(<path>)"),
        (
            "OSError: [Errno 13] Permission denied: '/home/ledger/quant-ledger/data/equity'",
            "OSError: [Errno 13] Permission denied: '<path>'",
        ),
        ("cannot open /Users/sangmok/Library/x", "cannot open <path>"),
        # 가리지 않아야 하는 것: 다른 URL·단위 표기·비율·JSON Pointer 진단 경로·흔한 영단어 루트.
        ("see https://example.com/docs for detail", "see https://example.com/docs for detail"),
        ("units 10 m/s and 3 /s", "units 10 m/s and 3 /s"),
        ("value 1.5/2.0 ratio and/or n/a", "value 1.5/2.0 ratio and/or n/a"),
        ("status=no_data detail=None", "status=no_data detail=None"),
        ("invalid pointer /factors/0/graph/nodes/2", "invalid pointer /factors/0/graph/nodes/2"),
        ("/data/universe_id is invalid", "/data/universe_id is invalid"),
        ("pointer /usr/count and /run/id", "pointer /usr/count and /run/id"),
        (
            "strategy.expression.calculation_non_finite@factors.0.graph.nodes.2: x",
            "strategy.expression.calculation_non_finite@factors.0.graph.nodes.2: x",
        ),
    ],
)
def test_run_error_masks_server_paths_but_keeps_urls_and_units(raw: str, masked: str) -> None:
    from strategy_workbench.application.backtest_run._service import _mask_paths

    assert _mask_paths(raw) == masked


def test_run_failure_codes_are_the_single_vocabulary_for_run_and_start_errors() -> None:
    """`_failure_code` 산출 집합 == `RunFailureCode` 어휘, 그중 422 코드는 계약 Literal 과 같다."""
    from typing import get_args, get_type_hints

    from strategy_workbench.adapters.inbound.http_api._backtest_contract import (
        BacktestRunInvalidDetail,
    )
    from strategy_workbench.adapters.inbound.http_api._execution_error_contract import (
        PortfolioDataUnavailableDetail,
        PortfolioRawObservationInvalidDetail,
        PortfolioStrategyInvalidDetail,
    )
    from strategy_workbench.application.backtest_run._service import (
        InvalidBacktestRunError,
        _failure_code,
    )
    from strategy_workbench.application.portfolio_design.facade.design import (
        InvalidPortfolioRequestError,
        PortfolioSnapshotMismatchError,
    )
    from strategy_workbench.domain.backtest.facade.runs import RUN_FAILURE_CODES
    from strategy_workbench.domain.strategy.facade.validation import StrategyValidation

    produced = {
        _failure_code(InvalidPortfolioRequestError(StrategyValidation(valid=False, issues=()))),
        _failure_code(RawObservationUnavailableError(DataLoadStatus.NO_DATA, None)),
        _failure_code(PortfolioSnapshotMismatchError(expected="a", actual="b")),
        _failure_code(InvalidBacktestRunError("x")),
        _failure_code(RuntimeError("x")),
    }
    assert produced == RUN_FAILURE_CODES

    def contract_code(detail_type: type) -> str:
        # future annotations 라 필드 타입이 문자열이다 — 평가해서 Literal 인자를 꺼낸다.
        (code,) = get_args(get_type_hints(detail_type)["code"])
        return code

    assert {
        contract_code(PortfolioStrategyInvalidDetail),
        contract_code(PortfolioDataUnavailableDetail),
        contract_code(PortfolioRawObservationInvalidDetail),
        contract_code(BacktestRunInvalidDetail),
    } == RUN_FAILURE_CODES - {"backtest.run.internal"}


def test_python_reference_and_rust_core_have_golden_result_and_metric_parity() -> None:
    client = TestClient(build_http_app())

    rust = _execute(client, "rust")
    python = _execute(client, "python")

    assert rust["metrics"] == python["metrics"]
    assert rust["series"] == python["series"]
    assert rust["artifacts"] == python["artifacts"]
    assert rust["manifest"]["engine_core"] == "rust"
    assert python["manifest"]["engine_core"] == "python"


def test_backtest_unknown_run_and_invalid_metric_window_return_structured_errors() -> None:
    client = TestClient(build_http_app())
    assert client.get("/api/v1/backtests/missing").status_code == 404
    assert client.get("/api/v1/backtests/missing/request").status_code == 404
    assert client.post("/api/v1/backtests/missing/cancel").status_code == 404

    body = _run_body(client)
    body["metric_windows"][0]["start"] = "2025-01-01"
    response = client.post("/api/v1/backtests", json=body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "backtest.run.invalid"
    TypeAdapter(Backtest422Response).validate_python(response.json())


def test_start_backtest_openapi_declares_every_actual_preflight_error() -> None:
    client = TestClient(build_http_app())
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/backtests"]["post"]

    assert {"202", "404", "409", "422"} <= set(operation["responses"])
    assert operation["responses"]["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "BacktestStrategyNotFoundResponse"
    )
    assert operation["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "BacktestStrategyStaleResponse"
    )
    detail = schema["components"]["schemas"]["BacktestUnprocessableResponse"]["properties"][
        "detail"
    ]
    assert detail["discriminator"]["propertyName"] == "code"
    # 관측 데이터 부재·계약 위반은 시작 요청이 아니라 run 상태 `failed` 로 전달된다(이슈 #158).
    assert set(detail["discriminator"]["mapping"]) == {
        "backtest.run.invalid",
        "backtest.run.environment_required",
        "backtest.strategy.requires_upgrade",
        "portfolio.strategy.invalid",
    }

    # 실행 설정 없는 시작 요청은 코드화된 422 다 — schema 1.2 문서에는 되돌아갈 값이 없다(P2-03).
    without_environment = _run_body(client, "python")
    without_environment.pop("environment")
    missing_environment = client.post("/api/v1/backtests", json=without_environment)
    assert missing_environment.status_code == 422, missing_environment.text
    assert missing_environment.json()["detail"]["code"] == "backtest.run.environment_required"
    TypeAdapter(Backtest422Response).validate_python(missing_environment.json())

    semantic = _run_body(client, "python")
    semantic["strategy"]["portfolio"]["weighting"] = "risk"
    semantic["strategy"]["risk"]["risk_field_id"] = None
    semantic_response = client.post("/api/v1/backtests", json=semantic)
    malformed_response = client.post("/api/v1/backtests", json={"core": "not-a-core"})

    assert semantic_response.status_code == malformed_response.status_code == 422
    assert semantic_response.json()["detail"]["code"] == "portfolio.strategy.invalid"
    assert isinstance(malformed_response.json()["detail"], list)
    adapter = TypeAdapter(Backtest422Response)
    adapter.validate_python(semantic_response.json())
    adapter.validate_python(malformed_response.json())


def test_run_resource_openapi_declares_typed_not_found_and_not_ready_errors() -> None:
    client = TestClient(build_http_app())
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    run_not_found_paths = (
        ("/api/v1/backtests/{run_id}", "get"),
        ("/api/v1/backtests/{run_id}/request", "get"),
        ("/api/v1/backtests/{run_id}/cancel", "post"),
        ("/api/v1/backtests/{run_id}/events", "get"),
    )
    for path, method in run_not_found_paths:
        response = paths[path][method]["responses"]["404"]
        assert response["content"]["application/json"]["schema"]["$ref"].endswith(
            "BacktestRunNotFoundResponse"
        )

    result_responses = paths["/api/v1/backtests/{run_id}/result"]["get"]["responses"]
    assert result_responses["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "BacktestRunNotFoundResponse"
    )
    assert result_responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "BacktestResultNotReadyResponse"
    )


def test_out_of_range_run_environment_is_rejected_at_accept_time() -> None:
    """실행 설정 검증은 데이터를 읽지 않는 검사라 접수 단계에 있어야 한다(이슈 #158 계약).

    없으면 202 로 접수된 뒤 run thread 가 `backtest.run.internal` 로 늦게 죽어, 클라이언트는
    어느 필드가 왜 틀렸는지 알 수 없고 화면은 성공으로 표시한 뒤 깨진다(리뷰 P1).
    """
    client = TestClient(build_http_app())
    environment = {
        "market": "KRX",
        "frequency": "daily",
        "start": "2026-01-02",
        "end": "2026-02-20",
        "universe_id": "krx.common-stock",
        "timing": "next_open",
        "participation_rate": 0.1,
        "fee_bps": 15.0,
        "slippage_bps": 10.0,
        "missing": "drop",
    }
    body = _run_body(client, "python")
    body.pop("environment")

    # 422 본문의 `input` 은 요청한 environment 객체를 통째로 되돌려주므로 모든 필드 이름이
    # 응답 텍스트에 들어 있다. 진단이 어느 필드를 지목하는지 보려면 `msg` 로 좁혀야 한다.
    for field_name, bad, expected in (
        ("participation_rate", 50.0, "field=participation_rate"),
        ("fee_bps", -1.0, "field=fee_bps"),
        ("start", "2026-12-31", "end must be on or after start"),
        ("universe_id", "   ", "requires a universe id"),
    ):
        response = client.post(
            "/api/v1/backtests",
            json={**body, "environment": {**environment, field_name: bad}},
        )
        assert response.status_code == 422, (field_name, response.text)
        detail = response.json()["detail"]
        assert len(detail) == 1, (field_name, detail)
        assert expected in detail[0]["msg"], (field_name, detail[0]["msg"])
        # 현재 `loc` 은 environment 객체까지만 가리킨다. 필드 단위 표면은 P3-02 결정 항목이다.
        assert detail[0]["loc"][-1] == "environment", (field_name, detail[0]["loc"])

    accepted = client.post("/api/v1/backtests", json={**body, "environment": environment})
    assert accepted.status_code == 202, accepted.text


def test_start_without_an_environment_is_a_coded_422() -> None:
    """schema 1.2 문서에는 실행 설정이 없으므로 시작 요청이 반드시 실어야 한다(P2-03).

    거절 주체는 `start` 이고 코드는 전용 `backtest.run.environment_required` 다 — 프론트가 이
    한 코드를 보고 실행 설정 패널로 보낸다.
    """
    client = TestClient(build_http_app())
    body = _run_body(client, "python")
    body.pop("environment")

    response = client.post("/api/v1/backtests", json=body)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "backtest.run.environment_required"
    assert "run_environment.required" in detail["message"]


def test_tape_stage_progress_advances_monotonically_within_a_bounded_event_count() -> None:
    """이슈 #162: tape 단계가 `0.02` 한 번만 내고 끝나 실행 시간 대부분 동안 2% 에 고정됐다.

    tape 단계 안에서 진행 값이 여러 번 오르고, 단계 이름은 `tape` 그대로이며, 전체 이벤트의 진행
    값은 단조 증가한다. 평가기가 종목마다 보고해도 이벤트 수는 제한된다(SSE·메모리 보호).
    """
    client = TestClient(build_http_app())
    accepted = client.post("/api/v1/backtests", json=_run_body(client))
    assert accepted.status_code == 202
    run_id = accepted.json()["run"]["run_id"]
    assert _wait(client, run_id)["status"] == "completed"

    stream = client.get(f"/api/v1/backtests/{run_id}/events").text
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream.splitlines()
        if line.startswith("data: ")
    ]
    progress = [event["progress"] for event in events]
    assert progress == sorted(progress), progress
    tape = [event for event in events if event["stage"] == "tape"]
    tape_progress = {event["progress"] for event in tape}
    assert len(tape_progress) >= 5, tape
    assert min(tape_progress) == pytest.approx(0.02)
    assert max(tape_progress) <= 0.8
    assert len(tape) <= 100, len(tape)
    assert {event["stage"] for event in events} >= {"queued", "tape", "data", "engine", "completed"}
