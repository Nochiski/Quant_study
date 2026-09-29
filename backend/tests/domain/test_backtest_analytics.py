from __future__ import annotations

import math
import statistics
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
            total_taxes=0.6,
            total_slippage_cost=1.2,
            total_carry_cost=0.4,
        ),
        registry,
    )

    assert registry.version == "metric-registry-v2"
    assert len(registry.definitions()) == 22
    assert _metric(report, "total_taxes").value == pytest.approx(0.6)
    assert _metric(report, "total_return").value == pytest.approx(0.1)
    assert _metric(report, "max_drawdown").value == pytest.approx(-0.2)
    assert _metric(report, "max_drawdown_duration_sessions").value == 2.0
    assert _metric(report, "max_drawdown_recovery_sessions").value == 2.0
    assert _metric(report, "benchmark_return").value == pytest.approx(0.08)
    assert _metric(report, "excess_return").value == pytest.approx(0.02)
    # 체결 금액 140 / 평균 equity 95
    assert _metric(report, "turnover").value == pytest.approx(140.0 / 95.0)
    # 세션 수익률 80/100-1, 90/80-1, 110/90-1. 평균 53/1080, 연율화 252.
    returns = (-0.2, 0.125, 2 / 9)
    mean = 53 / 1080
    sample_std = statistics.stdev(returns)  # 표본 표준편차(분모 n-1)
    downside_std = math.sqrt(0.2**2 / 3)  # 음수 수익률 제곱합 / 전체 수익률 수
    assert _metric(report, "volatility").value == pytest.approx(sample_std * math.sqrt(252))
    assert _metric(report, "sharpe").value == pytest.approx(mean / sample_std * math.sqrt(252))
    assert _metric(report, "sortino").value == pytest.approx(mean / downside_std * math.sqrt(252))
    # 1/2 → 1/7 은 5일이라 1년 미만: 예전처럼 1.1^(252/3) - 1 로 부풀리지 않고 비운다.
    for metric_id in ("cagr", "calmar"):
        assert _metric(report, metric_id).value is None
        assert _metric(report, metric_id).unavailable_reason == "period_under_one_year"
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
            # 1년이 넘어야 칼마 사유가 기간이 아니라 낙폭에서 나온다.
            points=tuple(
                AnalysisPoint(session, 100.0, 0.0, 0.0)
                for session in (date(2025, 1, 2), date(2025, 7, 1), date(2026, 1, 5))
            ),
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


