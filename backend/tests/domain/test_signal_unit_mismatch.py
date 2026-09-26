"""`signal.normalization: none` 이고 알파 팩터 출력 단위가 다르면 warning (P2-07, spec D4).

정규화 없이 원시 점수를 가중 합하면 원 단위 금액 팩터가 비율 팩터를 지배한다(US-CS-05). 막을 일은
아니라(1.1 에서 올라온 문서는 `none` 으로 예전 결과를 그대로 낸다) error 가 아니라 warning 이다.

- 단위는 팩터 출력 노드의 추론 단위(`NodeContract.unit`)다. 필드 계약이 없으면 필드 단위를 모르므로
  (`unknown`) 판정하지 않는다 — 어댑터가 없는 컨텍스트는 지금과 같다.
- 역가중에만 쓰이는 리스크 팩터(`weighting: risk` + `risk_factor_id`)는 합성에 들어가지 않으므로
  비교하지 않는다. 팩터가 하나면 경고하지 않는다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    hydrate_strategy_document,
)
from strategy_workbench.domain.strategy.facade.specification import (
    FieldMetadata,
    StrategyIdentity,
    StrategySpec,
)
from strategy_workbench.domain.strategy.facade.validation import (
    ValidationIssue,
    ValidationSeverity,
    validate_strategy,
)

_FIELDS = (
    FieldMetadata(field_id="price.close", unit="KRW"),
    FieldMetadata(field_id="valuation.pbr", unit="ratio"),
    FieldMetadata(field_id="price.market_cap", unit="KRW"),
)


def _factor(factor_id: str, field_id: str, *, standardize: bool = False) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [{"kind": "field", "node_id": "source", "field_id": field_id}]
    output = "source"
    if standardize:
        nodes.append(
            {
                "kind": "cross_sectional",
                "node_id": "standardized",
                "operator": "zscore",
                "input_node_id": "source",
            }
        )
        output = "standardized"
    return {
        "factor_id": factor_id,
        "label": factor_id,
        "direction": "high",
        "weight": 1.0,
        "graph": {"nodes": nodes, "output_node_id": output},
    }


def _spec(
    *factors: dict[str, Any], normalization: str = "none", risk: dict[str, Any] | None = None
) -> StrategySpec:
    document: dict[str, Any] = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "title": "단위 경고",
        "signal": {"normalization": normalization},
        "factors": copy.deepcopy(list(factors)),
        "portfolio": {"selection_count": 5, "rebalance": "monthly"},
        "risk": {"max_name_weight": 0.2},
    }
    if risk is not None:
        document["portfolio"]["weighting"] = "risk"
        document["risk"].update(risk)
    hydration = hydrate_strategy_document(document, identity=StrategyIdentity("draft", 0))
    assert hydration.ok and hydration.spec is not None, hydration.issues
    return hydration.spec


def _unit_warnings(spec: StrategySpec, **kwargs: Any) -> list[ValidationIssue]:
    return [
        issue
        for issue in validate_strategy(spec, **kwargs).issues
        if issue.code == "strategy.signal.unit_mismatch"
    ]


def test_raw_factors_with_different_units_warn_when_normalization_is_none() -> None:
    spec = _spec(_factor("momentum", "price.close"), _factor("value", "valuation.pbr"))

    validation = validate_strategy(spec, fields=_FIELDS)
    [warning] = _unit_warnings(spec, fields=_FIELDS)

    assert validation.valid, "경고는 실행을 막지 않는다"
    assert warning.severity is ValidationSeverity.WARNING
    assert warning.path == "signal.normalization"
    assert "'momentum': 'KRW'" in warning.message
    assert "'value': 'ratio'" in warning.message


@pytest.mark.parametrize("normalization", ["rank", "zscore"])
def test_normalizing_before_the_sum_silences_the_warning(normalization: str) -> None:
    spec = _spec(
        _factor("momentum", "price.close"),
        _factor("value", "valuation.pbr"),
        normalization=normalization,
    )

    assert _unit_warnings(spec, fields=_FIELDS) == []


def test_same_unit_factors_do_not_warn() -> None:
    spec = _spec(_factor("momentum", "price.close"), _factor("size", "price.market_cap"))

    assert _unit_warnings(spec, fields=_FIELDS) == []


def test_a_single_factor_does_not_warn() -> None:
    assert _unit_warnings(_spec(_factor("momentum", "price.close")), fields=_FIELDS) == []


def test_factors_standardized_inside_their_graph_are_dimensionless() -> None:
    """그래프 안에서 z-score 로 바꾼 두 팩터는 둘 다 무차원이라 경고하지 않는다(BACKLOG-003)."""
    spec = _spec(
        _factor("momentum", "price.close", standardize=True),
        _factor("value", "valuation.pbr", standardize=True),
    )

    assert _unit_warnings(spec, fields=_FIELDS) == []


def test_the_inverse_risk_factor_is_not_part_of_the_sum() -> None:
    spec = _spec(
        _factor("momentum", "price.close"),
        _factor("size", "price.market_cap"),
        _factor("vol", "valuation.pbr"),
        risk={"risk_factor_id": "vol"},
    )

    assert _unit_warnings(spec, fields=_FIELDS) == []


def test_without_field_contracts_units_are_unknown_and_nothing_is_judged() -> None:
    spec = _spec(_factor("momentum", "price.close"), _factor("value", "valuation.pbr"))

    assert _unit_warnings(spec) == []
