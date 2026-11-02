"""시장 참고선(코스피·코스닥)을 run 세션 달력과 초기자본에 맞추는 투영."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.market_reference import (
    MarketReferenceStatus,
    project_market_reference,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestSeries,
    RawArtifactBundle,
    RawSnapshot,
    RunManifest,
    StrategyProvenance,
    StrategySourceKind,
)

SESSIONS = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
REGISTRY = build_default_metric_registry()


def _result() -> BacktestRunResult:
    created = datetime(2024, 1, 5, tzinfo=UTC)
    manifest = RunManifest(
        run_id="run-1",
        created_at=created,
        completed_at=created,
        engine_version="engine",
        run_fingerprint="fingerprint",
        run_spec=BacktestRunSpec(initial_cash=1_000.0),
        strategy_hash="strategy-hash",
        data_snapshot_id="snapshot",
        target_tape_hash="tape",
        metric_registry_version="registry",
        environment=RunEnvironment(start=SESSIONS[0], end=SESSIONS[-1], universe_id="test"),
        environment_hash="environment-hash",
        strategy_provenance=StrategyProvenance(
            StrategySourceKind.SAVED_REVISION, "strategy-hash", "1.2", "strategy", 1
        ),
        warnings=(),
        factor_evaluation_version=None,
    )
    snapshots = tuple(RawSnapshot(session, 0.0, 1_000.0, 1.0, 1.0) for session in SESSIONS)
    return BacktestRunResult(
        manifest,
        (),
        (),
        BacktestSeries((), (), (), ()),
        RawArtifactBundle(snapshots, (), (), (), (), ()),
    )


def _metric(series, metric_id: str):
    return next(item for item in series.metrics if item.metric_id == metric_id)


def test_index_curve_starts_at_initial_cash_and_reuses_registry_metrics() -> None:
    closes = {"코스피": dict(zip(SESSIONS, (2_000.0, 2_200.0, 1_980.0), strict=True))}
    report = project_market_reference(_result(), closes, REGISTRY)
    kospi = report.series[0]
    assert (kospi.index_id, kospi.status) == ("kospi", MarketReferenceStatus.AVAILABLE)
    assert [point.value for point in kospi.points] == pytest.approx([1_000.0, 1_100.0, 990.0])
    assert _metric(kospi, "total_return").value == pytest.approx(-0.01)
    # 고점 1,100 에서 990 — 전략과 같은 레지스트리 공식이다.
    assert _metric(kospi, "max_drawdown").value == pytest.approx(-0.1)
    assert [point.drawdown for point in kospi.drawdown] == pytest.approx([0.0, 0.0, -0.1])
    # 1년 미만이라 칼마는 전략과 같은 사유로 빈다.
    calmar = _metric(kospi, "calmar")
    assert (calmar.value, calmar.unavailable_reason) == (None, "period_under_one_year")


def test_index_with_a_missing_session_is_not_filled() -> None:
    closes = {
        "코스피": dict(zip(SESSIONS, (2_000.0, 2_200.0, 1_980.0), strict=True)),
        "코스닥": {SESSIONS[0]: 800.0, SESSIONS[2]: 820.0},
    }
    kosdaq = project_market_reference(_result(), closes, REGISTRY).series[1]
    assert kosdaq.status is MarketReferenceStatus.NOT_AVAILABLE
    assert (kosdaq.points, kosdaq.drawdown, kosdaq.metrics) == ((), (), ())
    assert kosdaq.detail is not None and "first=2024-01-03" in kosdaq.detail


def test_source_without_indices_reports_its_reason_for_every_index() -> None:
    report = project_market_reference(_result(), {}, REGISTRY, unavailable_reason="지수 없음")
    assert [(item.status, item.detail) for item in report.series] == [
        (MarketReferenceStatus.NOT_AVAILABLE, "지수 없음"),
        (MarketReferenceStatus.NOT_AVAILABLE, "지수 없음"),
    ]
