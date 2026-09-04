from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, TypeAlias

from strategy_workbench.domain.analytics.facade.metrics import (
    DrawdownPoint,
    EquityCurvePoint,
    MetricDefinition,
    MetricScope,
    MetricValue,
    MonthlyReturnPoint,
    RollingMetricPoint,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec


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


class StrategySourceKind(StrEnum):
    SAVED_REVISION = "saved_revision"
    INLINE_DRAFT = "inline_draft"


@dataclass(frozen=True)
class SavedRevisionReference:
    """Run a stored revision; the run fails before starting if the hash no longer matches."""

    strategy_id: str
    revision: int
    expected_spec_hash: str
    kind: Literal["saved_revision"]


@dataclass(frozen=True)
class InlineDraft:
    """Run an unsaved spec (draft backtests only; never a deployment source).

    `source_hash` is client-asserted provenance: the server cannot verify it without the text
    and records it as given.
    """

    spec: StrategySpec
    kind: Literal["inline_draft"]
    source_hash: str | None = None


StrategySource: TypeAlias = SavedRevisionReference | InlineDraft


@dataclass(frozen=True)
class StrategyProvenance:
    """What exactly was run: recorded in the manifest so a result names its revision."""

    kind: StrategySourceKind
    spec_hash: str
    schema_version: str
    strategy_id: str | None = None
    revision: int | None = None
    source_hash: str | None = None


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
    strategy_provenance: StrategyProvenance
    warnings: tuple[DataWarning, ...] = ()
    schema_version: str = "backtest-run-v2"


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
    artifact_uri: str | None = None
    artifact_sha256: str | None = None


@dataclass(frozen=True)
class BacktestStartResponse:
    run: BacktestRunState
