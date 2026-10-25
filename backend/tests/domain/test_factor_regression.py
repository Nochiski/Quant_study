"""팩터 회귀(검증 랩 V4-05) — 손계산 소형 표본과 독립 numpy 구현으로 계수·HAC 표준오차를 본다."""

from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from strategy_workbench.domain.analytics.facade.factor_regression import (
    MIN_REGRESSION_OBSERVATIONS,
    ExcludedFactorPeriods,
    FactorKind,
    FactorPeriodGap,
    FactorPeriodInput,
    FactorRegressionUnavailableReason,
    FactorSecurityPeriod,
    StrategyPeriodReturn,
    factor_period_returns,
    newey_west_lag,
    newey_west_regression,
    regress_on_factors,
)

# 2024-01 의 한국은행 기준금리는 연 3.50%다(2023-01-13 ~ 2024-10-10).
_RATE_2024_01 = 0.035


def test_hac_regression_matches_hand_calculation() -> None:
    # y = a + b·x, x = 0..3, y = 1, 2, 2, 4 → a = b = 0.9, 잔차 0.1, 0.2, -0.7, 0.4.
    # (X'X)⁻¹ = [[0.7, -0.3], [-0.3, 0.2]].
    # S₀ = Σe²xx' = [[0.70, 1.50], [1.50, 3.44]]. lag 1(가중 0.5)의 교차항
    # Σe_t e_{t-1}(x_t x_{t-1}' + x_{t-1} x_t') = [[-0.80, -1.80], [-1.80, -3.92]]
    # → S = [[0.30, 0.60], [0.60, 1.48]].
    # (X'X)⁻¹ S (X'X)⁻¹ = [[0.0282, -0.0138], [-0.0138, 0.0142]], 소표본 보정 n/(n-k) = 4/2.
    fit = newey_west_regression([1.0, 2.0, 2.0, 4.0], [[0.0, 1.0, 2.0, 3.0]], lag=1)
    assert fit is not None
    assert fit.coefficients == pytest.approx((0.9, 0.9))
    assert fit.standard_errors == pytest.approx((math.sqrt(0.0564), math.sqrt(0.0284)))
    # SSR 0.70, SST 4.75.
    assert fit.r_squared == pytest.approx(1 - 0.70 / 4.75)


def test_lag_zero_is_white_heteroskedasticity_robust_error() -> None:
    # (X'X)⁻¹ S₀ (X'X)⁻¹ = [[0.0226, -0.0084], [-0.0084, 0.0206]], × 2.
    fit = newey_west_regression([1.0, 2.0, 2.0, 4.0], [[0.0, 1.0, 2.0, 3.0]], lag=0)
    assert fit is not None
    assert fit.standard_errors == pytest.approx((math.sqrt(0.0452), math.sqrt(0.0412)))


def test_collinear_design_is_singular() -> None:
    assert newey_west_regression([1.0, 2.0, 3.0, 5.0], [[2.0, 2.0, 2.0, 2.0]], lag=0) is None
    doubled = [0.0, 1.0, 2.0, 3.0]
    assert (
        newey_west_regression([1.0, 2.0, 2.0, 4.0], [doubled, [2 * v for v in doubled]], lag=0)
        is None
    )


def test_newey_west_automatic_lag() -> None:
    # ⌊4(n/100)^(2/9)⌋: 60 → 3.57, 100 → 4, 1000 → 6.67.
    assert [newey_west_lag(n) for n in (60, 100, 1000)] == [3, 4, 6]


def _security(
    security_id: str,
    market_cap: float,
    value_score: float,
    momentum_score: float,
    period_return: float,
) -> FactorSecurityPeriod:
    return FactorSecurityPeriod(
        security_id=security_id,
        market_cap=market_cap,
        book_equity=1.0,
        value_score=value_score,
        momentum_score=momentum_score,
        start_price=100.0,
        end_price=100.0 * (1 + period_return),
    )


