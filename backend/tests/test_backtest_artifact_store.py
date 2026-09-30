from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestArtifactUnreadableError,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import (
    DrawdownPoint,
    EquityCurvePoint,
    MetricScope,
    MetricUnavailableReason,
    MetricValue,
    MonthlyReturnPoint,
    RollingMetricPoint,
    build_default_metric_registry,
)
from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    environment_hash,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestSeries,
    DataWarning,
    ExecutionCore,
    MetricWindow,
    RawArtifactBundle,
    RawCost,
    RawFill,
    RawOrder,
    RawPosition,
    RawSnapshot,
    RawTrade,
    RunManifest,
    StrategyProvenance,
    StrategySourceKind,
    WarningSeverity,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FloatParameter,
    IntegerParameter,
)


def _result() -> BacktestRunResult:
    """결과 파일이 싣는 칸을 전부 채운 결과(지표·곡선·거래·경고·manifest·파라미터 값)."""
    created = datetime(2026, 9, 3, tzinfo=UTC)
    registry = build_default_metric_registry()
    template = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "unused",
    ).template()
    strategy = replace(
        template,
        parameters=(
            FloatParameter("weight", 0.5, 0.0, 1.0, "float", 0.1),
            IntegerParameter("count", 20, 5, 40, "integer", 5),
            ChoiceParameter("mode", "fast", ("fast", 2, True), "choice"),
        ),
    )
    environment = RunEnvironment(
        start=date(2021, 1, 1), end=date(2026, 8, 31), universe_id="krx.common-stock"
    )
    run_spec = BacktestRunSpec(
        strategy=strategy,
        environment=environment,
        benchmark_security_id="KRX:069500",
        metric_windows=(
            MetricWindow(MetricScope.WINDOW, date(2022, 1, 3), date(2022, 12, 29), "2022"),
        ),
        # 정수·실수·bool 이 JSON 을 지나도 같은 타입으로 돌아와야 한다(1 과 1.0 은 `==` 로 같다).
        parameter_values={"weight": 1.0, "count": 25, "mode": True},
    )
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
            fee_bps=environment.fee_bps,
            slippage_bps=environment.slippage_bps,
            participation_rate=environment.participation_rate,
            environment=environment,
            environment_hash=environment_hash(environment),
            strategy_provenance=StrategyProvenance(
                StrategySourceKind.INLINE_DRAFT, "strategy", "1.1", source_hash="d" * 64
            ),
            warnings=(
                DataWarning("portfolio.raw_observation", "late filing", WarningSeverity.INFO),
                DataWarning("backtest.data.gap", "missing bar"),
            ),
        ),
        metric_definitions=registry.definitions(),
        metrics=(
            MetricValue("total_return", 0.0, MetricScope.FULL, 1),
            MetricValue(
                "sharpe",
                None,
                MetricScope.FULL,
                1,
                unavailable_reason=MetricUnavailableReason.ZERO_RETURN_VARIANCE,
            ),
            MetricValue("cagr", 0.125, MetricScope.WINDOW, 250, scope_label="2022"),
        ),
        series=BacktestSeries(
            equity=(
                EquityCurvePoint(date(2026, 1, 2), 100.0, None),
                EquityCurvePoint(date(2026, 1, 5), 101.5, 100.25),
            ),
            drawdown=(DrawdownPoint(date(2026, 1, 5), -0.015),),
            monthly_returns=(MonthlyReturnPoint(2026, 1, 0.015),),
            rolling_sharpe=(
                RollingMetricPoint(date(2026, 1, 2), None),
                RollingMetricPoint(date(2026, 1, 5), 1.25),
            ),
            rolling_sharpe_window_sessions=126,
        ),
        artifacts=RawArtifactBundle(
            snapshots=(RawSnapshot(date(2026, 1, 2), 100.0, 100.0, 0.0, 0.0),),
            positions=(RawPosition(date(2026, 1, 5), "KRX:005930", "3", 30.0, 31.0, 93.0, 3.0),),
            orders=(
                RawOrder("o-1", "d-1", date(2026, 1, 2), "KRX:005930", "buy", "3", "market", "day"),
            ),
            fills=(
                RawFill(
                    "f-1", "o-1", date(2026, 1, 5), "KRX:005930", "buy", "3", 30.0, 0.1, 0.03, 700
                ),
            ),
            costs=(
                RawCost(date(2026, 1, 5), "fee", "KRX:005930", 0.1),
                RawCost(date(2026, 1, 5), "tax", None, 0.0),
            ),
            trades=(
                RawTrade(
                    "KRX:005930",
                    date(2026, 1, 5),
                    date(2026, 1, 6),
                    "long",
                    "3",
                    30.0,
                    31.0,
                    2.8,
                    0.2,
                    0.09,
                ),
            ),
        ),
    )


def test_local_artifact_store_commits_atomically_and_preserves_null_vs_zero(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)

    commit = store.commit(_result())

    result_path = tmp_path / "run-safe-001" / "result.json"
    manifest_path = tmp_path / "run-safe-001" / "manifest.json"
    payload = result_path.read_bytes()
    decoded = json.loads(payload)
    assert manifest_path.exists()
    assert commit.sha256 == hashlib.sha256(payload).hexdigest()
    assert decoded["metrics"][0]["value"] == 0.0
    assert decoded["metrics"][1]["value"] is None
    assert not tuple(tmp_path.glob(".*.tmp"))

    with pytest.raises(FileExistsError):
        store.commit(_result())

    store.discard("run-safe-001")
    assert not result_path.parent.exists()
    store.discard("run-safe-001")


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

    with pytest.raises(ValueError, match="escapes artifact root"):
        LocalArtifactStore(tmp_path).discard("../outside")

    with pytest.raises(ValueError, match="escapes artifact root"):
        LocalArtifactStore(tmp_path).load("../outside", sha256="0" * 64)


