from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.domain.factor.facade.analysis import analyze_factor_values
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    FactorReferenceValue,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    ConditionalNode,
    CrossSectionalNode,
    CrossSectionalOperator,
    FactorGraph,
    FieldMetadata,
    FieldNode,
    GroupNode,
    GroupOperator,
    MissingPolicy,
    ParameterNode,
    SavedFactorNode,
    SavedSubgraphNode,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.planning import (
    ResolvedFactorParameter,
    build_factor_matrix_cache_key,
    compile_factor_plan,
)
from strategy_workbench.domain.factor.facade.registry import (
    FactorAvailability,
    FactorCategory,
    build_default_factor_registry,
)
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph


def test_registry_is_the_versioned_source_of_truth_for_fifty_factor_ids() -> None:
    registry = build_default_factor_registry()
    definitions = registry.all()

    assert registry.version == "factor-registry-v1"
    assert len(definitions) == 50
    assert {item.category for item in definitions} == set(FactorCategory)
    implemented = [
        item for item in definitions if item.availability is FactorAvailability.IMPLEMENTED
    ]
    assert len(implemented) == 7
    assert {item.category for item in implemented} == set(FactorCategory)
    assert all(item.default_graph is not None for item in implemented)


def test_factor_catalog_document_tracks_every_registry_identifier() -> None:
    document = (Path(__file__).resolve().parents[2] / "FACTORS.md").read_text(encoding="utf-8")
    definitions = build_default_factor_registry().all()

    assert all(f"`{definition.factor_id}`" in document for definition in definitions)
    assert document.count("| implemented |") == 7


def test_validator_rejects_cycle_unit_type_and_insufficient_history() -> None:
    graph = FactorGraph(
        nodes=(
            FieldNode("price", "price.close", "field"),
            FieldNode("ratio", "short.ratio", "field"),
            BinaryNode("sum", BinaryOperator.ADD, "price", "ratio", "binary"),
            TimeSeriesNode("mean", TimeSeriesOperator.MEAN, "sum", 20, "time_series"),
            ConditionalNode("choose", "price", "mean", "sum", "conditional"),
        ),
        output_node_id="choose",
        missing_policy=MissingPolicy.CROSS_SECTIONAL_MEDIAN,
    )
    fields = (
        FieldMetadata("price.close", "KRW", available_history_sessions=10),
        FieldMetadata("short.ratio", "ratio", available_history_sessions=10),
    )

    validation = validate_factor_graph(graph, fields=fields)

    assert not validation.valid
    assert {issue.code for issue in validation.issues} >= {
        "factor.graph.unit_mismatch",
        "factor.graph.predicate_type",
        "factor.graph.insufficient_history",
    }
    cyclic = replace(
        graph,
        nodes=(
            UnaryNode("a", UnaryOperator.NEGATE, "b", "unary"),
            UnaryNode("b", UnaryOperator.NEGATE, "a", "unary"),
        ),
        output_node_id="a",
    )
    assert {issue.code for issue in validate_factor_graph(cyclic).issues} == {"factor.graph.cycle"}


def test_all_quick_transforms_evaluate_deterministically() -> None:
    graph = FactorGraph(
        nodes=(
            FieldNode("source", "value", "field"),
            CrossSectionalNode(
                "winsor", CrossSectionalOperator.WINSORIZE, "source", "cross_sectional"
            ),
            CrossSectionalNode(
                "zscore", CrossSectionalOperator.ZSCORE, "winsor", "cross_sectional"
            ),
            CrossSectionalNode("rank", CrossSectionalOperator.RANK, "zscore", "cross_sectional"),
            UnaryNode("lag", UnaryOperator.LAG, "rank", "unary", periods=1),
            GroupNode("neutral", GroupOperator.NEUTRALIZE, "lag", "sector", "group"),
        ),
        output_node_id="neutral",
    )
    observations = tuple(
        FactorObservation(
            as_of=date(2024, 1, day),
            security_id=f"s{security}",
            fields=(
                FactorFieldValue("value", float(day * security)),
                FactorFieldValue("sector", "A" if security < 3 else "B"),
            ),
            forward_return=security * 0.01,
        )
        for day in (2, 3, 4)
        for security in (1, 2, 3)
    )

    first = evaluate_factor_graph(graph, observations=observations)
    second = evaluate_factor_graph(graph, observations=observations)

    assert first == second
    assert any(value.value is not None for value in first.values)


