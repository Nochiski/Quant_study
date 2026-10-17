"""실행 수익률의 팩터 회귀(검증 랩 V4-05, spec D8, 수학 노트).

전략의 세션 초과수익을 시장·규모·가치·모멘텀 팩터 수익률에 회귀해 알파와 노출(β)을 잰다.
규칙의 정본은 이 파일 하나다.

**팩터 수익률** — Fama·French(1993)의 3팩터와 Carhart(1997) 모멘텀을 세션 단위로 만든다.

- 구간은 연속한 두 세션(`start` 종가 → `end` 종가)이다. 정렬 값·시가총액은 `start` 에 알려진
  값(PIT)이고 수익률은 수정주가의 두 종가 비다. 매 구간 다시 정렬한다(일별 팩터의 매일 갱신 판,
  AQR 의 timely 구성과 같은 방향). 팩터 모사 포트폴리오라 회전 비용은 들지 않는다.
- 시장(MKT) = 그 구간 유니버스 구성 종목의 `start` 시가총액 가중 수익률 − 무위험수익률.
- 규모·가치는 독립 2×3 정렬이다: 시가총액 중앙값으로 소형·대형, 가치 점수 30·70 분위로 하·중·상.
  SMB = (소형 셋 평균) − (대형 셋 평균), HML = (소형 상 + 대형 상)/2 − (소형 하 + 대형 하)/2.
  자본총계가 0 이하인 종목은 가치 정렬에서 뺀다(FF 의 양의 장부가 조건). 모멘텀은 같은 규모
  중앙값 × 모멘텀 30·70 분위로 UMD = (소형 상 + 대형 상)/2 − (소형 하 + 대형 하)/2 다.
  여섯 포트폴리오는 모두 `start` 시가총액 가중이다.
- 분위는 위치로 자른다: 값(같으면 종목 id) 오름차순으로 세운 n 개 중 아래 ⌊0.3n⌋ 이 하, 위
  ⌊0.3n⌋ 이 상, 나머지가 중이다. 규모는 아래 ⌊n/2⌋ 가 소형이다. 경계의 동점이 갈리는 것은 종목
  id 순이라 결정적이다. 브레이크포인트는 그 구간 유니버스 전체에서 정한다(NYSE 같은 기준 시장
  구분이 없어서다 — 시가총액 가중이 극소형주의 몫을 줄인다).
- 결측은 채우지 않는다: 수익률·시가총액·점수가 없는 종목은 해당 정렬에서 빠진다. 여섯 포트폴리오
  중 하나라도 비거나, 시장 가중치가 없거나, 무위험수익률을 모르는 구간은 회귀에서 빠지고 사유별로
  센다. 상장 종료로 끝 종가가 없는 종목은 그 구간에서 빠진다(상폐 손실을 팩터에 반영하지 못하는
  한계).

**회귀** — y(전략 초과수익) = α + β·[MKT, SMB, HML, UMD] + e 를 OLS 로 푼다. 표준오차는
Newey·West(1987) HAC(Bartlett 가중)이고 lag 는 Newey·West(1994) 자동 규칙 ⌊4(n/100)^(2/9)⌋,
소표본 보정 n/(n−k)을 곱한다. 빠진 구간이 있으면 남은 구간을 시간 순으로 이어 자기상관을
잰다. 연율 알파는 세션 알파 × 연환산 거래일(산술)이다. 일별 회귀는 비동시 거래로 β 가 작게 잡힐
수 있다(Dimson 보정은 하지 않는다).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from ._calculation import interval_risk_free

__all__ = [
    "MIN_REGRESSION_OBSERVATIONS",
    "ExcludedFactorPeriods",
    "FactorExposure",
    "FactorKind",
    "FactorPeriodGap",
    "FactorPeriodInput",
    "FactorPeriodReturns",
    "FactorRegressionFit",
    "FactorRegressionReport",
    "FactorRegressionUnavailableReason",
    "FactorSecurityPeriod",
    "HacRegression",
    "StrategyPeriodReturn",
    "factor_period_returns",
    "newey_west_lag",
    "newey_west_regression",
    "regress_on_factors",
    "unavailable_factor_regression",
]

# 회귀에 쓸 구간 수의 하한. 약 석 달(60세션)이다. 다섯 계수와 HAC lag(n=60 이면 3)를 재기에
# 이보다 짧으면 표준오차가 거의 정보가 없다.
MIN_REGRESSION_OBSERVATIONS = 60

# 2×3 정렬의 분위(Fama·French 의 30·70 브레이크포인트).
_TERCILE_SHARE = 0.3

# 정규방정식 피벗이 이 비율(대각 최댓값 대비)보다 작으면 설계 행렬이 특이하다고 본다.
_SINGULAR_TOLERANCE = 1e-12


class FactorKind(StrEnum):
    MARKET = "market"
    SIZE = "size"
    VALUE = "value"
    MOMENTUM = "momentum"


# 회귀 설계 행렬의 열 순서(상수항 뒤).
_FACTOR_ORDER = (FactorKind.MARKET, FactorKind.SIZE, FactorKind.VALUE, FactorKind.MOMENTUM)


@dataclass(frozen=True)
class FactorSecurityPeriod:
    """한 구간의 유니버스 구성 종목 하나.

    값은 모두 `start` 에 알려진 것이고 가격만 `end` 를 읽는다.
    """

    security_id: str
    market_cap: float | None
    book_equity: float | None
    value_score: float | None
    momentum_score: float | None
    start_price: float | None
    end_price: float | None


@dataclass(frozen=True)
class FactorPeriodInput:
    start: date
    end: date
    securities: tuple[FactorSecurityPeriod, ...]


@dataclass(frozen=True)
class StrategyPeriodReturn:
    start: date
    end: date
    value: float


class FactorPeriodGap(StrEnum):
    # 무위험수익률(기준금리 이력)을 모른다.
    BASE_RATE_NOT_COVERED = "base_rate_not_covered"
    # 시가총액과 수익률이 모두 있는 구성 종목이 없다.
    NO_MARKET_WEIGHT = "no_market_weight"
    # 규모 × 가치 여섯 포트폴리오 중 빈 것이 있다.
    SIZE_VALUE_EMPTY = "size_value_empty"
    # 규모 × 모멘텀 여섯 포트폴리오 중 빈 것이 있다.
    SIZE_MOMENTUM_EMPTY = "size_momentum_empty"
    # 전략 수익률 구간에 맞는 팩터 구간이 없다.
    NO_FACTOR_PERIOD = "no_factor_period"


@dataclass(frozen=True)
class FactorPeriodReturns:
    """한 구간의 팩터 수익률. `market` 은 무위험수익률을 뺀 초과수익이다."""

    start: date
    end: date
    market: float
    size: float
    value: float
    momentum: float
    risk_free: float

    def factor(self, kind: FactorKind) -> float:
        return {
            FactorKind.MARKET: self.market,
            FactorKind.SIZE: self.size,
            FactorKind.VALUE: self.value,
            FactorKind.MOMENTUM: self.momentum,
        }[kind]


@dataclass(frozen=True)
class _PricedSecurity:
    security_id: str
    market_cap: float
    period_return: float
    book_equity: float | None
    value_score: float | None
    momentum_score: float | None


def factor_period_returns(period: FactorPeriodInput) -> FactorPeriodReturns | FactorPeriodGap:
    """한 구간의 MKT·SMB·HML·UMD. 만들 수 없으면 그 사유(모듈 docstring)."""
    risk_free = interval_risk_free(period.start, period.end)
    if risk_free is None:
        return FactorPeriodGap.BASE_RATE_NOT_COVERED
    priced = tuple(
        _PricedSecurity(
            security_id=item.security_id,
            market_cap=item.market_cap,
            period_return=item.end_price / item.start_price - 1.0,
            book_equity=item.book_equity,
            value_score=item.value_score,
            momentum_score=item.momentum_score,
        )
        for item in period.securities
        if item.market_cap is not None
        and item.market_cap > 0
        and item.start_price is not None
        and item.start_price > 0
        and item.end_price is not None
        and item.end_price > 0
    )
    if not priced:
        return FactorPeriodGap.NO_MARKET_WEIGHT
    market = _weighted_return(priced)
    small_ids = _size_split(priced)
    value_sorted = _size_by_tercile(
        small_ids,
        [
            (item, item.value_score)
            for item in priced
            if item.value_score is not None
            and item.book_equity is not None
            and item.book_equity > 0
        ],
    )
    if value_sorted is None:
        return FactorPeriodGap.SIZE_VALUE_EMPTY
    momentum_sorted = _size_by_tercile(
        small_ids,
        [(item, item.momentum_score) for item in priced if item.momentum_score is not None],
    )
    if momentum_sorted is None:
        return FactorPeriodGap.SIZE_MOMENTUM_EMPTY
    return FactorPeriodReturns(
        start=period.start,
        end=period.end,
        market=market - risk_free,
        size=sum(value_sorted.small) / 3 - sum(value_sorted.big) / 3,
        value=_high_minus_low(value_sorted),
        momentum=_high_minus_low(momentum_sorted),
        risk_free=risk_free,
    )


@dataclass(frozen=True)
class _SixPortfolios:
    """규모(소·대) × 분위(하·중·상) 여섯 포트폴리오의 가중 수익률. 순서는 (하, 중, 상)이다."""

    small: tuple[float, float, float]
    big: tuple[float, float, float]


def _weighted_return(items: Sequence[_PricedSecurity]) -> float:
    total_cap = sum(item.market_cap for item in items)
    return sum(item.market_cap * item.period_return for item in items) / total_cap


def _size_split(items: Sequence[_PricedSecurity]) -> frozenset[str]:
    """시가총액 아래 절반(⌊n/2⌋)의 종목 id. 동점은 종목 id 순이다."""
    ordered = sorted(items, key=lambda item: (item.market_cap, item.security_id))
    return frozenset(item.security_id for item in ordered[: len(ordered) // 2])


def _size_by_tercile(
    small_ids: frozenset[str], scored: list[tuple[_PricedSecurity, float]]
) -> _SixPortfolios | None:
    """점수 30·70 분위와 규모의 독립 정렬. 빈 포트폴리오가 하나라도 있으면 None."""
    ordered = [item for item, _ in sorted(scored, key=lambda pair: (pair[1], pair[0].security_id))]
    cut = math.floor(_TERCILE_SHARE * len(ordered))
    if cut == 0:
        return None
    groups = (ordered[:cut], ordered[cut : len(ordered) - cut], ordered[len(ordered) - cut :])
    small: list[float] = []
    big: list[float] = []
    for group in groups:
        for target, members in (
            (small, [item for item in group if item.security_id in small_ids]),
            (big, [item for item in group if item.security_id not in small_ids]),
        ):
            if not members:
                return None
            target.append(_weighted_return(members))
    return _SixPortfolios(small=(small[0], small[1], small[2]), big=(big[0], big[1], big[2]))


def _high_minus_low(portfolios: _SixPortfolios) -> float:
    high = (portfolios.small[2] + portfolios.big[2]) / 2
    low = (portfolios.small[0] + portfolios.big[0]) / 2
    return high - low


# ---------------------------------------------------------------------------- 회귀


@dataclass(frozen=True)
class HacRegression:
    """OLS 계수와 Newey·West 표준오차. 순서는 설계 행렬 열 순서다."""

    coefficients: tuple[float, ...]
    standard_errors: tuple[float, ...]
    r_squared: float | None


def newey_west_lag(observations: int) -> int:
    """Newey·West(1994) 자동 lag ⌊4(n/100)^(2/9)⌋."""
    return math.floor(4 * (observations / 100) ** (2 / 9))


def newey_west_regression(
    response: Sequence[float], columns: Sequence[Sequence[float]], *, lag: int
) -> HacRegression | None:
    """상수항 + `columns` 에 대한 OLS 와 HAC 표준오차. 설계 행렬이 특이하면 None.

    공분산 = (X'X)⁻¹ S (X'X)⁻¹ · n/(n−k), S = Σ e²xx' + Σ_l (1 − l/(L+1)) Σ_t e_t e_{t−l}
    (x_t x_{t−l}' + x_{t−l} x_t').
    """
    n = len(response)
    rows = [(1.0, *(column[index] for column in columns)) for index in range(n)]
    k = len(rows[0])
    if n <= k:
        raise ValueError(f"regression needs more observations than coefficients — n={n} k={k}")
    gram = [[sum(row[i] * row[j] for row in rows) for j in range(k)] for i in range(k)]
    inverse = _inverse(gram)
    if inverse is None:
        return None
    moment = [sum(row[i] * y for row, y in zip(rows, response, strict=True)) for i in range(k)]
    coefficients = [sum(inverse[i][j] * moment[j] for j in range(k)) for i in range(k)]
    residuals = [
        y - sum(c * x for c, x in zip(coefficients, row, strict=True))
        for row, y in zip(rows, response, strict=True)
    ]
    meat = [
        [
            sum(e * e * row[i] * row[j] for row, e in zip(rows, residuals, strict=True))
            for j in range(k)
        ]
        for i in range(k)
    ]
    for distance in range(1, lag + 1):
        weight = 1 - distance / (lag + 1)
        for t in range(distance, n):
            product = residuals[t] * residuals[t - distance]
            current, previous = rows[t], rows[t - distance]
            for i in range(k):
                for j in range(k):
                    meat[i][j] += (
                        weight * product * (current[i] * previous[j] + previous[i] * current[j])
                    )
    bread_meat = _multiply(inverse, meat)
    covariance = _multiply(bread_meat, inverse)
    correction = n / (n - k)
    mean = sum(response) / n
    total = sum((y - mean) ** 2 for y in response)
    return HacRegression(
        coefficients=tuple(coefficients),
        standard_errors=tuple(math.sqrt(max(covariance[i][i] * correction, 0.0)) for i in range(k)),
        r_squared=None if total == 0 else 1 - sum(e * e for e in residuals) / total,
    )


def _multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    size = len(right)
    return [[sum(row[m] * right[m][j] for m in range(size)) for j in range(size)] for row in left]


def _inverse(matrix: list[list[float]]) -> list[list[float]] | None:
    """부분 피벗 가우스·조르당 역행렬.

    피벗이 대각 최댓값 대비 `_SINGULAR_TOLERANCE` 보다 작으면 None 이다.
    """
    size = len(matrix)
    scale = max(abs(matrix[i][i]) for i in range(size))
    if scale == 0:
        return None
    work = [[*row, *(1.0 if i == j else 0.0 for j in range(size))] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) <= _SINGULAR_TOLERANCE * scale:
            return None
        work[column], work[pivot] = work[pivot], work[column]
        lead = work[column][column]
        work[column] = [value / lead for value in work[column]]
        for row in range(size):
            if row != column:
                factor = work[row][column]
                work[row] = [a - factor * b for a, b in zip(work[row], work[column], strict=True)]
    return [row[size:] for row in work]


# ---------------------------------------------------------------------------- 보고


class FactorRegressionUnavailableReason(StrEnum):
    # 회귀에 쓸 구간이 `MIN_REGRESSION_OBSERVATIONS` 보다 적다.
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    # 팩터 수익률이 서로(또는 상수와) 선형 종속이다.
    SINGULAR_DESIGN = "singular_design"
    # 실행 결과의 데이터 판과 지금 연결된 데이터 판이 다르다(application 판정).
    DATA_SNAPSHOT_CHANGED = "data_snapshot_changed"
    # 팩터를 만들 원천 관측을 읽지 못했다(application 판정).
    DATA_UNAVAILABLE = "data_unavailable"


@dataclass(frozen=True)
class ExcludedFactorPeriods:
    reason: FactorPeriodGap
    count: int


@dataclass(frozen=True)
class FactorExposure:
    factor: FactorKind
    beta: float
    standard_error: float
    # 표준오차가 0 이면(잔차가 모두 0) t 값이 없다.
    t_value: float | None


@dataclass(frozen=True)
class FactorRegressionFit:
    alpha_per_session: float
    alpha_annualized: float
    alpha_standard_error: float
    alpha_t_value: float | None
    exposures: tuple[FactorExposure, ...]
    r_squared: float | None
    hac_lag: int


@dataclass(frozen=True)
class FactorRegressionReport:
    """회귀 결과. `fit` 이 없으면 `unavailable_reason` 이 있다."""

    observation_count: int
    strategy_period_count: int
    excluded_periods: tuple[ExcludedFactorPeriods, ...]
    annualization_days: int
    fit: FactorRegressionFit | None
    unavailable_reason: FactorRegressionUnavailableReason | None

    def __post_init__(self) -> None:
        if (self.fit is None) == (self.unavailable_reason is None):
            raise ValueError(
                "factor regression report needs exactly one of fit and unavailable_reason — "
                f"fit={self.fit is not None} reason={self.unavailable_reason}"
            )


def unavailable_factor_regression(
    reason: FactorRegressionUnavailableReason,
    *,
    strategy_period_count: int,
    annualization_days: int,
) -> FactorRegressionReport:
    """팩터 구간을 만들기 전에 멈춘 보고(데이터 판 불일치·원천 부재)."""
    return FactorRegressionReport(
        observation_count=0,
        strategy_period_count=strategy_period_count,
        excluded_periods=(),
        annualization_days=annualization_days,
        fit=None,
        unavailable_reason=reason,
    )


def regress_on_factors(
    strategy: Sequence[StrategyPeriodReturn],
    periods: Sequence[FactorPeriodInput],
    *,
    annualization_days: int,
) -> FactorRegressionReport:
    """전략 구간 수익률을 같은 (start, end) 의 팩터 수익률에 회귀한다(모듈 docstring)."""
    if annualization_days <= 0:
        raise ValueError(f"annualization_days must be positive — got {annualization_days}")
    by_interval = {(period.start, period.end): period for period in periods}
    gaps: dict[FactorPeriodGap, int] = {}
    response: list[float] = []
    columns: list[list[float]] = [[] for _ in _FACTOR_ORDER]
    for item in sorted(strategy, key=lambda point: point.start):
        period = by_interval.get((item.start, item.end))
        outcome = (
            FactorPeriodGap.NO_FACTOR_PERIOD if period is None else factor_period_returns(period)
        )
        if isinstance(outcome, FactorPeriodGap):
            gaps[outcome] = gaps.get(outcome, 0) + 1
            continue
        response.append(item.value - outcome.risk_free)
        for column, kind in zip(columns, _FACTOR_ORDER, strict=True):
            column.append(outcome.factor(kind))
    excluded = tuple(
        ExcludedFactorPeriods(reason=reason, count=gaps[reason])
        for reason in FactorPeriodGap
        if reason in gaps
    )

    def report(
        fit: FactorRegressionFit | None, reason: FactorRegressionUnavailableReason | None
    ) -> FactorRegressionReport:
        return FactorRegressionReport(
            observation_count=len(response),
            strategy_period_count=len(strategy),
            excluded_periods=excluded,
            annualization_days=annualization_days,
            fit=fit,
            unavailable_reason=reason,
        )

    if len(response) < MIN_REGRESSION_OBSERVATIONS:
        return report(None, FactorRegressionUnavailableReason.INSUFFICIENT_OBSERVATIONS)
    lag = newey_west_lag(len(response))
    regression = newey_west_regression(response, columns, lag=lag)
    if regression is None:
        return report(None, FactorRegressionUnavailableReason.SINGULAR_DESIGN)
    alpha, *betas = regression.coefficients
    alpha_error, *beta_errors = regression.standard_errors
    return report(
        FactorRegressionFit(
            alpha_per_session=alpha,
            alpha_annualized=alpha * annualization_days,
            alpha_standard_error=alpha_error,
            alpha_t_value=_t_value(alpha, alpha_error),
            exposures=tuple(
                FactorExposure(
                    factor=kind, beta=beta, standard_error=error, t_value=_t_value(beta, error)
                )
                for kind, beta, error in zip(_FACTOR_ORDER, betas, beta_errors, strict=True)
            ),
            r_squared=regression.r_squared,
            hac_lag=lag,
        ),
        None,
    )


def _t_value(estimate: float, standard_error: float) -> float | None:
    return None if standard_error == 0 else estimate / standard_error
