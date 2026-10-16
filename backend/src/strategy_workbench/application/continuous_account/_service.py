"""별도 이력으로 명시 실행하는 단일 계좌 재현. trial 실행·선택을 변경하지 않는다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataPort,
    BacktestDataQuery,
)
from strategy_workbench.domain.backtest.facade.account_report import project_account_summary
from strategy_workbench.domain.backtest.facade.environment import (
    cost_history_sessions,
    environment_hash,
)
from strategy_workbench.domain.backtest.facade.runs import DataWarning, WarningSeverity
from strategy_workbench.domain.experiment.facade.selection_schedule import (
    SelectionSchedule,
    SelectionScheduleStatus,
    WindowSelection,
)
from strategy_workbench.domain.portfolio.facade.scheduled_construction import (
    ScheduledCandidate,
    compile_scheduled_target_tape,
)
from strategy_workbench.domain.strategy.facade.specification import ParameterValue

from ._models import (
    ContinuousAccountLatest,
    ContinuousAccountManifest,
    ContinuousAccountNotFoundError,
    ContinuousAccountRejectedError,
    ContinuousAccountResult,
    ContinuousAccountStage,
    ContinuousAccountState,
    ContinuousAccountStatus,
    ContinuousCandidateProvenance,
    ContinuousCurvePoint,
    ContinuousReadiness,
    ContinuousScheduleProvenance,
    FrozenExperimentInput,
)
from .ports.outgoing.evaluation_store import ContinuousEvaluationStorePort
from .ports.outgoing.executor import ContinuousExecutionRequest, ContinuousExecutorPort
from .ports.outgoing.preparation import (
    ContinuousExperimentSourcePort,
    ContinuousPipelinePort,
    PreparedContinuousSignals,
)

logger = logging.getLogger(__name__)


class ContinuousAccountService:
    def __init__(
        self,
        source: ContinuousExperimentSourcePort,
        pipeline: ContinuousPipelinePort,
        data: BacktestDataPort,
        executor: ContinuousExecutorPort,
        store: ContinuousEvaluationStorePort,
        *,
        data_mode: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._source = source
        self._pipeline = pipeline
        self._data = data
        self._executor = executor
        self._store = store
        self._data_mode = data_mode
        self._now = now

    def recover(self) -> None:
        """단일 서버 프로세스 시작 시에만 호출한다. 이전 실행을 자동 재실행하지 않는다."""
        self._store.interrupt_running(self._now())

    def get(self, evaluation_id: str) -> ContinuousAccountState:
        return self._store.get(evaluation_id)

    def latest(self, experiment_id: str) -> ContinuousAccountLatest:
        readiness = self.readiness(experiment_id)
        evaluation = self._store.latest(experiment_id)
        return ContinuousAccountLatest(
            readiness,
            evaluation,
            "not_started" if evaluation is None else evaluation.status.value,
            self._data_mode,
        )

    def readiness(self, experiment_id: str) -> ContinuousReadiness:
        try:
            self._prepare_source(experiment_id, inspect_only=True)
        except ContinuousAccountRejectedError as error:
            return ContinuousReadiness(False, error.code, str(error))
        except LookupError:
            raise
        except Exception:
            logger.exception(
                "continuous account readiness failed — experiment_id=%s", experiment_id
            )
            return ContinuousReadiness(
                False,
                "continuous.prepare_failed",
                "연속 평가 입력을 확인하지 못했습니다. 서버 상태를 확인한 뒤 다시 조회해 주세요.",
            )
        return ContinuousReadiness(
            True,
            None,
            "저장된 선택·출처가 준비되었습니다. "
            "명시 실행 시 원본 결과를 검증한 뒤 mock 평가를 시작합니다.",
        )

    def _prepare_source(
        self, experiment_id: str, *, inspect_only: bool = False
    ) -> FrozenExperimentInput:
        if self._data_mode != "mock":
            raise ContinuousAccountRejectedError(
                "continuous.mock_only", "연속 계좌 평가는 현재 mock 자료에서만 실행할 수 있습니다."
            )
        source = (
            self._source.inspect(experiment_id)
            if inspect_only
            else self._source.freeze(experiment_id)
        )
        environment = source.base_run.environment
        if environment is None:
            raise ContinuousAccountRejectedError(
                "continuous.unsupported", "실행 설정이 없는 실험입니다."
            )
        environment = replace(
            environment,
            start=source.choices[0].window.test_start,
            end=source.choices[-1].window.test_end,
        )
        self._pipeline.preflight(source, environment)
        return source

    def run(self, experiment_id: str, client_request_id: str) -> ContinuousAccountState:
        """같은 요청 ID는 완료·중단·실패를 포함해 원래 이력만 반환한다."""
        try:
            evaluation_id = str(UUID(client_request_id))
        except ValueError as error:
            raise ContinuousAccountRejectedError(
                "continuous.request_invalid",
                "client_request_id는 UUID여야 합니다.",
                http_status=422,
            ) from error
        try:
            existing = self._store.get(evaluation_id)
        except ContinuousAccountNotFoundError:
            existing = None
        if existing is not None:
            return self._same_request(existing, experiment_id)
        try:
            source = self._prepare_source(experiment_id)
        except (ContinuousAccountRejectedError, LookupError):
            raise
        except Exception as error:
            logger.exception("continuous preparation failed — experiment_id=%s", experiment_id)
            raise ContinuousAccountRejectedError(
                "continuous.prepare_failed",
                "연속 평가 입력을 준비하지 못했습니다. 서버 상태를 확인해 주세요.",
            ) from error
        state = ContinuousAccountState(
            evaluation_id,
            experiment_id,
            ContinuousAccountStatus.RUNNING,
            0.0,
            "preparing",
            self._now(),
        )
        if not self._store.claim(state):
            return self._same_request(self._store.get(evaluation_id), experiment_id)

        def progress(fraction: float, stage: ContinuousAccountStage) -> None:
            nonlocal state
            bounded = min(0.99, max(state.progress, fraction))
            if bounded - state.progress < 0.01 and stage == state.stage:
                return
            state = replace(state, progress=bounded, stage=stage)
            self._store.save(state)

        try:
            result = self._execute(source, evaluation_id, state.created_at, progress)
            state = replace(
                state,
                status=ContinuousAccountStatus.COMPLETED,
                progress=1.0,
                stage="completed",
                completed_at=self._now(),
                result=result,
            )
        except Exception:
            logger.exception("continuous evaluation failed — evaluation_id=%s", evaluation_id)
            state = replace(
                state,
                status=ContinuousAccountStatus.FAILED,
                stage="failed",
                completed_at=self._now(),
                error_code="continuous.execution_failed",
                error_detail=(
                    "연속 평가 실행에 실패했습니다. 입력과 서버 상태를 확인한 뒤 "
                    "새 평가를 시작해 주세요."
                ),
                result=None,
            )
        self._store.save(state)
        return state

    @staticmethod
    def _same_request(state: ContinuousAccountState, experiment_id: str) -> ContinuousAccountState:
        if state.experiment_id != experiment_id:
            raise ContinuousAccountRejectedError(
                "continuous.request_conflict", "다른 실험에서 이미 사용한 요청 ID입니다."
            )
        return state

    def _execute(
        self,
        source: FrozenExperimentInput,
        evaluation_id: str,
        created_at: datetime,
        progress: Callable[[float, ContinuousAccountStage], None],
    ) -> ContinuousAccountResult:
        environment = source.base_run.environment
        if environment is None:
            raise RuntimeError("admitted continuous environment is missing")
        environment = replace(
            environment,
            start=source.choices[0].window.test_start,
            end=source.choices[-1].window.test_end,
        )
        calendar = self._data.load_backtest_dataset(
            BacktestDataQuery(environment.start, environment.end, (), None)
        ).sessions
        prepared_by_parameters: dict[
            tuple[tuple[str, ParameterValue], ...], PreparedContinuousSignals
        ] = {}
        pipeline_warnings: list[DataWarning] = []
        candidates: dict[str, ScheduledCandidate] = {}
        window_candidates: list[str] = []
        for index, choice in enumerate(source.choices):
            # 파라미터의 정규 직렬화나 신원을 새로 계산하지 않는다. 동등한 해소 튜플만 묶는다.
            key = choice.parameter_values
            prepared = prepared_by_parameters.get(key)
            if prepared is None:
                prepared = self._pipeline.prepare(
                    source,
                    choice,
                    environment,
                    progress=lambda fraction, _message, index=index: progress(
                        0.05 + 0.65 * (index + fraction) / len(source.choices), "pipeline"
                    ),
                )
                prepared_by_parameters[key] = prepared
                pipeline_warnings.extend(
                    DataWarning(
                        code="continuous.pipeline_warning",
                        message=message,
                        severity=WarningSeverity.WARNING,
                    )
                    for message in prepared.warnings
                )
            candidate = prepared.candidate
            candidates[candidate.candidate_id] = candidate
            if (
                candidate.data_snapshot_id != choice.data_snapshot_id
                or candidate.factor_evaluation_version != choice.factor_evaluation_version
            ):
                raise ValueError(
                    "continuous prepared candidate differs from frozen selection inputs"
                )
            window_candidates.append(candidate.candidate_id)
        schedule = SelectionSchedule(
            selections=tuple(
                WindowSelection(
                    window_index=choice.window_index,
                    window=choice.window,
                    candidate_id=candidate_id,
                    known_as_of=choice.window.train_end,
                )
                for choice, candidate_id in zip(source.choices, window_candidates, strict=True)
            ),
            sessions=calendar,
        )
        if schedule.status is not SelectionScheduleStatus.READY:
            raise ValueError("continuous schedule has no executable complete selection")
        tape = compile_scheduled_target_tape(
            schedule=schedule,
            candidates=tuple(candidates.values()),
            environment=environment,
            progress=lambda fraction: progress(0.70 + 0.10 * fraction, "compiling"),
        )
        security_ids = tuple(
            sorted({target.security_id for frame in tape.frames for target in frame.targets})
        )
        dataset = self._data.load_backtest_dataset(
            BacktestDataQuery(
                environment.start,
                environment.end,
                security_ids,
                None,
                history_sessions_before_start=cost_history_sessions(environment),
            )
        )
        if (
            dataset.sessions != schedule.sessions
            or dataset.data_snapshot_id != tape.data_snapshot_id
        ):
            raise ValueError("continuous calendar or snapshot changed during preparation")
        manifest = ContinuousAccountManifest(
            evaluation_id,
            source.experiment_id,
            created_at,
            self._executor.engine_version,
            tape.data_snapshot_id,
            tape.factor_evaluation_version,
            environment,
            environment_hash(environment),
            ContinuousScheduleProvenance.from_schedule(schedule),
            tape.tape_hash,
            source.strategy_id,
            source.revision,
            source.source,
            source.choices,
            tuple(
                ContinuousCandidateProvenance(
                    candidate.candidate_id,
                    candidate.strategy_hash,
                    candidate.source_hash,
                    candidate.parameter_values,
                    tuple(
                        (factor_id, plan.plan_hash) for factor_id, plan in candidate.factor_plans
                    ),
                )
                for candidate in candidates.values()
            ),
            source.base_run.initial_cash,
            source.base_run.core,
            source.base_run.annualization_days,
            tape.frame_sources,
            self._executor.metric_registry_version,
        )
        progress(0.82, "engine")
        output = self._executor.execute(
            ContinuousExecutionRequest(
                manifest,
                tape,
                dataset,
                tuple(candidate.spec for candidate in candidates.values()),
                source.base_run.core,
            ),
            progress=lambda fraction, _message: progress(0.82 + 0.16 * fraction, "engine"),
        )
        projection = project_account_summary(
            initial_cash=manifest.initial_cash, snapshots=output.artifacts.snapshots
        )
        if projection.summary is None:
            raise ValueError("continuous account summary is unavailable")
        taxes = tuple(row.sell_tax for row in output.artifacts.fills)
        if any(tax is None for tax in taxes):
            raise ValueError("new continuous fills must include their sell tax")
        return ContinuousAccountResult(
            manifest,
            projection.summary,
            tuple(
                ContinuousCurvePoint(row.session, row.equity) for row in output.artifacts.snapshots
            ),
            output.artifacts,
            sum(row.fee for row in output.artifacts.fills),
            sum(tax for tax in taxes if tax is not None),
            (
                *pipeline_warnings,
                *dataset.warnings,
                *output.warnings,
                *(
                    DataWarning(
                        code=warning.code.value,
                        message=warning.message,
                        severity=WarningSeverity.WARNING,
                    )
                    for warning in tape.warnings
                ),
            ),
            output.metric_definitions,
            output.metrics,
        )