def test_parameter_and_saved_references_are_first_class_nodes() -> None:
    graph = FactorGraph(
        nodes=(
            SavedFactorNode("factor", "quality", "saved_factor"),
            SavedSubgraphNode("subgraph", "scale", "saved_subgraph"),
            ParameterNode("weight", "w", "parameter"),
            BinaryNode("weighted", BinaryOperator.MULTIPLY, "factor", "weight", "binary"),
            BinaryNode("sum", BinaryOperator.ADD, "weighted", "subgraph", "binary"),
        ),
        output_node_id="sum",
    )
    validation = validate_factor_graph(
        graph,
        parameter_ids=("w",),
        factor_ids=("quality",),
        subgraph_ids=("scale",),
    )
    observation = FactorObservation(
        date(2024, 1, 2),
        "s1",
        (),
        (
            FactorReferenceValue("factor:quality", 2.0),
            FactorReferenceValue("subgraph:scale", 1.0),
        ),
    )

    evaluation = evaluate_factor_graph(
        graph,
        observations=(observation,),
        parameters=(ResolvedFactorParameter("w", 3.0),),
    )

    assert validation.valid
    assert evaluation.values[0].value == pytest.approx(7.0)


def test_plan_and_cache_fingerprints_cover_all_reproducibility_inputs() -> None:
    graph = FactorGraph(
        nodes=(
            FieldNode("source", "price.close", "field"),
            TimeSeriesNode("mean", TimeSeriesOperator.MEAN, "source", 5, "time_series"),
        ),
        output_node_id="mean",
    )
    plan = compile_factor_plan(graph, registry_version="registry-v1")
    repeated = compile_factor_plan(graph, registry_version="registry-v1")
    key = build_factor_matrix_cache_key(
        data_snapshot_id="snapshot-a",
        plan_hash=plan.plan_hash,
        registry_version="registry-v1",
        parameters=(ResolvedFactorParameter("window", 5),),
        as_of_start=date(2024, 1, 1),
        as_of_end=date(2024, 12, 31),
    )
    changed = build_factor_matrix_cache_key(
        data_snapshot_id="snapshot-b",
        plan_hash=plan.plan_hash,
        registry_version="registry-v1",
        parameters=(ResolvedFactorParameter("window", 5),),
        as_of_start=date(2024, 1, 1),
        as_of_end=date(2024, 12, 31),
    )

    assert plan == repeated
    assert tuple(step.node_id for step in plan.steps) == ("source", "mean")
    assert key.fingerprint != changed.fingerprint


def test_factor_analytics_exposes_professional_diagnostics_explicitly() -> None:
    graph = FactorGraph(
        nodes=(FieldNode("source", "value", "field"),),
        output_node_id="source",
    )
    observations = tuple(
        FactorObservation(
            date(2024, 1, day),
            f"s{security}",
            (FactorFieldValue("value", float(security)),),
            forward_return=security * 0.01,
        )
        for day in (2, 3)
        for security in (1, 2, 3)
    )
    evaluation = evaluate_factor_graph(graph, observations=observations)

    analytics = analyze_factor_values(evaluation.values, observations)

    assert analytics.information_coefficient == pytest.approx(1.0)
    assert analytics.rank_information_coefficient == pytest.approx(1.0)
    assert analytics.quantile_spread == pytest.approx(0.02)
    assert analytics.coverage == 1.0
    assert analytics.turnover == 0.0
    assert analytics.decay == pytest.approx(1.0)
