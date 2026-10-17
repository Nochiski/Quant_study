"""팩터 회귀 조회(검증 랩 V4-05)의 HTTP 응답 모양과 기존 완료 결과 오류 경계."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.runs import RunStatus
from tests.backtest_run_wait import wait_for_terminal_state
from tests.integration.test_account_report_http_api import _client, _input_result, _seed
from tests.integration.test_trial_ledger_http_api import _saved_run


def test_saved_mock_run_reports_counts_reason_and_lineage_threshold() -> None:
    client = TestClient(build_http_app())
    request = _saved_run(client)
    started = client.post("/api/v1/backtests", json=request)
    assert started.status_code == 202, started.text
    run_id = started.json()["run"]["run_id"]
    assert wait_for_terminal_state(client, run_id)["status"] == "completed"
    path = f"/api/v1/backtests/{run_id}"
    original = client.get(path + "/result").json()
    response = client.get(path + "/factor-regression")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["run_fingerprint"] == original["manifest"]["run_fingerprint"]
    assert body["data_snapshot_id"] == original["manifest"]["data_snapshot_id"]
    assert body["momentum_factor_id"] == "price.momentum_12_1"
    assert body["value_factor_id"] == "financial.book_to_market"
    assert body["trial_count"] == 1
    assert body["alpha_t_threshold"] == 2.0
    report = body["report"]
    # mock 유니버스(3 종목)는 2×3 정렬이 비어 회귀하지 않는다. 사유와 빠진 구간 수를 그대로 싣는다.
    assert report["fit"] is None
    assert report["unavailable_reason"] == "insufficient_observations"
    assert report["strategy_period_count"] == len(original["series"]["equity"]) - 1
    assert (
        sum(item["count"] for item in report["excluded_periods"])
        == (report["strategy_period_count"])
    )
    assert client.get(path + "/result").json() == original


def test_result_from_another_data_snapshot_is_not_regressed(tmp_path: Path) -> None:
    result = _input_result()
    _seed(tmp_path, result)
    response = _client(tmp_path).get(
        f"/api/v1/backtests/{result.manifest.run_id}/factor-regression"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_snapshot_id"] == result.manifest.data_snapshot_id
    assert body["report"]["unavailable_reason"] == "data_snapshot_changed"
    assert body["trial_count"] is None
    assert body["alpha_t_threshold"] is None


@pytest.mark.parametrize("damage", ["unknown", "unfinished", "missing", "hash", "decode"])
def test_report_preserves_verified_result_errors_without_path_disclosure(
    tmp_path: Path,
    damage: str,
) -> None:
    result = _input_result()
    _seed(
        tmp_path, result, status=RunStatus.FAILED if damage == "unfinished" else RunStatus.COMPLETED
    )
    path = tmp_path / "artifacts" / result.manifest.run_id / "result.json"
    if damage == "missing":
        path.unlink()
    elif damage in ("hash", "decode"):
        payload = b"{}"
        path.write_bytes(payload)
        if damage == "decode":
            repository = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")
            state = repository.get(result.manifest.run_id).run
            repository.update(replace(state, artifact_sha256=hashlib.sha256(payload).hexdigest()))
            repository.close()
    run_id = "unknown" if damage == "unknown" else result.manifest.run_id
    client = _client(tmp_path)
    response = client.get(f"/api/v1/backtests/{run_id}/factor-regression")
    original = client.get(f"/api/v1/backtests/{run_id}/result")
    assert response.status_code == original.status_code
    assert (
        response.status_code
        == {
            "unknown": 404,
            "unfinished": 409,
            "missing": 410,
            "hash": 410,
            "decode": 410,
        }[damage]
    )
    assert response.json() == original.json()
    assert str(tmp_path) not in response.text