def test_a_committed_result_loads_back_equal_in_every_field(tmp_path: Path) -> None:
    """인코더(`_json_value`)와 디코더의 왕복. dataclass `==` 가 칸 전부를 깊게 비교한다."""
    result = _result()
    commit = LocalArtifactStore(tmp_path).commit(result)

    loaded = LocalArtifactStore(tmp_path).load("run-safe-001", sha256=commit.sha256)

    assert loaded == result
    # `==` 는 1 과 1.0·True 를 같게 보므로 타입을 따로 본다.
    run_spec = loaded.manifest.run_spec
    values = run_spec.parameter_values
    assert [type(values[key]) for key in ("weight", "count", "mode")] == [float, int, bool]
    assert run_spec.strategy is not None
    choice = run_spec.strategy.parameters[2]
    assert isinstance(choice, ChoiceParameter)
    assert [type(value) for value in choice.choices] == [str, int, bool]


def test_a_result_file_written_before_the_rolling_window_field_still_loads(tmp_path: Path) -> None:
    """#303 전에 쓴 `result.json` 에는 롤링 창 칸이 없다. 410 대신 None 으로 읽힌다."""
    store = LocalArtifactStore(tmp_path)
    store.commit(_result())
    result_path = tmp_path / "run-safe-001" / "result.json"
    payload = result_path.read_bytes()
    old = payload.replace(b',"rolling_sharpe_window_sessions":126', b"")
    assert old != payload
    result_path.write_bytes(old)

    loaded = store.load("run-safe-001", sha256=hashlib.sha256(old).hexdigest())

    assert loaded.series.rolling_sharpe_window_sessions is None


def test_a_result_file_written_before_fill_cap_volume_still_loads(tmp_path: Path) -> None:
    """V4-04 2/2 전에 쓴 `result.json`(`backtest-artifacts-v1`)에는 체결 기준 거래량이 없다."""
    store = LocalArtifactStore(tmp_path)
    store.commit(_result())
    result_path = tmp_path / "run-safe-001" / "result.json"
    payload = result_path.read_bytes()
    old = payload.replace(b'"cap_volume":700,', b"").replace(
        b"backtest-artifacts-v2", b"backtest-artifacts-v1"
    )
    assert old.count(b"cap_volume") == 0 and b"backtest-artifacts-v1" in old
    result_path.write_bytes(old)

    loaded = store.load("run-safe-001", sha256=hashlib.sha256(old).hexdigest())

    assert [fill.cap_volume for fill in loaded.artifacts.fills] == [None]
    assert loaded.artifacts.schema_version == "backtest-artifacts-v1"


def test_a_missing_altered_or_undecodable_result_file_is_a_coded_error(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    commit = store.commit(_result())
    result_path = tmp_path / "run-safe-001" / "result.json"
    payload = result_path.read_bytes()

    with pytest.raises(BacktestArtifactUnreadableError, match="missing — run_id=other"):
        store.load("other", sha256=commit.sha256)

    result_path.write_bytes(payload.replace(b'"engine_version":"test"', b'"engine_version":"evil"'))
    with pytest.raises(BacktestArtifactUnreadableError, match="sha256 — run_id=run-safe-001"):
        store.load("run-safe-001", sha256=commit.sha256)

    # 해시는 맞지만 결과 모델이 읽지 못하는 파일(모델이 파일을 쓴 뒤 바뀐 경우).
    undecodable = payload.replace(b'"annualization_days":252', b'"annualization_days":"x"')
    result_path.write_bytes(undecodable)
    with pytest.raises(BacktestArtifactUnreadableError, match="does not decode") as raised:
        store.load("run-safe-001", sha256=hashlib.sha256(undecodable).hexdigest())
    # 칸 위치는 싣고 서버 경로와 파일 내용은 싣지 않는다.
    message = str(raised.value)
    assert "annualization_days" in message
    assert str(tmp_path) not in message and "'x'" not in message


def test_opening_the_store_removes_staging_left_by_a_killed_commit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    commit = LocalArtifactStore(tmp_path).commit(_result())
    orphan = tmp_path / ".run-killed.0123abcd.tmp"
    orphan.mkdir()
    (orphan / "result.json").write_bytes(b"{partial")

    with caplog.at_level(logging.WARNING):
        store = LocalArtifactStore(tmp_path)

    assert not orphan.exists()
    assert "orphan run artifact staging removed — name=.run-killed.0123abcd.tmp" in caplog.text
    # 커밋을 마친 run 은 그대로다.
    assert store.load("run-safe-001", sha256=commit.sha256) == _result()


def test_staging_that_cannot_be_removed_does_not_block_opening_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    orphan = tmp_path / ".run-locked.0123abcd.tmp"
    orphan.mkdir()

    def locked(path: Path) -> None:
        raise PermissionError(f"locked: {path}")

    monkeypatch.setattr(shutil, "rmtree", locked)
    LocalArtifactStore(tmp_path)

    assert orphan.exists()
    assert "orphan run artifact staging not removed — name=.run-locked.0123abcd.tmp" in caplog.text
