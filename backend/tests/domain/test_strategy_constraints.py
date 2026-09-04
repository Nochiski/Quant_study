"""P1-04 constraint catalog: one owner for scalar bounds shared by validation and contract schema."""

from __future__ import annotations

import dataclasses
import re
import types
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Union, get_args, get_origin, get_type_hints

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.strategy import _validation
from strategy_workbench.domain.strategy.facade.constraints import (
    SEMANTIC_ONLY_CODES,
    STRATEGY_SCALAR_CONSTRAINTS,
    AppliedStage,
    ScalarConstraint,
    field_default,
    resolve_scalar,
    scalar_constraint_index,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec
from strategy_workbench.domain.strategy.facade.validation import validate_strategy

VALIDATION_SOURCE = Path(_validation.__file__).read_text(encoding="utf-8")


def _template() -> StrategySpec:
    return StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2026, 9, 3)
    ).template()


def _with_scalar(spec: StrategySpec, pointer: str, value: float | int) -> StrategySpec:
    step, field = pointer.strip("/").split("/")
    section = getattr(spec, step)
    return replace(spec, **{step: replace(section, **{field: value})})


def _hint_at(pointer: str) -> object:
    current: object = StrategySpec
    for segment in pointer.strip("/").split("/"):
        assert isinstance(current, type), pointer
        current = get_type_hints(current)[segment]
    return current


def test_every_catalog_pointer_names_a_numeric_field_of_the_model() -> None:
    for constraint in STRATEGY_SCALAR_CONSTRAINTS:
        hint = _hint_at(constraint.pointer)
        members = get_args(hint) if get_origin(hint) in (Union, types.UnionType) else (hint,)
        numeric = {member for member in members if member in (int, float)}
        assert numeric, f"{constraint.pointer} is not numeric: {hint!r}"
        assert constraint.stage is AppliedStage(constraint.pointer.strip("/").split("/")[0])
        assert constraint.code.startswith("strategy.")
        assert constraint.message


def test_catalog_pointers_are_unique_and_index_matches() -> None:
    pointers = [constraint.pointer for constraint in STRATEGY_SCALAR_CONSTRAINTS]
    assert len(pointers) == len(set(pointers))
    assert set(scalar_constraint_index()) == set(pointers)


def test_field_default_reads_the_dataclass_not_the_catalog() -> None:
    assert field_default("/risk/max_name_weight") == dataclasses.fields(_template().risk)[2].default
    assert field_default("/risk/max_name_weight") == 0.1
    assert field_default("/portfolio/minimum_liquidity") is None
    with pytest.raises(KeyError, match="unknown authoring pointer"):
        field_default("/risk/nope")


@pytest.mark.parametrize("constraint", STRATEGY_SCALAR_CONSTRAINTS, ids=lambda c: c.pointer)
def test_validator_emits_the_catalog_code_and_path_when_a_bound_is_violated(
    constraint: ScalarConstraint,
) -> None:
    spec = _template()
    if constraint.minimum is not None:
        bad = constraint.minimum - 1 if not constraint.exclusive_minimum else constraint.minimum
    else:
        assert constraint.maximum is not None
        bad = constraint.maximum + 1

    validation = validate_strategy(_with_scalar(spec, constraint.pointer, bad))

    issues = [issue for issue in validation.issues if issue.code == constraint.code]
    assert issues, f"{constraint.pointer}={bad} did not raise {constraint.code}"
    assert issues[0].path == constraint.path
    assert issues[0].message == constraint.message
    assert not validation.valid


@pytest.mark.parametrize("constraint", STRATEGY_SCALAR_CONSTRAINTS, ids=lambda c: c.pointer)
def test_boundary_values_that_satisfy_the_catalog_pass_validation(
    constraint: ScalarConstraint,
) -> None:
    spec = _template()
    good = constraint.example if constraint.example is not None else constraint.minimum
    assert good is not None
    if constraint.maximum is not None and not constraint.exclusive_maximum:
        good = constraint.maximum  # inclusive upper bound must be accepted
    if constraint.pointer == "/risk/gross_exposure":
        good = 1.0  # keep the long-only exposure relation satisfied

    validation = validate_strategy(_with_scalar(spec, constraint.pointer, good))

    assert constraint.code not in {issue.code for issue in validation.issues}


def test_optional_scalar_left_unset_is_not_checked() -> None:
    spec = _template()
    assert resolve_scalar(spec, "/portfolio/minimum_liquidity") is None
    assert "strategy.portfolio.minimum_liquidity" not in {
        issue.code for issue in validate_strategy(spec).issues
    }


def test_changing_a_catalog_bound_changes_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    index = scalar_constraint_index()
    relaxed = replace(index["/risk/max_name_weight"], maximum=2.0)
    catalog = tuple(
        relaxed if c.pointer == "/risk/max_name_weight" else c for c in STRATEGY_SCALAR_CONSTRAINTS
    )
    spec = _with_scalar(_template(), "/risk/max_name_weight", 1.5)
    assert "strategy.risk.max_name_weight" in {i.code for i in validate_strategy(spec).issues}

    monkeypatch.setattr(_validation, "STRATEGY_SCALAR_CONSTRAINTS", catalog)

    assert "strategy.risk.max_name_weight" not in {i.code for i in validate_strategy(spec).issues}


def test_every_validation_code_has_exactly_one_owner() -> None:
    emitted = set(re.findall(r'"(strategy\.[a-z_.]+)"', VALIDATION_SOURCE))
    aliases = set(re.findall(r'"(strategy\.expression\.[a-z_]+)"', VALIDATION_SOURCE))
    catalog_codes = {constraint.code for constraint in STRATEGY_SCALAR_CONSTRAINTS}

    assert emitted - aliases == SEMANTIC_ONLY_CODES, sorted(emitted - aliases - SEMANTIC_ONLY_CODES)
    assert not catalog_codes & SEMANTIC_ONLY_CODES
    assert not (emitted & catalog_codes), "scalar codes must not be hand-written in the validator"


def test_expression_node_kinds_cover_the_union_exactly() -> None:
    from typing import get_args as _args

    from strategy_workbench.domain.factor.facade.expression import (
        EXPRESSION_NODE_KINDS,
        ExpressionNode,
    )

    members = set(_args(ExpressionNode))
    assert set(EXPRESSION_NODE_KINDS.values()) == members
    for kind, node_type in EXPRESSION_NODE_KINDS.items():
        assert _args(get_type_hints(node_type)["kind"]) == (kind,)
