"""적용 불가 필드 경고 (spec D4, P1-05).

`FIELD_APPLICABILITY`가 조건표의 유일한 owner다: validator는 문서에 **명시된** pointer에만 warning을
내고, runtime schema·FieldContract는 같은 행을 `x-applicable-when`으로 노출한다. predicate는 조건
데이터(`equals`/`not_null`)에서 파생되므로 선언과 판정이 어긋날 수 없다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from strategy_workbench.domain.strategy.facade.constraints import (
    FIELD_APPLICABILITY,
    ApplicabilityCondition,
    FieldApplicability,
    field_applicability_index,
    field_default,
    resolve_scalar,
)
from strategy_workbench.domain.strategy.facade.document import hydrate_strategy_document
from strategy_workbench.domain.strategy.facade.schema import (
    strategy_document_schema,
    strategy_field_contracts,
)
from strategy_workbench.domain.strategy.facade.specification import StrategyIdentity, StrategySpec
from strategy_workbench.domain.strategy.facade.validation import validate_strategy

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
DRAFT = StrategyIdentity("draft", 0)

# 각 행을 "읽히지 않는 모드"로 만드는 문서 변경. 기본 fixture(long_only, top_n, monthly, equal,
# liquidity/regime 없음)에서 조건이 성립하는 행은 없으므로 명시만 하면 전부 warning이다.
INAPPLICABLE_VALUES: dict[str, Any] = {
    "/portfolio/short_selection_count": 5,
    "/portfolio/selection_percentile": 0.2,
    "/portfolio/rebalance_every_n_sessions": 3,
    "/portfolio/minimum_liquidity": 1000.0,
    "/risk/sector_neutral": True,
    "/risk/risk_field_id": "price.market_cap",
    "/signal/regime_minimum": 0.5,
}
# 각 행의 조건을 성립시키는 문서 변경.
APPLICABLE_CONTEXT: dict[str, dict[str, Any]] = {
    "/portfolio/short_selection_count": {"/portfolio/side": "long_short"},
    "/portfolio/selection_percentile": {"/portfolio/selection_method": "percentile"},
    "/portfolio/rebalance_every_n_sessions": {"/portfolio/rebalance": "every_n_sessions"},
    "/portfolio/minimum_liquidity": {"/portfolio/liquidity_field_id": "price.trading_value"},
    "/risk/sector_neutral": {"/portfolio/side": "long_short", "/risk/net_exposure": 0.0},
    "/risk/risk_field_id": {"/portfolio/weighting": "risk"},
    "/signal/regime_minimum": {"/signal/regime_field_id": "price.close"},
}


def _document() -> dict[str, Any]:
    return json.loads((FIXTURES / "quality_momentum.json").read_text(encoding="utf-8"))


def _set(document: dict[str, Any], pointer: str, value: Any) -> None:
    section, key = pointer.strip("/").split("/")
    document.setdefault(section, {})[key] = value


def _hydrate(document: dict[str, Any]) -> StrategySpec:
    result = hydrate_strategy_document(document, identity=DRAFT)
    assert result.ok and result.spec is not None, result.issues
    return result.spec


def _written(document: dict[str, Any]) -> set[str]:
    return {
        f"/{section}/{key}"
        for section, block in document.items()
        if isinstance(block, dict)
        for key in block
    }


def test_table_covers_the_spec_d4_rows_exactly() -> None:
    assert {row.pointer for row in FIELD_APPLICABILITY} == set(INAPPLICABLE_VALUES)
    assert field_applicability_index().keys() == set(INAPPLICABLE_VALUES)


@pytest.mark.parametrize("pointer", sorted(INAPPLICABLE_VALUES))
def test_explicit_field_in_the_wrong_mode_is_reported_once(pointer: str) -> None:
    """warning 행은 warning 하나, 기존 error 규칙이 소유한 행은 그 error 하나만 보고한다."""
    document = _document()
    _set(document, pointer, INAPPLICABLE_VALUES[pointer])
    row = field_applicability_index()[pointer]

    validation = validate_strategy(_hydrate(document), written_pointers=_written(document))

    warnings = [i for i in validation.issues if i.code == "strategy.field.inapplicable"]
    if row.owned_by_error is None:
        assert [issue.path for issue in warnings] == [row.path]
        assert warnings[0].severity.value == "warning" and validation.valid
        assert "일 때만 적용됩니다" in warnings[0].message
    else:
        assert not warnings
        assert row.owned_by_error in {issue.code for issue in validation.issues}
        assert not validation.valid


@pytest.mark.parametrize("pointer", sorted(INAPPLICABLE_VALUES))
def test_explicit_field_in_its_own_mode_is_silent(pointer: str) -> None:
    document = _document()
    _set(document, pointer, INAPPLICABLE_VALUES[pointer])
    for context_pointer, value in APPLICABLE_CONTEXT[pointer].items():
        _set(document, context_pointer, value)

    validation = validate_strategy(_hydrate(document), written_pointers=_written(document))

    assert not [i for i in validation.issues if i.code == "strategy.field.inapplicable"]


def test_written_default_value_in_the_wrong_mode_is_silent() -> None:
    """canonical 문서(JSON 투영·legacy generated source·포맷 변환)는 기본값을 전부 적는다.
    경고 없음."""
    document = _document()
    for pointer in ("/portfolio/short_selection_count", "/portfolio/selection_percentile"):
        _set(document, pointer, field_default(pointer))

    validation = validate_strategy(_hydrate(document), written_pointers=_written(document))

    assert not [i for i in validation.issues if i.code == "strategy.field.inapplicable"]


def test_omitted_fields_never_warn_even_when_defaults_would_be_inapplicable() -> None:
    document = _document()

    spec = _hydrate(document)
    with_pointers = validate_strategy(spec, written_pointers=_written(document))
    without_pointers = validate_strategy(spec)

    for validation in (with_pointers, without_pointers):
        assert not [i for i in validation.issues if i.code == "strategy.field.inapplicable"]


def test_predicate_is_derived_from_the_declared_condition() -> None:
    """선언(`equals`/`not_null`)과 판정이 같은 데이터에서 나온다: 행마다 조건을 손으로 재계산."""
    spec = _hydrate(_document())
    for row in FIELD_APPLICABILITY:
        value = resolve_scalar(spec, row.condition.pointer)
        expected = (
            value is not None
            if row.condition.not_null
            else str(getattr(value, "value", value)) == row.condition.equals
        )
        assert row.applies_to(spec) is expected, row.pointer


def test_condition_must_be_exactly_one_kind() -> None:
    with pytest.raises(ValueError, match="exactly one of equals/not_null"):
        ApplicabilityCondition("/portfolio/side")
    with pytest.raises(ValueError, match="exactly one of equals/not_null"):
        ApplicabilityCondition("/portfolio/side", equals="long_short", not_null=True)
    assert replace(FieldApplicability("/a/b", ApplicabilityCondition("/x", not_null=True), "k"))


def test_runtime_schema_and_contracts_publish_the_same_rows() -> None:
    schema = strategy_document_schema()
    contracts = {row.pointer: row for row in strategy_field_contracts()}
    published: dict[str, dict[str, Any]] = {}
    for section, definition in schema["$defs"].items():
        for name, prop in definition.get("properties", {}).items():
            if "x-applicable-when" in prop:
                published[f"#/$defs/{section}/{name}"] = prop["x-applicable-when"]
    by_pointer = {
        row.pointer: {
            "pointer": row.condition.pointer,
            **({"equals": row.condition.equals} if row.condition.equals else {"not_null": True}),
            "description_key": row.description_key,
        }
        for row in FIELD_APPLICABILITY
    }
    assert {row.pointer for row in FIELD_APPLICABILITY if row.owned_by_error} == {
        "/portfolio/minimum_liquidity",
        "/risk/sector_neutral",
        "/signal/regime_minimum",
    }
    assert len(published) == len(FIELD_APPLICABILITY)
    assert all(value in by_pointer.values() for value in published.values()), published
    for row in FIELD_APPLICABILITY:
        contract = contracts[row.pointer]
        assert contract.applicable_when is not None
        assert contract.applicable_when.pointer == row.condition.pointer
        assert contract.applicable_when.equals == row.condition.equals
        assert contract.applicable_when.not_null is row.condition.not_null
        assert contract.applicable_when.description_key == row.description_key
    assert contracts["/risk/max_name_weight"].applicable_when is None
