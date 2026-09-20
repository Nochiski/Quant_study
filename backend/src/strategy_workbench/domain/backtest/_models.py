from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, get_args

from strategy_workbench.domain.analytics.facade.metrics import (
    DrawdownPoint,
    EquityCurvePoint,
    MetricDefinition,
    MetricScope,
    MetricValue,
    MonthlyReturnPoint,
    RollingMetricPoint,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.strategy.facade.constraints import (
    ScalarConstraint,
    scalar_constraint_index,
)
from strategy_workbench.domain.strategy.facade.provenance import (
    StrategyProvenance,
    StrategySource,
)
from strategy_workbench.domain.strategy.facade.specification import (
    CATALOG_UNIVERSE,
    DataFrequency,
    ExecutionTiming,
    Market,
    StrategySpec,
)


class ExecutionCore(StrEnum):
    RUST = "rust"
    PYTHON = "python"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class WarningSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"


# 실행 설정 수치 필드의 범위는 전략 제약 카탈로그(`domain/strategy/_constraints.py`)의
# `/execution/*` 행이 이미 SoT 다. 값을 여기에 다시 적지 않고 필드 이름으로만 다시 건다 —
# 같은 수치를 두 곳에 두면 한쪽이 조용히 stale 된다. `__post_init__` 검증과 런타임 스키마의
# `minimum`/`maximum` 이 같은 행을 읽는다. P2-03 이 `execution` 섹션을 지울 때 이 행들의 최종
# owner 를 `domain/backtest` 로 옮긴다.
NUMERIC_ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "participation_rate",
    "fee_bps",
    "slippage_bps",
)
_LEGACY_EXECUTION_POINTERS: dict[str, str] = {
    name: f"/execution/{name}" for name in NUMERIC_ENVIRONMENT_FIELDS
}
RUN_ENVIRONMENT_CONSTRAINTS: dict[str, ScalarConstraint] = {
    name: scalar_constraint_index()[pointer] for name, pointer in _LEGACY_EXECUTION_POINTERS.items()
}


def _describe_bound(constraint: ScalarConstraint) -> str:
    """`0 < x <= 1` 모양의 기대 범위 문장(진단 메시지용)."""
    parts: list[str] = []
    if constraint.minimum is not None:
        parts.append(f"{constraint.minimum} {'<' if constraint.exclusive_minimum else '<='} x")
    if constraint.maximum is not None:
        parts.append(f"x {'<' if constraint.exclusive_maximum else '<='} {constraint.maximum}")
    return " and ".join(parts) if parts else "any finite number"


@dataclass(frozen=True, kw_only=True)
class RunEnvironment:
    """한 번의 실행이 놓인 환경 — 시장·빈도·기간·유니버스·체결·비용·결측 정책(spec D6).

    전략 문서가 아니라 실행이 소유하는 사실이다. 같은 전략을 다른 기간·유니버스·수수료로
    돌려도 `spec_hash` 는 그대로고 `environment_hash` 만 갈린다. 그래서 실행 설정을 바꿔도
    전략 revision 이 늘지 않는다.

    enum 은 현재 소유 위치(`domain.strategy` 의 `Market`·`DataFrequency`·`ExecutionTiming`,
    `domain.factor` 의 `MissingPolicy`)를 그대로 읽는다. 물리 이동은 `DataStep`·`ExecutionStep`
    이 사라지는 P2-03 이다 — 지금 옮기면 `domain.strategy` 가 재수출해야 하고 의존 화살표가
    순환한다.
    """

    market: Market = Market.KRX
    frequency: DataFrequency = DataFrequency.DAILY
    start: date
    end: date
    universe_id: str = field(metadata=CATALOG_UNIVERSE)
    timing: ExecutionTiming = ExecutionTiming.NEXT_OPEN
    participation_rate: float = 0.1
    fee_bps: float = 15.0
    slippage_bps: float = 10.0
    missing: MissingPolicy = MissingPolicy.DROP

    def __post_init__(self) -> None:
        # 수치는 float 으로 정규화한다. `fee_bps=15` 와 `fee_bps=15.0` 은 같은 실행 설정인데
        # canonical JSON 이 `15` 와 `15.0` 으로 갈려 `environment_hash` 가 달라진다.
        for name in NUMERIC_ENVIRONMENT_FIELDS:
            object.__setattr__(self, name, float(getattr(self, name)))
        if self.start > self.end:
            raise ValueError(
                "run environment end must be on or after start — "
                f"start={self.start} end={self.end} universe_id={self.universe_id!r}"
            )
        if not self.universe_id.strip():
            raise ValueError(
                "run environment requires a universe id — "
                f"universe_id={self.universe_id!r} range={self.start}..{self.end}"
            )
        for name, constraint in RUN_ENVIRONMENT_CONSTRAINTS.items():
            value = getattr(self, name)
            if not constraint.satisfied_by(value):
                raise ValueError(
                    "run environment value is out of range — "
                    f"field={name} value={value!r} expected={_describe_bound(constraint)} "
                    f"({constraint.message})"
                )


