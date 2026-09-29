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
    ImpactModel,
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
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    ExpressionNode,
    FactorGraph,
    FieldNode,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.strategy.facade.specification import (
    STRATEGY_SEMANTIC_HASH_VERSION,
    FactorDirection,
    FactorSignal,
    FloatParameter,
    StrategyIdentity,
    StrategySpec,
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
    ("impact_model", {"impact_model": ImpactModel.SQRT}, True),
    ("impact_coefficient", {"impact_coefficient": 0.5}, True),
    ("impact_coefficient", {"impact_coefficient": 2.0}, False),
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
    # 직접 입력 거래세율의 기준은 법정 세율표 최대값(30bp)이다. 그 이상은 한 시도로 묶이고 낮으면
    # 값마다 다른 시도다. 방식(법정·직접·없음)을 바꾸는 것은 그 자체로 새 시도다.
    custom = {"sell_tax": SellTax.CUSTOM}
    assert _key(**custom, sell_tax_bps=30.0) == _key(**custom, sell_tax_bps=60.0)
    assert _key(**custom, sell_tax_bps=5.0) != _key(**custom, sell_tax_bps=30.0)
    # 기준은 세율표의 최소(15bp)가 아니라 최대다. 20bp 는 과거 법정 세율보다 낮은 탐색이다.
    assert _key(**custom, sell_tax_bps=20.0) != _key(**custom, sell_tax_bps=60.0)
    assert _key(**custom, sell_tax_bps=5.0) != _key(**custom, sell_tax_bps=10.0)
    assert _key(**custom, sell_tax_bps=30.0) != trial_key(_BASE)


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
        # 팩터 표시 이름과 스키마 판본(업그레이드만 한 리비전)도 의미가 아니다.
        (
            replace(
                _STRATEGY,
                factors=(replace(_STRATEGY.factors[0], label="표시 이름"), *_STRATEGY.factors[1:]),
            ),
            False,
        ),
        (
            replace(
                _STRATEGY, identity=replace(_STRATEGY.identity, schema_version="9.9", revision=7)
            ),
            False,
        ),
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


def _momentum_over_volatility(
    names: tuple[str, str, str, str] = ("close", "mom", "vol", "score"),
    *,
    order: tuple[int, ...] = (0, 1, 2, 3),
    swap: bool = False,
    window: int = 20,
    extra: tuple[ExpressionNode, ...] = (),
) -> StrategySpec:
    """손으로 만든 전략: 팩터 하나(종가 20일 모멘텀 ÷ 종가 20일 표준편차), 나머지는 모델 기본값.

    `names` 는 노드 이름, `order` 는 선언 순서, `swap` 은 나누기 인자 순서다. 템플릿에 기대지
    않아 판본 고정 테스트가 템플릿 변경에 흔들리지 않는다.
    """
    close, mom, vol, score = names
    nodes = (
        FieldNode(node_id=close, field_id="price.close", kind="field"),
        TimeSeriesNode(
            node_id=mom,
            operator=TimeSeriesOperator.MOMENTUM,
            input_node_id=close,
            window=window,
            kind="time_series",
        ),
        TimeSeriesNode(
            node_id=vol,
            operator=TimeSeriesOperator.STANDARD_DEVIATION,
            input_node_id=close,
            window=20,
            kind="time_series",
        ),
        BinaryNode(
            node_id=score,
            operator=BinaryOperator.DIVIDE,
            left_node_id=vol if swap else mom,
            right_node_id=mom if swap else vol,
            kind="binary",
        ),
    )
    graph = FactorGraph(nodes=tuple(nodes[index] for index in order) + extra, output_node_id=score)
    factor = FactorSignal(
        factor_id="momentum_risk",
        label="모멘텀 대비 변동성",
        direction=FactorDirection.HIGH,
        graph=graph,
    )
    return StrategySpec(
        identity=StrategyIdentity(strategy_id="s", revision=1), title="손 예시", factors=(factor,)
    )


@pytest.mark.parametrize(
    ("strategy", "new_trial"),
    [
        # 노드 이름만 바꿨다(이슈 #335 DOMAIN-V1-03). 계산이 같아 같은 시도다.
        (_momentum_over_volatility(("c", "momentum_20", "risk", "out")), False),
        # 선언 순서만 바꿨다. 평가는 출력에서 참조를 따라가 선언 순서를 쓰지 않는다.
        (_momentum_over_volatility(order=(3, 2, 0, 1)), False),
        # 출력에 닿지 않는 노드는 계산되지 않는다.
        (
            _momentum_over_volatility(
                extra=(FieldNode(node_id="unused", field_id="price.volume", kind="field"),)
            ),
            False,
        ),
        # 나누기 인자를 바꾸면 값이 역수가 된다. 인자 순서는 정렬하지 않는다.
        (_momentum_over_volatility(swap=True), True),
        # 노드 파라미터 값(모멘텀 창)을 바꾸면 계산이 달라진다.
        (_momentum_over_volatility(window=60), True),
    ],
)
def test_the_strategy_axis_ignores_node_names_and_declaration_order_but_not_structure(
    strategy: Any, new_trial: bool
) -> None:
    base = trial_key(replace(_BASE, strategy=_momentum_over_volatility()))

    assert (trial_key(replace(_BASE, strategy=strategy)) != base) is new_trial


def test_renaming_a_factor_and_its_risk_reference_keeps_the_strategy_axis() -> None:
    def weighted(factor_id: str) -> StrategySpec:
        strategy = _momentum_over_volatility()
        risk = replace(strategy.factors[0], factor_id="risk_source", label="위험")
        return replace(
            strategy,
            factors=(replace(strategy.factors[0], factor_id=factor_id), risk),
            risk=replace(strategy.risk, risk_factor_id="risk_source"),
        )

    renamed = weighted("mom_vol")
    renamed = replace(
        renamed,
        factors=(renamed.factors[0], replace(renamed.factors[1], factor_id="inverse_risk")),
        risk=replace(renamed.risk, risk_factor_id="inverse_risk"),
    )

    assert strategy_semantic_hash(renamed) == strategy_semantic_hash(weighted("momentum_risk"))
    # 참조가 다른 팩터를 가리키면 다른 시도다.
    pointed_elsewhere = replace(renamed, risk=replace(renamed.risk, risk_factor_id="mom_vol"))
    assert strategy_semantic_hash(pointed_elsewhere) != strategy_semantic_hash(renamed)


# 의미 해시가 보는 칸이나 정규화를 바꾸는 PR 은 `STRATEGY_SEMANTIC_HASH_VERSION` 을 올리고 이 짝을
# 함께 고친다(SoT 시도 키 행). 판본을 올리면 계열마다 다음 실행이 한 번 새 시도로 셀 수 있다.
_PINNED_SEMANTIC_HASH = (
    "strategy-semantic-v2",
    "8831b52527272c9aac1fe18b670a1ec73d4ac5842d2a11fbead79be86708b41f",
)


def test_semantic_hash_changes_come_with_a_version_bump() -> None:
    assert (
        STRATEGY_SEMANTIC_HASH_VERSION,
        strategy_semantic_hash(_momentum_over_volatility()),
    ) == _PINNED_SEMANTIC_HASH


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
    "impact_model": "fixed_bps",
    "impact_coefficient": 1.0,
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
        future_cost_bps: float = 1.0

    monkeypatch.setitem(TRIAL_KEY_ROLES, "future_cost_bps", TRIAL_KEY_ROLES["fee_bps"])
    widened = _WithNewField(
        **{item.name: getattr(_ENVIRONMENT, item.name) for item in fields(_ENVIRONMENT)}
    )

    assert trial_key(replace(_BASE, environment=widened)) == trial_key(_BASE)
    assert trial_key(
        replace(_BASE, environment=replace(widened, future_cost_bps=0.5))
    ) != trial_key(_BASE)
