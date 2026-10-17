"""검증 카드 조회의 불변 지표·재시작·기존 완료 결과 오류 경계를 합성 입력으로 검증한다."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.domain.analytics.facade.metrics import (
    MetricScope,
    MetricUnavailableReason,
    MetricValue,
)
from strategy_workbench.domain.backtest.facade.runs import RunStatus
from tests.integration.test_account_report_http_api import _client, _input_result, _seed


@pytest.mark.parametrize("psr", [None, 0.314, 1.0])
def test_stored_psr_and_current_lineage_reason_are_distinct_and_result_is_unchanged(
    tmp_path: Path,
    psr: float | None,
) -> None:
    original = _input_result()
    result = replace(
        original,
        metrics=(
            MetricValue(
                "probabilistic_sharpe",
                psr,
                MetricScope.FULL,
                889,
                unavailable_reason=MetricUnavailableReason.TWO_VALUED_RETURNS
                if psr is None
                else None,
            ),
            MetricValue("sharpe", 0.8, MetricScope.FULL, 889),
            MetricValue("sharpe_standard_error", 0.4, MetricScope.FULL, 889),
        ),
    )
    _seed(tmp_path, result)
    client = _client(tmp_path)
    path = f"/api/v1/backtests/{result.manifest.run_id}"
    before = client.get(path + "/result").json()
    response = client.get(path + "/validation-report")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["psr"] == before["metrics"][0]
    assert report["psr"]["sample_count"] == 889
    assert report["dsr"]["unavailable_reason"] == "no_lineage"
    assert report["dsr"]["trial_count"] is None
    assert report["dsr"]["value"] is None
    assert report["observed_at"]
    assert report["run_fingerprint"] == result.manifest.run_fingerprint
    assert report["metric_registry_version"] == result.manifest.metric_registry_version
    assert client.get(path + "/result").json() == before
    restarted = _client(tmp_path).get(path + "/validation-report").json()
    assert restarted["psr"] == report["psr"]
    assert restarted["dsr"] == report["dsr"]


def test_legacy_psr_not_recorded_is_not_recomputed(tmp_path: Path) -> None:
    result = replace(
        _input_result(),
        metrics=(
            MetricValue("sharpe", 0.8, MetricScope.FULL, 889),
            MetricValue("sharpe_standard_error", 0.4, MetricScope.FULL, 889),
        ),
    )
    _seed(tmp_path, result)
    response = _client(tmp_path).get(
        f"/api/v1/backtests/{result.manifest.run_id}/validation-report"
    )
    assert response.status_code == 200
    assert response.json()["psr"] is None


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
    response = client.get(f"/api/v1/backtests/{run_id}/validation-report")
    original = client.get(f"/api/v1/backtests/{run_id}/result")
    assert (
        response.status_code
        == original.status_code
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


def test_saved_run_has_numeric_dsr_and_keeps_result_and_existing_ledger_api() -> None:
    from fastapi.testclient import TestClient

    from strategy_workbench.bootstrap.facade.http import build_http_app
    from tests.backtest_run_wait import wait_for_terminal_state
    from tests.integration.test_trial_ledger_http_api import _saved_run

    client = TestClient(build_http_app())
    request = _saved_run(client)
    started = client.post("/api/v1/backtests", json=request)
    assert started.status_code == 202, started.text
    run_id = started.json()["run"]["run_id"]
    assert wait_for_terminal_state(client, run_id)["status"] == "completed"
    path = f"/api/v1/backtests/{run_id}"
    original = client.get(path + "/result").json()
    lineage = request["strategy_source"]["strategy_id"]
    ledger_before = client.get(f"/api/v1/strategies/{lineage}/trials").json()
    response = client.get(path + "/validation-report")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["dsr"]["unavailable_reason"] is None
    assert 0 <= report["dsr"]["value"] <= 1
    assert report["dsr"]["trial_count"] == 1
    assert report["dsr"]["representative_count"] == 1
    assert report["dsr"]["expected_maximum_sharpe"] == 0
    assert report["psr"] == next(
        m
        for m in original["metrics"]
        if m["metric_id"] == "probabilistic_sharpe" and m["scope"] == "full"
    )
    assert client.get(path + "/result").json() == original
    assert client.get(f"/api/v1/strategies/{lineage}/trials").json() == ledger_before
