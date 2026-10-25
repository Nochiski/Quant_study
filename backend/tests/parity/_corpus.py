"""대조 하네스 말뭉치(columnar-tape spec 5절). 모든 사례는 이 모듈이 직접 만든 입력이다.

- L1 평가기: 연산자 그래프(`test_factor_evaluation_version._graphs` + 확장) × 결측 정책 4종
  × 합성 패널
- L2 컴파일러: 직교 표본 32개 조합 × 합성 `PortfolioObservation` 패널
- L3 파이프라인: fixture 전략 문서, 레지스트리 `implemented` 기본 graph, 합성 원시 패널 위
  조합, 음성 사례

합성 패널은 `numpy.random.default_rng` 로 만들고, 셀 값은 (seed, 필드 id)만으로 정해진다. 그래서
질의 구간과 필드 순서가 달라도 같은 (날짜, 종목, 필드) 셀은 같다(창 불변성).

덮지 않는 것(SEMANTICS.md 항목 중):

- 연속 일정 컴파일러(`_scheduled_compiler.py`). T5-03 범위다.
- 후보 trace 의 `math.isclose` 허용오차(`_candidate_trace`) 경계에 걸리는 값.
- 취소·진행 보고(spec D7). 결과 값이 아니라 대조 대상이 아니다.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.factor_research.facade.ports import (
    FactorMetadataPort,
    FactorMetadataSnapshot,
)
from strategy_workbench.application.portfolio_design._service import _to_factor_observation
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioPipelineOptions,
    PortfolioStartingHolding,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawFieldValue,
    RawObservation,
    RawObservationPort,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import (
    FieldCatalogPort,
    SourceFormat,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DataLoadStatus,
    DatasetFieldProfile,
    DataSnapshot,
)
from strategy_workbench.domain.factor.facade.evaluation import FactorFieldValue, FactorObservation
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    CrossSectionalNode,
    CrossSectionalOperator,
    ExpressionNode,
    FactorComparisonOperator,
    FactorGraph,
    FieldNode,
    FillNode,
    GroupNode,
    GroupOperator,
    MissingPolicy,
    ParameterNode,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.planning import ResolvedFactorParameter
from strategy_workbench.domain.factor.facade.registry import (
    FactorAvailability,
    build_default_factor_registry,
    factor_availability,
)
from strategy_workbench.domain.factor.facade.trace import TraceSelection
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
    PortfolioTraceSelection,
)
from strategy_workbench.domain.strategy.facade.specification import (
    EligibilityOperator,
    EligibilityRule,
    EligibilityStep,
    FactorDirection,
    FactorSignal,
    PortfolioSide,
    PortfolioStep,
    RebalanceFrequency,
    RiskStep,
    SelectionMethod,
    SignalNormalization,
    SignalStep,
    StrategyIdentity,
    StrategySpec,
    WeightingMethod,
)
from tests.domain.test_factor_evaluation_version import _graphs

REGISTRY = build_default_factor_registry()
SECURITIES = tuple(f"S{index:02d}" for index in range(12))
SEEDS = (11, 23)
_DOCUMENTS = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_documents"
# mock 은 달력(2024-01-02~12) 밖을 절대 세션 번호로 합성한다. 두 구간을 다 지나게 잡는다.
MOCK_WINDOW = (date(2023, 7, 3), date(2024, 1, 12))


def _business_days(start: date, count: int) -> tuple[date, ...]:
    days: list[date] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


# 분기말(2023-12-29)이 L2 전체와 L3 측정 구간(SESSIONS[20]~) 안에 들어 분기 리밸런싱
# 프레임이 생긴다.
SESSIONS = _business_days(date(2023, 11, 20), 48)


def _rng(seed: int, name: str) -> np.random.Generator:
    """(seed, 이름)만으로 정해지는 난수열. 필드 순서·질의와 무관하다."""
    return np.random.default_rng([seed, zlib.crc32(name.encode())])


def _rows(seed: int, securities: int = len(SECURITIES)) -> tuple[np.ndarray, np.ndarray]:
    """행 유무와 구성원 여부(날짜 × 종목). 행 공백·상장·폐지·비구성원 변동을 넣는다."""
    rng = _rng(seed, "rows")
    days = np.arange(len(SESSIONS))
    present = np.ones((len(SESSIONS), securities), dtype=bool)
    present[:, 5] = rng.random(len(SESSIONS)) >= 0.25  # 행 공백
    present[:12, 6] = False  # 늦은 상장
    present[34:, 7] = False  # 폐지
    member = np.ones_like(present)
    member[:, 8] = rng.random(len(SESSIONS)) >= 0.3
    member[:, 9] = (days // 3) % 2 == 0  # 3세션마다 들고 난다
    return present, member


def _number(rng: np.random.Generator, security: int, day: int, *, extreme: bool) -> float:
    """종목마다 다른 값 성질. 정수·상수 창·극단 크기·±0·동점·연속 분포."""
    if security == 0:
        # 정수 값(정수 꼴 float — 원시 계약은 int 를 거절한다, T2-02), 4세션 상수 창
        return float((day // 4) % 5 - 2)
    if security == 1 and extreme:
        return float(rng.choice((-1.0, 1.0)) * rng.uniform(1.0, 9.0) * 1e300)
    if security == 2 and extreme:
        return float(rng.choice((5e-324, -5e-324, 1e-310, 2.5e-300, 7e-291)))
    if security == 3:
        return float(rng.choice((0.0, -0.0, 0.5, -0.5)))
    if security >= 10:
        return float(rng.normal(0.0, 1.0))
    return float(rng.integers(-6, 7)) / 4.0  # 동점이 많다


def _previous_weight(security: int) -> float:
    """첫 프레임 시드 비중. 작은 롱·이름 상한(0.12)보다 큰 롱·작은 숏·0 을 섞는다.

    최소 거래 조건의 세 갈래(측 호환, 상한 안, |Δ| < 문턱)가 각각 결과를 가르게 한다.
    """
    return (0.04, 0.15, -0.03, 0.0)[security % 4]


def _numeric_cell(
    rng: np.random.Generator, field_id: str, security: int, day: int, *, extreme: bool
) -> RawFieldValue | None:
    """셀 하나. None 이면 생략(셀 없음)이고, 값 None 셀(결측 사유)과 다르다."""
    value = _number(rng, security, day, extreme=extreme)
    draw = rng.random()
    available = SESSIONS[max(day - int(rng.integers(0, 3)), 0)]
    if security == 4 and 10 <= day < 17:
        return RawFieldValue(field_id, None, available, CellKind.MISSING)  # 결측 연속
    if draw < 0.04:
        return None
    if draw < 0.10:
        kind = (CellKind.MISSING, CellKind.NOT_COLLECTED, CellKind.COVERAGE_GAP)[day % 3]
        return RawFieldValue(field_id, None, available, kind)
    if draw < 0.14:
        return RawFieldValue(field_id, None, available, CellKind.MASKED)
    if draw < 0.16:
        return RawFieldValue(field_id, 0.0, available, CellKind.SOURCE_OMITTED_ZERO)
    return RawFieldValue(field_id, value, available)


def _group_cell(rng: np.random.Generator, field_id: str, day: int) -> RawFieldValue | None:
    draw = rng.random()
    if draw < 0.06:
        return None
    if draw < 0.12:
        return RawFieldValue(field_id, None, SESSIONS[day], CellKind.MISSING)  # 그룹 결측
    return RawFieldValue(field_id, str(rng.choice(("A", "B", "C"))), SESSIONS[day])


def synthetic_raw_panel(
    seed: int,
    field_ids: tuple[str, ...],
    *,
    group: str,
    extreme: str,
    securities: int = len(SECURITIES),
) -> tuple[RawObservation, ...]:
    """원시 관측 수준 합성 패널(전 달력). `group` 은 범주형 필드, `extreme` 은 극단 크기 필드다.

    `securities` 가 12 를 넘으면 늘어난 종목은 연속 분포 값이다(비트가 꽉 찬 점수).
    """
    present, member = _rows(seed, securities)
    cells: dict[str, list[list[RawFieldValue | None]]] = {}
    for field_id in sorted(set(field_ids) | {group}):
        rng = _rng(seed, field_id)
        cells[field_id] = [
            [
                _group_cell(rng, field_id, day)
                if field_id == group
                else _numeric_cell(rng, field_id, security, day, extreme=field_id == extreme)
                for security in range(securities)
            ]
            for day in range(len(SESSIONS))
        ]
    observations: list[RawObservation] = []
    for day, session in enumerate(SESSIONS):
        for security in range(securities):
            security_id = f"S{security:02d}"
            if not present[day, security]:
                continue
            fields = tuple(
                cell for name in field_ids if (cell := cells[name][day][security]) is not None
            )
            sector = cells[group][day][security]
            observations.append(
                RawObservation(
                    as_of=session,
                    security_id=security_id,
                    universe_member=bool(member[day, security]),
                    fields=fields,
                    sector_id=sector.value if sector and isinstance(sector.value, str) else None,
                    previous_weight=_previous_weight(security),
                )
            )
    return tuple(observations)


# --- L1 평가기 --------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluatorCase:
    key: str
    graph: FactorGraph
    observations: tuple[FactorObservation, ...]
    missing: MissingPolicy
    parameters: tuple[ResolvedFactorParameter, ...] = ()


def _extra_graphs() -> list[FactorGraph]:
    """판본 고정 묶음에 없는 조합: 넘침, 파라미터, 시계열→횡단면, lag 뒤 그룹 순위, 원소·lag,
    시계열 끝점·min/max."""
    x = FieldNode("x", "x", "field")
    y = FieldNode("y", "y", "field")
    return [
        FactorGraph((x, BinaryNode("out", BinaryOperator.MULTIPLY, "x", "x", "binary")), "out"),
        FactorGraph(
            (
                x,
                ParameterNode("scale", "scale", "parameter"),
                BinaryNode("out", BinaryOperator.MULTIPLY, "x", "scale", "binary"),
            ),
            "out",
        ),
        FactorGraph(
            (
                x,
                TimeSeriesNode("std", TimeSeriesOperator.STANDARD_DEVIATION, "x", 3, "time_series"),
                CrossSectionalNode("out", CrossSectionalOperator.ZSCORE, "std", "cross_sectional"),
            ),
            "out",
        ),
        FactorGraph(
            (
                y,
                UnaryNode("lag", UnaryOperator.LAG, "y", "unary", periods=1),
                GroupNode("out", GroupOperator.RANK, "lag", "sector", "group"),
            ),
            "out",
        ),
        *_elementwise_graphs(x, y),
        *_time_series_graphs(x, y),
        *_section_graphs(x, y),
    ]


def _elementwise_graphs(x: FieldNode, y: FieldNode) -> list[FactorGraph]:
    """열 평가기 골격(T3-01)이 다루는 원소·lag 노드만 쓰는 조합.

    lag 와 섞는 연산의 가림 구간, bool lag·bool 가지 조건, 숫자 predicate, −0.0 분모와 넘침,
    여러 노드가 넘칠 때 평가 순서상 첫 노드, 상수만 있는 그래프를 덮는다. `fill`(#608)은 가린 칸·
    숫자 자리의 bool·str 칸, lag 워밍업 칸, ÷0 칸(−0.0 으로 채움), "값이 있으면 1" 지시자를 덮는다.
    """

    def lag(node_id: str, source: str, periods: int) -> UnaryNode:
        return UnaryNode(node_id, UnaryOperator.LAG, source, "unary", periods=periods)

    def binary(node_id: str, operator: BinaryOperator, left: str, right: str) -> BinaryNode:
        return BinaryNode(node_id, operator, left, right, "binary")

    def compare(
        node_id: str, operator: FactorComparisonOperator, left: str, right: str
    ) -> ComparisonNode:
        return ComparisonNode(node_id, operator, left, right, "comparison")

    negate = UnaryNode("neg", UnaryOperator.NEGATE, "x", "unary")
    ratio = binary("ratio", BinaryOperator.DIVIDE, "y", "x")  # x 가 아주 작으면 넘친다
    square = binary("square", BinaryOperator.MULTIPLY, "x", "x")  # x 가 아주 크면 넘친다
    nodes: list[tuple[ExpressionNode, ...]] = [
        (x, lag("l3", "x", 3), binary("out", BinaryOperator.SUBTRACT, "x", "l3")),
        (
            x,
            lag("l1", "x", 1),
            binary("d", BinaryOperator.SUBTRACT, "x", "l1"),
            lag("out", "d", 2),
        ),
        (
            x,
            y,
            lag("ly", "y", 1),
            compare("gate", FactorComparisonOperator.GREATER_THAN, "x", "ly"),
            negate,
            ConstantNode("zero", 0.0, "constant"),
            ConditionalNode("out", "gate", "neg", "zero", "conditional"),
        ),
        (
            x,
            y,
            compare("gate", FactorComparisonOperator.GREATER_THAN_OR_EQUAL, "x", "y"),
            lag("out", "gate", 1),
        ),
        (
            x,
            y,
            compare("p", FactorComparisonOperator.LESS_THAN, "x", "y"),
            compare("t", FactorComparisonOperator.EQUAL, "x", "y"),
            compare("f", FactorComparisonOperator.LESS_THAN_OR_EQUAL, "x", "y"),
            ConditionalNode("out", "p", "t", "f", "conditional"),
        ),
        (x, y, ConditionalNode("out", "y", "x", "y", "conditional")),
        (x, y, negate, binary("out", BinaryOperator.DIVIDE, "neg", "y")),
        (x, y, negate, binary("out", BinaryOperator.DIVIDE, "y", "neg")),
        (x, y, ratio, square, binary("out", BinaryOperator.ADD, "ratio", "square")),
        (x, y, ratio, square, binary("out", BinaryOperator.SUBTRACT, "square", "ratio")),
        (ConstantNode("out", 1.5, "constant"),),
        (x, FillNode("out", "x", 0.0, "fill")),
        (x, lag("l2", "x", 2), FillNode("out", "l2", -1.5, "fill")),
        (x, y, binary("q", BinaryOperator.DIVIDE, "x", "y"), FillNode("out", "q", -0.0, "fill")),
        (
            x,
            compare("same", FactorComparisonOperator.EQUAL, "x", "x"),
            ConstantNode("one", 1.0, "constant"),
            ConditionalNode("p", "same", "one", "one", "conditional"),
            FillNode("out", "p", 0.0, "fill"),
        ),
    ]
    return [FactorGraph(graph, "out") for graph in nodes]


def _time_series_graphs(x: FieldNode, y: FieldNode) -> list[FactorGraph]:
    """열 평가기 시계열 끝점·min/max(T3-02)가 다루는 조합.

    창 1·2(양끝만)·20(상장 S06·폐지 S07·결측 연속 S04 경계에 걸친다), lag 0·5, S03 의 ±0 동점과
    first == 0, 시계열 → 시계열·이항 체인, bool 입력을 덮는다. 창 3·lag 1 은 판본 고정 묶음에 있다.
    """
    operators = (
        TimeSeriesOperator.MOMENTUM,
        TimeSeriesOperator.DELTA,
        TimeSeriesOperator.MINIMUM,
        TimeSeriesOperator.MAXIMUM,
    )

    def ts(
        node_id: str, operator: TimeSeriesOperator, source: str, window: int, lag: int = 0
    ) -> TimeSeriesNode:
        return TimeSeriesNode(node_id, operator, source, window, "time_series", lag=lag)

    nodes: list[tuple[ExpressionNode, ...]] = [
        *((x, ts("out", operator, "x", 2)) for operator in operators),
        *((x, ts("out", operator, "x", 20, lag=5)) for operator in operators),
        (x, ts("out", TimeSeriesOperator.MOMENTUM, "x", 1, lag=3)),
        (
            x,
            ts("low", TimeSeriesOperator.MINIMUM, "x", 4),
            ts("out", TimeSeriesOperator.DELTA, "low", 3, lag=2),
        ),
        (
            x,
            ts("high", TimeSeriesOperator.MAXIMUM, "x", 3, lag=1),
            ts("out", TimeSeriesOperator.MOMENTUM, "high", 5),
        ),
        # 오늘 값과 건너뛴 창을 섞으면 가림 구간이 둘을 덮는다
        (
            x,
            ts("mom", TimeSeriesOperator.MOMENTUM, "x", 6, lag=2),
            BinaryNode("out", BinaryOperator.SUBTRACT, "x", "mom", "binary"),
        ),
        (
            x,
            y,
            ts("drop", TimeSeriesOperator.DELTA, "y", 3),
            BinaryNode("out", BinaryOperator.DIVIDE, "drop", "x", "binary"),
        ),
        (
            x,
            y,
            ComparisonNode("gate", FactorComparisonOperator.GREATER_THAN, "x", "y", "comparison"),
            ts("out", TimeSeriesOperator.MAXIMUM, "gate", 2),
        ),
    ]
    return [FactorGraph(graph, "out") for graph in nodes]


def _section_graphs(x: FieldNode, y: FieldNode) -> list[FactorGraph]:
    """열 평가기 T3-05(시계열 mean·std, 횡단면·그룹 노드, 채움 자리)가 다루는 조합.

    창 1·2·20 의 mean·std(lag 포함), 횡단면 출력을 시계열이 읽는 사슬(채운 값이 창에 든다, #312
    리뷰 P2-3), 그룹 노드를 다시 횡단면·그룹으로 잇는 사슬, 분위 끝값 winsorize, lag 로 옮긴 가린
    칸 위의 그룹 노드, 원소 연산 뒤 그룹 노드를 덮는다. 결측 정책 4종과 곱해 채움 세 자리가 모두
    든다.
    """

    def ts(
        node_id: str, operator: TimeSeriesOperator, source: str, window: int, lag: int = 0
    ) -> TimeSeriesNode:
        return TimeSeriesNode(node_id, operator, source, window, "time_series", lag=lag)

    def cs(
        node_id: str,
        operator: CrossSectionalOperator,
        source: str,
        quantiles: tuple[float, float] = (0.01, 0.99),
    ) -> CrossSectionalNode:
        return CrossSectionalNode(node_id, operator, source, "cross_sectional", *quantiles)

    def group(node_id: str, operator: GroupOperator, source: str) -> GroupNode:
        return GroupNode(node_id, operator, source, "sector", "group")

    mean, std = TimeSeriesOperator.MEAN, TimeSeriesOperator.STANDARD_DEVIATION
    nodes: list[tuple[ExpressionNode, ...]] = [
        *((x, ts("out", operator, "x", window)) for operator in (mean, std) for window in (1, 2)),
        *((x, ts("out", operator, "x", 20, lag=2)) for operator in (mean, std)),
        (
            x,
            cs("z", CrossSectionalOperator.ZSCORE, "x"),
            ts("out", mean, "z", 3),
        ),
        (
            x,
            group("n", GroupOperator.NEUTRALIZE, "x"),
            ts("s", std, "n", 2),
            cs("out", CrossSectionalOperator.RANK, "s"),
        ),
        (
            x,
            group("n", GroupOperator.NEUTRALIZE, "x"),
            cs("out", CrossSectionalOperator.WINSORIZE, "n", (0.0, 1.0)),
        ),
        (
            x,
            cs("w", CrossSectionalOperator.WINSORIZE, "x", (0.25, 0.75)),
            group("out", GroupOperator.RANK, "w"),
        ),
        (
            x,
            UnaryNode("l", UnaryOperator.LAG, "x", "unary", periods=2),
            group("out", GroupOperator.NEUTRALIZE, "l"),
        ),
        (
            x,
            y,
            BinaryNode("d", BinaryOperator.SUBTRACT, "x", "y", "binary"),
            group("g", GroupOperator.RANK, "d"),
            cs("out", CrossSectionalOperator.DEMEAN, "g"),
        ),
        (
            x,
            ts("m", mean, "x", 2),
            cs("out", CrossSectionalOperator.ZSCORE, "m"),
        ),
        # `fill` 뒤 횡단면: 채운 칸은 모집단에 들고 가린 칸은 결측 정책 채움 자리로 간다(#608)
        (
            x,
            FillNode("f", "x", 0.0, "fill"),
            cs("out", CrossSectionalOperator.ZSCORE, "f"),
        ),
    ]
    return [FactorGraph(graph, "out") for graph in nodes]


def factor_panel(seed: int) -> tuple[FactorObservation, ...]:
    """L1 입력. S11 의 `x` 몇 칸에 bool·str 을 넣는다(숫자 입력 자리라 평가기가 None 으로 본다)."""
    raw = synthetic_raw_panel(seed, ("x", "y", "sector"), group="sector", extreme="x")
    rows = [_to_factor_observation(item, checkpoint=lambda: None) for item in raw]
    odd: dict[int, bool | str] = {2: True, 5: False, 9: "A"}
    for position, row in enumerate(rows):
        day = SESSIONS.index(row.as_of)
        if row.security_id == "S11" and day % 11 in odd:
            fields = tuple(
                replace(item, value=odd[day % 11])
                if item.field_id == "x" and not item.masked
                else item
                for item in row.fields
            )
            rows[position] = replace(row, fields=fields)
    return tuple(rows)


def evaluator_cases() -> list[EvaluatorCase]:
    graphs = [*_graphs(), *_extra_graphs()]
    parameters = (ResolvedFactorParameter("scale", 2.5),)
    cases: list[EvaluatorCase] = []
    for seed in SEEDS:
        observations = factor_panel(seed)
        for index, graph in enumerate(graphs):
            for policy in MissingPolicy:
                cases.append(
                    EvaluatorCase(
                        f"L1/seed={seed}/graph={index}/missing={policy.value}",
                        graph,
                        observations,
                        policy,
                        parameters,
                    )
                )
    return cases + _overflow_fill_cases()


def _overflow_fill_cases() -> list[EvaluatorCase]:
    """중앙값 채움이 넘쳐 출력 채움까지 ±inf·NaN 이 가는 사례(#598 리뷰 P1-1).

    S0·S1 의 중앙값 (a + b) / 2 가 넘쳐 S2 입력이 inf 로 채워지고, demean 은 −inf·NaN 을,
    winsorize 는 inf 를 낸다. 그래프 출력 채움은 출력 노드의 유한값 검사보다 먼저 돌아 그 값의
    중앙값을 구한다.
    실패는 첫 비유한 칸(node_id·as_of·security_id·value)까지 대조한다.
    """
    x = FieldNode("x", "x", "field")
    rows = tuple(
        FactorObservation(
            as_of=SESSIONS[0], security_id=security, fields=(FactorFieldValue("x", value),)
        )
        for security, value in (("S0", 1.7e308), ("S1", 1.6e308), ("S2", None))
    )
    operators = (CrossSectionalOperator.DEMEAN, CrossSectionalOperator.WINSORIZE)
    return [
        EvaluatorCase(
            f"L1/overflow-fill/{operator.value}/missing={policy.value}",
            FactorGraph(
                (x, CrossSectionalNode("out", operator, "x", "cross_sectional", 0.0, 1.0)), "out"
            ),
            rows,
            policy,
        )
        for operator in operators
        for policy in MissingPolicy
    ]


# --- L2 컴파일러 ------------------------------------------------------------------------------

# 합성 패널의 필드 이름 → 실제 필드 id. L2 는 이름 그대로, L3 는 mock 필드 계약을 쓴다.
FieldMap = dict[str, str]
_L2_FIELDS: FieldMap = {name: name for name in ("e1", "e2", "liq", "vol", "reg")}
_FACTOR_IDS = ("f1", "f2", "tb", "riskf")
# 2수준 열의 마스크. 4수준 둘(가중 = 비트 0·1, 정규화 = 비트 2·3)과 모두 직교한다.
_TWO_LEVEL_MASKS = (5, 6, 7, 9, 10, 11, 13, 14, 15)
# GF(4) 에서 α 를 곱한 값. 리밸런싱 = 가중 XOR α·정규화 는 L3 결측 정책(가중 XOR 정규화)과
# 직교하는 라틴 방격이라 리밸런싱이 가중·정규화·결측 정책 어느 쪽과도 겹치지 않는다.
_TIMES_ALPHA = (0, 2, 3, 1)
_REBALANCES = (
    RebalanceFrequency.EVERY_N_SESSIONS,
    RebalanceFrequency.WEEKLY,
    RebalanceFrequency.MONTHLY,
    RebalanceFrequency.QUARTERLY,
)
# 절대 규칙(연산자, 임계값). 행 번호 `(index + index // 4) % 6` 으로 고르면 가중 4수준과
# 모두 만난다.
_ABSOLUTE_RULES: tuple[tuple[EligibilityOperator, float] | None, ...] = (
    None,
    (EligibilityOperator.GREATER_THAN, -1.0),
    (EligibilityOperator.GREATER_THAN_OR_EQUAL, 0.5),
    (EligibilityOperator.LESS_THAN, 2.0),
    (EligibilityOperator.LESS_THAN_OR_EQUAL, 1.5),
    (EligibilityOperator.EQUAL, 1.0),
)


@dataclass(frozen=True)
class CompilerCase:
    key: str
    spec: StrategySpec
    sessions: tuple[date, ...]
    observations: tuple[PortfolioObservation, ...]


def _orthogonal_rows() -> list[tuple[int, int, tuple[int, ...]]]:
    """강도 2 직교 배열 OA(16) 과 그 접힘(fold-over)으로 32행. (가중, 정규화, 2수준 열들)."""
    rows = []
    for index in range(32):
        base, folded = index % 16, index >= 16
        bits = tuple((bin(base & mask).count("1") % 2) ^ folded for mask in _TWO_LEVEL_MASKS)
        rows.append((base & 3, (base >> 2) & 3, bits))
    return rows


def _field_signal(factor_id: str, direction: FactorDirection, weight: float) -> FactorSignal:
    graph = FactorGraph((FieldNode("value", "x", "field"),), "value")
    return FactorSignal(
        factor_id=factor_id, label=factor_id, direction=direction, weight=weight, graph=graph
    )


def combination_specs(
    fields: FieldMap, signal: Callable[[str, FactorDirection, float], FactorSignal]
) -> list[tuple[str, StrategySpec]]:
    """정규화 × 가중 × 선정 × 롱숏 × 버퍼·최소 거래·상한·섹터 중립·보조 순위·top_percent·top_count.

    전부 곱하지 않고 직교 배열 32행(`_orthogonal_rows`)만 쓴다. 두 열의 모든 수준 쌍이 나온다.
    절대 규칙은 행 번호로 돌려 쓰고, 보조 방향은 접힘 비트에서 고른다. 유동성·레짐·역가중 원천은
    접힘 비트와 4수준 열의 비트를 XOR 한 2수준 열이라 가중·정규화·리밸런싱·결측 정책의 모든 수준과
    두 값 모두 만난다(#573 리뷰 P3: `index // 4` 는 정규화 수준과 묶여 있었다).
    """
    specs: list[tuple[str, StrategySpec]] = []
    for index, (weighting, normalization, bits) in enumerate(_orthogonal_rows()):
        percentile, short, buffer, trade, cap, neutral, tie, top_percent, top_count = bits
        method = tuple(WeightingMethod)[weighting]
        folded = index >= 16
        # RISK 행의 절반은 팩터, 절반은 필드(`risk_field_id`)로 역가중한다. RISK 행 안에서도
        # 정규화 수준마다 두 갈래가 다 나온다. 다만 RISK 행 8개 안에서는 이 열이 2수준 열 trade·
        # cap(반대 방향)·neutral(같은 방향)과 완전히 묶인다(OA 접힘의 성질, #577 리뷰 P3-1).
        risk_factor = method is WeightingMethod.RISK and folded ^ bool(normalization >> 1)
        liquidity = folded ^ bool(weighting & 1)
        regime = folded ^ bool(normalization & 1)
        factors = [signal("f1", FactorDirection.HIGH, 0.6), signal("f2", FactorDirection.LOW, 0.4)]
        factors += [signal("tb", FactorDirection.LOW, 1.0)] if tie else []
        factors += [signal("riskf", FactorDirection.LOW, 1.0)] if risk_factor else []
        absolute = _ABSOLUTE_RULES[(index + index // 4) % len(_ABSOLUTE_RULES)]
        rules = [] if absolute is None else [EligibilityRule(fields["e1"], *absolute)]
        rules += [EligibilityRule(fields["e1"], EligibilityOperator.TOP_PERCENT, 0.6)] * top_percent
        rules += [EligibilityRule(fields["e2"], EligibilityOperator.TOP_COUNT, 7)] * top_count
        rebalance = _REBALANCES[weighting ^ _TIMES_ALPHA[normalization]]
        spec = StrategySpec(
            identity=StrategyIdentity(strategy_id=f"parity-{index}", revision=1),
            title=f"대조 조합 {index}",
            eligibility=EligibilityStep(tuple(rules)),
            factors=tuple(factors),
            signal=SignalStep(
                normalization=(
                    SignalNormalization.NONE,
                    SignalNormalization.RANK,
                    SignalNormalization.ZSCORE,
                    SignalNormalization.RANK,
                )[normalization],
                score_threshold=0.1 if index % 5 == 0 else None,
                regime_field_id=fields["reg"] if regime else None,
                regime_minimum=-1.0 if regime else None,
            ),
            portfolio=PortfolioStep(
                side=PortfolioSide.LONG_SHORT if short else PortfolioSide.LONG_ONLY,
                selection_count=4,
                short_selection_count=3,
                selection_method=SelectionMethod.PERCENTILE
                if percentile
                else SelectionMethod.TOP_N,
                selection_percentile=0.3,
                weighting=method,
                rebalance=rebalance,
                rebalance_every_n_sessions=5,
                turnover_buffer_count=2 * buffer,
                minimum_trade_weight=0.05 * trade,
                liquidity_field_id=fields["liq"] if liquidity else None,
                minimum_liquidity=0.0 if liquidity else None,
                tie_breaker_factor_id="tb" if tie else None,
                tie_breaker_direction=(
                    FactorDirection.LOW if tie and index >= 16 else FactorDirection.HIGH
                ),
            ),
            risk=RiskStep(
                gross_exposure=1.6 if short else 1.0,
                net_exposure=(0.0, 0.2)[index % 2] if short else 1.0,
                max_name_weight=0.12 if cap else 1.0,
                # 섹터 중립은 롱숏에서만 쓰인다(validator). 롱 전용이면 그 열이 섹터 상한을 조인다.
                max_sector_weight=0.5 if cap or (neutral and not short) else 1.0,
                sector_neutral=bool(neutral and short),
                risk_field_id=fields["vol"]
                if method is WeightingMethod.RISK and not risk_factor
                else None,
                risk_factor_id="riskf" if risk_factor else None,
            ),
        )
        specs.append((f"combo={index}", spec))
    return specs


def _portfolio_value(
    rng: np.random.Generator, security: int, day: int, *, positive: bool
) -> float | None:
    if rng.random() < 0.08:
        return None
    if positive:
        return float(rng.choice((0.5, 1.0, 1.5, 2.0, 3.0, 0.0, -1.0)))
    return _number(rng, security, day, extreme=True)


def portfolio_panel(seed: int) -> tuple[PortfolioObservation, ...]:
    """컴파일러 입력 수준 합성 패널. 늦은 공개(FUTURE_DATA)·bool 필드·섹터 결측을 더 넣는다."""
    present, member = _rows(seed)
    factor_rngs = {name: _rng(seed, name) for name in _FACTOR_IDS}
    field_rngs = {name: _rng(seed, name) for name in _L2_FIELDS}
    sector_rng = _rng(seed, "sector")
    observations: list[PortfolioObservation] = []
    for day, session in enumerate(SESSIONS):
        for security, security_id in enumerate(SECURITIES):
            factors = tuple(
                PortfolioFactorValue(
                    name,
                    _portfolio_value(rng, security, day, positive=name == "riskf"),
                    session + timedelta(days=1) if rng.random() < 0.03 else session,
                )
                for name, rng in factor_rngs.items()
            )
            fields = tuple(
                PortfolioFieldValue(
                    name,
                    True if name == "e1" and rng.random() < 0.02 else value,
                    session + timedelta(days=1) if rng.random() < 0.02 else session,
                )
                for name, rng in field_rngs.items()
                if rng.random() >= 0.04
                for value in (_portfolio_value(rng, security, day, positive=name != "e2"),)
            )
            sector = sector_rng.choice(("A", "B", "C", "none"))
            if present[day, security]:
                observations.append(
                    PortfolioObservation(
                        as_of=session,
                        security_id=security_id,
                        universe_member=bool(member[day, security]),
                        factor_values=factors,
                        fields=fields,
                        sector_id=None if sector == "none" else str(sector),
                        previous_weight=_previous_weight(security),
                    )
                )
    return tuple(observations)


def _overflow_case(panel: tuple[PortfolioObservation, ...]) -> CompilerCase:
    """합성 점수가 넘치는 음성 사례. 가중 1 인 두 팩터 값이 모두 1.7e308 이면 합이 inf 다."""
    factors = tuple(_field_signal(name, FactorDirection.HIGH, 1.0) for name in ("f1", "f2"))
    spec = StrategySpec(
        identity=StrategyIdentity(strategy_id="parity-overflow", revision=1),
        title="넘침",
        factors=factors,
        signal=SignalStep(normalization=SignalNormalization.NONE),
    )
    huge = tuple(
        replace(
            row,
            factor_values=tuple(
                replace(value, value=1.7e308, available_date=row.as_of)
                if value.factor_id in {"f1", "f2"}
                else value
                for value in row.factor_values
            ),
        )
        if row.security_id == "S00"
        else row
        for row in panel
    )
    return CompilerCase("L2/negative/overflow", spec, SESSIONS, huge)


def compiler_cases() -> list[CompilerCase]:
    panels = {seed: portfolio_panel(seed) for seed in SEEDS}
    cases = [
        CompilerCase(f"L2/{key}/seed={seed}", spec, SESSIONS, panels[seed])
        for position, (key, spec) in enumerate(combination_specs(_L2_FIELDS, _field_signal))
        for seed in (SEEDS[position % len(SEEDS)],)
    ]
    return [*cases, _overflow_case(panels[SEEDS[0]])]


# --- L3 파이프라인 ----------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineCase:
    key: str
    spec: StrategySpec
    environment: RunEnvironment
    # "mock" 또는 "synthetic"·"synthetic-<변형>"(`SyntheticRawSource` 의 `variant`).
    # 원천은 `_engines.pipeline_source` 가 만든다.
    source: str
    options: PortfolioPipelineOptions = field(default_factory=PortfolioPipelineOptions)


class SyntheticRawSource:
    """합성 원시 패널을 질의 구간대로 잘라 주는 원시 관측 포트. 필드 계약·스냅숏은 mock 의 것이다.

    `variant` 는 "" (12종목), "wide"(40종목), 그리고 관문 음성 사례다.

    - "lookahead": 첫 관측의 첫 셀 공개일을 하루 뒤로 밀어 소비 경계 위반을 만든다.
    - "snapshot": 필드 계약과 다른 스냅숏 id 로 답한다.
    - "outside": 질의 끝을 무시하고 달력 끝까지 세션을 답한다(구간 밖 세션).
    """

    def __init__(self, seed: int, *, variant: str = "") -> None:
        self._mock = MockEquityDataAdapter.demo()
        self._seed = seed
        self._variant = variant
        self._securities = 40 if variant == "wide" else len(SECURITIES)

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        return self._mock.resolve_factor_fields(field_ids)

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        return self.load_raw_observations_cancellable(query, checkpoint=lambda: None)

    def load_raw_observations_cancellable(
        self, query: RawObservationQuery, *, checkpoint: Callable[[], None]
    ) -> RawObservationSet:
        end = SESSIONS[-1] if self._variant == "outside" else query.end
        requested = tuple(day for day in SESSIONS if query.start <= day <= end)
        before = [day for day in SESSIONS if day < query.start]
        history = tuple(before[len(before) - query.history_sessions_before_start :])
        dates = set(history + requested)
        panel = synthetic_raw_panel(
            self._seed,
            query.field_ids,
            group=SYNTHETIC_FIELDS["sector"],
            extreme=_L3_EXTREME,
            securities=self._securities,
        )
        rows = [item for item in panel if item.as_of in dates]
        if self._variant == "lookahead" and rows and rows[0].fields:
            first = rows[0].fields[0]
            late = replace(first, available_date=rows[0].as_of + timedelta(days=1))
            rows[0] = replace(rows[0], fields=(late, *rows[0].fields[1:]))
        snapshot = self._mock.snapshot().snapshot_id
        return RawObservationSet(
            status=DataLoadStatus.OK,
            data_snapshot_id=f"{snapshot}-stale" if self._variant == "snapshot" else snapshot,
            sessions=requested,
            history_sessions=history,
            observations=tuple(rows),
            validation_checkpoint=checkpoint,
        )


SYNTHETIC_FIELDS: FieldMap = {
    "x": "price.adj_close",
    "y": "price.market_cap",
    "sector": "classification.sector",
    "e1": "price.market_cap",
    "e2": "price.trading_value",
    "liq": "price.trading_value",
    "vol": "short.short_balance_ratio",
    "reg": "flow.foreign_ownership",
}
_L3_EXTREME = SYNTHETIC_FIELDS["y"]
_SYNTHETIC_ENVIRONMENT = RunEnvironment(
    start=SESSIONS[20], end=SESSIONS[-1], universe_id="krx.common-stock"
)


def _synthetic_signal(factor_id: str, direction: FactorDirection, weight: float) -> FactorSignal:
    """L3 합성 조합의 팩터 graph. 팩터마다 다른 연산 경로를 지난다."""
    x = FieldNode("x", SYNTHETIC_FIELDS["x"], "field")
    y = FieldNode("y", SYNTHETIC_FIELDS["y"], "field")
    graphs = {
        "f1": FactorGraph(
            (x, TimeSeriesNode("out", TimeSeriesOperator.MOMENTUM, "x", 3, "time_series")), "out"
        ),
        "f2": FactorGraph(
            (
                y,
                GroupNode(
                    "out", GroupOperator.NEUTRALIZE, "y", SYNTHETIC_FIELDS["sector"], "group"
                ),
            ),
            "out",
        ),
        "tb": FactorGraph((FieldNode("out", SYNTHETIC_FIELDS["e2"], "field"),), "out"),
        "riskf": FactorGraph(
            (
                x,
                TimeSeriesNode("out", TimeSeriesOperator.STANDARD_DEVIATION, "x", 5, "time_series"),
            ),
            "out",
        ),
    }
    return FactorSignal(
        factor_id=factor_id,
        label=factor_id,
        direction=direction,
        weight=weight,
        graph=graphs[factor_id],
    )


class EquityAdapter(RawObservationPort, FactorMetadataPort, FieldCatalogPort, Protocol):
    """대조에 쓰는 equity 어댑터(mock·duckdb). 원시 관측·필드 계약·스냅숏을 한 객체가 답한다."""

    def snapshot(self) -> DataSnapshot: ...

    def list_fields(self) -> tuple[DatasetFieldProfile, ...]: ...


def fixture_specs(adapter: EquityAdapter) -> list[tuple[str, StrategySpec]]:
    """`fixtures/strategy_documents/**` 가운데 `adapter` 필드 계약으로 compile 되는 문서."""
    authoring = StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version=REGISTRY.version,
        dataset_snapshot_id=lambda: adapter.snapshot().snapshot_id,
        field_catalog=adapter,
    )
    specs: list[tuple[str, StrategySpec]] = []
    for path in sorted(_DOCUMENTS.rglob("*")):
        source_format = {".yaml": SourceFormat.YAML, ".json": SourceFormat.JSON}.get(path.suffix)
        if source_format is None:
            continue
        compiled = authoring.compile(
            CompileRequest(path.read_text(encoding="utf-8"), source_format)
        )
        if compiled.spec is not None:
            specs.append((path.relative_to(_DOCUMENTS).as_posix(), compiled.spec))
    return specs


def registry_specs(provided: Collection[str]) -> list[tuple[str, StrategySpec]]:
    """`provided` 필드로 도는 레지스트리 `implemented` 기본 graph 를 최소 전략으로 감싼다."""
    specs: list[tuple[str, StrategySpec]] = []
    for definition in REGISTRY.all():
        graph = definition.default_graph
        if (
            graph is None
            or factor_availability(definition, provided) is not FactorAvailability.IMPLEMENTED
        ):
            continue
        # 레지스트리 선호(high·low)와 전략 방향은 같은 어휘다.
        direction = FactorDirection(definition.preference.value)
        signal = FactorSignal(
            factor_id=definition.factor_id, label=definition.label, direction=direction, graph=graph
        )
        spec = StrategySpec(
            identity=StrategyIdentity(strategy_id=definition.factor_id, revision=1),
            title=definition.label,
            factors=(signal,),
            portfolio=PortfolioStep(
                selection_count=2,
                rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
                rebalance_every_n_sessions=5,
            ),
            risk=RiskStep(max_name_weight=1.0, max_sector_weight=1.0),
        )
        specs.append((definition.factor_id, spec))
    return specs


def _overflow_spec() -> StrategySpec:
    y = FieldNode("y", SYNTHETIC_FIELDS["y"], "field")
    graph = FactorGraph((y, BinaryNode("out", BinaryOperator.MULTIPLY, "y", "y", "binary")), "out")
    signal = FactorSignal(
        factor_id="overflow", label="넘침", direction=FactorDirection.HIGH, graph=graph
    )
    return StrategySpec(identity=StrategyIdentity("overflow", 1), title="넘침", factors=(signal,))


def _wide_specs() -> list[tuple[str, StrategySpec]]:
    """상한 배분의 합·예산 차감 순서가 결과 비트에 드러나는 넓은 사례(DEFECT-CT-02).

    40종목에서 20개를 고르고, 표준화 점수라 비트가 꽉 찬다. 상한은 일부 종목에만 걸린다.
    """
    level = FactorSignal(
        factor_id="level",
        label="level",
        direction=FactorDirection.HIGH,
        graph=FactorGraph((FieldNode("x", SYNTHETIC_FIELDS["x"], "field"),), "x"),
    )
    variants = (
        (WeightingMethod.FACTOR_SCORE, (level,), None),
        (
            WeightingMethod.RISK,
            (level, _synthetic_signal("riskf", FactorDirection.LOW, 1.0)),
            "riskf",
        ),
    )
    return [
        (
            f"weighting={weighting.value}",
            StrategySpec(
                identity=StrategyIdentity(strategy_id=f"wide-{weighting.value}", revision=1),
                title="넓은 상한 배분",
                factors=factors,
                signal=SignalStep(normalization=SignalNormalization.ZSCORE),
                portfolio=PortfolioStep(
                    selection_count=20,
                    weighting=weighting,
                    rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
                    rebalance_every_n_sessions=5,
                ),
                risk=RiskStep(max_name_weight=0.15, max_sector_weight=1.0, risk_factor_id=risk),
            ),
        )
        for weighting, factors, risk in variants
    ]


def adapter_cases(
    adapter: EquityAdapter, environment: RunEnvironment, *, source: str
) -> list[PipelineCase]:
    """어댑터 필드 계약으로 도는 fixture 문서와 레지스트리 graph. 실원장 대조도 이 사례를 쓴다."""
    fields = [profile.field_id for profile in adapter.list_fields()]
    named = (("fixture", fixture_specs(adapter)), ("registry", registry_specs(fields)))
    return [
        PipelineCase(f"L3/{kind}/{key}", spec, environment, source)
        for kind, specs in named
        for key, spec in specs
    ]


def pipeline_cases() -> list[PipelineCase]:
    mock = RunEnvironment(start=MOCK_WINDOW[0], end=MOCK_WINDOW[1], universe_id="krx.common-stock")
    policies = tuple(MissingPolicy)
    cases = adapter_cases(MockEquityDataAdapter.demo(), mock, source="mock")
    combinations = combination_specs(SYNTHETIC_FIELDS, _synthetic_signal)
    for index, (key, spec) in enumerate(combinations):
        # 가중 XOR 정규화는 라틴 방격이라 결측 정책이 두 열·리밸런싱과 모두 직교한다.
        missing = policies[(index ^ (index >> 2)) & 3]
        environment = replace(_SYNTHETIC_ENVIRONMENT, missing=missing)
        cases.append(PipelineCase(f"L3/synthetic/{key}", spec, environment, "synthetic"))
    traced = cases[-1]
    # 최소 거래·이름 상한이 켜진 조합에 시작 보유를 준다. 목록에 없는 종목(0.0)과 관측에 없는
    # 종목(ZZ99)이 함께 든다. 유동성 거르기는 합성 거래대금에서 후보를 거의 다 떨어뜨리고, 월·분기
    # 리밸런싱은 측정 구간에 프레임이 한두 개라 선정이 빌 수 있어 그 조합은 고르지 않는다.
    held = next(
        case
        for case in cases
        if case.key.startswith("L3/synthetic/")
        and case.spec.portfolio.minimum_trade_weight > 0
        and case.spec.risk.max_name_weight < 1
        and case.spec.portfolio.liquidity_field_id is None
        and case.spec.portfolio.rebalance
        in (RebalanceFrequency.EVERY_N_SESSIONS, RebalanceFrequency.WEEKLY)
    )
    holdings = (("S01", 0.14), ("S02", -0.02), ("S05", 0.03), ("ZZ99", 0.1))
    cases.append(
        replace(
            held,
            key=f"{held.key}/holdings",
            options=PortfolioPipelineOptions(
                starting_holdings=tuple(PortfolioStartingHolding(*item) for item in holdings)
            ),
        )
    )
    cases += [
        PipelineCase(f"L3/wide/{key}", spec, _SYNTHETIC_ENVIRONMENT, "synthetic-wide")
        for key, spec in _wide_specs()
    ]
    cases.append(
        replace(
            traced,
            key=f"{traced.key}/trace",
            options=PortfolioPipelineOptions(
                trace_factor_id="f1",
                trace_selection=TraceSelection(max_rows=400),
                construction_trace_selection=PortfolioTraceSelection(
                    as_of=None, security_ids=("S00", "S03"), include_order_delta=True
                ),
            ),
        )
    )
    cases.append(
        PipelineCase("L3/negative/overflow", _overflow_spec(), _SYNTHETIC_ENVIRONMENT, "synthetic")
    )
    cases.append(replace(traced, key="L3/negative/lookahead", source="synthetic-lookahead"))
    cases.append(replace(traced, key="L3/negative/snapshot", source="synthetic-snapshot"))
    cases.append(
        replace(
            traced,
            key="L3/negative/outside-range",
            environment=replace(traced.environment, end=SESSIONS[-3]),
            source="synthetic-outside",
        )
    )
    cases.append(
        replace(
            traced,
            key="L3/negative/trace-scope",
            options=PortfolioPipelineOptions(
                construction_trace_selection=PortfolioTraceSelection(
                    as_of=None, security_ids=("S99",), include_order_delta=False
                )
            ),
        )
    )
    return cases