# 일곱 종목: 규모 아래 ⌊7/2⌋=3(A·B·C)이 소형, 분위 ⌊0.3·7⌋=2 개씩 하·상.
# 가치 하 = A·D, 중 = B·E·F, 상 = C·G. 모멘텀 하 = B·E, 중 = A·F·D, 상 = C·G.
_SEVEN = (
    _security("A", 10, 0.1, 0.5, 0.01),
    _security("B", 20, 0.4, 0.1, 0.02),
    _security("C", 30, 0.8, 0.8, 0.03),
    _security("D", 40, 0.2, 0.7, -0.01),
    _security("E", 50, 0.5, 0.2, 0.0),
    _security("F", 60, 0.6, 0.6, 0.01),
    _security("G", 70, 0.9, 0.9, 0.04),
)
_START = date(2024, 1, 8)


def test_factor_returns_match_hand_calculation() -> None:
    outcome = factor_period_returns(FactorPeriodInput(_START, _START + timedelta(days=1), _SEVEN))
    assert not isinstance(outcome, FactorPeriodGap)
    assert outcome.risk_free == pytest.approx(_RATE_2024_01 / 365)
    # 시장: Σ시총·수익률 / Σ시총 = (0.1+0.4+0.9-0.4+0+0.6+2.8) / 280 = 4.4 / 280.
    assert outcome.market + outcome.risk_free == pytest.approx(4.4 / 280)
    # 가치 여섯: 소형 (A 1%, B 2%, C 3%), 대형 (D -1%, E·F 가중 0.6/110, G 4%).
    big_middle = 0.6 / 110
    assert outcome.size == pytest.approx((0.01 + 0.02 + 0.03) / 3 - (-0.01 + big_middle + 0.04) / 3)
    assert outcome.value == pytest.approx((0.03 + 0.04) / 2 - (0.01 - 0.01) / 2)
    # 모멘텀: 소형 하 B 2%·상 C 3%, 대형 하 E 0%·상 G 4% → (3 + 4)/2 - (2 + 0)/2 = 2.5%.
    # 0 이 아니어서 상·하가 뒤바뀌면 부호로 드러난다.
    assert outcome.momentum == pytest.approx(0.025)
    assert outcome.factor(FactorKind.VALUE) == outcome.value


def test_non_positive_book_equity_leaves_value_sort() -> None:
    # A 를 빼면 가치 정렬 6 종목, 분위 ⌊1.8⌋=1 → 하 = D 하나라 소형 하가 빈다.
    securities = (replace(_SEVEN[0], book_equity=-5.0), *_SEVEN[1:])
    period = FactorPeriodInput(_START, _START + timedelta(days=1), securities)
    assert factor_period_returns(period) is FactorPeriodGap.SIZE_VALUE_EMPTY


def test_missing_inputs_are_excluded_not_filled() -> None:
    # 끝 종가가 없는 종목(상장 종료 등)은 시장 가중치에서도 빠진다 — 0 수익률로 채우지 않는다.
    delisted = (*_SEVEN, replace(_SEVEN[6], security_id="H", market_cap=1e9, end_price=None))
    outcome = factor_period_returns(FactorPeriodInput(_START, _START + timedelta(days=1), delisted))
    assert not isinstance(outcome, FactorPeriodGap)
    assert outcome.market + outcome.risk_free == pytest.approx(4.4 / 280)
    no_caps = tuple(replace(item, market_cap=None) for item in _SEVEN)
    assert (
        factor_period_returns(FactorPeriodInput(_START, _START + timedelta(days=1), no_caps))
        is FactorPeriodGap.NO_MARKET_WEIGHT
    )
    no_momentum = tuple(replace(item, momentum_score=None) for item in _SEVEN)
    assert (
        factor_period_returns(FactorPeriodInput(_START, _START + timedelta(days=1), no_momentum))
        is FactorPeriodGap.SIZE_MOMENTUM_EMPTY
    )


def test_period_before_base_rate_history_is_a_gap() -> None:
    old = date(1990, 1, 2)
    outcome = factor_period_returns(FactorPeriodInput(old, old + timedelta(days=1), _SEVEN))
    assert outcome is FactorPeriodGap.BASE_RATE_NOT_COVERED


