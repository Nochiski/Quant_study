"""시도 키 — 분류표의 칸마다 "바꾸면 새 시도인가"(검증 랩 spec D2, V1-05).

같은 시도는 같은 키, 새 시도는 다른 키다. 기준 실행과 한 칸만 다른 실행을 견준다.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields, replace
from datetime import date
from typing import Any

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import MetricScope
from strategy_workbench.domain.backtest.facade.environment import (
    ParticipationBasis,
    RunEnvironment,
    SellTax,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    ExecutionCore,
    MetricWindow,
)
from strategy_workbench.domain.backtest.facade.trials import TRIAL_KEY_ROLES, trial_key
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.strategy.facade.specification import (
    FloatParameter,
    strategy_semantic_hash,
    strategy_spec_hash,
)

_TEMPLATE = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "s").template()
_STRATEGY = replace(
    _TEMPLATE,
    parameters=(FloatParameter("tilt", 0.5, 0.0, 1.0, kind="float"),),
)
_ENVIRONMENT = RunEnvironment(start=date(2021, 1, 4), end=date(2021, 6, 30), universe_id="u")
_BASE = BacktestRunSpec(strategy=_STRATEGY, environment=_ENVIRONMENT)

# (칸, 바꾼 값, 새 시도인가). 칸마다 한 줄 이상이고, 방향 칸은 유리·불리 양쪽을 본다.
_ENVIRONMENT_VARIATIONS: tuple[tuple[str, dict[str, Any], bool], ...] = (
    # 시장·빈도·체결 시점은 enum 값이 하나뿐이라 다른 문자열로 "바뀌었다"를 흉내 낸다.
    ("market", {"market": "XNAS"}, True),
    ("frequency", {"frequency": "weekly"}, True),
    ("timing", {"timing": "same_close"}, True),
    ("start", {"start": date(2021, 1, 5)}, True),
    ("end", {"end": date(2021, 12, 30)}, False),
    ("universe_id", {"universe_id": "other"}, True),
    ("participation_rate", {"participation_rate": 0.2}, True),
    ("participation_rate", {"participation_rate": 0.05}, False),
    ("participation_basis", {"participation_basis": ParticipationBasis.ADV20}, True),
    ("fee_bps", {"fee_bps": 10.0}, True),
    ("fee_bps", {"fee_bps": 30.0}, False),
    ("slippage_bps", {"slippage_bps": 5.0}, True),
    ("slippage_bps", {"slippage_bps": 25.0}, False),
    ("sell_tax", {"sell_tax": SellTax.NONE}, True),
    ("sell_tax_bps", {"sell_tax": SellTax.CUSTOM, "sell_tax_bps": 40.0}, True),
    ("missing", {"missing": MissingPolicy.ZERO}, True),
)


def _key(**environment: Any) -> str:
    return trial_key(replace(_BASE, environment=replace(_ENVIRONMENT, **environment)))


@pytest.mark.parametrize(("_field", "changes", "new_trial"), _ENVIRONMENT_VARIATIONS)
def test_each_run_environment_field_is_a_new_trial_only_when_its_role_says_so(
    _field: str, changes: dict[str, Any], new_trial: bool
) -> None:
    assert (_key(**changes) != trial_key(_BASE)) is new_trial


def test_every_classified_field_has_a_variation() -> None:
    assert {name for name, _changes, _new in _ENVIRONMENT_VARIATIONS} == set(TRIAL_KEY_ROLES)


def test_favourable_costs_carry_their_value_and_unfavourable_ones_fold_into_the_default() -> None:
    # 유리한 쪽은 값마다 다른 시도, 불리한 쪽은 얼마든 기본값과 한 시도다.
    assert _key(fee_bps=10.0) != _key(fee_bps=5.0)
    assert _key(fee_bps=30.0) == _key(fee_bps=60.0) == _key(fee_bps=15.0)
    assert _key(participation_rate=0.05) == _key(participation_rate=0.01) == trial_key(_BASE)
    # 직접 입력 거래세는 견줄 스키마 기본값이 없어 값이 늘 키에 든다.
    custom = {"sell_tax": SellTax.CUSTOM}
    assert _key(**custom, sell_tax_bps=10.0) != _key(**custom, sell_tax_bps=40.0)


@pytest.mark.parametrize(
    "changes",
    [
        {"core": ExecutionCore.PYTHON},
        {"initial_cash": 5_000_000.0},
        {"benchmark_security_id": "005930"},
        {"annualization_days": 250},
        {
            "metric_windows": (
                MetricWindow(MetricScope.OUT_OF_SAMPLE, date(2021, 3, 2), date(2021, 6, 30)),
            )
        },
        {"lineage_strategy_id": "s-1"},
    ],
)
def test_run_options_outside_the_environment_never_make_a_new_trial(
    changes: dict[str, Any],
) -> None:
    assert trial_key(replace(_BASE, **changes)) == trial_key(_BASE)


@pytest.mark.parametrize(
    ("strategy", "new_trial"),
    [
        (replace(_STRATEGY, title="이름만 바꿈"), False),
        (replace(_STRATEGY, description="설명만 바꿈"), False),
        # 범위 정의만 바꾸고 기본값이 같으면 같은 시도다.
        (
            replace(_STRATEGY, parameters=(FloatParameter("tilt", 0.5, 0.1, 2.0, kind="float"),)),
            False,
        ),
        (
            replace(_STRATEGY, parameters=(FloatParameter("tilt", 0.7, 0.0, 1.0, kind="float"),)),
            True,
        ),
        (
            replace(
                _STRATEGY,
                factors=(replace(_STRATEGY.factors[0], weight=2.0), *_STRATEGY.factors[1:]),
            ),
            True,
        ),
    ],
)
def test_only_meaning_and_resolved_parameter_values_split_the_strategy_axis(
    strategy: Any, new_trial: bool
) -> None:
    assert (trial_key(replace(_BASE, strategy=strategy)) != trial_key(_BASE)) is new_trial


@pytest.mark.parametrize(
    ("parameter_values", "new_trial"),
    [
        ({"tilt": 0.7}, True),
        # 기본값을 명시한 요청은 생략한 요청과 같은 해소 값이다. 정수 1 은 실수 칸에서 1.0 이다.
        ({"tilt": 0.5}, False),
        ({"tilt": 1}, True),
    ],
)
def test_a_requested_parameter_value_is_a_new_trial_unless_it_resolves_to_the_default(
    parameter_values: dict[str, Any], new_trial: bool
) -> None:
    keyed = trial_key(replace(_BASE, parameter_values=parameter_values))

    assert (keyed != trial_key(_BASE)) is new_trial
    assert trial_key(replace(_BASE, parameter_values={"tilt": 1})) == trial_key(
        replace(_BASE, parameter_values={"tilt": 1.0})
    )


def test_the_semantic_hash_ignores_the_title_that_the_spec_hash_keeps() -> None:
    renamed = replace(_STRATEGY, title="이름만 바꿈")

    assert strategy_spec_hash(renamed) != strategy_spec_hash(_STRATEGY)
    assert strategy_semantic_hash(renamed) == strategy_semantic_hash(_STRATEGY)


# 스키마 기본값은 키 표기의 기준이라 바꾸면 기본값으로 돌던 모든 실행의 시도 키가 바뀐다. 바꾸는
# PR 은 이 스냅샷을 고치면서 그 변경을 명시한다(spec D2). 기본값이 없는 칸은 늘 키에 든다.
_SCHEMA_DEFAULTS = {
    "market": "KRX",
    "frequency": "daily",
    "start": MISSING,
    "end": MISSING,
    "universe_id": MISSING,
    "timing": "next_open",
    "participation_rate": 0.1,
    "participation_basis": "session_volume",
    "fee_bps": 15.0,
    "slippage_bps": 10.0,
    "sell_tax": "krx_statutory",
    "sell_tax_bps": None,
    "missing": "drop",
}


def test_schema_defaults_match_the_snapshot_that_the_trial_key_was_built_on() -> None:
    assert {item.name: item.default for item in fields(RunEnvironment)} == _SCHEMA_DEFAULTS


def test_adding_a_field_at_its_default_keeps_every_existing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass(frozen=True, kw_only=True)
    class _WithNewField(RunEnvironment):
        impact_coefficient: float = 1.0

    monkeypatch.setitem(TRIAL_KEY_ROLES, "impact_coefficient", TRIAL_KEY_ROLES["fee_bps"])
    widened = _WithNewField(
        **{item.name: getattr(_ENVIRONMENT, item.name) for item in fields(_ENVIRONMENT)}
    )

    assert trial_key(replace(_BASE, environment=widened)) == trial_key(_BASE)
    assert trial_key(
        replace(_BASE, environment=replace(widened, impact_coefficient=0.5))
    ) != trial_key(_BASE)
