"""원장이 가린 셀(MASKED)은 사건 경계다 — 시간을 가로지르는 연산이 건너면 결측이다(#315).

수정주가 층 이동(`krx_base_inconsistent`)은 원장 뷰가 적용일 한 행만 가린다. 창 연산은 창 안 결측을
결측으로 잇지만, `lag` 는 k칸 앞 값 하나만 읽어 그 사이의 가린 칸을 보지 않았다. 그래서
`adj / lag(adj, 20)` 이 층 이동 뒤 19세션 동안 층 배수(025440 ×4.26)를 정상 값으로 냈다. 가리지 않은
결측은 경계가 아니라 예전처럼 건너도 된다.
"""

from __future__ import annotations

from datetime import date, timedelta

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
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)

_FIELD = "price.adj_close"
_DAYS = tuple(date(2024, 10, 1) + timedelta(days=n) for n in range(61))
_EVENT = 30  # 층 이동 적용일 자리(원장이 가린 행)
_SHIFT = 4.26  # 025440 2024-11-19 의 층 배수


def _observations(**event_cells: FactorFieldValue) -> tuple[FactorObservation, ...]:
    """종목마다 적용일 앞은 100 + p, 뒤는 그 층 배수인 수정주가. 적용일 칸만 종목별로 준다."""
    return tuple(
        FactorObservation(
            day,
            security,
            (
                cell
                if position == _EVENT
                else FactorFieldValue(_FIELD, (100.0 + position) * (
                    _SHIFT if position > _EVENT else 1.0
                )),
            ),
        )
        for security, cell in event_cells.items()
        for position, day in enumerate(_DAYS)
    )


def _missing(
    graph: FactorGraph,
    observations: tuple[FactorObservation, ...],
    security: str,
    missing: MissingPolicy = MissingPolicy.DROP,
) -> list[int]:
    values = evaluate_factor_graph(graph, observations=observations, missing=missing).values
    return [
        _DAYS.index(value.as_of)
        for value in values
        if value.security_id == security and value.value is None
    ]


def _ratio_to(lagged_input: str, *extra: TimeSeriesNode) -> FactorGraph:
    """`adj / lag(<lagged_input>, 20)` — k세션 수익률을 lag 로 짠 모양."""
    return FactorGraph(
        nodes=(
            FieldNode("adj", _FIELD, "field"),
            *extra,
            UnaryNode("lagged", UnaryOperator.LAG, lagged_input, "unary", periods=20),
            BinaryNode("ratio", BinaryOperator.DIVIDE, "adj", "lagged", "binary"),
        ),
        output_node_id="ratio",
    )


@pytest.mark.parametrize("missing", list(MissingPolicy))
def test_lag_across_a_masked_level_shift_is_missing(missing: MissingPolicy) -> None:
    """층 이동 뒤 19세션(31~49)의 20세션 수익률이 결측이다. 앞뒤 창은 층 배수 없이 맞는 값이다.

    30 은 적용일(가린 칸), 50 은 가린 칸을 읽는 자리라 예전에도 결측이었다. 가린 칸은 어느 결측
    정책에서도 채우지 않으므로 경계도 정책과 무관하다(#298).
    """
    observations = _observations(MASKED=FactorFieldValue(_FIELD, None, masked=True))
    evaluation = evaluate_factor_graph(_ratio_to("adj"), observations=observations, missing=missing)
    masked = {
        _DAYS.index(value.as_of): value.value
        for value in evaluation.values
        if value.security_id == "MASKED"
    }

    assert [p for p in range(20, 61) if masked[p] is None] == list(range(_EVENT, _EVENT + 21))
    for position in (*range(20, _EVENT), *range(_EVENT + 21, 61)):
        assert masked[position] == pytest.approx((100.0 + position) / (80.0 + position))


def test_lag_across_an_unknown_missing_value_keeps_reading_through() -> None:
    """가리지 않은 결측은 경계가 아니다 — 그 칸과 그 칸을 읽는 자리만 결측이고 사이는 값이 선다."""
    observations = _observations(UNKNOWN=FactorFieldValue(_FIELD, None))

    missing = _missing(_ratio_to("adj"), observations, "UNKNOWN")

    assert [p for p in missing if p >= 20] == [_EVENT, _EVENT + 20]


def test_chained_lags_carry_the_boundary() -> None:
    """`lag` 가 보는 구간은 오늘 칸까지다 — `lag(lag(adj, 10), 10)` 도 `lag(adj, 20)` 과 같은
    30~50 이 결측이다.

    오늘 칸을 빼면 안쪽 lag 가 적용일에 층 앞 값을 내고, 바깥 lag 가 그 값을 층 뒤로 옮긴다. 그러면
    31 에서 층 배수가 정상 값으로 나온다.
    """
    graph = FactorGraph(
        nodes=(
            FieldNode("adj", _FIELD, "field"),
            UnaryNode("lag10", UnaryOperator.LAG, "adj", "unary", periods=10),
            UnaryNode("lag20", UnaryOperator.LAG, "lag10", "unary", periods=10),
            BinaryNode("ratio", BinaryOperator.DIVIDE, "adj", "lag20", "binary"),
        ),
        output_node_id="ratio",
    )
    observations = _observations(MASKED=FactorFieldValue(_FIELD, None, masked=True))

    missing = _missing(graph, observations, "MASKED")

    assert [p for p in missing if p >= 20] == list(range(_EVENT, _EVENT + 21))


def test_the_boundary_reaches_lag_through_a_window_operator() -> None:
    """가린 칸을 품은 창의 출력도 가린 칸이다 — 그 출력을 `lag` 로 옮겨 견주어도 결측이다.

    5세션 이동평균은 적용일을 품는 30~34 가 가린 칸이 된다. 그 칸을 20세션 건너 오늘 값과 견주는
    30~54 는 결측이고, 55 부터는 층 뒤 평균끼리라 값이 선다.
    """
    mean = TimeSeriesNode("mean", TimeSeriesOperator.MEAN, "adj", 5, "time_series")
    observations = _observations(MASKED=FactorFieldValue(_FIELD, None, masked=True))

    missing = _missing(_ratio_to("mean", mean), observations, "MASKED")

    assert [p for p in missing if p >= 24] == list(range(_EVENT, _EVENT + 25))


def test_a_masked_input_cannot_carry_a_value() -> None:
    """가린 셀은 값이 없다 — 값이 실려 오면 결측 정책·중앙값 모집단이 그 값을 쓰므로 만들 때 막는다
    (#311 리뷰 P3-2). raw 포트·연구 패널 셀과 같은 계약이다."""
    with pytest.raises(ValueError, match="field_id='price.adj_close' value=1.0"):
        FactorFieldValue(_FIELD, 1.0, masked=True)


def test_a_window_operator_only_looks_inside_its_window() -> None:
    """창 연산은 창 안 값끼리 견준다 — 건너뛰는 세션에 가린 칸이 있어도 창 밖이면 값이 선다.

    창 5 · 건너뛰는 세션 3 의 모멘텀은 자리 p 에서 [p-7, p-3] 을 본다. 적용일을 창에 품는 33~37 만
    결측이고, 적용일 뒤 30~32(창은 적용일 앞)는 맞는 값이다.
    """
    graph = FactorGraph(
        nodes=(
            FieldNode("adj", _FIELD, "field"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "adj", 5, "time_series", 3),
        ),
        output_node_id="mom",
    )
    observations = _observations(MASKED=FactorFieldValue(_FIELD, None, masked=True))

    assert [p for p in _missing(graph, observations, "MASKED") if p >= 7] == list(range(33, 38))
