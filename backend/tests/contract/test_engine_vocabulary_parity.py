"""실행 엔진 어휘와 workbench domain 어휘의 대조(#495).

domain은 엔진 커널을 import할 수 없어 같은 사실을 자기 어휘로 둔다. 어댑터가 그 사이를 옮기므로
두 어휘가 갈라지면 조용히 0이 되거나(비용 합계) 다른 사건 집합을 쓰게 된다(가격 배수).
"""

import pytest

from backtest_engine.types.events import PRICE_ADJUSTING_ACTION_TYPES, CostKind
from strategy_workbench.domain.backtest.facade.environment import PRICE_ADJUSTING_ACTIONS
from strategy_workbench.domain.backtest.facade.runs import RunCostKind


def test_every_engine_cost_kind_has_a_result_vocabulary() -> None:
    assert {RunCostKind(kind.value) for kind in CostKind} == set(RunCostKind)


def test_unknown_cost_kind_fails_instead_of_vanishing_from_totals() -> None:
    with pytest.raises(ValueError):
        RunCostKind("stamp_duty")


def test_price_adjusting_actions_match_the_engine_owner() -> None:
    assert PRICE_ADJUSTING_ACTIONS == {kind.value for kind in PRICE_ADJUSTING_ACTION_TYPES}
