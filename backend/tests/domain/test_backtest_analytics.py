from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from itertools import pairwise

import pytest

from strategy_workbench.domain.analytics._base_rate import base_rate
from strategy_workbench.domain.analytics.facade.metrics import (
    BASE_RATE_CONFIRMED_ON,
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

    assert registry.version == "metric-registry-v3"
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
    sample_std = statistics.stdev(returns)  # 표본 표준편차(분모 n-1)
    assert _metric(report, "volatility").value == pytest.approx(sample_std * math.sqrt(252))
    # 샤프·소르티노의 분자는 기준금리 초과수익 평균이다. 기준금리 2.50%(2025-05-29 변경)를 직전
    # 세션부터 달력 일수만큼 ACT/365로 일할한다: 1/2 → 1/5 는 주말을 끼어 3일, 나머지는 1일.
    mean = 53 / 1080 - 0.025 * 5 / 365 / 3
    downside_std = math.sqrt((0.2 + 0.025 * 3 / 365) ** 2 / 3)  # 음의 초과수익 제곱합 / 전체 수
    # 샤프 분모는 원수익률 표준편차 그대로다.
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


@pytest.mark.parametrize(
    ("equity", "sharpe_reason", "sortino", "sortino_reason"),
    [
        # 평평한 곡선은 구간마다 같은 무위험수익률 f 만큼 뒤처져 초과수익이 모두 -f 다. 변동이 0이라
        # 샤프는 비고, 소르티노 = -f / sqrt(f²) · sqrt(252) = -sqrt(252).
        ((100.0, 100.0, 100.0), "zero_return_variance", -math.sqrt(252), None),
        # 구간마다 무위험수익률(0.005 · 183 / 365)보다 더 벌면 하방 변동이 없다.
        ((100.0, 101.0, 103.0), None, None, "no_downside_variation"),
    ],
)
def test_flat_equity_curve_reports_unavailable_ratios_instead_of_zero(
    equity: tuple[float, ...],
    sharpe_reason: str | None,
    sortino: float | None,
    sortino_reason: str | None,
) -> None:
    # 1년이 넘어야 칼마 사유가 기간이 아니라 낙폭에서 나온다. 두 구간 모두 183일이고 기준금리가
    # 0.50%(2020-05-28 ~ 2021-08-25)로 같아 무위험수익률도 같다.
    sessions = (date(2020, 6, 1), date(2020, 12, 1), date(2021, 6, 2))
    report = compute_analytics(
        AnalyticsInput(
            points=tuple(
                AnalysisPoint(session, value, 0.0, 0.0)
                for session, value in zip(sessions, equity, strict=True)
            ),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
    )

    # 초과수익 변동·하방 변동·낙폭이 0이면 비율을 0으로 위장하지 않는다.
    assert _metric(report, "max_drawdown").value == 0.0
    assert _metric(report, "calmar").unavailable_reason == "no_drawdown"
    assert _metric(report, "sharpe").unavailable_reason == sharpe_reason
    assert _metric(report, "sortino").value == pytest.approx(sortino)
    assert _metric(report, "sortino").unavailable_reason == sortino_reason


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


@pytest.mark.parametrize(
    ("day", "rate"),
    [
        (date(1999, 5, 5), None),  # 첫 변경일 전날: 이력이 없어 지어내지 않는다
        (date(1999, 5, 6), 0.0475),
        (date(2026, 7, 15), 0.025),  # 변경 전날은 옛 금리
        (date(2026, 7, 16), 0.0275),  # 변경일 당일은 새 금리
    ],
)
def test_base_rate_is_effective_from_its_change_date(day: date, rate: float | None) -> None:
    assert base_rate(day) == pytest.approx(rate)


@pytest.mark.parametrize("with_base", [False, True])
def test_risk_free_uses_the_rate_at_interval_start_over_calendar_days(with_base: bool) -> None:
    # 7/16 에 2.50% → 2.75%. 7/15 → 7/16 구간은 시작일 금리 2.50%를 쓰고, 7/17 → 7/20 은
    # 주말을 끼어 3일이다. 7/15 를 구간 기준점(base)으로 넘겨도 첫 구간은 base 날짜 금리를 쓴다.
    points = tuple(
        AnalysisPoint(date(2026, 7, day), value, 0.0, 0.0)
        for day, value in ((15, 100.0), (16, 101.0), (17, 100.0), (20, 102.0))
    )
    report = compute_analytics(
        AnalyticsInput(
            points=points[1:] if with_base else points,
            traded_notional=0.0,
            base=points[0] if with_base else None,
        ),
        build_default_metric_registry(),
        rolling_window=3,
    )

    returns = (0.01, 100 / 101 - 1, 0.02)
    excess = (0.01 - 0.025 / 365, 100 / 101 - 1 - 0.0275 / 365, 0.02 - 0.0275 * 3 / 365)
    mean = statistics.mean(excess)
    sharpe = mean / statistics.stdev(returns) * math.sqrt(252)
    assert _metric(report, "sharpe").value == pytest.approx(sharpe)
    # 롤링 샤프도 같은 식을 쓴다. 창이 3이라 마지막 점은 전체 샤프와 같다.
    assert report.rolling_sharpe[-1].value == pytest.approx(sharpe)
    assert _metric(report, "sortino").value == pytest.approx(
        mean / math.sqrt(excess[1] ** 2 / 3) * math.sqrt(252)
    )


def test_deposit_like_curve_earning_the_base_rate_has_sharpe_near_zero() -> None:
    # DEFECT-1: 연 3.5%로 거의 흔들리지 않고 오르는 곡선이 무위험수익률 0 가정에서 샤프 4 이상을
    # 받았다. 기준금리 3.50%(2023-01-13 ~ 2024-10-10) 안의 평일 401세션 동안 자산이 기준금리
    # 일할 이자에 ±0.05%를 번갈아 더해 불어난다.
    days = (date(2023, 1, 16) + timedelta(days=offset) for offset in range(600))
    sessions = tuple(day for day in days if day.weekday() < 5)[:401]
    equity = [100.0]
    for index, (before, after) in enumerate(pairwise(sessions)):
        noise = 0.0005 if index % 2 == 0 else -0.0005
        equity.append(equity[-1] * (1 + 0.035 * (after - before).days / 365 + noise))
    report = compute_analytics(
        AnalyticsInput(
            points=tuple(
                AnalysisPoint(session, value, 0.0, 0.0)
                for session, value in zip(sessions, equity, strict=True)
            ),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
    )

    returns = tuple(after / before - 1 for before, after in pairwise(equity))
    assert statistics.mean(returns) / statistics.stdev(returns) * math.sqrt(252) > 4
    # 초과수익은 ±0.05%가 번갈아 400개라 평균이 0이다.
    assert _metric(report, "sharpe").value == pytest.approx(0.0, abs=1e-9)
    assert _metric(report, "sortino").value == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("noise", [0.0, 0.0001])
def test_cash_curve_counts_only_raw_return_swings_as_sharpe_risk(noise: float) -> None:
    # 기준금리 3.50% 고정 기간의 평일 1년. 무위험수익률은 평일 1일·월요일 3일 치라 구간마다 다르지만
    # 구간 시작에 이미 알려진 값이라 위험이 아니다. 분모가 초과수익 표준편차였다면 현금 곡선의
    # 샤프가 이 일할 차이만으로 -27 이 됐다.
    days = (date(2023, 1, 16) + timedelta(days=offset) for offset in range(365))
    sessions = tuple(day for day in days if day.weekday() < 5)
    equity = [100.0]
    for index in range(1, len(sessions)):
        equity.append(equity[-1] * (1 + (noise if index % 2 else -noise)))
    report = compute_analytics(
        AnalyticsInput(
            points=tuple(
                AnalysisPoint(session, value, 0.0, 0.0)
                for session, value in zip(sessions, equity, strict=True)
            ),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
    )

    returns = tuple(after / before - 1 for before, after in pairwise(equity))
    excess = tuple(
        value - 0.035 * (after - before).days / 365
        for value, (before, after) in zip(returns, pairwise(sessions), strict=True)
    )
    sharpe = statistics.mean(excess) / statistics.stdev(returns) * 252**0.5 if noise else None
    # 현금만 들면 원수익률 분산이 0이라 샤프를 비운다. ±0.01%만 흔들려도 기준금리에 꾸준히
    # 뒤처지는 진짜 결과라 큰 음수가 나온다.
    assert _metric(report, "sharpe").value == pytest.approx(sharpe)
    assert _metric(report, "sharpe").unavailable_reason == (
        None if noise else "zero_return_variance"
    )
    # 소르티노 목표는 무위험수익률이다. 모든 구간이 목표에 못 미치면 평균/하방편차가 -1 에 가까워
    # -√252 근처의 큰 음수가 된다(일할이 1일·3일로 섞여 현금 곡선은 약 -13.8).
    downside = math.sqrt(sum(min(item, 0.0) ** 2 for item in excess) / len(excess))
    assert _metric(report, "sortino").value == pytest.approx(
        statistics.mean(excess) / downside * 252**0.5
    )


def test_sessions_before_the_base_rate_history_leave_sharpe_sortino_and_rolling_empty() -> None:
    # 1999-05-04 → 05-06 구간은 시작일 금리가 없다(이력은 1999-05-06 부터).
    report = compute_analytics(
        AnalyticsInput(
            points=tuple(
                AnalysisPoint(date(1999, 5, day), value, 0.0, 0.0)
                for day, value in ((4, 100.0), (6, 101.0), (7, 99.0), (10, 102.0))
            ),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
        rolling_window=2,
    )

    assert _metric(report, "volatility").value is not None
    for metric_id in ("sharpe", "sortino"):
        assert _metric(report, metric_id).value is None
        assert _metric(report, metric_id).unavailable_reason == "base_rate_not_covered"
    assert [item.value for item in report.rolling_sharpe] == [None] * 4


def test_sessions_after_the_confirmed_date_carry_the_last_rate() -> None:
    # 확인일 뒤로는 마지막 변경 금리를 바뀔 때까지 이어 쓰고, 그 금리로 시작한 구간을 따로 적는다.
    # 이력을 갱신해도 이 테스트는 그대로다.
    confirmed = BASE_RATE_CONFIRMED_ON
    assert base_rate(confirmed) is not None
    assert base_rate(confirmed + timedelta(days=1000)) == base_rate(confirmed)
    sessions = tuple(confirmed + timedelta(days=offset) for offset in (-1, 0, 1, 2))
    report = compute_analytics(
        AnalyticsInput(
            points=tuple(AnalysisPoint(session, 100.0, 0.0, 0.0) for session in sessions),
            traded_notional=0.0,
        ),
        build_default_metric_registry(),
    )

    assert report.base_rate_carried_sessions == (sessions[2],)


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
