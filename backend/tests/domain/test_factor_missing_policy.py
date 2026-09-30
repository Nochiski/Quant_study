"""P2-02: 결측 정책이 팩터 그래프가 아니라 실행 설정에서 plan·평가로 들어온다.

세 가지를 고정한다. (1) `missing` 은 `compile_factor_plan` 인자이고 `plan_hash` 에 남는다.
(2) 기본값 경로(`MissingPolicy.DROP`)의 `plan_hash` 는 P2-02 이전과 같다. (3) 평가의 결측
채우기는 그래프의 `missing_policy` 가 아니라 넘겨준 `missing` 을 따른다.

#312: 채우는 정책(`zero`·`cross_sectional_median`)은 잎이 아니라 값이 횡단면으로 넘어가는 자리
(횡단면·그룹 노드 입력, 그래프 출력)에서 채운다. 아래 손계산 사례가 그 뜻을 고정한다.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date, timedelta

import pytest

from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    CrossSectionalNode,
    CrossSectionalOperator,
    FactorGraph,
    FieldNode,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.factor.facade.planning import compile_factor_plan
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry
from strategy_workbench.domain.factor.facade.trace import evaluate_factor_graph_with_trace

# 같은 그래프·같은 기본 결측 정책의 plan hash 골든. P2-02 는 정책을 인자로 옮기면서도 이 값을
# 유지했지만(`6aa3a445…`), P2-03 이 `FactorGraph.missing_policy` 필드 자체를 지우면서 `graph_hash`
# 가 바뀌어 값이 한 번 갈렸다. 팩터 행렬 캐시는 코드베이스에 아직 없어 잘못된 히트는 불가능하고,
# 영향은 이 골든 하나다(1.1 문서는 P2-09 업그레이더를 거쳐 들어온다).
PLAN_HASH_1_2 = "e8bdd889366602ffa16c40e3c1d4de04c7f040f637252ec7217cf50679d13f4e"
# 같은 그래프의 `zero` plan hash. #312 가 채움 자리를 옮기며 채우는 정책의 payload 에만 표지
# (`missing_fill`)를 실어 값이 달라진 판을 갈랐다 — 평가 규칙 판본의 첫 사례(#375 DR-A-10)다.
PLAN_HASH_ZERO = "788aa0abc41bbe39f46ee622ac93b07f8aa3b54b4883aa04ca20c3a403fc4107"


def _graph() -> FactorGraph:
    return FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 3, "time_series"),
        ),
        output_node_id="mom",
    )


def _field_graph() -> FactorGraph:
    return FactorGraph(nodes=(FieldNode("close", "price.close", "field"),), output_node_id="close")


def _observations() -> tuple[FactorObservation, ...]:
    return (
        FactorObservation(
            as_of=date(2024, 1, 8),
            security_id="005930",
            fields=(FactorFieldValue("price.close", 100.0),),
        ),
        FactorObservation(
            as_of=date(2024, 1, 8),
            security_id="000660",
            fields=(FactorFieldValue("price.close", None),),
        ),
    )


def test_plan_hash_splits_on_the_environment_missing_policy() -> None:
    graph = _graph()

    dropped = compile_factor_plan(graph, registry_version="r", missing=MissingPolicy.DROP)
    zeroed = compile_factor_plan(graph, registry_version="r", missing=MissingPolicy.ZERO)

    assert dropped.plan_hash != zeroed.plan_hash
    assert (dropped.missing_policy, zeroed.missing_policy) == ("drop", "zero")
    # 그래프 자체는 같다: 갈리는 것은 plan 이지 팩터 식이 아니다.
    assert dropped.graph_hash == zeroed.graph_hash


def test_default_missing_policy_plan_hash_is_pinned() -> None:
    plan = compile_factor_plan(
        _graph(), registry_version="test-registry", missing=MissingPolicy.DROP
    )

    assert plan.plan_hash == PLAN_HASH_1_2


def test_the_graph_no_longer_carries_a_missing_policy() -> None:
    """P2-03: 결측 정책의 owner 는 실행 설정 하나다 — 그래프에 같은 사실을 두 번 두지 않는다.

    필드가 남아 있으면 `graph_hash` 가 정책에 따라 갈려서, 실행 설정만 바꾼 두 실행이 서로 다른
    팩터 식으로 취급된다.
    """
    assert not hasattr(_graph(), "missing_policy")
    assert "missing_policy" not in {field.name for field in fields(FactorGraph)}


def test_evaluation_fills_by_the_argument() -> None:
    observations = _observations()

    dropped = evaluate_factor_graph(
        _field_graph(), observations=observations, missing=MissingPolicy.DROP
    )
    zeroed = evaluate_factor_graph(
        _field_graph(), observations=observations, missing=MissingPolicy.ZERO
    )

    assert [value.value for value in dropped.values] == [100.0, None]
    assert [value.value for value in zeroed.values] == [100.0, 0.0]


def _masked_cross_section() -> tuple[FactorObservation, ...]:
    """한 날짜의 세 종목 — 값 · 모르는 결측 · 원장이 가린 결측(#298)."""
    day = date(2024, 1, 8)
    return (
        FactorObservation(day, "A", (FactorFieldValue("price.close", 100.0),)),
        FactorObservation(day, "B", (FactorFieldValue("price.close", None),)),
        FactorObservation(day, "C", (FactorFieldValue("price.close", None, masked=True),)),
    )


def test_no_missing_policy_fills_a_cell_the_ledger_masked() -> None:
    """결측 정책은 모르는 값만 채운다. 원장이 틀린 값이라 가린 셀은 어느 정책에서도 결측이다(#298).

    채우면 가리기 전보다 더 틀린다 — 잔고 0·가격 0 이 모멘텀·변화를 −100% 쪽으로 끌어간다.
    """
    expected = {
        MissingPolicy.DROP: [100.0, None, None],
        MissingPolicy.KEEP: [100.0, None, None],
        MissingPolicy.ZERO: [100.0, 0.0, None],
        MissingPolicy.CROSS_SECTIONAL_MEDIAN: [100.0, 100.0, None],
    }
    for policy, values in expected.items():
        evaluation = evaluate_factor_graph(
            _field_graph(), observations=_masked_cross_section(), missing=policy
        )
        assert [value.value for value in evaluation.values] == values, policy


def test_a_window_over_an_unknown_cell_is_filled_only_at_the_output_never_over_a_masked_one() -> (
    None
):
    """시계열 창은 모르는 결측도 채우지 않는다 — 창 가운데가 비면 창 값이 결측이다(#312).

    채움은 그래프 출력(횡단면으로 넘어가는 자리)에서만이다: 같은 날 B 의 모멘텀 110 / 100 − 1 =
    0.1 이 있으니 `zero` 는 A 를 0, `cross_sectional_median` 은 0.1 로 채운다. 창에 원장이 가린
    칸이 들면 어느 정책도 채우지 않는다(#298·#337). 예전에는 잎에서 가운데 칸을 채워 창이 섰다.
    """
    days = [date(2024, 1, day) for day in (8, 9, 10)]

    def momentum(middle: FactorFieldValue, missing: MissingPolicy) -> float | None:
        closes = {
            "A": (
                FactorFieldValue("price.close", 100.0),
                middle,
                FactorFieldValue("price.close", 110.0),
            ),
            "B": tuple(FactorFieldValue("price.close", value) for value in (100.0, 105.0, 110.0)),
        }
        observations = tuple(
            FactorObservation(day, security, (close,))
            for security, series in closes.items()
            for day, close in zip(days, series, strict=True)
        )
        return _values(_graph(), observations, missing)[(days[-1], "A")]

    unknown = FactorFieldValue("price.close", None)
    masked = FactorFieldValue("price.close", None, masked=True)
    assert momentum(unknown, MissingPolicy.DROP) is None
    assert momentum(unknown, MissingPolicy.ZERO) == 0.0
    assert momentum(unknown, MissingPolicy.CROSS_SECTIONAL_MEDIAN) == pytest.approx(0.1)
    for policy in MissingPolicy:
        assert momentum(masked, policy) is None, policy


def _default_graph(factor_id: str) -> FactorGraph:
    graph = build_default_factor_registry().get(factor_id).default_graph
    assert graph is not None, factor_id
    return graph


def _values(
    graph: FactorGraph, observations: tuple[FactorObservation, ...], missing: MissingPolicy
) -> dict[tuple[date, str], float | None]:
    evaluation = evaluate_factor_graph(graph, observations=observations, missing=missing)
    return {(value.as_of, value.security_id): value.value for value in evaluation.values}


def test_margin_balance_change_does_not_read_a_filled_balance() -> None:
    """신용잔고 20세션 변화(`credit.margin_balance_change_20d`) — 잔고를 채우면 가짜 급변이 생긴다.

    21세션, 잔고율 = 잔고 ÷ 상장주식수. A 는 마지막 날 잔고를 모른다. B 는 10,000/1,000,000 →
    12,000/1,000,000 이라 변화 0.002, C 는 30,000/3,000,000 → 27,000/3,000,000 이라 −0.001 이다.
    A 는 창이 서지 않아 출력에서 채운다: `zero` 는 0, `cross_sectional_median` 은
    (0.002 − 0.001) / 2 = 0.0005 다. 예전에는 잎을 채워 `zero` 가 잔고 0 으로 −0.01(잔고율
    1% → 0)을, `median` 이 잔고 19,500(다른 종목 잔고의 중앙값)으로 +0.0095 를 지어냈다.
    """
    days = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(21)]
    series: dict[str, tuple[list[float | None], float]] = {
        "A": ([*[10_000.0] * 20, None], 1_000_000.0),
        "B": ([*[10_000.0] * 20, 12_000.0], 1_000_000.0),
        "C": ([*[30_000.0] * 20, 27_000.0], 3_000_000.0),
    }
    observations = tuple(
        FactorObservation(
            day,
            security,
            (
                FactorFieldValue("credit.margin_balance", balance),
                FactorFieldValue("price.shares_outstanding", shares),
            ),
        )
        for security, (balances, shares) in series.items()
        for day, balance in zip(days, balances, strict=True)
    )
    graph = _default_graph("credit.margin_balance_change_20d")

    def change(missing: MissingPolicy) -> dict[str, float | None]:
        values = _values(graph, observations, missing)
        return {security: values[(days[-1], security)] for security in series}

    assert change(MissingPolicy.DROP) == {
        "A": None,
        "B": pytest.approx(0.002),
        "C": pytest.approx(-0.001),
    }
    assert change(MissingPolicy.ZERO)["A"] == 0.0
    assert change(MissingPolicy.CROSS_SECTIONAL_MEDIAN)["A"] == pytest.approx(0.0005)


