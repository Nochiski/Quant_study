"""직접 만든 완료 결과로 계좌 보고서의 조회·재시작·실패 경계를 검증한다."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.analytics.facade.metrics import MetricScope, MetricValue
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunState,
    RawSnapshot,
    RunStatus,
)
from tests.test_backtest_artifact_store import _result


def _input_result() -> BacktestRunResult:
    original = _result()
    # 초기 100원에서 첫 거래비용 1원이 이미 차감된 99원, 마지막 평가액 110원이다.
    return replace(
        original,
        manifest=replace(
            original.manifest,
            strategy_hash="b" * 64,
            strategy_provenance=replace(original.manifest.strategy_provenance, spec_hash="b" * 64),
        ),
        artifacts=replace(
            original.artifacts,
            snapshots=(
                RawSnapshot(original.manifest.environment.start, 9, 99, 0, 0),
                RawSnapshot(original.manifest.environment.end, 10, 110, 0, 0),
            ),
        ),
        metrics=(MetricValue("total_return", 110 / 99 - 1, MetricScope.FULL, 2),),
    )


def _seed(
    tmp_path: Path, result: BacktestRunResult, *, status: RunStatus = RunStatus.COMPLETED
) -> None:
    commit = LocalArtifactStore(tmp_path / "artifacts").commit(result)
    repository = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")
    repository.add(
        BacktestRunState(
            result.manifest.run_id,
            status,
            1,
            "completed",
            "합성 결과",
            result.manifest.created_at,
            result.manifest.completed_at,
            artifact_sha256=commit.sha256,
        ),
        result.manifest.strategy_provenance,
        result.manifest.run_spec,
        lineage_id=None,
        trial_key="a" * 64,
    )
    repository.close()


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        build_http_app(
            research_db_path=tmp_path / "research.sqlite3", artifact_root=tmp_path / "artifacts"
        )
    )


@pytest.mark.parametrize("final_equity", [90.0, 100.0, 110.0])
def test_report_uses_initial_capital_preserves_original_and_survives_restart(
    tmp_path: Path, final_equity: float
) -> None:
    result = _input_result()
    result = replace(
        result,
        artifacts=replace(
            result.artifacts,
            snapshots=(
                result.artifacts.snapshots[0],
                replace(result.artifacts.snapshots[-1], equity=final_equity),
            ),
        ),
        metrics=(MetricValue("total_return", final_equity / 99 - 1, MetricScope.FULL, 2),),
    )
    _seed(tmp_path, result)
    client = _client(tmp_path)
    run_id = result.manifest.run_id
    original = client.get(f"/api/v1/backtests/{run_id}/result").json()
    response = client.get(f"/api/v1/backtests/{run_id}/account-report")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "available"
    assert report["evaluation_basis"] == "initial_capital"
    assert report["summary"] == {
        "initial_cash": 100,
        "first_snapshot_date": "2021-01-01",
        "last_snapshot_date": "2026-08-31",
        "final_cash": 10,
        "final_equity": final_equity,
        "pnl": final_equity - 100,
        "total_return": pytest.approx(final_equity / 100 - 1),
        "snapshot_count": 2,
    }
    assert original["metrics"][0]["value"] == pytest.approx(final_equity / 99 - 1)
    assert original["metrics"][0]["value"] != pytest.approx(report["summary"]["total_return"])
    assert report["warnings"] == original["manifest"]["warnings"]
    assert report["provenance"]["run_fingerprint"] == original["manifest"]["run_fingerprint"]
    assert "청산 현금" in report["interpretation"]
    assert client.get(f"/api/v1/backtests/{run_id}/result").json() == original
    restarted = _client(tmp_path)
    assert restarted.get(f"/api/v1/backtests/{run_id}/account-report").json() == report
    assert restarted.get(f"/api/v1/backtests/{run_id}/result").json() == original


def test_application_report_loads_the_verified_result_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _input_result()
    _seed(tmp_path, result)
    loaded: list[str] = []
    original_load = LocalArtifactStore.load

    def counted_load(store: LocalArtifactStore, run_id: str, *, sha256: str) -> BacktestRunResult:
        loaded.append(run_id)
        return original_load(store, run_id, sha256=sha256)

    monkeypatch.setattr(LocalArtifactStore, "load", counted_load)
    service = build_container(
        research_db_path=tmp_path / "research.sqlite3", artifact_root=tmp_path / "artifacts"
    ).backtest_runs
    report = service.account_report(result.manifest.run_id)
    assert report.summary is not None
    assert report.summary.pnl == 10
    assert loaded == [result.manifest.run_id]


@pytest.mark.parametrize("unavailable", ["empty", "unordered"])
def test_report_exposes_typed_unavailable_without_rewriting_result(
    tmp_path: Path, unavailable: str
) -> None:
    result = _input_result()
    result = replace(
        result,
        artifacts=replace(
            result.artifacts,
            snapshots=() if unavailable == "empty" else tuple(reversed(result.artifacts.snapshots)),
        ),
    )
    _seed(tmp_path, result)
    response = _client(tmp_path).get(f"/api/v1/backtests/{result.manifest.run_id}/account-report")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == ("empty_snapshots" if unavailable == "empty" else "invalid_input")
    assert report["summary"] is None
    assert result.manifest.run_id in report["detail"]


def test_unknown_and_unfinished_runs_keep_distinct_errors(tmp_path: Path) -> None:
    result = _input_result()
    _seed(tmp_path, result, status=RunStatus.FAILED)
    client = _client(tmp_path)
    unknown = client.get("/api/v1/backtests/unknown/account-report")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "backtest.run.not_found"
    unfinished = client.get(f"/api/v1/backtests/{result.manifest.run_id}/account-report")
    assert unfinished.status_code == 409
    assert unfinished.json()["detail"]["code"] == "backtest.result.not_ready"


@pytest.mark.parametrize("damage", ["missing", "hash", "decode"])
def test_missing_hash_and_decode_errors_keep_410_and_redact_paths(
    tmp_path: Path, damage: str
) -> None:
    result = _input_result()
    _seed(tmp_path, result)
    path = tmp_path / "artifacts" / result.manifest.run_id / "result.json"
    # 테스트가 직접 만든 임시 입력을 변조해 기존 실제 저장소의 검증 경계를 통과시킨다.
    if damage == "missing":
        path.unlink()
    elif damage == "hash":
        path.write_bytes(b"{}")
    else:
        payload = b"{}"
        path.write_bytes(payload)
        repository = SQLiteBacktestRunRepository(tmp_path / "research.sqlite3")
        state = repository.get(result.manifest.run_id).run
        repository.update(replace(state, artifact_sha256=hashlib.sha256(payload).hexdigest()))
        repository.close()
    client = _client(tmp_path)
    response = client.get(f"/api/v1/backtests/{result.manifest.run_id}/account-report")
    original_error = client.get(f"/api/v1/backtests/{result.manifest.run_id}/result")
    assert response.status_code == original_error.status_code == 410
    assert response.json() == original_error.json()
    detail = response.json()["detail"]
    assert detail["code"] == "backtest.result.unreadable"
    expected = {"missing": "missing", "hash": "sha256", "decode": "does not decode"}
    assert expected[damage] in detail["message"]
    assert str(tmp_path) not in detail["message"]
