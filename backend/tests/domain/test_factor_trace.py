"""P1.5-02 bounded factor trace: same values as evaluation, explained None, deterministic bounds."""

from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    FactorGraph,
    FieldNode,
    GroupNode,
    GroupOperator,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.trace import (
    TraceSelection,
    TraceValueStatus,
    trace_factor_graph,
)

DAYS = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))


def _observation(day: date, security: str, close: float | None, sector: str | None = "tech"):
    fields = [FactorFieldValue("price.close", close)]
    if sector is not None:
        fields.append(FactorFieldValue("sector", sector))
    return FactorObservation(as_of=day, security_id=security, fields=tuple(fields))


def _panel() -> tuple[FactorObservation, ...]:
    # b is listed out of order on purpose: the trace must sort by (as_of, security_id).
    return (
        _observation(DAYS[0], "b", 10.0),
        _observation(DAYS[0], "a", 0.0),
        _observation(DAYS[1], "a", 11.0),
        _observation(DAYS[1], "b", None),
        _observation(DAYS[2], "a", 12.0),
        _observation(DAYS[2], "b", 12.0, sector=None),
        _observation(DAYS[3], "a", 13.0),
        _observation(DAYS[3], "b", 13.0),
    )


def _graph() -> FactorGraph:
    return FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 2, "time_series"),
            UnaryNode("lagged", UnaryOperator.LAG, "close", "unary", periods=1),
            BinaryNode("ratio", BinaryOperator.DIVIDE, "lagged", "close", "binary"),
            GroupNode("sector_rank", GroupOperator.RANK, "close", "sector", "group"),
            BinaryNode("out", BinaryOperator.ADD, "ratio", "sector_rank", "binary"),
            BinaryNode("final", BinaryOperator.ADD, "out", "mom", "binary"),
        ),
        output_node_id="final",
    )


def _rows(trace, node_id: str):
    (node,) = [node for node in trace.nodes if node.node_id == node_id]
    return {(row.as_of, row.security_id): row for row in node.values}


def test_trace_values_equal_evaluation_values_for_every_selected_row() -> None:
    graph, panel = _graph(), _panel()
    evaluation = {
        (v.as_of, v.security_id): v.value
        for v in evaluate_factor_graph(graph, observations=panel).values
    }

    trace = trace_factor_graph(graph, observations=panel)

    output = _rows(trace, "final")
    assert set(output) == set(evaluation)
    for key, row in output.items():
        assert row.value == evaluation[key]
        assert (row.status is TraceValueStatus.OK) == (row.value is not None)
    # every node reachable from the output appears once, after all of its inputs
    order = [node.node_id for node in trace.nodes]
    assert sorted(order) == ["close", "final", "lagged", "mom", "out", "ratio", "sector_rank"]
    for node in trace.nodes:
        assert all(order.index(dep) < order.index(node.node_id) for dep in node.input_node_ids)
    assert trace.output_node_id == "final"


def test_rows_are_ordered_by_as_of_then_security_and_carry_inputs() -> None:
    trace = trace_factor_graph(_graph(), observations=_panel())

    (close,) = [node for node in trace.nodes if node.node_id == "close"]
    assert [(row.as_of, row.security_id) for row in close.values][:4] == [
        (DAYS[0], "a"),
        (DAYS[0], "b"),
        (DAYS[1], "a"),
        (DAYS[1], "b"),
    ]
    ratio = _rows(trace, "ratio")
    assert ratio[(DAYS[1], "a")].inputs == (0.0, 11.0)
    assert ratio[(DAYS[1], "a")].value == 0.0


