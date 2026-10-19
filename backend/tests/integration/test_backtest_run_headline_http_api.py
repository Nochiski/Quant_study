"""완료 run 의 머리 지표(재설계 결정 1).

목록·한 행 응답이 결과 파일을 열지 않고 총수익률·샤프·최대 낙폭을 싣는다. 값은 결과의 FULL 범위
지표 그대로이고, 끝나지 않은 run 은 비어 있으며 재시작 뒤에도 남는다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.research_sqlite._schema import V9_SCHEMA_OBJECTS
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from tests.backtest_run_wait import wait_for_terminal_state

_ENVIRONMENT = {"start": "2025-01-02", "end": "2026-01-16", "universe_id": "krx.common-stock"}


def _start(client: TestClient) -> str:
    template = client.get("/api/v1/strategies/template").json()
    template.pop("identity")
    template["schema_version"] = "1.2"
    document = client.post(
        "/api/v1/strategy-documents",
        json={"format": "json", "source": json.dumps(template, ensure_ascii=False, default=str)},
    ).json()
    response = client.post(
        "/api/v1/backtests",
        json={
            "strategy_source": {
                "kind": "saved_revision",
                "strategy_id": document["strategy_id"],
                "revision": document["revision"],
                "expected_spec_hash": document["spec_hash"],
            },
            "core": "python",
            "environment": _ENVIRONMENT,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["run"]["run_id"]


def _full(result: dict[str, Any], metric_id: str) -> float | None:
    return next(
        metric["value"]
        for metric in result["metrics"]
        if metric["scope"] == "full" and metric["metric_id"] == metric_id
    )


def test_completed_runs_carry_headline_metrics_in_list_and_summary(tmp_path: Path) -> None:
    research = tmp_path / "research.sqlite3"
    client = TestClient(build_http_app(research_db_path=research, artifact_root=tmp_path / "runs"))
    run_id = _start(client)
    assert wait_for_terminal_state(client, run_id)["status"] == "completed"

    result = client.get(f"/api/v1/backtests/{run_id}/result").json()
    expected = {
        "total_return": _full(result, "total_return"),
        "sharpe": _full(result, "sharpe"),
        "max_drawdown": _full(result, "max_drawdown"),
    }
    [item] = client.get("/api/v1/backtests").json()["items"]
    assert item["headline"] == expected
    assert client.get(f"/api/v1/backtests/{run_id}/summary").json()["headline"] == expected

    # 재시작한 서버도 결과 파일을 열지 않고 같은 머리 지표를 읽는다.
    restarted = TestClient(
        build_http_app(research_db_path=research, artifact_root=tmp_path / "runs")
    )
    assert restarted.get("/api/v1/backtests").json()["items"][0]["headline"] == expected


def test_v9_file_migrates_and_old_runs_have_no_headline(tmp_path: Path) -> None:
    path = tmp_path / "old-research.sqlite3"
    with sqlite3.connect(path) as connection:
        for _, _, statement in V9_SCHEMA_OBJECTS:
            connection.execute(statement)
        connection.execute("PRAGMA application_id = 1398231635")
        connection.execute("PRAGMA user_version = 9")

    SQLiteBacktestRunRepository(path).close()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'backtest_run_headlines'"
        ).fetchone() == ("backtest_run_headlines",)
