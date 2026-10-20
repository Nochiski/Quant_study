"""trace 는 노드 캐시 뷰만 읽는다(columnar-tape T3-06, spec D5).

객체 평가기 캐시(`_NodeEvaluator.cache`)와 열 평가기 캐시(`FactorNodeCache`)로 만든 trace 가 노드별
상태·원인 전파·masked 칸·값·입력까지 비트 단위로 같아야 한다. 두 캐시가 같은 뷰를 내면 trace 의 상태
규칙은 한 벌이라, 이 대조는 뷰의 차이(타입·결측·가림 읽기)를 잡는다.

상태 규칙 자체가 바뀌면 두 캐시가 같이 바뀌어 대조로는 드러나지 않는다. 그래서 같은 사례에서 SoT 가
정한 trace 성질 셋도 본다("팩터의 세션 읽기 창"·"원장이 가린 셀과 결측 정책" 행).

- 선택이 상태를 바꾸지 않는다: 출력 노드만 고른 좁은 선택은 선택 밖 입력의 원인을 재귀로 따라가고,
  전체 선택은 위상 순서로 메모가 입력부터 찬다. 원인 전파를 한 단계 빠뜨리면 두 경로가 갈린다.
- 조건 노드는 실제로 고른 가지의 원인만 잇는다.
- `masked` 상태는 평가기가 가린 칸에만 있고, 가린 칸은 `masked` 이거나 이력 부족이다.

성질로 다 묶이지 않는 규칙(그룹 값이 문자열이 아니면 그룹 없음, 이력 부족이 가림보다 먼저 등)은
RNG 없는 손 패널의 상태 열을 두 캐시에 같은 기대 표로 고정해 지킨다(#600 리뷰 P3-1).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from strategy_workbench.domain.factor._evaluation import _compute_nodes
from strategy_workbench.domain.factor._trace import (
    FactorTrace,
    TracedValue,
    TraceNodeCache,
    TraceSelection,
    TraceValueStatus,
    _project_trace,
    _trace_context,
)
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    NonFiniteFactorCalculationError,
)
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    FactorComparisonOperator,
    FactorGraph,
    FieldNode,
    GroupNode,
    GroupOperator,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.panel_evaluation import (
    FactorPanel,
    evaluate_panel_nodes,
)

from ._compare import compare, format_report
from ._corpus import EvaluatorCase, evaluator_cases, factor_panel
from ._fuzz import fuzz_case

# CI 퍼저 seed 수. PR 증거의 대량 실행은 같은 사례를 더 많은 seed 로 돌린다
_FUZZ_SEEDS = 300


def _branch_cases() -> list[EvaluatorCase]:
    """고른 가지와 다른 가지의 이력 부족이 갈리는 조건 칸.

    말뭉치·퍼저의 조건은 predicate 도 이력이 모자라 결측이 되므로, 가지가 고른 칸에서 두 가지의
    이력 부족이 갈리는 일이 드물다. 상수 비교로 가지를 고정해 그 칸을 만든다.
    """
    cases: list[EvaluatorCase] = []
    for chosen in (True, False):
        graph = FactorGraph(
            nodes=(
                FieldNode("x", "x", "field"),
                ConstantNode("one", 1.0, "constant"),
                ConstantNode("other", 1.0 if chosen else 2.0, "constant"),
                ComparisonNode(
                    "gate", FactorComparisonOperator.EQUAL, "one", "other", "comparison"
                ),
                TimeSeriesNode("slow", TimeSeriesOperator.MEAN, "x", 5, "time_series"),
                ConditionalNode("out", "gate", "slow", "x", "conditional"),
            ),
            output_node_id="out",
        )
        cases += [
            EvaluatorCase(
                f"T/branch={chosen}/missing={policy.value}", graph, factor_panel(11), policy
            )
            for policy in MissingPolicy
        ]
    return cases


def _cases() -> list[EvaluatorCase]:
    # 정수 상수는 hydrate 가 float 로 바꾸므로 열 평가기가 받지 않는다(L1 과 같다)
    corpus = [
        case
        for case in evaluator_cases()[::2]
        if not any(
            isinstance(node, ConstantNode) and not isinstance(node.value, float)
            for node in case.graph.nodes
        )
    ]
    return corpus + [fuzz_case(seed) for seed in range(_FUZZ_SEEDS)] + _branch_cases()


def _selections(case: EvaluatorCase) -> tuple[TraceSelection, TraceSelection]:
    """모든 노드·칸을 담는 전체 선택과, 출력 노드·종목 셋에 하나·날짜 넷에 하나만 고른 좁은 선택."""
    rows = len(case.observations)
    securities = tuple(sorted({row.security_id for row in case.observations})[::3])
    dates = tuple(sorted({row.as_of for row in case.observations})[::4])
    full = TraceSelection(max_rows=rows * len(case.graph.nodes))
    narrow = TraceSelection(
        node_ids=(case.graph.output_node_id,),
        security_ids=securities,
        as_of=dates,
        max_rows=rows,
    )
    return full, narrow


def _trace(case: EvaluatorCase, cache: TraceNodeCache, selection: TraceSelection) -> FactorTrace:
    bounds, nodes, order = _trace_context(case.graph, case.observations, selection)
    return _project_trace(case.graph, case.observations, bounds, nodes, order, cache)


def _caches(case: EvaluatorCase) -> tuple[TraceNodeCache, TraceNodeCache] | None:
    """두 평가기의 캐시. 넘침으로 평가가 실패한 사례는 L1 이 실패 내용을 대조하므로 뺀다."""
    try:
        evaluator = _compute_nodes(
            case.graph,
            observations=case.observations,
            missing=case.missing,
            parameters=case.parameters,
        )
    except NonFiniteFactorCalculationError:
        return None
    columnar = evaluate_panel_nodes(
        case.graph,
        panel=FactorPanel.from_observations(case.observations, case.graph),
        missing=case.missing,
        parameters=case.parameters,
    )
    return evaluator.cache, columnar


def _violations(
    case: EvaluatorCase, trace: FactorTrace, cache: TraceNodeCache
) -> tuple[list[str], int]:
    """전체 선택 trace 가 조건 가지·가린 칸 성질을 어긴 칸과, 가지를 고른 칸 가운데 두 가지의 이력
    부족이 갈려 가지를 잘못 고르면 상태가 바뀌는 칸 수."""
    index = {
        (row.as_of, row.security_id): position for position, row in enumerate(case.observations)
    }
    status = {
        (node.node_id, row.as_of, row.security_id): row.status
        for node in trace.nodes
        for row in node.values
    }
    nodes = {node.node_id: node for node in case.graph.nodes}
    found: list[str] = []
    deciding = 0
    for traced in trace.nodes:
        for row in traced.values:
            where = f"{case.key} node={traced.node_id} as_of={row.as_of} security={row.security_id}"
            masked = cache.is_masked(traced.node_id, index[(row.as_of, row.security_id)])
            if (row.status is TraceValueStatus.MASKED) != masked and not (
                masked and row.status is TraceValueStatus.WARM_UP
            ):
                found.append(f"masked {where} status={row.status} masked={masked}")
            if (
                isinstance(nodes[traced.node_id], ConditionalNode)
                and row.value is None
                and row.status is not TraceValueStatus.MASKED
            ):
                predicate, when_true, when_false = traced.input_node_ids
                chosen = (
                    when_true
                    if row.inputs[0] is True
                    else when_false
                    if row.inputs[0] is False
                    else predicate
                )
                warming = {
                    node_id: status[(node_id, row.as_of, row.security_id)]
                    is TraceValueStatus.WARM_UP
                    for node_id in traced.input_node_ids
                }
                expected = (
                    TraceValueStatus.WARM_UP if warming[chosen] else TraceValueStatus.MISSING_INPUT
                )
                if row.status is not expected:
                    found.append(f"conditional {where} status={row.status} chosen={chosen}")
                if chosen != predicate and warming[when_true] != warming[when_false]:
                    deciding += 1
    return found, deciding


def _narrowed(
    trace: FactorTrace, case: EvaluatorCase, selection: TraceSelection
) -> tuple[TracedValue, ...]:
    """전체 선택 trace 에서 좁은 선택이 고른 출력 노드 칸만 남긴다."""
    (output,) = [node for node in trace.nodes if node.node_id == case.graph.output_node_id]
    return tuple(
        row
        for row in output.values
        if row.security_id in (selection.security_ids or ())
        and row.as_of in (selection.as_of or ())
    )


def test_trace_from_either_node_cache_is_bit_identical() -> None:
    caches_pairs: list[tuple[str, object, object]] = []
    selection_pairs: list[tuple[str, object, object]] = []
    violations: list[str] = []
    statuses: set[TraceValueStatus] = set()
    traced_cases = narrow_rows = deciding = 0
    for case in _cases():
        caches = _caches(case)
        if caches is None:
            continue
        traced_cases += 1
        object_cache, columnar_cache = caches
        full, narrow = _selections(case)
        object_full = _trace(case, object_cache, full)
        object_narrow = _trace(case, object_cache, narrow)
        caches_pairs.append((f"{case.key}#full", object_full, _trace(case, columnar_cache, full)))
        caches_pairs.append(
            (f"{case.key}#narrow", object_narrow, _trace(case, columnar_cache, narrow))
        )
        selection_pairs.append(
            (case.key, _narrowed(object_full, case, narrow), object_narrow.nodes[0].values)
        )
        found, decided = _violations(case, object_full, object_cache)
        violations += found
        deciding += decided
        statuses |= {row.status for node in object_full.nodes for row in node.values}
        narrow_rows += object_narrow.row_count
        assert not object_full.truncated
    report = compare("T-cache", caches_pairs)
    assert report.ok, format_report(report, values=True)
    report = compare("T-selection", selection_pairs)
    assert report.ok, format_report(report, values=True)
    assert violations[:20] == []
    # 사례가 줄거나 상태가 한쪽으로 몰려 조용히 통과하지 않게
    assert traced_cases >= 500
    assert narrow_rows >= 5_000
    assert statuses == set(TraceValueStatus)
    assert deciding >= 20


# 손 패널 상태표의 글자. 값은 싣지 않아 OS·libm 차이에 흔들리지 않는다
_CODES = {
    TraceValueStatus.OK: "O",
    TraceValueStatus.MISSING_INPUT: "M",
    TraceValueStatus.WARM_UP: "W",
    TraceValueStatus.DIVIDE_BY_ZERO: "Z",
    TraceValueStatus.GROUP_MISSING: "G",
    TraceValueStatus.MASKED: "K",
}
_FIXED_DAYS = tuple(date(2024, 1, 1) + timedelta(days=offset) for offset in range(5))
# 종목마다 날짜 순 셀. 숫자는 값, None 은 값 없는 셀, "masked" 는 원장이 가린 셀, "omit" 은 셀 없음
_FIXED_CELLS: dict[str, dict[str, tuple[object, ...]]] = {
    "a": {
        "x": (10.0, 11.0, 12.0, 13.0, 14.0),
        "d": (1.0, 0.0, 2.0, 2.0, 2.0),
        "g": ("A", "A", "A", "A", "A"),
    },
    "b": {
        "x": (None, 0.0, 5.0, "masked", 6.0),
        "d": (1.0, 1.0, 1.0, 1.0, 1.0),
        "g": ("A", None, 3.0, "B", "B"),
    },
    "c": {
        "x": ("masked", 20.0, 21.0, 22.0, 23.0),
        "d": (1.0, 1.0, 1.0, 1.0, 1.0),
        "g": ("B", "B", "B", "omit", "B"),
    },
}


def _fixed_cell(field_id: str, value: object) -> tuple[FactorFieldValue, ...]:
    if value == "omit":
        return ()
    if value == "masked":
        return (FactorFieldValue(field_id, None, masked=True),)
    if not (value is None or isinstance(value, (str, float))):
        raise TypeError(f"손 패널 셀 값 — field_id={field_id!r} value={value!r}")
    return (FactorFieldValue(field_id, value),)


def _fixed_case() -> EvaluatorCase:
    """상태 여섯 가지와 원인 전파를 한 그래프로 덮는 손 패널(RNG 없음).

    그룹 값 str·None·3.0·셀 없음, 가린 칸, 이력 부족, 0 분모, 모멘텀 0 시작, 고르지 않은 조건 가지
    (참·거짓 두 조건 모두 이력이 모자란 `lag` 쪽 가지를 고르지 않는다)를 넣었다.
    """
    graph = FactorGraph(
        nodes=(
            FieldNode("x", "x", "field"),
            FieldNode("d", "d", "field"),
            BinaryNode("ratio", BinaryOperator.DIVIDE, "x", "d", "binary"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "x", 2, "time_series"),
            GroupNode("grp", GroupOperator.RANK, "x", "g", "group"),
            UnaryNode("lag", UnaryOperator.LAG, "x", "unary", periods=1),
            ConstantNode("one", 1.0, "constant"),
            ConstantNode("two", 2.0, "constant"),
            ComparisonNode("yes", FactorComparisonOperator.EQUAL, "one", "one", "comparison"),
            ComparisonNode("no", FactorComparisonOperator.EQUAL, "one", "two", "comparison"),
            ConditionalNode("pick_true", "yes", "x", "lag", "conditional"),
            ConditionalNode("pick_false", "no", "lag", "x", "conditional"),
            BinaryNode("s1", BinaryOperator.ADD, "ratio", "mom", "binary"),
            BinaryNode("s2", BinaryOperator.ADD, "grp", "pick_true", "binary"),
            BinaryNode("s3", BinaryOperator.ADD, "s2", "pick_false", "binary"),
            BinaryNode("out", BinaryOperator.ADD, "s1", "s3", "binary"),
        ),
        output_node_id="out",
    )
    rows = tuple(
        FactorObservation(
            day,
            security,
            tuple(
                cell
                for field_id, values in cells.items()
                for cell in _fixed_cell(field_id, values[position])
            ),
        )
        for position, day in enumerate(_FIXED_DAYS)
        for security, cells in _FIXED_CELLS.items()
    )
    return EvaluatorCase("T/fixed", graph, rows, MissingPolicy.DROP)


def _status_table(case: EvaluatorCase, cache: TraceNodeCache) -> dict[str, dict[str, str]]:
    """전체 선택 trace 의 상태 열만: 노드 → 종목 → 날짜 순 상태 글자."""
    trace = _trace(case, cache, _selections(case)[0])
    table: dict[str, dict[str, str]] = {}
    for node in trace.nodes:
        row = table.setdefault(node.node_id, {})
        for value in node.values:
            row[value.security_id] = row.get(value.security_id, "") + _CODES[value.status]
    return table


# main(8e78acbe) trace 로 손계산과 대조해 고정한 상태표다. 칸을 읽는 법(종목 b·c 는 날짜 0~4):
# - grp b `MGGKO`: 그룹 "A" 인데 x 결측 → 입력 결측, 그룹 None·3.0 → 그룹 없음(문자열만
#   그룹), 가린 칸. c 의 날짜 3 `G` 는 그룹 셀이 없는 칸이다.
# - mom c `WK...`: 날짜 0 은 가린 칸이 창에 들어도 이력 부족이 먼저다. b 날짜 1 `M`(창에 결측),
#   날짜 2 `Z`(모멘텀이 0 에서 시작).
# - ratio a 날짜 1 `Z`(0 분모). s1 a `WM...`: 입력의 이력 부족은 전파하고 0 분모는 입력 결측이다.
# - pick_true·pick_false b 날짜 0 `M`: 고른 가지 x 의 결측만 잇고, 고르지 않은 lag 의 이력
#   부족은 읽지 않는다.
_FIXED_STATUSES = {
    "x": {"a": "OOOOO", "b": "MOOKO", "c": "KOOOO"},
    "d": {"a": "OOOOO", "b": "OOOOO", "c": "OOOOO"},
    "ratio": {"a": "OZOOO", "b": "MOOKO", "c": "KOOOO"},
    "mom": {"a": "WOOOO", "b": "WMZKK", "c": "WKOOO"},
    "s1": {"a": "WMOOO", "b": "WMMKK", "c": "KKOOO"},
    "grp": {"a": "OOOOO", "b": "MGGKO", "c": "KOOGO"},
    "one": {"a": "OOOOO", "b": "OOOOO", "c": "OOOOO"},
    "yes": {"a": "OOOOO", "b": "OOOOO", "c": "OOOOO"},
    "lag": {"a": "WOOOO", "b": "WMOOK", "c": "WKOOO"},
    "pick_true": {"a": "OOOOO", "b": "MOOKK", "c": "KKOOO"},
    "s2": {"a": "OOOOO", "b": "MMMKK", "c": "KKOMO"},
    "two": {"a": "OOOOO", "b": "OOOOO", "c": "OOOOO"},
    "no": {"a": "OOOOO", "b": "OOOOO", "c": "OOOOO"},
    "pick_false": {"a": "OOOOO", "b": "MOOKK", "c": "KKOOO"},
    "s3": {"a": "OOOOO", "b": "MMMKK", "c": "KKOMO"},
    "out": {"a": "WMOOO", "b": "WMMKK", "c": "KKOMO"},
}


@pytest.mark.parametrize("cache_index", [0, 1], ids=["object", "columnar"])
def test_fixed_panel_trace_statuses_are_pinned_for_both_caches(cache_index: int) -> None:
    """두 캐시가 함께 쓰는 상태 규칙을 손 패널의 상태 열로 고정한다(#600 리뷰 P3-1).

    두 캐시 대조는 규칙이 두 캐시에서 함께 바뀌면 잡지 못한다. 상태 글자만 보므로 값의 OS·libm
    차이와 무관하고, 말뭉치·퍼저 생성기가 바뀌어도 이 표는 그대로다.
    """
    case = _fixed_case()
    caches = _caches(case)
    assert caches is not None
    table = _status_table(case, caches[cache_index])
    assert table == _FIXED_STATUSES
    assert {code for row in table.values() for codes in row.values() for code in codes} == set(
        _CODES.values()
    )
