"""대조 하네스 말뭉치(columnar-tape spec 5절). 모든 사례는 이 모듈이 직접 만든 입력이다.

- L1 평가기: 연산자 그래프(`test_factor_evaluation_version._graphs` + 확장) × 결측 정책 4종
  × 합성 패널
- L2 컴파일러: 직교 표본 32개 조합 × 합성 `PortfolioObservation` 패널
- L3 파이프라인: fixture 전략 문서, 레지스트리 `implemented` 기본 graph, 합성 원시 패널 위
  조합, 음성 사례

합성 패널은 `numpy.random.default_rng` 로 만들고, 셀 값은 (seed, 필드 id)만으로 정해진다. 그래서
질의 구간과 필드 순서가 달라도 같은 (날짜, 종목, 필드) 셀은 같다(창 불변성).
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
from strategy_workbench.domain.factor.facade.evaluation import FactorObservation
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    CrossSectionalNode,
    CrossSectionalOperator,
    FactorGraph,
    FieldNode,
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


SESSIONS = _business_days(date(2024, 1, 1), 48)


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
        return int((day // 4) % 5) - 2  # 정수 값, 4세션 상수 창
    if security == 1 and extreme:
        return float(rng.choice((-1.0, 1.0)) * rng.uniform(1.0, 9.0) * 1e300)
    if security == 2 and extreme:
        return float(rng.choice((5e-324, -5e-324, 1e-310, 2.5e-300, 7e-291)))
    if security == 3:
        return float(rng.choice((0.0, -0.0, 0.5, -0.5)))
    if security >= 10:
        return float(rng.normal(0.0, 1.0))
    return float(rng.integers(-6, 7)) / 4.0  # 동점이 많다


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
                    previous_weight=0.04 if security % 4 == 0 else 0.0,
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
    """판본 고정 묶음에 없는 조합: 넘침, 파라미터, 시계열→횡단면 연쇄, lag 뒤 그룹 순위."""
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
    ]


def factor_panel(seed: int) -> tuple[FactorObservation, ...]:
    raw = synthetic_raw_panel(seed, ("x", "y", "sector"), group="sector", extreme="x")
    return tuple(_to_factor_observation(item, checkpoint=lambda: None) for item in raw)


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
    return cases


# --- L2 컴파일러 ------------------------------------------------------------------------------

# 합성 패널의 필드 이름 → 실제 필드 id. L2 는 이름 그대로, L3 는 mock 필드 계약을 쓴다.
FieldMap = dict[str, str]
_L2_FIELDS: FieldMap = {name: name for name in ("e1", "e2", "liq", "vol", "reg")}
_FACTOR_IDS = ("f1", "f2", "tb", "riskf")
# 2수준 열의 마스크. 4수준 둘(가중 = 비트 0·1, 정규화 = 비트 2·3)과 모두 직교한다.
_TWO_LEVEL_MASKS = (5, 6, 7, 9, 10, 11, 13, 14, 15)


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
    """
    specs: list[tuple[str, StrategySpec]] = []
    for index, (weighting, normalization, bits) in enumerate(_orthogonal_rows()):
        percentile, short, buffer, trade, cap, neutral, tie, top_percent, top_count = bits
        method = tuple(WeightingMethod)[weighting]
        risk_factor = method is WeightingMethod.RISK and index % 2 == 1
        factors = [signal("f1", FactorDirection.HIGH, 0.6), signal("f2", FactorDirection.LOW, 0.4)]
        factors += [signal("tb", FactorDirection.LOW, 1.0)] if tie else []
        factors += [signal("riskf", FactorDirection.LOW, 1.0)] if risk_factor else []
        rules = [EligibilityRule(fields["e1"], EligibilityOperator.GREATER_THAN, -1.0)]
        rules = rules if index % 3 == 1 else []
        rules += [EligibilityRule(fields["e1"], EligibilityOperator.TOP_PERCENT, 0.6)] * top_percent
        rules += [EligibilityRule(fields["e2"], EligibilityOperator.TOP_COUNT, 7)] * top_count
        rebalance = (
            RebalanceFrequency.EVERY_N_SESSIONS,
            RebalanceFrequency.WEEKLY,
            RebalanceFrequency.MONTHLY,
        )[index % 3]
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
                regime_field_id=fields["reg"] if index % 6 == 2 else None,
                regime_minimum=-1.0 if index % 6 == 2 else None,
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
                minimum_trade_weight=0.01 * trade,
                liquidity_field_id=fields["liq"] if index % 4 == 1 else None,
                minimum_liquidity=0.0 if index % 4 == 1 else None,
                tie_breaker_factor_id="tb" if tie else None,
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
                        previous_weight=0.05 if security % 4 == 0 else 0.0,
                    )
                )
    return tuple(observations)


def compiler_cases() -> list[CompilerCase]:
    panels = {seed: portfolio_panel(seed) for seed in SEEDS}
    return [
        CompilerCase(f"L2/{key}/seed={seed}", spec, SESSIONS, panels[seed])
        for position, (key, spec) in enumerate(combination_specs(_L2_FIELDS, _field_signal))
        for seed in (SEEDS[position % len(SEEDS)],)
    ]


# --- L3 파이프라인 ----------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineCase:
    key: str
    spec: StrategySpec
    environment: RunEnvironment
    # "mock" 또는 `SyntheticRawSource` 의 "synthetic"(12종목)·"synthetic-wide"(40종목)·
    # "synthetic-lookahead"(공개일 위반 음성 사례). 원천은 `_engines.pipeline_source` 가 만든다.
    source: str
    options: PortfolioPipelineOptions = field(default_factory=PortfolioPipelineOptions)


class SyntheticRawSource:
    """합성 원시 패널을 질의 구간대로 잘라 주는 원시 관측 포트. 필드 계약·스냅숏은 mock 의 것이다.

    `lookahead` 면 첫 관측의 첫 셀 공개일을 하루 뒤로 밀어 소비 경계 위반을 만든다.
    """

    def __init__(
        self, seed: int, *, lookahead: bool = False, securities: int = len(SECURITIES)
    ) -> None:
        self._mock = MockEquityDataAdapter.demo()
        self._seed = seed
        self._lookahead = lookahead
        self._securities = securities

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        return self._mock.resolve_factor_fields(field_ids)

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        return self.load_raw_observations_cancellable(query, checkpoint=lambda: None)

    def load_raw_observations_cancellable(
        self, query: RawObservationQuery, *, checkpoint: Callable[[], None]
    ) -> RawObservationSet:
        requested = tuple(day for day in SESSIONS if query.start <= day <= query.end)
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
        if self._lookahead and rows and rows[0].fields:
            first = rows[0].fields[0]
            late = replace(first, available_date=rows[0].as_of + timedelta(days=1))
            rows[0] = replace(rows[0], fields=(late, *rows[0].fields[1:]))
        return RawObservationSet(
            status=DataLoadStatus.OK,
            data_snapshot_id=self._mock.snapshot().snapshot_id,
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
        # (가중 + 정규화) mod 4 는 라틴 방격이라 결측 정책이 두 열과 모두 직교한다.
        missing = policies[(index + index // 4) % len(policies)]
        environment = replace(_SYNTHETIC_ENVIRONMENT, missing=missing)
        cases.append(PipelineCase(f"L3/synthetic/{key}", spec, environment, "synthetic"))
    traced = cases[-1]
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
    return cases
