"""서버를 다시 띄워도 백테스트 이력과 결과가 남는다(검증 랩 spec D3, V1-03·V1-04, US-SM-12 비고).

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
from strategy_workbench.bootstrap.facade.container import (
    AssistantSettings,
    build_container,
    scripted_provider_factories,
)
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
    # 어시스턴트 DB 는 프로세스마다 새로 뜬다(in-memory). 결과 설명 세션은 결과 포트만 본다.
    assistant = AssistantSettings(provider_factories=scripted_provider_factories())
    return TestClient(
        build_http_app(research_db_path=tmp_path / "research.sqlite3", assistant=assistant)
    )


def _completed_run(client: TestClient) -> str:
    run_id = client.post("/api/v1/backtests", json=_body(client)).json()["run"]["run_id"]
    completed = wait_for_terminal_state(client, run_id)
    assert completed["status"] == "completed", completed
    return str(run_id)


def _result_session(client: TestClient, run_id: str) -> Any:
    """결과 설명 세션을 연다. 세션을 만들 때 결과 포트로 결과를 읽어 요약한다."""
    created = client.post(
        "/api/v1/assistant/providers",
        json={"kind": "anthropic", "label": "Claude", "secret": "sk-restart-0001"},
    )
    assert created.status_code == 201, created.text
    return client.post("/api/v1/assistant/sessions", json={"document_ref": {"run_id": run_id}})


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


def test_completed_runs_keep_their_list_status_request_and_result_after_a_restart(
    tmp_path: Path,
) -> None:
    before = _client(tmp_path)
    run_id = _completed_run(before)
    completed = before.get(f"/api/v1/backtests/{run_id}").json()
    listed = before.get("/api/v1/backtests").json()
    request = before.get(f"/api/v1/backtests/{run_id}/request").json()
    result = before.get(f"/api/v1/backtests/{run_id}/result")
    assert result.status_code == 200, result.text

    after = _client(tmp_path)

    assert after.get("/api/v1/backtests").json() == listed
    assert after.get(f"/api/v1/backtests/{run_id}").json() == completed
    assert after.get(f"/api/v1/backtests/{run_id}/request").json() == request
    # 결과는 산출물 파일에서 다시 읽는다(V1-04). 재시작 전과 글자 하나까지 같다.
    reloaded = after.get(f"/api/v1/backtests/{run_id}/result")
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json() == result.json()
    assert reloaded.json()["artifacts"]["fills"], "결과가 비어 있으면 왕복 비교가 약하다"
    # AI 결과 설명도 같은 결과를 읽는다.
    session = _result_session(after, run_id)
    assert session.status_code == 201, session.text
    # 진행 이벤트는 메모리에만 있었다. 종결된 run 의 스트림은 이벤트 없이 닫힌다.
    assert after.get(f"/api/v1/backtests/{run_id}/events").text == ""


def test_a_damaged_or_missing_result_file_is_answered_with_a_code(tmp_path: Path) -> None:
    before = _client(tmp_path)
    run_id = _completed_run(before)
    assert before.get(f"/api/v1/backtests/{run_id}/result").status_code == 200
    result_path = _container.DEFAULT_RUN_ARTIFACT_ROOT / run_id / "result.json"
    payload = result_path.read_bytes()
    # run 을 끝낸 프로세스도 결과를 메모리에 들지 않고 파일에서 읽는다.
    result_path.unlink()
    assert before.get(f"/api/v1/backtests/{run_id}/result").status_code == 410
    result_path.write_bytes(payload.replace(b'"core":"python"', b'"core":"rust"'))

    client = _client(tmp_path)

    damaged = client.get(f"/api/v1/backtests/{run_id}/result")
    assert damaged.status_code == 410, damaged.text
    assert damaged.json()["detail"]["code"] == "backtest.result.unreadable"
    message = damaged.json()["detail"]["message"]
    assert "sha256" in message
    # 서버 경로(실제 산출물 루트)는 싣지 않는다. JSON 본문은 Windows `\` 를 이스케이프하므로
    # 해석한 메시지와 대조한다.
    root = _container.DEFAULT_RUN_ARTIFACT_ROOT.resolve()
    assert str(root) not in message and root.as_posix() not in message
    # AI 결과 설명은 "설명할 결과가 없다"로 접는다.
    session = _result_session(client, run_id)
    assert session.status_code == 422, session.text
    assert session.json()["detail"]["code"] == "assistant.result_unavailable"

    result_path.unlink()
    missing = client.get(f"/api/v1/backtests/{run_id}/result")
    assert missing.status_code == 410, missing.text
    assert missing.json()["detail"]["code"] == "backtest.result.unreadable"
    assert "missing" in missing.json()["detail"]["message"]


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
            lineage_id="s-1",
            trial_key="b" * 64,
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
