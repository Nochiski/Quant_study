"""서버를 다시 띄워도 백테스트 이력이 남는다(검증 랩 spec D3, V1-03, US-SM-12 비고).

같은 연구 기록 파일로 앱을 두 번 만들어 재시작을 흉내 낸다. 브라우저 e2e 로 재현하지 않는다 —
e2e 러너는 서버 프로세스를 한 번만 띄운다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.application.backtest_run.facade.runs import BacktestRunSummary
from strategy_workbench.bootstrap import _container
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    BacktestRunState,
    RunStatus,
    SavedRevisionReference,
    StrategyProvenance,
    StrategySourceKind,
)
from tests.backtest_run_wait import wait_for_terminal_state


def _client(tmp_path: Path) -> TestClient:
    return TestClient(build_http_app(research_db_path=tmp_path / "research.sqlite3"))


def _body(client: TestClient) -> dict[str, Any]:
    spec = client.get("/api/v1/strategies/template").json()
    return {
        "strategy_source": {"kind": "inline_draft", "spec": spec, "source_hash": "d" * 64},
        "core": "python",
        "environment": {
            "start": "2026-01-02",
            "end": "2026-02-20",
            "universe_id": "krx.common-stock",
        },
    }


def test_completed_runs_keep_their_list_status_and_request_after_a_restart(
    tmp_path: Path,
) -> None:
    before = _client(tmp_path)
    run_id = before.post("/api/v1/backtests", json=_body(before)).json()["run"]["run_id"]
    completed = wait_for_terminal_state(before, run_id)
    assert completed["status"] == "completed", completed
    listed = before.get("/api/v1/backtests").json()
    request = before.get(f"/api/v1/backtests/{run_id}/request").json()

    after = _client(tmp_path)

    assert after.get("/api/v1/backtests").json() == listed
    assert after.get(f"/api/v1/backtests/{run_id}").json() == completed
    assert after.get(f"/api/v1/backtests/{run_id}/request").json() == request
    # 결과 파일 재적재는 V1-04 다. 그 전까지 재시작 전에 끝난 run 의 결과는 409 로 답한다.
    result = after.get(f"/api/v1/backtests/{run_id}/result")
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "backtest.result.not_ready"
    # 진행 이벤트는 메모리에만 있었다. 종결된 run 의 스트림은 이벤트 없이 닫힌다.
    assert after.get(f"/api/v1/backtests/{run_id}/events").text == ""


def test_runs_left_unfinished_by_the_previous_process_are_closed_as_interrupted(
    tmp_path: Path,
) -> None:
    at = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
    repository = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")
    request = BacktestRunSpec(
        strategy_source=SavedRevisionReference("s-1", 1, "a" * 64, "saved_revision"),
        environment=RunEnvironment(
            start=date(2026, 1, 2), end=date(2026, 2, 20), universe_id="krx.common-stock"
        ),
    )
    statuses = {
        "queued": RunStatus.QUEUED,
        "running": RunStatus.RUNNING,
        "cancel-requested": RunStatus.CANCEL_REQUESTED,
        "completed": RunStatus.COMPLETED,
    }
    for run_id, status in statuses.items():
        repository.add(
            BacktestRunSummary(
                BacktestRunState(run_id, status, 0.5, "tape", "Compiling target tape", at, at),
                StrategyProvenance(StrategySourceKind.SAVED_REVISION, "a" * 64, "1.2", "s-1", 1),
            ),
            request,
        )
    repository.close()

    client = _client(tmp_path)

    for run_id in ("queued", "running", "cancel-requested"):
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        assert state["status"] == "failed"
        assert state["error_code"] == "backtest.run.interrupted"
        assert f"status={statuses[run_id].value} stage=tape" in state["error"]
        # 취소도 이미 닫힌 상태를 그대로 돌려준다.
        assert client.post(f"/api/v1/backtests/{run_id}/cancel").json() == state
    assert client.get("/api/v1/backtests/completed").json()["status"] == "completed"


def test_building_the_container_locks_the_research_database_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """요청에 인라인 초안 전략 원문이 담기므로 파일을 현재 사용자 전용으로 잠근다(`_file_guard`)."""
    locked: list[Path] = []
    monkeypatch.setattr(_container, "restrict_to_current_user", locked.append)

    build_container(research_db_path=tmp_path / "nested" / "research.sqlite3")
    build_container()

    # in-memory(경로 없음)는 잠글 파일이 없다.
    assert locked == [(tmp_path / "nested" / "research.sqlite3").resolve()]
