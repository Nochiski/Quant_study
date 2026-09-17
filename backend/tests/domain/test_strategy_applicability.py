"""적용 불가 필드 경고 (spec D4, P1-05).

`FIELD_APPLICABILITY`가 조건표의 유일한 owner다: validator는 문서에 **명시된** pointer에만 warning을
내고, runtime schema·FieldContract는 같은 행을 `x-applicable-when`으로 노출한다. predicate는 조건
데이터(`equals`/`not_null`, AND 조합)에서 파생되므로 선언과 판정이 어긋날 수 없다.
"""

from __future__ import annotations

import dataclasses
import json
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

# 행마다 (기본값과 다른 명시값, 읽히지 않게 만드는 문맥, 읽히게 만드는 문맥).
# 기본 fixture: long_only, top_n, monthly, equal, liquidity/regime 없음.
CASES: dict[str, tuple[Any, dict[str, Any], dict[str, Any]]] = {
    "/portfolio/selection_count": (50, {"/portfolio/selection_method": "percentile"}, {}),
    "/portfolio/short_selection_count": (5, {}, {"/portfolio/side": "long_short"}),
    "/portfolio/selection_percentile": (0.2, {}, {"/portfolio/selection_method": "percentile"}),
    "/portfolio/rebalance_every_n_sessions": (
        3,
        {},
        {"/portfolio/rebalance": "every_n_sessions"},
    ),
    "/portfolio/minimum_liquidity": (
        1000.0,
        {},
        {"/portfolio/liquidity_field_id": "price.trading_value"},
    ),
    "/risk/sector_neutral": (
        True,
        {},
        {"/portfolio/side": "long_short", "/risk/net_exposure": 0.0},
    ),
    "/risk/risk_field_id": ("price.market_cap", {}, {"/portfolio/weighting": "risk"}),
    "/signal/regime_minimum": (0.5, {}, {"/signal/regime_field_id": "price.close"}),
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


def _warnings(validation: Any) -> list[Any]:
    return [i for i in validation.issues if i.code == "strategy.field.inapplicable"]


def test_table_covers_the_spec_d4_rows_plus_selection_count() -> None:
    assert {row.pointer for row in FIELD_APPLICABILITY} == set(CASES)
    assert field_applicability_index().keys() == set(CASES)


@pytest.mark.parametrize("pointer", sorted(CASES))
def test_explicit_field_in_the_wrong_mode_is_reported_once(pointer: str) -> None:
    """warning 행은 warning 하나, 기존 error 규칙이 소유한 행은 그 error 하나만 보고한다."""
    value, inapplicable_context, _applicable_context = CASES[pointer]
    document = _document()
    _set(document, pointer, value)
    for context_pointer, context_value in inapplicable_context.items():
        _set(document, context_pointer, context_value)
    row = field_applicability_index()[pointer]

    validation = validate_strategy(_hydrate(document), written_pointers=_written(document))

    warnings = _warnings(validation)
    if row.owned_by_error is None:
        assert [issue.path for issue in warnings] == [row.path]
        assert warnings[0].severity.value == "warning" and validation.valid
        assert "일 때만 적용됩니다" in warnings[0].message
    else:
        assert not warnings
        assert row.owned_by_error in {issue.code for issue in validation.issues}
        assert not validation.valid


@pytest.mark.parametrize("pointer", sorted(CASES))
def test_explicit_field_in_its_own_mode_is_silent(pointer: str) -> None:
    value, _inapplicable_context, applicable_context = CASES[pointer]
    document = _document()
    _set(document, pointer, value)
    for context_pointer, context_value in applicable_context.items():
        _set(document, context_pointer, context_value)

    validation = validate_strategy(_hydrate(document), written_pointers=_written(document))

    assert not _warnings(validation)


def test_short_selection_count_needs_both_long_short_and_top_n() -> None:
    """P1-05 리뷰 P2-002: percentile 모드에서는 롱숏이어도 short_selection_count를 읽지 않는다."""
    document = _document()
    _set(document, "/portfolio/short_selection_count", 5)
    _set(document, "/portfolio/side", "long_short")
    _set(document, "/portfolio/selection_method", "percentile")

    validation = validate_strategy(_hydrate(document), written_pointers=_written(document))

    assert [issue.path for issue in _warnings(validation)] == ["portfolio.short_selection_count"]
    assert "그리고" in _warnings(validation)[0].message


def test_written_default_value_in_the_wrong_mode_is_silent() -> None:
    """canonical 문서(JSON 투영·legacy generated source·포맷 변환)는 기본값을 전부 적는다.
    경고 없음."""
    document = _document()
    for pointer in ("/portfolio/short_selection_count", "/portfolio/selection_percentile"):
        _set(document, pointer, field_default(pointer))

    validation = validate_strategy(_hydrate(document), written_pointers=_written(document))

    assert not _warnings(validation)


def test_omitted_fields_never_warn_even_when_defaults_would_be_inapplicable() -> None:
    document = _document()

    spec = _hydrate(document)
    with_pointers = validate_strategy(spec, written_pointers=_written(document))
    without_pointers = validate_strategy(spec)

    for validation in (with_pointers, without_pointers):
        assert not _warnings(validation)


def test_predicate_is_derived_from_the_declared_conditions() -> None:
    """선언(`equals`/`not_null`, AND)과 판정이 같은 데이터에서 나온다: 행마다 손으로 재계산."""
    spec = _hydrate(_document())
    for row in FIELD_APPLICABILITY:
        expected = True
        for condition in row.conditions:
            value = resolve_scalar(spec, condition.pointer)
            expected = expected and (
                value is not None
                if condition.not_null
                else str(getattr(value, "value", value)) == condition.equals
            )
        assert row.applies_to(spec) is expected, row.pointer


def test_condition_must_be_exactly_one_kind_and_equals_is_a_string() -> None:
    with pytest.raises(ValueError, match="exactly one of equals/not_null"):
        ApplicabilityCondition("/portfolio/side")
    with pytest.raises(ValueError, match="exactly one of equals/not_null"):
        ApplicabilityCondition("/portfolio/side", equals="long_short", not_null=True)
    with pytest.raises(TypeError, match="enum's string value"):
        ApplicabilityCondition("/risk/sector_neutral", equals=True)  # pyright: ignore[reportArgumentType]  # reason: 런타임 가드 검증
    with pytest.raises(ValueError, match="needs a condition"):
        FieldApplicability("/a/b", (), "k")


def test_runtime_schema_and_contracts_publish_identical_rows() -> None:
    """P1-05 리뷰 P2-003·004: 두 투영이 같은 모양(all_of·description_key·owned_by_error)이다."""
    schema = strategy_document_schema()
    contracts = {row.pointer: row for row in strategy_field_contracts()}
    published: list[dict[str, Any]] = [
        prop["x-applicable-when"]
        for definition in schema["$defs"].values()
        for prop in definition.get("properties", {}).values()
        if "x-applicable-when" in prop
    ]
    assert len(published) == len(FIELD_APPLICABILITY)
    for row in FIELD_APPLICABILITY:
        contract = contracts[row.pointer].applicable_when
        assert contract is not None
        as_dict = json.loads(json.dumps(dataclasses.asdict(contract)))  # 스키마와 같은 JSON 값 모양
        assert as_dict in published, row.pointer
        assert as_dict["owned_by_error"] == row.owned_by_error
        assert [c["pointer"] for c in as_dict["all_of"]] == [c.pointer for c in row.conditions]
        assert all(("equals" in c and "not_null" in c) for c in as_dict["all_of"])
    assert {row.pointer for row in FIELD_APPLICABILITY if row.owned_by_error} == {
        "/portfolio/minimum_liquidity",
        "/risk/sector_neutral",
        "/signal/regime_minimum",
    }
    assert contracts["/risk/max_name_weight"].applicable_when is None