@dataclass(frozen=True)
class MetricWindow:
    scope: MetricScope
    start: date
    end: date
    label: str | None = None

    def __post_init__(self) -> None:
        if self.scope is MetricScope.FULL:
            raise ValueError("full scope is implicit and cannot be configured as a window")
        if self.start > self.end:
            raise ValueError("metric window start must be before or equal to end")


@dataclass(frozen=True)
class BacktestRunSpec:
    """A request names its strategy once: `strategy` (legacy inline spec) or `strategy_source`.

    `strategy` stays for compatibility with the JSON editors; new callers use `strategy_source`
    so a run can name a saved revision (P1-09). The run service rejects a request carrying both
    or neither (one coded 422, `backtest.run.invalid`).
    After resolution the run spec stored in the manifest carries both: `strategy` is the exact
    spec that was executed and `strategy_source` says where it came from.
    """

    strategy: StrategySpec | None = None
    strategy_source: StrategySource | None = None
    # 실행 설정. 없으면 서비스가 1.1 문서에서 브리지로 만든다(P2-01). 주어지면 문서의
    # `data`·`execution` 보다 우선한다. P2-03 에서 필수가 된다.
    environment: RunEnvironment | None = None
    core: ExecutionCore = ExecutionCore.RUST
    initial_cash: float = 100_000_000.0
    benchmark_security_id: str | None = None
    annualization_days: int = 252
    metric_windows: tuple[MetricWindow, ...] = ()

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.annualization_days <= 0:
            raise ValueError("annualization_days must be positive")


@dataclass(frozen=True)
class DataWarning:
    code: str
    message: str
    severity: WarningSeverity = WarningSeverity.WARNING


@dataclass(frozen=True)
class RunManifest:
    """What a finished run was made of.

    `strategy_hash` and `strategy_provenance.spec_hash` always carry the same value: one run
    executed one strategy.
    """

    run_id: str
    created_at: datetime
    completed_at: datetime
    engine_core: ExecutionCore
    engine_version: str
    run_fingerprint: str
    run_spec: BacktestRunSpec
    strategy_hash: str
    data_snapshot_id: str
    target_tape_hash: str
    metric_registry_version: str
    initial_cash: float
    annualization_days: int
    fee_bps: float
    slippage_bps: float
    participation_rate: float
    # 이 run 이 실제로 쓴 실행 설정과 그 hash. `strategy_hash` 와 별개 축이라, 같은 전략을
    # 다른 기간·비용으로 돌린 두 run 을 매니페스트만 보고 구분할 수 있다.
    environment: RunEnvironment
    environment_hash: str
    strategy_provenance: StrategyProvenance
    warnings: tuple[DataWarning, ...] = ()
    schema_version: str = "backtest-run-v2"

    def __post_init__(self) -> None:
        # The two fields have different producers — `strategy_hash` is the compiler's, carried on
        # the TargetTape, and `spec_hash` is the application's, recorded when the run request was
        # resolved. Nothing but this check keeps a manifest from naming two strategies for one run
        # if the spec handed to the two sides ever diverges (DEFECT-105).
        if self.strategy_hash != self.strategy_provenance.spec_hash:
            raise ValueError(
                "manifest records two strategies for one run — "
                f"run_id={self.run_id} target_tape.strategy_hash={self.strategy_hash} "
                f"provenance.spec_hash={self.strategy_provenance.spec_hash} "
                f"provenance.kind={self.strategy_provenance.kind.value} "
                f"strategy_id={self.strategy_provenance.strategy_id} "
                f"revision={self.strategy_provenance.revision}"
            )
        # 비용·참여율은 `environment` 가 owner 다. 평면 필드는 1.1 소비자 호환으로 남아 있을
        # 뿐이라 두 축이 갈리면 리포트가 읽는 축에 따라 같은 run 의 수수료가 달라진다. 평면
        # 필드 제거는 소비자 정리가 끝나는 P2-03 이다.
        divergent = {
            name: (getattr(self, name), getattr(self.environment, name))
            for name in NUMERIC_ENVIRONMENT_FIELDS
            if getattr(self, name) != getattr(self.environment, name)
        }
        if divergent:
            raise ValueError(
                "manifest records two execution cost models for one run — "
                f"run_id={self.run_id} environment_hash={self.environment_hash} "
                + " ".join(
                    f"{name}: manifest={flat!r} environment={owned!r}"
                    for name, (flat, owned) in sorted(divergent.items())
                )
            )