def _random_periods(count: int, seed: int) -> list[FactorPeriodInput]:
    rng = random.Random(seed)
    periods: list[FactorPeriodInput] = []
    day = date(2024, 1, 2)
    while len(periods) < count:
        securities = tuple(
            FactorSecurityPeriod(
                security_id=f"S{index:02d}",
                market_cap=rng.uniform(1, 100),
                book_equity=rng.uniform(1, 10),
                value_score=rng.random(),
                momentum_score=rng.random(),
                start_price=100.0,
                end_price=100.0 * (1 + rng.gauss(0, 0.02)),
            )
            for index in range(20)
        )
        period = FactorPeriodInput(day, day + timedelta(days=1), securities)
        # 여섯 칸이 모두 찬 단면만 쓴다(빈 칸 구간은 다른 테스트가 본다).
        if not isinstance(factor_period_returns(period), FactorPeriodGap):
            periods.append(period)
            day += timedelta(days=1)
    return periods


def _reference_hac(y: np.ndarray, x: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray]:
    """독립 구현: numpy 최소제곱 + 행렬식 Newey·West."""
    n, k = x.shape
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    residual = y - x @ beta
    scores = x * residual[:, None]
    meat = scores.T @ scores
    for distance in range(1, lag + 1):
        gamma = scores[distance:].T @ scores[:-distance]
        meat += (1 - distance / (lag + 1)) * (gamma + gamma.T)
    bread = np.linalg.inv(x.T @ x)
    covariance = bread @ meat @ bread * n / (n - k)
    return beta, np.sqrt(np.diag(covariance))


def test_regression_recovers_exposures_and_matches_reference() -> None:
    periods = _random_periods(120, seed=7)
    rng = random.Random(11)
    strategy: list[StrategyPeriodReturn] = []
    design: list[list[float]] = []
    response: list[float] = []
    for period in periods:
        factors = factor_period_returns(period)
        assert not isinstance(factors, FactorPeriodGap)
        excess = (
            0.0004
            + 1.2 * factors.market
            + 0.3 * factors.size
            - 0.2 * factors.value
            + 0.5 * factors.momentum
            + rng.gauss(0, 0.001)
        )
        strategy.append(StrategyPeriodReturn(period.start, period.end, excess + factors.risk_free))
        design.append([1.0, factors.market, factors.size, factors.value, factors.momentum])
        response.append(excess)
    report = regress_on_factors(strategy, periods, annualization_days=252)
    assert report.unavailable_reason is None
    assert report.fit is not None
    assert report.observation_count == 120
    lag = newey_west_lag(120)
    assert report.fit.hac_lag == lag
    beta, errors = _reference_hac(np.array(response), np.array(design), lag)
    fit = report.fit
    assert fit.alpha_per_session == pytest.approx(beta[0], rel=1e-9)
    assert fit.alpha_annualized == pytest.approx(beta[0] * 252, rel=1e-9)
    assert fit.alpha_standard_error == pytest.approx(errors[0], rel=1e-9)
    assert fit.alpha_t_value == pytest.approx(beta[0] / errors[0], rel=1e-9)
    assert [item.factor for item in fit.exposures] == list(FactorKind)
    assert [item.beta for item in fit.exposures] == pytest.approx(list(beta[1:]), rel=1e-9)
    assert [item.standard_error for item in fit.exposures] == pytest.approx(
        list(errors[1:]), rel=1e-9
    )
    # 잡음이 작아 심어 둔 노출이 그대로 나온다.
    assert [item.beta for item in fit.exposures] == pytest.approx([1.2, 0.3, -0.2, 0.5], abs=0.05)


def test_unmatched_and_short_samples_are_reported_not_regressed() -> None:
    periods = _random_periods(MIN_REGRESSION_OBSERVATIONS, seed=3)
    strategy = [StrategyPeriodReturn(p.start, p.end, 0.001) for p in periods]
    # 팩터 구간이 없는 전략 구간 하나 → 59 개만 남아 하한 미달.
    strategy[5] = replace(strategy[5], end=strategy[5].end + timedelta(days=1))
    report = regress_on_factors(strategy, periods, annualization_days=252)
    assert report.fit is None
    assert report.unavailable_reason is FactorRegressionUnavailableReason.INSUFFICIENT_OBSERVATIONS
    assert report.observation_count == MIN_REGRESSION_OBSERVATIONS - 1
    assert report.strategy_period_count == MIN_REGRESSION_OBSERVATIONS
    assert report.excluded_periods == (ExcludedFactorPeriods(FactorPeriodGap.NO_FACTOR_PERIOD, 1),)
