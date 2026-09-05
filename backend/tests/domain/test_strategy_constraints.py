"""P1-04 constraint catalog.

One owner for scalar bounds, shared by semantic validation and the contract schema.
"""

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
from strategy_workbench.application.portfolio_design import _service as _portfolio_service
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.factor.facade.expression import ConstantNode
from strategy_workbench.domain.strategy import _validation
from strategy_workbench.domain.strategy.facade.constraints import (
    EXPRESSION_CODES,
    SEMANTIC_ONLY_CODES,
    STRATEGY_SCALAR_CONSTRAINTS,
    AppliedStage,
    ScalarConstraint,
    field_default,
    resolve_scalar,
    scalar_constraint_index,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    ComparisonOperator,
    EligibilityRule,
    EligibilityStep,
    FloatParameter,
    StrategySpec,
)
from strategy_workbench.domain.strategy.facade.validation import (
    semantic_issue,
    validate_strategy,
)

VALIDATION_SOURCE = Path(_validation.__file__).read_text(encoding="utf-8")
# Every layer that can mint a `strategy.*` code, not just the domain validator (D-007).
CODE_PRODUCER_SOURCES = {
    "domain/strategy/_validation.py": VALIDATION_SOURCE,
    "application/portfolio_design/_service.py": Path(_portfolio_service.__file__).read_text(
        encoding="utf-8"
    ),
}


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
        assert isinstance(constraint.stage, AppliedStage)
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
    with pytest.raises(KeyError, match="required field has no default"):
        field_default("/data/universe_id")


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
    assert not EXPRESSION_CODES & (SEMANTIC_ONLY_CODES | catalog_codes)


def test_no_layer_mints_a_strategy_code_outside_the_registry() -> None:
    """D-007: the application used to build ValidationIssue directly, bypassing the code gate."""
    owned = SEMANTIC_ONLY_CODES | EXPRESSION_CODES | {c.code for c in STRATEGY_SCALAR_CONSTRAINTS}

    for location, source in CODE_PRODUCER_SOURCES.items():
        emitted = set(re.findall(r'"(strategy\.[a-z_.]+)"', source))
        assert emitted <= owned, (location, sorted(emitted - owned))
        # `semantic_issue` is the only place a ValidationIssue is built; it lives in the validator.
        expected_constructions = 1 if source is VALIDATION_SOURCE else 0
        assert source.count("ValidationIssue(") == expected_constructions, (
            f"{location} constructs ValidationIssue directly — call semantic_issue() so the code "
            "registry stays the single owner"
        )


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


def test_fee_and_slippage_report_one_issue_each_with_their_own_path() -> None:
    spec = _template()
    spec = replace(spec, execution=replace(spec.execution, fee_bps=-1.0, slippage_bps=-2.0))

    issues = [i for i in validate_strategy(spec).issues if i.code == "strategy.execution.cost"]

    assert [(i.path, i.message[:4]) for i in issues] == [
        ("execution.fee_bps", "수수료는"),
        ("execution.slippage_bps", "슬리피지"),
    ]


@pytest.mark.parametrize("constraint", STRATEGY_SCALAR_CONSTRAINTS, ids=lambda c: c.pointer)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_value_never_satisfies_a_bound(
    constraint: ScalarConstraint, value: float
) -> None:
    spec = _with_scalar(_template(), constraint.pointer, value)
    assert constraint.code in {issue.code for issue in validate_strategy(spec).issues}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_every_unbounded_strategy_numeric_leaf_must_be_finite(value: float) -> None:
    spec = _template()
    factor = spec.factors.factors[0]
    constant_index = len(factor.graph.nodes)
    variants = (
        (
            replace(
                spec,
                factors=replace(
                    spec.factors,
                    factors=(replace(factor, weight=value),),
                ),
            ),
            "factors.factors.0.weight",
        ),
        (
            replace(
                spec,
                factors=replace(
                    spec.factors,
                    factors=(
                        replace(
                            factor,
                            graph=replace(
                                factor.graph,
                                nodes=(*factor.graph.nodes, ConstantNode("bad", value, "constant")),
                            ),
                        ),
                    ),
                ),
            ),
            f"factors.factors.0.graph.nodes.{constant_index}.value",
        ),
        (
            replace(
                spec,
                eligibility=EligibilityStep(
                    (EligibilityRule("price.close", ComparisonOperator.GREATER_THAN, value),)
                ),
            ),
            "eligibility.rules.0.value",
        ),
        (
            replace(spec, signal=replace(spec.signal, score_threshold=value)),
            "signal.score_threshold",
        ),
        (
            replace(
                spec,
                parameters=(FloatParameter("scale", value, 0.0, 1.0, "float"),),
            ),
            "parameters.0.default",
        ),
        (
            replace(
                spec,
                parameters=(ChoiceParameter("scale", 1.0, (1.0, value), "choice"),),
            ),
            "parameters.0.choices.1",
        ),
    )

    for variant, expected_path in variants:
        issues = [
            issue
            for issue in validate_strategy(variant).issues
            if issue.code == "strategy.number.non_finite"
        ]
        assert expected_path in {issue.path for issue in issues}


def test_inclusive_minimum_boundaries_are_accepted() -> None:
    spec = _template()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio, selection_count=1, minimum_trade_weight=0.0, turnover_buffer_count=0
        ),
        execution=replace(spec.execution, fee_bps=0.0, slippage_bps=0.0),
    )
    codes = {issue.code for issue in validate_strategy(spec).issues}
    assert not codes & {
        "strategy.portfolio.selection_count",
        "strategy.portfolio.minimum_trade_weight",
        "strategy.portfolio.turnover_buffer_count",
        "strategy.execution.cost",
    }


def test_unowned_validation_code_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="no owner"):
        semantic_issue("strategy.bogus.code", "bogus", "x")

    # Codes outside the `strategy.` namespace belong to another registry and pass through.
    assert semantic_issue("factor.graph.other", "p", "x").code == "factor.graph.other"
