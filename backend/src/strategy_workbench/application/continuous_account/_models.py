"""단일 전략 실행 신원과 구별되는 연속 계좌 평가 계약."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
from math import isfinite
from typing import Literal

from strategy_workbench.domain.analytics.facade.metrics import MetricDefinition, MetricValue
from strategy_workbench.domain.backtest.facade.account_report import AccountReportSummary
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    DataWarning,
    ExecutionCore,
    RawArtifactBundle,
)
from strategy_workbench.domain.canonical.facade.encoding import canonical_json_hash
from strategy_workbench.domain.experiment.facade.design import WalkForwardWindow
from strategy_workbench.domain.experiment.facade.selection_schedule import (
    SelectionSchedule,
    SelectionTransition,
    WindowSelection,
)
from strategy_workbench.domain.portfolio.facade.scheduled_construction import ScheduledFrameSource
from strategy_workbench.domain.strategy.facade.specification import ParameterValue, StrategySpec

CONTINUOUS_ACCOUNT_INTERPRETATION = (
    "동결된 창별 학습 선택을 하나의 초기자본과 실제 보유 수량으로 이어 실행한 "
    "비용 후 계좌 평가입니다. 각 선택은 학습 종료일 종가에 선택 가능했다고 가정한 "
    "재현 정책이며 과거 실제 선택 시각의 증명이 아닙니다. "
    "현재 mock 자료만 실행하며 실자료 수익성이나 실거래 준비를 뜻하지 않습니다. "
    "수익률은 최종자산 / 초기자본 − 1이고 비용은 이미 반영되어 다시 차감하지 않습니다. "
    "최종자산은 보유 평가를 포함하며 청산 현금이나 정지·상폐 정산 완료를 뜻하지 않습니다."
)

ContinuousAccountStage = Literal[
    "preparing", "pipeline", "compiling", "engine", "completed", "failed", "interrupted"
]
ContinuousAccountCode = Literal[
    "continuous.mock_only",
    "continuous.unfinished",
    "continuous.unsupported",
    "continuous.no_cell",
    "continuous.source_invalid",
    "continuous.prepare_failed",
    "continuous.request_invalid",
    "continuous.request_conflict",
    "continuous.busy",
    "continuous.execution_failed",
    "continuous.interrupted",
    "continuous.not_found",
    "continuous.result_unreadable",
]


class ContinuousAccountStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class ContinuousReadiness:
    ready: bool
    code: ContinuousAccountCode | None
    detail: str

    def __post_init__(self) -> None:
        if self.ready != (self.code is None) or not self.detail:
            raise ValueError("연속 평가 준비 상태·사유 계약을 위반했습니다.")


@dataclass(frozen=True)
class FrozenWindowChoice:
    window_index: int
    window: WalkForwardWindow
    trial_index: int
    training_run_id: str
    validation_run_id: str
    parameter_values: tuple[tuple[str, ParameterValue], ...]
    data_snapshot_id: str | None = None
    factor_evaluation_version: str | None = None


@dataclass(frozen=True)
class FrozenExperimentInput:
    experiment_id: str
    base_run: BacktestRunSpec
    strategy: StrategySpec
    source: str
    strategy_id: str
    revision: int
    choices: tuple[FrozenWindowChoice, ...]


@dataclass(frozen=True)
class ContinuousCandidateProvenance:
    candidate_id: str
    strategy_hash: str
    source_hash: str
    parameter_values: tuple[tuple[str, ParameterValue], ...]
    factor_plan_hashes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ContinuousScheduleProvenance:
    """도메인이 확정한 파생 사실을 HTTP·저장 경계에서 빠뜨리지 않는 명시 투영."""

    schedule_hash: str
    policy_version: str
    execution_timing: str
    time_in_force: str
    target_scope: str
    sessions: tuple[date, ...]
    selections: tuple[WindowSelection, ...]
    transitions: tuple[SelectionTransition, ...]

    def __post_init__(self) -> None:
        expected = SelectionSchedule(selections=self.selections, sessions=self.sessions)
        if (
            self.schedule_hash != expected.schedule_hash
            or self.policy_version != expected.policy_version
            or self.execution_timing != expected.execution_timing
            or self.time_in_force != expected.time_in_force
            or self.target_scope != expected.target_scope
            or self.transitions != expected.transitions
        ):
            raise ValueError("연속 평가 일정 출처가 도메인 확정 사실과 일치하지 않습니다.")

    @classmethod
    def from_schedule(cls, schedule: SelectionSchedule) -> ContinuousScheduleProvenance:
        return cls(
            schedule.schedule_hash,
            schedule.policy_version,
            schedule.execution_timing,
            schedule.time_in_force,
            schedule.target_scope,
            schedule.sessions,
            schedule.selections,
            schedule.transitions,
        )


@dataclass(frozen=True)
class ContinuousAccountManifest:
    evaluation_id: str
    experiment_id: str
    created_at: datetime
    engine_version: str
    data_snapshot_id: str
    factor_evaluation_version: str
    environment: RunEnvironment
    environment_hash: str
    schedule: ContinuousScheduleProvenance
    scheduled_tape_hash: str
    strategy_id: str
    revision: int
    source: str
    choices: tuple[FrozenWindowChoice, ...]
    candidates: tuple[ContinuousCandidateProvenance, ...]
    initial_cash: float
    core: ExecutionCore
    annualization_days: int
    frame_sources: tuple[ScheduledFrameSource, ...]
    metric_registry_version: str
    schema_version: Literal["continuous-account-result-v1"] = "continuous-account-result-v1"
    evaluator_version: Literal["continuous-account-v1"] = "continuous-account-v1"
    selection_time_policy: Literal["assumed_train_end_close"] = "assumed_train_end_close"
    evaluation_basis: Literal["continuous_account"] = "continuous_account"
    data_mode: Literal["mock"] = "mock"
    evaluation_fingerprint: str = ""

    def __post_init__(self) -> None:
        # 직접 조립한 정수 초기자본도 JSON 경계의 float와 같은 실행 신원을 갖는다.
        object.__setattr__(self, "initial_cash", float(self.initial_cash))
        payload = {
            "schema_version": self.schema_version,
            "evaluator_version": self.evaluator_version,
            "core": self.core,
            "annualization_days": self.annualization_days,
            "engine_version": self.engine_version,
            "data_snapshot_id": self.data_snapshot_id,
            "factor_evaluation_version": self.factor_evaluation_version,
            "environment_hash": self.environment_hash,
            "schedule_hash": self.schedule.schedule_hash,
            "scheduled_tape_hash": self.scheduled_tape_hash,
            "frame_sources": tuple(asdict(row) for row in self.frame_sources),
            "initial_cash": self.initial_cash,
            "strategy_id": self.strategy_id,
            "revision": self.revision,
            "source": self.source,
            "candidates": tuple(asdict(row) for row in self.candidates),
            "selection_time_policy": self.selection_time_policy,
            "metric_registry_version": self.metric_registry_version,
        }
        expected = canonical_json_hash(payload)
        if self.evaluation_fingerprint and self.evaluation_fingerprint != expected:
            raise ValueError("연속 평가 지문이 확정된 실행 입력과 일치하지 않습니다.")
        object.__setattr__(self, "evaluation_fingerprint", expected)


@dataclass(frozen=True)
class ContinuousCurvePoint:
    session: date
    equity: float


@dataclass(frozen=True)
class ContinuousAccountResult:
    manifest: ContinuousAccountManifest
    summary: AccountReportSummary
    curve: tuple[ContinuousCurvePoint, ...]
    artifacts: RawArtifactBundle
    total_fees: float
    total_sell_tax: float
    warnings: tuple[DataWarning, ...]
    metric_definitions: tuple[MetricDefinition, ...]
    metrics: tuple[MetricValue, ...]
    interpretation: str = CONTINUOUS_ACCOUNT_INTERPRETATION


@dataclass(frozen=True)
class ContinuousAccountState:
    evaluation_id: str
    experiment_id: str
    status: ContinuousAccountStatus
    progress: float
    stage: ContinuousAccountStage
    created_at: datetime
    completed_at: datetime | None = None
    error_code: ContinuousAccountCode | None = None
    error_detail: str | None = None
    result: ContinuousAccountResult | None = None

    def __post_init__(self) -> None:
        completed = self.status is ContinuousAccountStatus.COMPLETED
        terminal = self.status is not ContinuousAccountStatus.RUNNING
        if (
            not isinstance(self.status, ContinuousAccountStatus)
            or not isfinite(self.progress)
            or not 0 <= self.progress <= 1
            or completed != (self.result is not None)
            or terminal != (self.completed_at is not None)
            or (
                completed
                and (
                    self.progress != 1
                    or self.error_code is not None
                    or self.error_detail is not None
                )
            )
            or (not terminal and (self.error_code is not None or self.error_detail is not None))
            or (terminal and not completed and (not self.error_code or not self.error_detail))
        ):
            raise ValueError("연속 평가 상태·진행·결과·오류 계약을 위반했습니다.")


@dataclass(frozen=True)
class ContinuousAccountLatest:
    readiness: ContinuousReadiness
    evaluation: ContinuousAccountState | None
    status: Literal["not_started", "running", "completed", "failed", "interrupted"]
    data_mode: str
    interpretation: str = CONTINUOUS_ACCOUNT_INTERPRETATION


class ContinuousAccountRejectedError(ValueError):
    def __init__(self, code: ContinuousAccountCode, detail: str, *, http_status: int = 409) -> None:
        super().__init__(detail)
        self.code: ContinuousAccountCode = code
        self.http_status = http_status


class ContinuousAccountNotFoundError(LookupError):
    pass


class ContinuousAccountUnreadableError(RuntimeError):
    """저장된 별도 평가 이력의 무결성·역직렬화 검증에 실패했다."""
