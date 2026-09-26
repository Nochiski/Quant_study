"""`risk.risk_factor_id` — 팩터 출력으로 역가중하고 그 팩터를 합성에서 뺀다 (spec D3 S6, P2-06).

의미의 정본은 spec D3 S6이다.

- 제외는 `weighting: risk` 이고 `risk_factor_id` 가 설정됐을 때만 일어난다. 다른 모드에서는 그
  팩터가 일반 알파로 남는다(적용 조건 warning 은 `FIELD_APPLICABILITY` 행이 낸다).
- 역가중은 `signal.normalization` 이전의 **원시** 출력을 쓴다. `<= 0` 이나 결측은 `MISSING_RISK`.
- 제외한 뒤 알파가 0개면 compile error `strategy.signal.no_alpha_factor` — 막지 않으면 합성 점수가
  전부 `None` 이 되어 `security_id` 사전순 상위 N이 조용히 선정된다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, timedelta

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.portfolio.facade.construction import (
    ExclusionReason,
    PortfolioFactorValue,
    PortfolioObservation,
    PortfolioTraceSelection,
    compile_target_tape,
    compile_target_tape_with_trace,
)
from strategy_workbench.domain.strategy.facade.explanation import explain_strategy
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorGraph,
    FactorSignal,
    FieldNode,
    RebalanceFrequency,
    SignalNormalization,
    StrategySpec,
    WeightingMethod,
)
from strategy_workbench.domain.strategy.facade.validation import (
    ValidationSeverity,
    validate_strategy,
)

DAY = date(2026, 1, 2)


def _factor(
    factor_id: str, *, direction: FactorDirection = FactorDirection.HIGH, weight: float = 1.0
) -> FactorSignal:
    return FactorSignal(
        factor_id=factor_id,
        label=factor_id,
        direction=direction,
        weight=weight,
        graph=FactorGraph(
            nodes=(FieldNode(node_id="source", field_id=f"f.{factor_id}", kind="field"),),
            output_node_id="source",
        ),
    )


def _base(*factors: FactorSignal, normalization: SignalNormalization) -> StrategySpec:
    template = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "u").template()
    return replace(
        template,
        factors=factors,
        signal=replace(template.signal, normalization=normalization),
        portfolio=replace(
            template.portfolio,
            selection_count=2,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
        risk=replace(template.risk, max_name_weight=1.0, max_sector_weight=1.0),
    )


def _with_risk_factor(
    spec: StrategySpec, factor_id: str | None, *, weighting: WeightingMethod
) -> StrategySpec:
    return replace(
        spec,
        portfolio=replace(spec.portfolio, weighting=weighting),
        risk=replace(spec.risk, risk_factor_id=factor_id),
    )


# 알파 두 개(`value`·`quality`)는 a > b > c > d > e 로 같은 순서다. 변동성(`vol`, 낮을수록 선호)은
# a·b 가 가장 높아서, 합성에 들어가면 어떤 정규화에서도 상위 2개가 a·b 가 아니게 된다 — 그래서
# "선정이 같다"는 단언이 제외를 빼먹은 구현을 실제로 잡는다(아래 대조 문서가 그 사실을 먼저 단언).
_VALUES: dict[str, dict[str, float | None]] = {
    "value": {"a": 5.0, "b": 4.0, "c": 3.0, "d": 2.0, "e": 1.0},
    "quality": {"a": 5.0, "b": 4.0, "c": 3.0, "d": 2.0, "e": 1.0},
    "vol": {"a": 10.0, "b": 9.0, "c": 1.0, "d": 2.0, "e": 3.0},
}


def _observations(
    values: Mapping[str, Mapping[str, float | None]] = _VALUES,
) -> tuple[PortfolioObservation, ...]:
    securities = sorted({security for column in values.values() for security in column})
    return tuple(
        PortfolioObservation(
            as_of=DAY,
            security_id=security_id,
            universe_member=True,
            factor_values=tuple(
                PortfolioFactorValue(
                    factor_id=factor_id, value=column.get(security_id), available_date=DAY
                )
                for factor_id, column in values.items()
            ),
            fields=(),
            sector_id=security_id,
        )
        for security_id in securities
    )


def _environment() -> RunEnvironment:
    return RunEnvironment(start=date(2026, 1, 1), end=date(2026, 12, 31), universe_id="krx.x")


def _frame(spec: StrategySpec, observations: tuple[PortfolioObservation, ...]):
    tape = compile_target_tape(
        spec,
        environment=_environment(),
        data_snapshot_id="snapshot-1",
        sessions=(DAY, DAY + timedelta(days=1)),
        observations=observations,
    )
    return tape.frames[0]


def _weights(spec: StrategySpec, observations=None) -> dict[str, float]:
    frame = _frame(spec, observations or _observations())
    return {target.security_id: target.weight for target in frame.targets}


def _codes(spec: StrategySpec, severity: ValidationSeverity) -> list[str]:
    return [i.code for i in validate_strategy(spec).issues if i.severity is severity]


# --- 수치: 알파 N개 문서 vs 거기에 변동성 팩터 + `risk_factor_id` 를 붙인 문서 -----------------


@pytest.mark.parametrize("normalization", tuple(SignalNormalization))
def test_risk_factor_changes_only_the_weights_never_the_selection(
    normalization: SignalNormalization,
) -> None:
    """기준선: **알파 2개 문서**(`equal`). 비교: 같은 문서에 변동성 팩터 1개 + `risk_factor_id`.

    "붙이기 전후"가 아니다 — 변동성 팩터가 합성에 들어간 문서(대조)는 선정이 바뀌는 것이 정상이고,
    이 테스트는 그 사실을 먼저 단언해 픽스처가 판별력을 갖는지 확인한다.
    """
    alpha = _base(_factor("value"), _factor("quality"), normalization=normalization)
    with_vol = replace(
        alpha, factors=(*alpha.factors, _factor("vol", direction=FactorDirection.LOW))
    )
    risk_weighted = _with_risk_factor(with_vol, "vol", weighting=WeightingMethod.RISK)
    control = _with_risk_factor(with_vol, None, weighting=WeightingMethod.EQUAL)

    baseline = _weights(alpha)
    weighted = _weights(risk_weighted)

    assert set(_weights(control)) != set(baseline), "대조 문서가 선정을 바꾸지 못하면 판별력 없음"
    assert set(baseline) == {"a", "b"}
    assert set(weighted) == set(baseline)
    assert weighted != pytest.approx(baseline)
    # 원시값 역수 비례: 1/10 : 1/9. `rank` 였다면 1/1 : 1/0.75, `zscore` 였다면 음수·0 근처라
    # 이 비율이 나올 수 없다.
    inverse = {"a": 1 / 10.0, "b": 1 / 9.0}
    total = sum(inverse.values())
    assert weighted == pytest.approx({key: value / total for key, value in inverse.items()})


def test_risk_factor_is_read_raw_even_under_rank_normalization() -> None:
    """`rank` 기본값에서 최하위 변동성 종목은 정규화 값이 0 이다 — 그 역수를 쓰면 0 나눗셈이다."""
    alpha = _base(_factor("value"), normalization=SignalNormalization.RANK)
    spec = _with_risk_factor(
        replace(alpha, factors=(*alpha.factors, _factor("vol"))),
        "vol",
        weighting=WeightingMethod.RISK,
    )
    values = {
        "value": {"a": 2.0, "b": 1.0, "c": 0.0},
        "vol": {"a": 0.5, "b": 2.0, "c": 4.0},
    }

    weights = _weights(spec, _observations(values))

    # a 의 변동성은 횡단면 최하위(순위 0.0)지만 원시값 0.5 로 역가중된다: 1/0.5 : 1/2 = 4 : 1.
    assert weights == pytest.approx({"a": 0.8, "b": 0.2})


def test_non_positive_or_missing_risk_factor_value_keeps_the_missing_risk_exclusion() -> None:
    """`<= 0` 은 기존 `MISSING_RISK` 분기다.

    결측은 선정을 흔들지 않고(알파가 아니다) 그 종목의 비중만 막는다.
    """
    alpha = _base(_factor("value"), normalization=SignalNormalization.NONE)
    spec = _with_risk_factor(
        replace(alpha, factors=(*alpha.factors, _factor("vol"))),
        "vol",
        weighting=WeightingMethod.RISK,
    )
    values: dict[str, dict[str, float | None]] = {
        "value": {"a": 3.0, "b": 2.0, "c": 1.0},
        "vol": {"a": 2.0, "b": -1.0, "c": None},
    }

    frame = _frame(spec, _observations(values))
    decisions = {item.security_id: item for item in frame.candidates}

    assert {target.security_id: target.weight for target in frame.targets} == pytest.approx(
        {"a": 1.0}
    )
    assert ExclusionReason.MISSING_RISK in decisions["b"].exclusion_reasons
    # 결측 변동성은 알파 결측(`MISSING_FACTOR`)이 아니다 — c 는 여전히 eligible 이고 순위를 받는다.
    assert decisions["c"].eligible
    assert ExclusionReason.MISSING_FACTOR not in decisions["c"].exclusion_reasons
    assert decisions["c"].rank == 3


def test_other_weightings_keep_the_referenced_factor_in_the_composite() -> None:
    """적용 조건 밖(`equal`)에서는 `risk_factor_id` 를 읽지 않는다 — 합성이 대조 문서와 같다."""
    alpha = _base(_factor("value"), _factor("quality"), normalization=SignalNormalization.NONE)
    with_vol = replace(
        alpha, factors=(*alpha.factors, _factor("vol", direction=FactorDirection.LOW))
    )
    ignored = _with_risk_factor(with_vol, "vol", weighting=WeightingMethod.EQUAL)
    control = _with_risk_factor(with_vol, None, weighting=WeightingMethod.EQUAL)

    def scores(spec: StrategySpec) -> dict[str, float | None]:
        frame = _frame(spec, _observations())
        return {item.security_id: item.composite_score for item in frame.candidates}

    assert scores(ignored) == scores(control)
    assert _codes(ignored, ValidationSeverity.WARNING) == []


def test_construction_trace_lists_only_the_alpha_contributions() -> None:
    alpha = _base(_factor("value"), normalization=SignalNormalization.NONE)
    spec = _with_risk_factor(
        replace(alpha, factors=(*alpha.factors, _factor("vol"))),
        "vol",
        weighting=WeightingMethod.RISK,
    )

    result = compile_target_tape_with_trace(
        spec,
        environment=_environment(),
        data_snapshot_id="snapshot-1",
        sessions=(DAY, DAY + timedelta(days=1)),
        observations=_observations({"value": {"a": 2.0}, "vol": {"a": 1.0}}),
        trace_selection=PortfolioTraceSelection(DAY, ("a",)),
    )

    assert result.trace is not None
    candidate = result.trace.candidates[0]
    assert [item.factor_id for item in candidate.factor_contributions] == ["value"]
    assert candidate.composite_score == pytest.approx(2.0)


# --- 검증 ------------------------------------------------------------------------------------


def _risk_weighted(*factors: FactorSignal, risk_factor_id: str | None) -> StrategySpec:
    return _with_risk_factor(
        _base(*factors, normalization=SignalNormalization.RANK),
        risk_factor_id,
        weighting=WeightingMethod.RISK,
    )


def test_single_factor_referenced_as_risk_leaves_no_alpha_and_is_one_compile_error() -> None:
    spec = _risk_weighted(_factor("vol"), risk_factor_id="vol")

    assert _codes(spec, ValidationSeverity.ERROR) == ["strategy.signal.no_alpha_factor"]
    assert not validate_strategy(spec).valid


def test_excluding_the_risk_factor_is_reported_as_a_warning() -> None:
    spec = _risk_weighted(_factor("value"), _factor("vol"), risk_factor_id="vol")

    validation = validate_strategy(spec)

    assert validation.valid
    excluded = [i for i in validation.issues if i.code == "strategy.risk.risk_factor_excluded"]
    assert [(i.path, i.severity) for i in excluded] == [
        ("risk.risk_factor_id", ValidationSeverity.WARNING)
    ]
    assert "'vol'" in excluded[0].message


def test_risk_field_and_risk_factor_together_are_a_conflict_error() -> None:
    spec = _risk_weighted(_factor("value"), _factor("vol"), risk_factor_id="vol")
    spec = replace(spec, risk=replace(spec.risk, risk_field_id="price.volatility"))

    errors = [i for i in validate_strategy(spec).issues if i.severity is ValidationSeverity.ERROR]

    assert [(i.code, i.path) for i in errors] == [
        ("strategy.risk.risk_source_conflict", "risk.risk_factor_id")
    ]


def test_risk_factor_alone_satisfies_the_risk_weighting_source_rule() -> None:
    """`weighting: risk` 는 리스크 원천 하나를 요구한다 — 필드든 팩터든."""
    with_factor = _risk_weighted(_factor("value"), _factor("vol"), risk_factor_id="vol")
    without_source = _risk_weighted(_factor("value"), risk_factor_id=None)

    assert "strategy.risk.risk_field" not in _codes(with_factor, ValidationSeverity.ERROR)
    assert _codes(without_source, ValidationSeverity.ERROR) == ["strategy.risk.risk_field"]


def test_risk_factor_id_must_name_a_factor_of_the_document() -> None:
    spec = _risk_weighted(_factor("value"), risk_factor_id="volatility")

    errors = [i for i in validate_strategy(spec).issues if i.severity is ValidationSeverity.ERROR]

    assert [(i.code, i.path) for i in errors] == [
        ("strategy.risk.risk_factor_missing", "risk.risk_factor_id")
    ]
    assert "'volatility'" in errors[0].message and "'value'" in errors[0].message


def test_explanation_combines_only_the_alpha_factors_and_names_the_risk_factor() -> None:
    spec = _risk_weighted(_factor("value"), _factor("vol"), risk_factor_id="vol")

    steps = {step.stage: step for step in explain_strategy(spec).steps}

    assert steps["signal"].summary.startswith("팩터 1개를")
    assert steps["signal"].details == ("value",)
    assert any("vol" in detail and "역가중" in detail for detail in steps["risk"].details)
