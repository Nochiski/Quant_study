from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.domain.analytics.facade.metrics import (
    AnalysisPoint,
    AnalyticsInput,
    MetricScope,
    MetricUnavailableReason,
    TradeOutcome,
    build_default_metric_registry,
    compute_analytics,
    unavailable_metric_values,
)


def _metric(report, metric_id: str, scope: MetricScope = MetricScope.FULL):
    return next(
        item for item in report.metrics if item.metric_id == metric_id and item.scope is scope
    )


def test_versioned_registry_calculates_risk_benchmark_trade_exposure_and_cost_metrics() -> None:
    registry = build_default_metric_registry()
    report = compute_analytics(
        AnalyticsInput(
            points=(
                AnalysisPoint(date(2026, 1, 2), 100.0, 0.2, 0.2, 100.0),
                AnalysisPoint(date(2026, 1, 5), 80.0, 0.8, 0.4, 105.0),
                AnalysisPoint(date(2026, 1, 6), 90.0, 1.0, -0.2, 106.0),
                AnalysisPoint(date(2026, 1, 7), 110.0, 0.6, 0.1, 108.0),
            ),
            traded_notional=140.0,
            trades=(
                TradeOutcome("005930", date(2026, 1, 6), 10.0, 1.0, 0.5),
                TradeOutcome("000660", date(2026, 1, 7), -5.0, 2.0, 0.7),
            ),
            total_fees=3.0,
            total_slippage_cost=1.2,
            total_carry_cost=0.4,
        ),
        registry,
    )

    assert registry.version == "metric-registry-v1"
    assert len(registry.definitions()) == 21
    assert _metric(report, "total_return").value == pytest.approx(0.1)
    assert _metric(report, "max_drawdown").value == pytest.approx(-0.2)
    assert _metric(report, "max_drawdown_duration_sessions").value == 2.0
    assert _metric(report, "max_drawdown_recovery_sessions").value == 2.0
    assert _metric(report, "benchmark_return").value == pytest.approx(0.08)
    assert _metric(report, "excess_return").value == pytest.approx(0.02)
    # 체결 금액 140 / 평균 equity 95
    assert _metric(report, "turnover").value == pytest.approx(140.0 / 95.0)
    assert _metric(report, "trade_count").value == 2.0
    assert _metric(report, "win_rate").value == 0.5
    assert _metric(report, "profit_factor").value == 2.0
    assert _metric(report, "average_gross_exposure").value == pytest.approx(0.65)
    assert _metric(report, "average_net_exposure").value == pytest.approx(0.125)
    assert _metric(report, "total_fees").value == 3.0
    assert _metric(report, "total_slippage_cost").value == 1.2
    assert _metric(report, "total_carry_cost").value == 0.4


def test_metric_values_preserve_zero_and_explain_unavailable_values_per_scope() -> None:
    report = compute_analytics(
        AnalyticsInput(
            points=(
                AnalysisPoint(date(2026, 1, 2), 100.0, 0.0, 0.0),
                AnalysisPoint(date(2026, 1, 5), 100.0, 0.0, 0.0),
            ),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
        scope=MetricScope.OUT_OF_SAMPLE,
        scope_label="OOS 2026",
    )

    total_return = _metric(report, "total_return", MetricScope.OUT_OF_SAMPLE)
    sharpe = _metric(report, "sharpe", MetricScope.OUT_OF_SAMPLE)
    fees = _metric(report, "total_fees", MetricScope.OUT_OF_SAMPLE)

    assert total_return.value == 0.0
    assert fees.value == 0.0
    assert sharpe.value is None
    assert sharpe.unavailable_reason == "zero_return_variance"
    assert sharpe.scope_label == "OOS 2026"
    assert sharpe.sample_count == 1


def test_flat_equity_curve_reports_unavailable_ratios_instead_of_zero() -> None:
    report = compute_analytics(
        AnalyticsInput(
            points=tuple(AnalysisPoint(date(2026, 1, day), 100.0, 0.0, 0.0) for day in (5, 6, 7)),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
    )

    assert _metric(report, "volatility").value == 0.0
    assert _metric(report, "max_drawdown").value == 0.0
    # 변동성·하방 변동·낙폭이 0이면 비율을 0으로 위장하지 않는다.
    assert _metric(report, "sharpe").unavailable_reason == "zero_return_variance"
    assert _metric(report, "sortino").unavailable_reason == "no_downside_variation"
    assert _metric(report, "calmar").unavailable_reason == "no_drawdown"


def test_empty_equity_curve_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one equity point"):
        compute_analytics(
            AnalyticsInput(points=(), traded_notional=0.0), build_default_metric_registry()
        )


def test_monthly_returns_include_the_previous_month_close_boundary() -> None:
    report = compute_analytics(
        AnalyticsInput(
            points=(
                AnalysisPoint(date(2026, 1, 30), 100.0, 0.0, 0.0),
                AnalysisPoint(date(2026, 1, 31), 110.0, 0.0, 0.0),
                AnalysisPoint(date(2026, 2, 2), 99.0, 0.0, 0.0),
            ),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
    )

    assert tuple(item.value for item in report.monthly_returns) == pytest.approx((0.1, -0.1))


def test_requested_empty_scope_is_serialized_as_unavailable_instead_of_disappearing() -> None:
    values = unavailable_metric_values(
        build_default_metric_registry(),
        scope=MetricScope.VALIDATION,
        scope_label="weekend only",
        reason=MetricUnavailableReason.NO_OBSERVATIONS_IN_SCOPE,
    )

    assert len(values) == 21
    assert all(item.value is None for item in values)
    assert all(item.sample_count == 0 for item in values)
    assert {item.scope for item in values} == {MetricScope.VALIDATION}
    assert {item.unavailable_reason for item in values} == {"no_observations_in_scope"}
