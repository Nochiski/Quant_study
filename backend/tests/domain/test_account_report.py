"""초기자본 기준 보고서를 합성 계좌의 독립 손계산과 비교한다."""

import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime

import pytest

from strategy_workbench.domain.analytics.facade.metrics import (
    AnalysisPoint,
    AnalyticsInput,
    MetricScope,
    build_default_metric_registry,
    compute_analytics,
)
from strategy_workbench.domain.backtest.facade.account_report import (
    AccountReportStatus,
    project_account_report,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestSeries,
    DataWarning,
    RawArtifactBundle,
    RawCost,
    RawSnapshot,
    RunManifest,
    StrategyProvenance,
    StrategySourceKind,
)


def _snapshot(day: int, equity: float, cash: float = 80.0) -> RawSnapshot:
    return RawSnapshot(date(2024, 1, day), cash, equity, 0.0, 0.0)


def _result(snapshots: tuple[RawSnapshot, ...], initial_cash: float = 1000.0) -> BacktestRunResult:
    environment = RunEnvironment(start=date(2024, 1, 2), end=date(2024, 1, 5), universe_id="test")
    created = datetime(2024, 1, 6, tzinfo=UTC)
    spec = BacktestRunSpec(initial_cash=1000.0)
    # 저장/실행 접수의 검증과 별개로 손상된 과거 값에 대한 조회 경계를 검산한다.
    object.__setattr__(spec, "initial_cash", initial_cash)
    manifest = RunManifest(
        run_id="account-run",
        created_at=created,
        completed_at=created,
        engine_version="historical-engine",
        run_fingerprint="original-fingerprint",
        run_spec=spec,
        strategy_hash="strategy-hash",
        data_snapshot_id="original-snapshot",
        target_tape_hash="original-tape",
        metric_registry_version="original-registry",
        environment=environment,
        environment_hash="original-environment-hash",
        strategy_provenance=StrategyProvenance(
            StrategySourceKind.SAVED_REVISION, "strategy-hash", "1.2", "strategy", 7
        ),
        warnings=(DataWarning("original.warning", "기존 경고입니다: security=A"),),
        factor_evaluation_version=None,
    )
    return BacktestRunResult(
        manifest,
        (),
        (),
        BacktestSeries((), (), (), ()),
        RawArtifactBundle(
            snapshots, (), (), (), (RawCost(date(2024, 1, 2), "fee", "A", 10.0),), ()
        ),
    )


