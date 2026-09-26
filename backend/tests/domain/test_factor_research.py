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

    first = evaluate_factor_graph(graph, observations=observations, missing=MissingPolicy.DROP)
    second = evaluate_factor_graph(graph, observations=observations, missing=MissingPolicy.DROP)

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
        missing=MissingPolicy.DROP,
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
    plan = compile_factor_plan(graph, registry_version="registry-v1", missing=MissingPolicy.DROP)
    repeated = compile_factor_plan(
        graph, registry_version="registry-v1", missing=MissingPolicy.DROP
    )
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
    evaluation = evaluate_factor_graph(graph, observations=observations, missing=MissingPolicy.DROP)

    analytics = analyze_factor_values(evaluation.values, observations)

    assert analytics.information_coefficient == pytest.approx(1.0)
    assert analytics.rank_information_coefficient == pytest.approx(1.0)
    assert analytics.quantile_spread == pytest.approx(0.02)
    assert analytics.coverage == 1.0
    assert analytics.turnover == 0.0
    assert analytics.decay == pytest.approx(1.0)


def _member_row(day: int, security: str, value: float, *, member: bool) -> FactorObservation:
    return FactorObservation(
        as_of=date(2024, 1, day),
        security_id=security,
        fields=(FactorFieldValue("value", value), FactorFieldValue("sector", "A")),
        universe_member=member,
    )


def test_cross_sectional_operators_ignore_non_members(
    # D-001 regression: a delisted / removed name must not move a member's z-score or rank.
) -> None:
    graph = FactorGraph(
        nodes=(
            FieldNode("source", "value", "field"),
            CrossSectionalNode("z", CrossSectionalOperator.ZSCORE, "source", "cross_sectional"),
        ),
        output_node_id="z",
    )
    members = (_member_row(2, "s1", 2.0, member=True), _member_row(2, "s2", 1.0, member=True))
    with_outsider = (*members, _member_row(2, "s3", 100.0, member=False))

    alone = {
        v.security_id: v.value
        for v in evaluate_factor_graph(
            graph, observations=members, missing=MissingPolicy.DROP
        ).values
    }
    mixed = {
        v.security_id: v.value
        for v in evaluate_factor_graph(
            graph, observations=with_outsider, missing=MissingPolicy.DROP
        ).values
    }

    assert alone == {"s1": 1.0, "s2": -1.0}
    assert mixed["s1"] == alone["s1"] and mixed["s2"] == alone["s2"]
    assert mixed["s3"] == 0.0  # non-members form their own cross-section, kept positionally


def test_rank_group_and_median_fill_all_use_the_member_peer_group() -> None:
    """Every peer-group operator shares the `(as_of, universe_member)` key, not just zscore."""
    members = (_member_row(2, "s1", 4.0, member=True), _member_row(2, "s2", 2.0, member=True))
    outsider = _member_row(2, "s3", 999.0, member=False)
    missing_member = FactorObservation(
        as_of=date(2024, 1, 2),
        security_id="s4",
        fields=(FactorFieldValue("sector", "A"),),
        universe_member=True,
    )

    def outputs(node: object, observations: tuple[FactorObservation, ...]) -> dict[str, object]:
        graph = FactorGraph(
            nodes=(FieldNode("source", "value", "field"), node),  # pyright: ignore[reportArgumentType]  # reason: parametrised over node kinds
            output_node_id="out",
        )
        return {
            v.security_id: v.value
            for v in evaluate_factor_graph(
                graph,
                observations=observations,
                missing=MissingPolicy.CROSS_SECTIONAL_MEDIAN,
            ).values
        }

    for node in (
        CrossSectionalNode("out", CrossSectionalOperator.RANK, "source", "cross_sectional"),
        CrossSectionalNode("out", CrossSectionalOperator.DEMEAN, "source", "cross_sectional"),
        GroupNode("out", GroupOperator.NEUTRALIZE, "source", "sector", "group"),
    ):
        alone = outputs(node, (*members, missing_member))
        mixed = outputs(node, (*members, missing_member, outsider))
        assert {key: mixed[key] for key in alone} == alone, node


def test_cross_sectional_demean_subtracts_the_member_peer_mean() -> None:
    """schema 1.1 S4: 1.0의 `unary: neutralize`가 하던 계산이 `cross_sectional: demean`으로 남는다.
    같은 날 유니버스 구성원의 평균을 빼며, 비구성원은 자기 횡단면에서 따로 계산된다."""
    members = (_member_row(2, "s1", 4.0, member=True), _member_row(2, "s2", 2.0, member=True))
    outsider = _member_row(2, "s3", 999.0, member=False)
    graph = FactorGraph(
        nodes=(
            FieldNode("source", "value", "field"),
            CrossSectionalNode("out", CrossSectionalOperator.DEMEAN, "source", "cross_sectional"),
        ),
        output_node_id="out",
    )

    values = {
        v.security_id: v.value
        for v in evaluate_factor_graph(
            graph, observations=(*members, outsider), missing=MissingPolicy.DROP
        ).values
    }

    assert values == {"s1": 1.0, "s2": -1.0, "s3": 0.0}