def test_none_values_are_explained_by_status() -> None:
    trace = trace_factor_graph(_graph(), observations=_panel())

    close = _rows(trace, "close")
    assert close[(DAYS[1], "b")].status is TraceValueStatus.MISSING_INPUT

    momentum = _rows(trace, "mom")
    assert momentum[(DAYS[0], "a")].status is TraceValueStatus.WARM_UP
    assert momentum[(DAYS[1], "a")].status is TraceValueStatus.DIVIDE_BY_ZERO  # window starts at 0
    assert momentum[(DAYS[1], "b")].status is TraceValueStatus.MISSING_INPUT  # None in window
    assert momentum[(DAYS[3], "a")].status is TraceValueStatus.OK

    lagged = _rows(trace, "lagged")
    assert lagged[(DAYS[0], "a")].status is TraceValueStatus.WARM_UP
    assert lagged[(DAYS[2], "b")].status is TraceValueStatus.MISSING_INPUT  # lagged None

    ratio = _rows(trace, "ratio")
    assert ratio[(DAYS[1], "b")].status is TraceValueStatus.MISSING_INPUT

    sector_rank = _rows(trace, "sector_rank")
    assert sector_rank[(DAYS[2], "b")].status is TraceValueStatus.GROUP_MISSING
    assert sector_rank[(DAYS[1], "b")].status is TraceValueStatus.MISSING_INPUT


def test_division_by_zero_is_distinguished_from_missing_input() -> None:
    graph = FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            FieldNode("zero", "zero", "field"),
            BinaryNode("div", BinaryOperator.DIVIDE, "close", "zero", "binary"),
        ),
        output_node_id="div",
    )
    panel = (
        FactorObservation(
            DAYS[0], "a", (FactorFieldValue("price.close", 1.0), FactorFieldValue("zero", 0.0))
        ),
    )
    trace = trace_factor_graph(graph, observations=panel)

    assert _rows(trace, "div")[(DAYS[0], "a")].status is TraceValueStatus.DIVIDE_BY_ZERO


def test_selection_bounds_nodes_securities_dates_and_rows() -> None:
    graph, panel = _graph(), _panel()

    bounded = trace_factor_graph(
        graph,
        observations=panel,
        selection=TraceSelection(
            node_ids=("final", "close"), security_ids=("a",), as_of=(DAYS[3],)
        ),
    )
    assert [node.node_id for node in bounded.nodes] == ["close", "final"]
    assert all(len(node.values) == 1 for node in bounded.nodes)
    assert bounded.row_count == 2 and not bounded.truncated

    capped = trace_factor_graph(graph, observations=panel, selection=TraceSelection(max_rows=5))
    assert capped.truncated and capped.row_count == 5
    assert [node.node_id for node in capped.nodes] == ["close"]
    assert len(capped.nodes[0].values) == 5

    with pytest.raises(ValueError, match="unknown or not reachable"):
        trace_factor_graph(graph, observations=panel, selection=TraceSelection(node_ids=("nope",)))
    with pytest.raises(ValueError, match="max_rows must be positive"):
        trace_factor_graph(graph, observations=panel, selection=TraceSelection(max_rows=0))


def test_trace_is_deterministic_across_calls_and_input_order() -> None:
    graph, panel = _graph(), _panel()
    first = trace_factor_graph(graph, observations=panel)
    second = trace_factor_graph(graph, observations=tuple(reversed(panel)))

    assert first == second


def test_exact_cap_boundary_does_not_emit_an_empty_trailing_node() -> None:
    graph, panel = _graph(), _panel()
    capped = trace_factor_graph(graph, observations=panel, selection=TraceSelection(max_rows=8))

    assert [node.node_id for node in capped.nodes] == ["close"]
    assert capped.truncated and capped.row_count == 8


def test_duplicate_rows_fail_closed_before_evaluation() -> None:
    graph, panel = _graph(), _panel()
    duplicated = panel + (_observation(DAYS[1], "a", 99.0),)

    with pytest.raises(ValueError, match="unique \(as_of, security_id\)"):
        trace_factor_graph(graph, observations=duplicated)


def test_unreachable_node_selection_is_an_error_not_an_empty_trace() -> None:
    graph = _graph()
    orphaned = FactorGraph(
        nodes=graph.nodes + (FieldNode("orphan", "price.close", "field"),),
        output_node_id=graph.output_node_id,
    )
    with pytest.raises(ValueError, match="not reachable"):
        trace_factor_graph(
            orphaned, observations=_panel(), selection=TraceSelection(node_ids=("orphan",))
        )
