"""완료 run 의 팩터 회귀 입력을 모은다(검증 랩 V4-05).

팩터 수익률·회귀 규칙은 `domain/analytics` 의 `_factor_regression` 이 소유한다. 여기서는 그 입력만
만든다: 실행 결과의 세션 수익률, 그리고 run 과 같은 실행 설정으로 기존 포트폴리오 파이프라인을
돌린 유니버스 단면(시가총액·자본총계·수정주가 필드와 레지스트리 기본 graph 의 모멘텀·가치 값).

새 팩터 정의는 만들지 않는다(spec D8). 모멘텀은 `price.momentum_12_1`, 가치는
`financial.book_to_market` 의 레지스트리 기본 graph 를 그대로 쓰고, 규모는 가치 graph 가 이미 읽는
시가총액 필드로 정렬한다. 두 graph 가 읽는 필드(수정주가·시가총액·자본총계)가 파이프라인 관측의
`fields` 에 실려 오므로 별도 데이터 포트가 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

from strategy_workbench.domain.analytics.facade.factor_regression import (
    FactorPeriodInput,
    FactorRegressionReport,
    FactorSecurityPeriod,
    StrategyPeriodReturn,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult
from strategy_workbench.domain.factor.facade.registry import FactorRegistry
from strategy_workbench.domain.portfolio.facade.construction import PortfolioObservation
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorSignal,
    StrategyIdentity,
    StrategySpec,
)

__all__ = [
    "MOMENTUM_FACTOR_ID",
    "VALUE_FACTOR_ID",
    "RunFactorRegression",
    "factor_periods",
    "factor_regression_spec",
    "strategy_period_returns",
]

MOMENTUM_FACTOR_ID = "price.momentum_12_1"
VALUE_FACTOR_ID = "financial.book_to_market"
# 두 기본 graph 가 읽는 필드다. 레지스트리 정의의 `required_field_ids` 와 다르면
# `factor_regression_spec` 이 멈춘다.
_ADJUSTED_CLOSE = "price.adj_close"
_MARKET_CAP = "price.market_cap"
_BOOK_EQUITY = "financial.book_equity"
_REQUIRED_FIELDS = {
    MOMENTUM_FACTOR_ID: frozenset({_ADJUSTED_CLOSE}),
    VALUE_FACTOR_ID: frozenset({_BOOK_EQUITY, _MARKET_CAP}),
}


@dataclass(frozen=True)
class RunFactorRegression:
    """불변 실행과 그 데이터 판으로 잰 팩터 회귀. 계열 통계(N·t 기준)는 조회 시점 원장이다."""

    run_id: str
    run_fingerprint: str
    data_snapshot_id: str
    momentum_factor_id: str
    value_factor_id: str
    trial_count: int | None = field(
        metadata={"description": "조회 시점 계열 원장의 시도 수 N. 계열 없는 실행은 null."}
    )
    alpha_t_threshold: float | None = field(
        metadata={
            "description": (
                "알파 t 값의 기준(N ≥ 20 이면 3.0, 아니면 2.0, Harvey·Liu·Zhu 2016). "
                "계열이 없어 N 을 모르면 null."
            )
        }
    )
    report: FactorRegressionReport


def factor_regression_spec(registry: FactorRegistry) -> StrategySpec:
    """모멘텀·가치 기본 graph 두 개만 든 파이프라인용 spec.

    포트폴리오 단계는 기본값이며 쓰지 않는다.
    """
    factors: list[FactorSignal] = []
    for factor_id in (MOMENTUM_FACTOR_ID, VALUE_FACTOR_ID):
        definition = registry.get(factor_id)
        if definition.default_graph is None or (
            frozenset(definition.required_field_ids) != _REQUIRED_FIELDS[factor_id]
        ):
            raise ValueError(
                "factor regression expects the registry default graph and its input fields — "
                f"factor_id={factor_id} registry={registry.version} "
                f"has_graph={definition.default_graph is not None} "
                f"fields={definition.required_field_ids} "
                f"expected={sorted(_REQUIRED_FIELDS[factor_id])}"
            )
        factors.append(
            FactorSignal(
                factor_id=factor_id,
                label=definition.label,
                direction=FactorDirection.HIGH,
                graph=definition.default_graph,
            )
        )
    return StrategySpec(
        identity=StrategyIdentity(strategy_id="validation.factor-regression", revision=1),
        title="팩터 회귀 입력",
        factors=tuple(factors),
    )


def strategy_period_returns(result: BacktestRunResult) -> tuple[StrategyPeriodReturn, ...]:
    """자산 곡선의 연속한 두 세션 사이 수익률."""
    return tuple(
        StrategyPeriodReturn(before.session, after.session, after.equity / before.equity - 1.0)
        for before, after in pairwise(result.series.equity)
    )


def factor_periods(observations: Sequence[PortfolioObservation]) -> tuple[FactorPeriodInput, ...]:
    """연속한 두 세션마다 `start` 의 유니버스 구성 종목 단면. 끝 가격은 `end` 행에서 읽는다."""
    by_session: dict[date, dict[str, PortfolioObservation]] = {}
    for item in observations:
        by_session.setdefault(item.as_of, {})[item.security_id] = item
    periods: list[FactorPeriodInput] = []
    for start, end in pairwise(sorted(by_session)):
        closing = by_session[end]
        periods.append(
            FactorPeriodInput(
                start=start,
                end=end,
                securities=tuple(
                    _security(item, closing.get(item.security_id))
                    for item in by_session[start].values()
                    if item.universe_member
                ),
            )
        )
    return tuple(periods)


def _security(
    opening: PortfolioObservation, closing: PortfolioObservation | None
) -> FactorSecurityPeriod:
    fields = _numeric_fields(opening)
    factors = {value.factor_id: value.value for value in opening.factor_values}
    return FactorSecurityPeriod(
        security_id=opening.security_id,
        market_cap=fields.get(_MARKET_CAP),
        book_equity=fields.get(_BOOK_EQUITY),
        value_score=factors.get(VALUE_FACTOR_ID),
        momentum_score=factors.get(MOMENTUM_FACTOR_ID),
        start_price=fields.get(_ADJUSTED_CLOSE),
        end_price=None if closing is None else _numeric_fields(closing).get(_ADJUSTED_CLOSE),
    )


def _numeric_fields(observation: PortfolioObservation) -> dict[str, float]:
    """숫자 필드만. 참거짓·문자열·결측은 빠진다(정렬·가격에 쓸 수 없다)."""
    return {
        item.field_id: float(item.value)
        for item in observation.fields
        if isinstance(item.value, int | float) and not isinstance(item.value, bool)
    }
