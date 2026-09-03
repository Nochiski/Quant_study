from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import (
    EquityCurvePoint,
    MetricScope,
    MetricValue,
    build_default_metric_registry,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestSeries,
    ExecutionCore,
    RawArtifactBundle,
    RawSnapshot,
    RunManifest,
)


def _result() -> BacktestRunResult:
    created = datetime(2026, 9, 3, tzinfo=UTC)
    registry = build_default_metric_registry()
    strategy = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "unused",
        today=lambda: date(2026, 9, 3),
    ).template()
    run_spec = BacktestRunSpec(strategy)
    return BacktestRunResult(
        manifest=RunManifest(
            run_id="run-safe-001",
            created_at=created,
            completed_at=created,
            engine_core=ExecutionCore.RUST,
            engine_version="test",
            run_fingerprint="fingerprint",
            run_spec=run_spec,
            strategy_hash="strategy",
            data_snapshot_id="snapshot",
            target_tape_hash="tape",
            metric_registry_version=registry.version,
            initial_cash=100.0,
            annualization_days=252,
            fee_bps=0.0,
            slippage_bps=0.0,
            participation_rate=1.0,
        ),
        metric_definitions=registry.definitions(),
        metrics=(
            MetricValue("total_return", 0.0, MetricScope.FULL, 1),
            MetricValue(
                "sharpe",
                None,
                MetricScope.FULL,
                1,
                unavailable_reason="zero_return_variance",
            ),
        ),
        series=BacktestSeries(
            equity=(EquityCurvePoint(date(2026, 1, 2), 100.0, None),),
            drawdown=(),
            monthly_returns=(),
            rolling_sharpe=(),
        ),
        artifacts=RawArtifactBundle(
            snapshots=(RawSnapshot(date(2026, 1, 2), 100.0, 100.0, 0.0, 0.0),),
            positions=(),
            orders=(),
            fills=(),
            costs=(),
            trades=(),
        ),
    )


def test_local_artifact_store_commits_atomically_and_preserves_null_vs_zero(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)

    commit = store.commit(_result())

    result_path = tmp_path / "run-safe-001" / "result.json"
    manifest_path = tmp_path / "run-safe-001" / "manifest.json"
    payload = result_path.read_bytes()
    decoded = json.loads(payload)
    assert result_path.as_uri() == commit.uri
    assert manifest_path.exists()
    assert commit.sha256 == hashlib.sha256(payload).hexdigest()
    assert decoded["metrics"][0]["value"] == 0.0
    assert decoded["metrics"][1]["value"] is None
    assert not tuple(tmp_path.glob(".*.tmp"))

    with pytest.raises(FileExistsError):
        store.commit(_result())


def test_local_artifact_store_rejects_run_ids_that_escape_the_root(tmp_path) -> None:
    result = _result()
    escaped = BacktestRunResult(
        manifest=RunManifest(
            **{
                **result.manifest.__dict__,
                "run_id": "../outside",
            }
        ),
        metric_definitions=result.metric_definitions,
        metrics=result.metrics,
        series=result.series,
        artifacts=result.artifacts,
    )

    with pytest.raises(ValueError, match="escapes artifact root"):
        LocalArtifactStore(tmp_path).commit(escaped)
