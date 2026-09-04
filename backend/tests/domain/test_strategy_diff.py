"""P1-08 semantic diff: canonical payload differences only, identity and comments excluded."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.strategy.facade.diff import DiffEntry, DiffKind, diff_strategy_specs
from strategy_workbench.domain.strategy.facade.document import SourceFormat
from strategy_workbench.domain.strategy.facade.specification import (
    ComparisonOperator as Op,
)
from strategy_workbench.domain.strategy.facade.specification import (
    EligibilityRule,
    EligibilityStep,
    FloatParameter,
    StrategyIdentity,
    StrategySpec,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"


def _template() -> StrategySpec:
    return StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2026, 9, 3)
    ).template()


def test_identical_specs_and_identity_changes_produce_no_entries() -> None:
    base = _template()
    renamed = replace(base, identity=StrategyIdentity("other", 7))
    assert diff_strategy_specs(base, base) == ()
    assert diff_strategy_specs(base, renamed) == ()


def test_scalar_changes_are_reported_per_leaf_with_json_values() -> None:
    base = _template()
    target = replace(
        base,
        title="바뀐 제목",
        risk=replace(base.risk, max_name_weight=0.05),
        data=replace(base.data, end=date(2026, 12, 31)),
    )

    entries = diff_strategy_specs(base, target)

    assert entries == (
        DiffEntry("/data/end", DiffKind.CHANGED, "2026-09-03", "2026-12-31"),
        DiffEntry("/risk/max_name_weight", DiffKind.CHANGED, 0.1, 0.05),
        DiffEntry("/title", DiffKind.CHANGED, "새 팩터 전략", "바뀐 제목"),
    )


def test_array_items_are_positional_and_added_or_removed_as_subtrees() -> None:
    base = _template()
    rule = EligibilityRule("price.market_cap", Op.GREATER_THAN, 1e9)
    with_rule = replace(base, eligibility=EligibilityStep(rules=(rule,)))
    with_parameter = replace(
        with_rule, parameters=(FloatParameter("lookback", 20.0, 5.0, 60.0, "float"),)
    )

    added = diff_strategy_specs(base, with_parameter)
    assert added == (
        DiffEntry(
            "/eligibility/rules/0",
            DiffKind.ADDED,
            None,
            {"field_id": "price.market_cap", "operator": "gt", "value": 1e9},
        ),
        DiffEntry(
            "/parameters/0",
            DiffKind.ADDED,
            None,
            {
                "default": 20.0,
                "kind": "float",
                "maximum": 60.0,
                "minimum": 5.0,
                "parameter_id": "lookback",
                "step": None,
            },
        ),
    )
    removed = diff_strategy_specs(with_parameter, base)
    assert [(e.pointer, e.kind) for e in removed] == [
        ("/eligibility/rules/0", DiffKind.REMOVED),
        ("/parameters/0", DiffKind.REMOVED),
    ]
    assert removed[0].before == added[0].after and removed[0].after is None


def test_comment_only_source_changes_are_invisible_to_the_diff() -> None:
    authoring = StrategyAuthoringService(
        RuamelDocumentCodec(), factor_registry_version="r", dataset_snapshot_id=lambda: "s"
    )
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    base = authoring.compile(CompileRequest(text, SourceFormat.YAML)).spec
    commented = authoring.compile(
        CompileRequest(text + "\n# reviewed 2026-09-04\n", SourceFormat.YAML)
    ).spec
    as_json = authoring.compile(
        CompileRequest(
            (FIXTURES / "quality_momentum.json").read_text(encoding="utf-8"), SourceFormat.JSON
        )
    ).spec
    assert base is not None and commented is not None and as_json is not None

    assert diff_strategy_specs(base, commented) == ()
    assert diff_strategy_specs(base, as_json) == ()


def test_equal_values_of_different_json_types_are_changes() -> None:
    from strategy_workbench.domain.strategy.facade.specification import (
        ChoiceParameter,
        strategy_spec_hash,
    )

    base = replace(_template(), parameters=(ChoiceParameter("p", True, (True, False), "choice"),))
    target = replace(_template(), parameters=(ChoiceParameter("p", 1, (1, 0), "choice"),))

    assert strategy_spec_hash(base) != strategy_spec_hash(target)
    entries = diff_strategy_specs(base, target)
    assert DiffEntry("/parameters/0/default", DiffKind.CHANGED, True, 1) in entries
    assert all(entry.kind is DiffKind.CHANGED for entry in entries)


def test_parameter_kind_change_reports_added_and_removed_keys() -> None:
    from strategy_workbench.domain.strategy.facade.specification import ChoiceParameter

    base = replace(_template(), parameters=(FloatParameter("p", 20.0, 5.0, 60.0, "float"),))
    target = replace(_template(), parameters=(ChoiceParameter("p", 20, (20, 40), "choice"),))

    entries = diff_strategy_specs(base, target)
    assert {(e.pointer, e.kind) for e in entries} == {
        ("/parameters/0/choices", DiffKind.ADDED),
        ("/parameters/0/default", DiffKind.CHANGED),
        ("/parameters/0/kind", DiffKind.CHANGED),
        ("/parameters/0/maximum", DiffKind.REMOVED),
        ("/parameters/0/minimum", DiffKind.REMOVED),
        ("/parameters/0/step", DiffKind.REMOVED),
    }


def test_pointer_tokens_are_rfc6901_escaped() -> None:
    from strategy_workbench.domain.strategy import _diff

    entries: list[DiffEntry] = []
    _diff._walk({"a/b": 1, "c~d": 2}, {"a/b": 2, "c~d": 2}, "", entries)  # pyright: ignore[reportPrivateUsage]  # reason: unit
    assert [e.pointer for e in entries] == ["/a~1b"]
