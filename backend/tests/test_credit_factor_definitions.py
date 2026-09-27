"""신용잔고 팩터 두 개의 정의가 원장 필드 단위와 맞는지 고정한다 (#224).

원장 정본(`equity_duckdb` `FIELD_SPECS`)에서 `credit.margin_balance`는 신용융자 잔고 **주식수**다
(금액축은 단위 미상이라 나가지 않는다). 원장 FACTORS 정본 F09 신용잔고율은 `융자잔고 / 상장주식수`
이고 S20 factor_readiness도 이 레지스트리 id에 같은 재료를 잇는다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb._specs import FIELD_BY_ID
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry


def test_신용잔고율은_잔고_주식수를_상장주식수로_나눈다() -> None:
    definition = build_default_factor_registry().get("credit.margin_balance_ratio")
    assert definition.required_field_ids == ("credit.margin_balance", "price.shares_outstanding")
    numerator, denominator = (FIELD_BY_ID[field_id].unit for field_id in definition.required_field_ids)
    # 분자·분모 단위가 같아야 선언 단위 ratio(무차원)가 선다 — 주식수 ÷ 원이면 주가 수준에 끌려간다.
    assert (numerator, denominator, definition.output_unit) == ("shares", "shares", "ratio")


def _observations(balances: dict[str, list[float]]) -> tuple[FactorObservation, ...]:
    start = date(2024, 1, 1)
    return tuple(
        FactorObservation(
            as_of=start + timedelta(days=index),
            security_id=security_id,
            fields=(FactorFieldValue("credit.margin_balance", value),),
        )
        for security_id, series in balances.items()
        for index, value in enumerate(series)
    )


def test_신용잔고_변화는_규모와_무관한_20세션_변화율이다() -> None:
    """같은 10% 증가는 잔고 규모가 달라도 같은 값이어야 횡단면으로 견줄 수 있다.

    예전 기본 그래프는 DELTA(주식수 차이)라 선언 단위 ratio 와 달랐고, 큰 종목일수록 값이 컸다.
    """
    definition = build_default_factor_registry().get("credit.margin_balance_change_20d")
    assert definition.default_graph is not None
    assert definition.output_unit == "ratio"
    small = [1_000.0] * 19 + [1_100.0]
    large = [1_000_000.0] * 19 + [1_100_000.0]
    evaluation = evaluate_factor_graph(
        definition.default_graph,
        observations=_observations({"SMALL": small, "LARGE": large}),
        missing=MissingPolicy.DROP,
    )
    last = date(2024, 1, 1) + timedelta(days=19)
    values = {item.security_id: item.value for item in evaluation.values if item.as_of == last}
    assert values == {"SMALL": pytest.approx(0.1), "LARGE": pytest.approx(0.1)}