def test_book_to_market_median_fills_the_ratio_not_the_book_value() -> None:
    """`financial.book_to_market`(자본총계 ÷ 시총 → 순위) — 비율을 채우지 원값을 채우지 않는다.

    하루, 네 종목. 비율 B 0.5 · C 0.2 · D 0.09 이고 A 는 자본총계를 모른다. 순위 입력에서 A 를
    비율 중앙값 0.2 로 채우면 D < A = C < B 라 A 의 순위는 (2.5 − 1) / 3 = 0.5 다. `zero` 는 비율
    0 이라 가장 낮다(0). 예전 `median` 은 자본총계를 다른 회사 중앙값 50 으로 채워 A 의 비율이
    50 / 100 = 0.5 가 되고, B 와 같이 (3.5 − 1) / 3 ≈ 0.83 으로 올라갔다.
    """
    day = date(2024, 1, 8)
    rows = {"A": (None, 100.0), "B": (50.0, 100.0), "C": (20.0, 100.0), "D": (90.0, 1_000.0)}
    observations = tuple(
        FactorObservation(
            day,
            security,
            (
                FactorFieldValue("financial.book_equity", book),
                FactorFieldValue("price.market_cap", cap),
            ),
        )
        for security, (book, cap) in rows.items()
    )
    graph = _default_graph("financial.book_to_market")

    median_rank = _values(graph, observations, MissingPolicy.CROSS_SECTIONAL_MEDIAN)
    assert {security: median_rank[(day, security)] for security in rows} == {
        "A": pytest.approx(0.5),
        "B": pytest.approx(1.0),
        "C": pytest.approx(0.5),
        "D": pytest.approx(0.0),
    }
    assert _values(graph, observations, MissingPolicy.ZERO)[(day, "A")] == 0.0
    assert _values(graph, observations, MissingPolicy.DROP)[(day, "A")] is None