def test_initial_capital_includes_first_snapshot_cost_without_changing_full() -> None:
    result = _result((_snapshot(2, 990.0), _snapshot(5, 1080.0)))
    report = project_account_report(result)
    assert report.status is AccountReportStatus.AVAILABLE
    assert report.summary is not None
    assert report.summary.pnl == 80.0
    assert report.summary.total_return == pytest.approx(0.08)
    assert report.summary.final_cash == 80.0
    assert report.summary.final_equity == 1080.0
    assert report.summary.initial_cash == 1000.0
    assert report.summary.first_snapshot_date == date(2024, 1, 2)
    assert report.summary.last_snapshot_date == date(2024, 1, 5)
    assert report.summary.snapshot_count == 2
    analytics = compute_analytics(
        AnalyticsInput(
            points=tuple(
                AnalysisPoint(s.session, s.equity, 0, 0) for s in result.artifacts.snapshots
            ),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
    )
    full_return = next(
        item.value
        for item in analytics.metrics
        if item.metric_id == "total_return" and item.scope is MetricScope.FULL
    )
    assert full_return == pytest.approx(1080 / 990 - 1)
    assert full_return != pytest.approx(report.summary.total_return)


@pytest.mark.parametrize(
    ("snapshots", "pnl", "total_return"),
    [
        ((_snapshot(2, 990.0),), -10.0, -0.01),
        ((_snapshot(2, 1000.0, 1000.0), _snapshot(5, 1000.0, 1000.0)), 0.0, 0.0),
        ((_snapshot(2, 0.0, -10.0),), -1000.0, -1.0),
        ((_snapshot(2, -100.0, -200.0),), -1100.0, -1.1),
    ],
)
def test_single_snapshot_no_trade_and_debt_are_account_facts(
    snapshots: tuple[RawSnapshot, ...], pnl: float, total_return: float
) -> None:
    report = project_account_report(_result(snapshots))
    assert report.summary is not None
    assert report.summary.pnl == pnl
    assert report.summary.total_return == pytest.approx(total_return)
    assert report.summary.final_cash == snapshots[-1].cash


def test_projection_preserves_source_warnings_and_input_without_new_validation_claims() -> None:
    result = _result((_snapshot(2, 990.0), _snapshot(5, 1080.0)))
    before = asdict(result)
    report = project_account_report(result)
    assert asdict(result) == before
    assert project_account_report(result) == report
    assert report.warnings is result.manifest.warnings
    assert report.provenance.strategy_provenance is result.manifest.strategy_provenance
    for field, value in asdict(report.provenance).items():
        assert value == asdict(result.manifest)[field]
    assert report.provenance.factor_evaluation_version is None
    assert report.evaluation_basis == "initial_capital"
    assert "청산 현금" in report.interpretation
    assert "기존 전체 구간(FULL) 수익률은 첫 계좌 기록" in report.interpretation


def test_empty_snapshots_have_explicit_status_without_synthetic_initial_point() -> None:
    report = project_account_report(_result(()))
    assert report.status is AccountReportStatus.EMPTY_SNAPSHOTS
    assert report.summary is None
    assert report.detail is not None
    assert "snapshot_count=0" in report.detail
    assert "run_id=account-run" in report.detail


@pytest.mark.parametrize("initial_cash", [float("nan"), float("inf"), -float("inf"), 0.0, -1.0])
def test_invalid_initial_capital_is_not_leaked_as_a_json_number(initial_cash: float) -> None:
    report = project_account_report(_result((_snapshot(2, 990.0),), initial_cash))
    assert report.status is AccountReportStatus.INVALID_INPUT
    assert report.summary is None
    assert report.detail is not None and "initial_cash=" in report.detail
    json.dumps(asdict(report), default=str, allow_nan=False)


@pytest.mark.parametrize("field", ["cash", "equity"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_snapshot_amounts_are_rejected_even_before_the_final_snapshot(
    field: str, value: float
) -> None:
    first = replace(_snapshot(2, 990.0), **{field: value})
    report = project_account_report(_result((first, _snapshot(5, 1080.0))))
    assert report.status is AccountReportStatus.INVALID_INPUT
    assert report.summary is None
    assert report.detail is not None and f"field={field}" in report.detail
    assert "index=0" in report.detail
    json.dumps(asdict(report), default=str, allow_nan=False)


@pytest.mark.parametrize("days", [(5, 2), (2, 2)])
def test_sessions_are_not_sorted_or_deduplicated(days: tuple[int, int]) -> None:
    result = _result(tuple(_snapshot(day, 1000.0) for day in days))
    original = result.artifacts.snapshots
    report = project_account_report(result)
    assert report.status is AccountReportStatus.INVALID_INPUT
    assert report.summary is None
    assert result.artifacts.snapshots is original
    assert report.detail is not None and "previous_session=" in report.detail


@pytest.mark.parametrize(("initial_cash", "final_equity"), [(1e-308, 1e308), (1e308, -1e308)])
def test_finite_inputs_with_arithmetic_overflow_are_explicitly_unavailable(
    initial_cash: float, final_equity: float
) -> None:
    report = project_account_report(_result((_snapshot(2, final_equity),), initial_cash))
    assert report.status is AccountReportStatus.INVALID_INPUT
    assert report.summary is None
    assert report.detail is not None and "계산 결과가 유한하지" in report.detail
    json.dumps(asdict(report), default=str, allow_nan=False)


def test_report_rejects_impossible_status_summary_detail_combinations() -> None:
    available = project_account_report(_result((_snapshot(2, 990.0),)))
    with pytest.raises(ValueError, match="run_id=account-run"):
        replace(available, summary=None)
    with pytest.raises(ValueError, match="status="):
        replace(available, status=AccountReportStatus.INVALID_INPUT)
    with pytest.raises(ValueError, match="detail="):
        replace(available, detail="unexpected")
    unavailable = project_account_report(_result(()))
    with pytest.raises(ValueError, match="detail="):
        replace(unavailable, detail=None)
    assert available.summary is not None
    with pytest.raises(ValueError, match="snapshot_count=0"):
        replace(available.summary, snapshot_count=0)