def test_cross_sectional_rank_pins_the_percentile_values() -> None:
    """`cross_sectional_rank` 추출(P2-04)의 동작 보존을 값으로 고정한다.

    격리 테스트(`..._ignore_non_members`)는 분모를 바꿔도 `alone` 과 `mixed` 가 똑같이 바뀌어
    통과한다. 백분위 **값**을 고정해야 공식 변경이 실패로 드러난다(P2-04 리뷰 P2-3).
    """
    graph = FactorGraph(
        nodes=(
            FieldNode("source", "value", "field"),
            CrossSectionalNode("out", CrossSectionalOperator.RANK, "source", "cross_sectional"),
        ),
        output_node_id="out",
    )
    rows = (
        _member_row(2, "s1", 1.0, member=True),
        _member_row(2, "s2", 2.0, member=True),
        _member_row(2, "s3", 2.0, member=True),
        _member_row(2, "s4", 4.0, member=True),
    )

    values = {
        v.security_id: v.value
        for v in evaluate_factor_graph(graph, observations=rows, missing=MissingPolicy.DROP).values
    }

    # 순위 1, 2.5, 2.5, 4 → (r - 1) / (n - 1) = (r - 1) / 3.
    assert values == {"s1": 0.0, "s2": 0.5, "s3": 0.5, "s4": 1.0}


def test_group_rank_pins_the_percentile_values_within_each_sector() -> None:
    """`GroupOperator.RANK` 도 같은 공식을 쓴다 — 섹터 안에서 0~1 백분위."""
    graph = FactorGraph(
        nodes=(
            FieldNode("source", "value", "field"),
            GroupNode("out", GroupOperator.RANK, "source", "sector", "group"),
        ),
        output_node_id="out",
    )
    rows = (
        replace(_member_row(2, "a1", 1.0, member=True), fields=_sector_fields(1.0, "A")),
        replace(_member_row(2, "a2", 3.0, member=True), fields=_sector_fields(3.0, "A")),
        replace(_member_row(2, "b1", 9.0, member=True), fields=_sector_fields(9.0, "B")),
    )

    values = {
        v.security_id: v.value
        for v in evaluate_factor_graph(graph, observations=rows, missing=MissingPolicy.DROP).values
    }

    # 섹터 A 는 2개라 0.0/1.0, 섹터 B 는 1개라 분모가 1 로 막혀 0.0.
    assert values == {"a1": 0.0, "a2": 1.0, "b1": 0.0}


def _sector_fields(value: float, sector: str) -> tuple[FactorFieldValue, ...]:
    return (FactorFieldValue("value", value), FactorFieldValue("sector", sector))


def test_evaluation_reports_progress_inside_the_time_series_node() -> None:
    """이슈 #162: 실데이터에서 시계열 노드 하나가 tape 단계의 절반 가까이를 쓴다.

    노드 완료 단위로만 진행을 올리면 그 노드 동안 막대가 멈춰 보이므로 종목 단위로도 올린다.
    진행 콜백은 결과를 바꾸지 않는다.
    """
    graph = FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 2, "time_series"),
        ),
        output_node_id="mom",
    )
    observations = tuple(
        FactorObservation(
            as_of=date(2024, 1, day),
            security_id=f"s{security}",
            fields=(FactorFieldValue("price.close", float(day + security)),),
            forward_return=None,
        )
        for day in (2, 3, 4)
        for security in (1, 2, 3, 4)
    )
    reported: list[float] = []

    # `missing` 은 P2-02 이후 실행 설정이 소유하는 필수 인자다.
    # #193 테스트를 P2-02 위로 옮기며 넣었다.
    evaluation = evaluate_factor_graph(
        graph, observations=observations, missing=MissingPolicy.DROP, progress=reported.append
    )

    assert evaluation == evaluate_factor_graph(
        graph, observations=observations, missing=MissingPolicy.DROP
    )
    assert reported == sorted(reported)
    # 가중치 field 1 + 시계열 20 = 21. field 완료가 1/21 이고, 시계열 노드는 종목 4개(관측 3개씩)를
    # 끝낼 때마다 5/21 씩 올린다. 필드 노드가 팩터 구간의 절반을 가져가지 않는다(리뷰 P3-1).
    # 노드 계산은 0.96 까지이고 출력 값 조립이 1.0 을 채운다.
    nodes = [value for value in reported if value <= 0.96]
    assert sorted(set(nodes)) == pytest.approx(
        [0.96 * share for share in (1 / 21, 6 / 21, 11 / 21, 16 / 21, 1.0)]
    )
    assert reported[-1] == 1.0