def test_cagr_counts_calendar_days_from_the_base_session_not_sessions() -> None:
    # 기준일 2025-01-02 → 2027-01-02 는 730일 = 2년. 연수를 첫 곡선 점(6/30)부터 세면 551/365 년이라
    # CAGR 이 0.135 가 된다. 세션은 둘뿐이라 예전 `세션 수 / 252` 로는 CAGR 이 터무니없이 커졌다.
    report = compute_analytics(
        AnalyticsInput(
            points=(
                AnalysisPoint(date(2025, 6, 30), 90.0, 0.0, 0.0),
                AnalysisPoint(date(2027, 1, 2), 121.0, 0.0, 0.0),
            ),
            traded_notional=0.0,
            base=AnalysisPoint(date(2025, 1, 2), 100.0, 0.0, 0.0),
        ),
        build_default_metric_registry(),
    )

    # 1.21 ** (1 / 2) - 1 = 0.1, 칼마 = 0.1 / |90 / 100 - 1| = 1.0
    assert _metric(report, "cagr").value == pytest.approx(0.1)
    assert _metric(report, "calmar").value == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("first_session", "last_session", "cagr", "reason"),
    [
        # 364일: 1년 미만이라 연율화하지 않는다(GIPS). 총수익률은 그대로 보인다.
        (date(2025, 1, 2), date(2026, 1, 1), None, "period_under_one_year"),
        # 365일 = 1년: 연율화한 값이 총수익률과 같다.
        (date(2025, 1, 2), date(2026, 1, 2), 0.05, None),
        # 윤년이 낀 달력 1년 366일: 연율화하고, 연수가 366/365 라 총수익률보다 조금 낮다.
        (date(2028, 1, 2), date(2029, 1, 2), 1.05 ** (365 / 366) - 1, None),
    ],
)
def test_cagr_and_calmar_are_not_annualized_under_one_calendar_year(
    first_session: date, last_session: date, cagr: float | None, reason: str | None
) -> None:
    report = compute_analytics(
        AnalyticsInput(
            points=(
                AnalysisPoint(first_session, 100.0, 0.0, 0.0),
                AnalysisPoint(date(first_session.year, 6, 2), 95.0, 0.0, 0.0),
                AnalysisPoint(last_session, 105.0, 0.0, 0.0),
            ),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
    )

    assert _metric(report, "total_return").value == pytest.approx(0.05)
    assert _metric(report, "cagr").value == pytest.approx(cagr)
    assert _metric(report, "cagr").unavailable_reason == reason
    # 칼마의 분자가 CAGR 이라 같은 사유로 빈다. 값이 있으면 CAGR / |95 / 100 - 1|
    assert _metric(report, "calmar").value == pytest.approx(None if cagr is None else cagr / 0.05)
    assert _metric(report, "calmar").unavailable_reason == reason


@pytest.mark.parametrize(
    ("points", "base"),
    [
        # 곡선 중간에 자산이 0이면 다음 수익률이 0으로 나누기다.
        ((100.0, 0.0, 0.0), None),
        # 구간 기준 자산이 0이면 총수익률이 0으로 나누기다.
        ((90.0,), 0.0),
    ],
)
def test_non_positive_equity_breaks_the_engine_contract(
    points: tuple[float, ...], base: float | None
) -> None:
    # 엔진은 자산 0 이하에서 EquityWipedOut 으로 멈추므로 이런 곡선은 오지 않는다.
    with pytest.raises(ValueError, match="positive equity — session=.* equity=0.0"):
        compute_analytics(
            AnalyticsInput(
                points=tuple(
                    AnalysisPoint(date(2025, 1, 2 + index), equity, 0.0, 0.0)
                    for index, equity in enumerate(points)
                ),
                traded_notional=0.0,
                base=None if base is None else AnalysisPoint(date(2024, 12, 30), base, 0.0, 0.0),
            ),
            build_default_metric_registry(),
        )


def test_window_base_starts_returns_drawdown_and_years_but_stays_off_the_curve() -> None:
    # 구간 직전 세션(1/30, 120)이 기준이다. 구간 첫날 120 → 110 하락이 수익률·낙폭에 들어간다.
    report = compute_analytics(
        AnalyticsInput(
            points=(
                AnalysisPoint(date(2026, 2, 2), 110.0, 0.2, 0.2, 210.0),
                AnalysisPoint(date(2026, 2, 3), 99.0, 0.4, 0.0, 231.0),
            ),
            traded_notional=0.0,
            base=AnalysisPoint(date(2026, 1, 30), 120.0, 1.0, 1.0, 200.0),
        ),
        build_default_metric_registry(),
        scope=MetricScope.OUT_OF_SAMPLE,
    )

    def metric(metric_id: str):
        return _metric(report, metric_id, MetricScope.OUT_OF_SAMPLE)

    assert metric("total_return").value == pytest.approx(99.0 / 120.0 - 1.0)
    assert metric("total_return").sample_count == 2
    assert metric("max_drawdown").value == pytest.approx(99.0 / 120.0 - 1.0)
    assert metric("max_drawdown_duration_sessions").value == 2.0
    assert metric("benchmark_return").value == pytest.approx(0.155)
    # 기준점은 곡선·노출에 들어가지 않는다.
    assert metric("average_gross_exposure").value == pytest.approx(0.3)
    assert metric("turnover").value == 0.0
    assert [item.session for item in report.equity_curve] == [date(2026, 2, 2), date(2026, 2, 3)]
    assert [item.session for item in report.drawdown_curve] == [date(2026, 2, 2), date(2026, 2, 3)]
    assert [item.value for item in report.monthly_returns] == pytest.approx([99.0 / 120.0 - 1.0])


def test_window_base_must_precede_the_window() -> None:
    point = AnalysisPoint(date(2026, 2, 2), 100.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="base must precede"):
        compute_analytics(
            AnalyticsInput(points=(point,), traded_notional=0.0, base=point),
            build_default_metric_registry(),
        )


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

    assert len(values) == 22
    assert all(item.value is None for item in values)
    assert all(item.sample_count == 0 for item in values)
    assert {item.scope for item in values} == {MetricScope.VALIDATION}
    assert {item.unavailable_reason for item in values} == {"no_observations_in_scope"}