@dataclass(frozen=True)
class RawSnapshot:
    session: date
    cash: float
    equity: float
    gross_exposure: float
    net_exposure: float


@dataclass(frozen=True)
class RawPosition:
    session: date
    security_id: str
    quantity: str
    average_price: float
    market_price: float
    market_value: float
    unrealized_pnl: float


@dataclass(frozen=True)
class RawOrder:
    order_id: str
    decision_id: str
    session: date
    security_id: str
    side: str
    quantity: str
    order_type: str
    time_in_force: str


@dataclass(frozen=True)
class RawFill:
    fill_id: str
    order_id: str
    session: date
    security_id: str
    side: str
    quantity: str
    price: float
    fee: float
    slippage_per_share: float


@dataclass(frozen=True)
class RawCost:
    session: date
    kind: str
    security_id: str | None
    amount: float


@dataclass(frozen=True)
class RawTrade:
    security_id: str
    opened_on: date
    closed_on: date
    side: str
    quantity: str
    entry_price: float
    exit_price: float
    pnl: float
    fees: float
    slippage_cost: float


@dataclass(frozen=True)
class RawArtifactBundle:
    snapshots: tuple[RawSnapshot, ...]
    positions: tuple[RawPosition, ...]
    orders: tuple[RawOrder, ...]
    fills: tuple[RawFill, ...]
    costs: tuple[RawCost, ...]
    trades: tuple[RawTrade, ...]
    schema_version: str = "backtest-artifacts-v1"


@dataclass(frozen=True)
class BacktestSeries:
    equity: tuple[EquityCurvePoint, ...]
    drawdown: tuple[DrawdownPoint, ...]
    monthly_returns: tuple[MonthlyReturnPoint, ...]
    rolling_sharpe: tuple[RollingMetricPoint, ...]


@dataclass(frozen=True)
class BacktestRunResult:
    manifest: RunManifest
    metric_definitions: tuple[MetricDefinition, ...]
    metrics: tuple[MetricValue, ...]
    series: BacktestSeries
    artifacts: RawArtifactBundle


@dataclass(frozen=True)
class RunProgressEvent:
    sequence: int
    run_id: str
    status: RunStatus
    progress: float
    stage: str
    message: str
    occurred_at: datetime


# run 실패 코드 어휘의 단일 정본. 앞 넷은 시작 요청 422 의 diagnostic 코드와 같은 문자열이고,
# 마지막은 분류되지 않은 내부 오류다. 프론트는 이 어휘를 `backtest.run.error.<code>` 로 번역한다
# (시작 422 의 `backtest.error.*` 와 namespace 가 다르다 — 툴바는 서버 detail 을 그대로 쓰는
# 화면이라 키를 합치면 detail 이 덮인다).
RunFailureCode = Literal[
    "portfolio.strategy.invalid",
    "portfolio.data.unavailable",
    "portfolio.raw_observation.invalid",
    "backtest.run.invalid",
    "backtest.run.internal",
]
RUN_FAILURE_CODES: frozenset[str] = frozenset(get_args(RunFailureCode))


@dataclass(frozen=True)
class BacktestRunState:
    run_id: str
    status: RunStatus
    progress: float
    stage: str
    message: str
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    # 실패의 기계 판독 분류(`failed`, 그리고 실패와 취소가 겹쳐 `cancelled` 로 끝난 run 에도 사유
    # 보존을 위해 실린다). 시작 요청이 데이터를 읽지 않게 되면서(#158) 데이터 의존 실패가 422
    # 코드 대신 이 필드로 온다 — 어휘 SoT 는 `RunFailureCode`.
    error_code: RunFailureCode | None = None
    artifact_uri: str | None = None
    artifact_sha256: str | None = None


@dataclass(frozen=True)
class BacktestStartResponse:
    run: BacktestRunState
