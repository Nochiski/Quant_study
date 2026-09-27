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
    numerator, denominator = (
        FIELD_BY_ID[field_id].unit for field_id in definition.required_field_ids
    )
    # 분자·분모 단위가 같아야 선언 단위 ratio(무차원)가 선다 — 주식수 ÷ 원이면 주가 수준에 끌려간다.
    assert (numerator, denominator, definition.output_unit) == ("shares", "shares", "ratio")


_SHARES = "price.shares_outstanding"
_BALANCE = "credit.margin_balance"
_START = date(2024, 1, 1)


def _observations(
    series: dict[str, tuple[list[float], list[float]]],
) -> tuple[FactorObservation, ...]:
    """종목 → (세션별 잔고, 세션별 상장주식수). 값은 어댑터가 랙을 적용해 **건넨** 값이다."""
    return tuple(
        FactorObservation(
            as_of=_START + timedelta(days=index),
            security_id=security_id,
            fields=(FactorFieldValue(_BALANCE, balance), FactorFieldValue(_SHARES, shares)),
        )
        for security_id, (balances, shares_series) in series.items()
        for index, (balance, shares) in enumerate(zip(balances, shares_series, strict=True))
    )


def _change_at(series: dict[str, tuple[list[float], list[float]]], day: int) -> dict[str, float]:
    definition = build_default_factor_registry().get("credit.margin_balance_change_20d")
    assert definition.default_graph is not None
    evaluation = evaluate_factor_graph(
        definition.default_graph,
        observations=_observations(series),
        missing=MissingPolicy.DROP,
    )
    as_of = _START + timedelta(days=day)
    return {
        item.security_id: item.value
        for item in evaluation.values
        if item.as_of == as_of and item.value is not None
    }


def test_신용잔고_변화는_잔고율의_20세션_변화라_규모와_무관하다() -> None:
    """잔고율 0.10 → 0.11 은 잔고 규모가 1,000배 달라도 같은 +0.01 이다."""
    definition = build_default_factor_registry().get("credit.margin_balance_change_20d")
    assert definition.required_field_ids == (_BALANCE, _SHARES)
    assert definition.output_unit == "ratio"
    n = 25
    small = ([1_000.0] * (n - 1) + [1_100.0], [10_000.0] * n)
    large = ([1_000_000.0] * (n - 1) + [1_100_000.0], [10_000_000.0] * n)
    assert _change_at({"SMALL": small, "LARGE": large}, n - 1) == {
        "SMALL": pytest.approx(0.01),
        "LARGE": pytest.approx(0.01),
    }


def test_분할은_신용_증가로_읽히지_않는다() -> None:
    """#234 — 035720 5:1 분할 뒤 원주식수 변화율은 +300% 안팎이었다(실제 잔고율은 약 −10%).

    어댑터가 건네는 두 값이 같은 세션에 5배가 되는 경우다. 액면 분할·병합·감자에서 신용잔고
    원천은 거래정지 첫날부터 새 주식수 단위로 바뀌어 주식수 급변일보다 대개 0~2세션 앞서고, 공개
    랙(신용잔고 3 · 주식수 1)을 거치면 둘이 대개 같은 세션에 들어온다. 그러면 잔고율은 그대로라
    변화는 0 이다. 무상증자는 원천 잔고가 새 단위로 바뀌지 않아 이 불변성이 서지 않는다(#249).
    """
    n, split = 40, 20
    shares = [10_000.0 if day < split else 50_000.0 for day in range(n)]
    balance = [1_000.0 if day < split else 5_000.0 for day in range(n)]
    for day in range(19, n):
        assert _change_at({"S": (balance, shares)}, day) == {"S": pytest.approx(0.0)}, day


def test_작은_첫_값과_0_에서_시작한_잔고도_유한한_변화를_낸다() -> None:
    """#234 P3-2 — 1주에서 100주로 늘면 변화율은 +9,900% 였고, 0 에서 시작하면 값이 없었다."""
    n = 25
    tiny = ([1.0] * (n - 1) + [100.0], [1_000_000.0] * n)
    zero = ([0.0] * (n - 1) + [1_000.0], [1_000_000.0] * n)
    assert _change_at({"TINY": tiny, "ZERO": zero}, n - 1) == {
        "TINY": pytest.approx(99 / 1_000_000),
        "ZERO": pytest.approx(0.001),
    }


def test_변화는_20세션_창의_처음과_끝을_견준다() -> None:
    """창 길이를 고정한다(#244 리뷰 P3-1) — 잔고율이 창 가운데(10세션째)에서 바뀐다.

    DELTA(20) 은 24세션째에서 5세션째와 견주므로 +0.01 을 본다. 창이 10 으로 줄면 15세션째와
    견주어 0 이 된다. 마지막 세션에서만 바뀌는 규모 테스트로는 창 길이를 가를 수 없었다.
    """
    definition = build_default_factor_registry().get("credit.margin_balance_change_20d")
    assert definition.minimum_history_sessions == 20
    n = 25
    series = ([1_000.0] * 10 + [1_100.0] * (n - 10), [10_000.0] * n)
    assert _change_at({"S": series}, n - 1) == {"S": pytest.approx(0.01)}