def test_a_new_listing_gets_the_neutral_momentum_of_the_policy() -> None:
    """이력이 모자란 새 상장도 팩터 값이 빈 것이라 횡단면 순위 입력에서 채운다(#312).

    3세션 모멘텀 → 순위. A 100 → 121 은 0.21, B 100 → 105 는 0.05, C 는 마지막 날 상장해 창이
    없다. `zero` 는 C 를 모멘텀 0 으로 넣어 C < B < A(순위 0 · 0.5 · 1)이고,
    `cross_sectional_median` 은 (0.21 + 0.05) / 2 = 0.13 이라 B < C < A(0 · 0.5 · 1)다. `drop` 은
    예전처럼 빠진다.
    """
    days = [date(2024, 1, day) for day in (8, 9, 10)]
    closes = {"A": (100.0, 110.0, 121.0), "B": (100.0, 102.0, 105.0)}
    observations = (
        *(
            FactorObservation(day, security, (FactorFieldValue("price.close", close),))
            for security, series in closes.items()
            for day, close in zip(days, series, strict=True)
        ),
        FactorObservation(days[-1], "C", (FactorFieldValue("price.close", 50.0),)),
    )
    graph = FactorGraph(
        nodes=(
            *_graph().nodes,
            CrossSectionalNode("rank", CrossSectionalOperator.RANK, "mom", "cross_sectional"),
        ),
        output_node_id="rank",
    )

    def ranks(missing: MissingPolicy) -> dict[str, float | None]:
        values = _values(graph, observations, missing)
        return {security: values[(days[-1], security)] for security in ("A", "B", "C")}

    assert ranks(MissingPolicy.ZERO) == {"A": 1.0, "B": 0.5, "C": 0.0}
    assert ranks(MissingPolicy.CROSS_SECTIONAL_MEDIAN) == {"A": 1.0, "B": 0.0, "C": 0.5}
    assert ranks(MissingPolicy.DROP) == {"A": 1.0, "B": 0.0, "C": None}


def test_a_day_without_any_value_is_not_filled() -> None:
    """같은 날 동료에 값이 하나도 없으면 `zero` 도 채우지 않는다 — 없는 팩터를 지어내지 않는다."""
    observations = tuple(
        FactorObservation(date(2024, 1, 8), security, (FactorFieldValue("price.close", None),))
        for security in ("A", "B")
    )
    for policy in MissingPolicy:
        values = _values(_field_graph(), observations, policy)
        assert list(values.values()) == [None, None], policy


def test_only_filling_policies_carry_the_fill_stage_in_the_plan() -> None:
    """채우는 정책만 plan payload 에 채움 자리 표지를 싣는다 — drop 골든은 그대로다."""
    zeroed = compile_factor_plan(
        _graph(), registry_version="test-registry", missing=MissingPolicy.ZERO
    )

    assert zeroed.plan_hash == PLAN_HASH_ZERO


def test_trace_evaluation_reads_the_same_argument() -> None:
    observations = _observations()

    evaluation, trace = evaluate_factor_graph_with_trace(
        _field_graph(), observations=observations, missing=MissingPolicy.ZERO
    )

    assert [value.value for value in evaluation.values] == [100.0, 0.0]
    assert trace.output_node_id == "close"
