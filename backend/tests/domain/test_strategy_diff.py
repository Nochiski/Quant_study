"""P1-08 semantic diff: canonical payload differences only, identity and comments excluded."""

from __future__ import annotations

from dataclasses import replace
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
from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    SourceFormat,
    hydrate_strategy_document,
)
from strategy_workbench.domain.strategy.facade.specification import (
    EligibilityOperator as Op,
)
from strategy_workbench.domain.strategy.facade.specification import (
    EligibilityRule,
    EligibilityStep,
    FloatParameter,
    SignalNormalization,
    StrategyIdentity,
    StrategySpec,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"


def _template() -> StrategySpec:
    return StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "unused").template()


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
        portfolio=replace(base.portfolio, selection_count=30),
    )

    entries = diff_strategy_specs(base, target)

    assert entries == (
        DiffEntry("/portfolio/selection_count", DiffKind.CHANGED, 20, 30),
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


def test_normalization_change_is_one_leaf_entry() -> None:
    """결합 전 정규화를 바꾸면 semantic diff 가 `/signal/normalization` 한 줄로 보고한다(P2-04)."""
    base = _template()
    target = replace(base, signal=replace(base.signal, normalization=SignalNormalization.NONE))

    assert diff_strategy_specs(base, target) == (
        DiffEntry("/signal/normalization", DiffKind.CHANGED, "rank", "none"),
    )


# -- compile 이 붙인 승격 노드 (BACKLOG-014) -------------------------------------------------------

_BREAKOUT_NODES: list[dict[str, object]] = [
    {"kind": "field", "node_id": "close", "field_id": "price.close"},
    {
        "kind": "time_series",
        "node_id": "mean",
        "operator": "mean",
        "input_node_id": "close",
        "window": 20,
    },
    {"kind": "field", "node_id": "close_2", "field_id": "price.close"},
    {
        "kind": "comparison",
        "node_id": "gt",
        "operator": "gt",
        "left_node_id": "close_2",
        "right_node_id": "mean",
    },
]


def _breakout(nodes: list[dict[str, object]], output: str = "gt") -> StrategySpec:
    document = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "title": "20일 이평 돌파",
        "factors": [
            {
                "factor_id": "breakout",
                "direction": "high",
                "graph": {"nodes": nodes, "output_node_id": output},
            }
        ],
    }
    hydration = hydrate_strategy_document(document, identity=StrategyIdentity("draft", 0))
    assert hydration.ok and hydration.spec is not None, hydration.issues
    return hydration.spec


def test_adding_a_node_to_a_promoted_graph_is_one_added_entry() -> None:
    # 비교 출력 그래프는 hydrate 가 끝에 승격 노드 셋을 붙인다(P2-07). 위치 비교만 하면 사용자가
    # 노드 하나를 더할 때 붙인 노드 셋이 한 칸씩 밀려 kind·node_id·value 가 바뀐 것으로 나온다.
    base = _breakout(_BREAKOUT_NODES)
    extra: dict[str, object] = {"kind": "field", "node_id": "volume", "field_id": "price.volume"}
    target = _breakout([*_BREAKOUT_NODES, extra])
    assert base.factors[0].graph.output_node_id == target.factors[0].graph.output_node_id

    entries = diff_strategy_specs(base, target)

    assert entries == (
        DiffEntry(
            "/factors/0/graph/nodes/4",
            DiffKind.ADDED,
            None,
            {"field_id": "price.volume", "kind": "field", "node_id": "volume"},
        ),
    )


def test_changing_the_output_away_from_a_comparison_reports_the_authored_output() -> None:
    # 출력이 숫자 노드로 바뀌면 승격이 사라진다. 사용자가 바꾼 것은 출력 하나이고, 붙인 노드가
    # 사라진 것은 그 결과다 — 표는 문서에 쓴 값(gt → mean)으로 말한다.
    base = _breakout(_BREAKOUT_NODES)
    target = _breakout(_BREAKOUT_NODES, output="mean")

    assert diff_strategy_specs(base, target) == (
        DiffEntry("/factors/0/graph/output_node_id", DiffKind.CHANGED, "gt", "mean"),
    )


def test_a_user_written_numeric_conditional_is_not_folded() -> None:
    # 예약 id 를 쓰지 않은 사용자 조건 노드는 승격이 아니다: 그대로 비교한다.
    nodes = [
        *_BREAKOUT_NODES,
        {"kind": "constant", "node_id": "one", "value": 1.0},
        {"kind": "constant", "node_id": "zero", "value": 0.0},
        {
            "kind": "conditional",
            "node_id": "score",
            "predicate_node_id": "gt",
            "true_node_id": "one",
            "false_node_id": "zero",
        },
    ]
    base = _breakout(nodes, output="score")
    target = _breakout(nodes, output="gt")

    assert diff_strategy_specs(base, target) == (
        DiffEntry("/factors/0/graph/output_node_id", DiffKind.CHANGED, "score", "gt"),
    )
