"""기존 유스케이스의 공개 facade를 연속 평가의 자체 포트로 연결한다."""

from collections.abc import Callable
from dataclasses import replace

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.application.backtest_run.facade.runs import BacktestRunService
from strategy_workbench.application.continuous_account.facade.evaluation import (
    ContinuousAccountRejectedError,
    FrozenExperimentInput,
    FrozenWindowChoice,
)
from strategy_workbench.application.continuous_account.facade.ports import PreparedContinuousSignals
from strategy_workbench.application.experiment_run.facade.experiments import ExperimentRunService
from strategy_workbench.application.experiment_run.facade.ports import ExperimentRepositoryPort
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPipelineOptions,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_design.facade.ports import StrategyRepositoryPort
from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    require_research_window,
)
from strategy_workbench.domain.backtest.facade.runs import RunStatus
from strategy_workbench.domain.experiment.facade.trial import ExperimentStatus, TrialStatus
from strategy_workbench.domain.portfolio.facade.scheduled_construction import ScheduledCandidate
from strategy_workbench.domain.strategy.facade.provenance import SavedRevisionReference
from strategy_workbench.domain.strategy.facade.specification import resolve_parameter_values


class ContinuousExperimentSourceAdapter:
    def __init__(
        self,
        repository: ExperimentRepositoryPort,
        experiments: ExperimentRunService,
        runs: BacktestRunService,
        strategies: StrategyRepositoryPort,
    ) -> None:
        self._repository = repository
        self._experiments = experiments
        self._runs = runs
        self._strategies = strategies

    def freeze(self, experiment_id: str) -> FrozenExperimentInput:
        return self._read(experiment_id, verify_results=True)

    def inspect(self, experiment_id: str) -> FrozenExperimentInput:
        return self._read(experiment_id, verify_results=False)

    def _read(self, experiment_id: str, *, verify_results: bool) -> FrozenExperimentInput:
        experiment = self._experiments.get(experiment_id)
        record = experiment.record
        if not record.design.walks_forward or not record.design.windows:
            raise ContinuousAccountRejectedError(
                "continuous.unsupported", "측정 창이 있는 파라미터 탐색 실험만 지원합니다."
            )
        saved_picks = self._repository.picks(experiment_id)
        if any(pick.trial_index is None for pick in saved_picks):
            raise ContinuousAccountRejectedError(
                "continuous.no_cell", "선택된 학습 후보가 없는 창이 있어 실행할 수 없습니다."
            )
        if experiment.status is not ExperimentStatus.COMPLETED or not experiment.settled:
            raise ContinuousAccountRejectedError(
                "continuous.unfinished",
                "학습과 검증 실행이 모두 완료된 실험만 연속 평가할 수 있습니다.",
            )
        reference = record.run.strategy_source
        if not isinstance(reference, SavedRevisionReference):
            raise ContinuousAccountRejectedError(
                "continuous.source_invalid", "고정된 저장 리비전 출처가 필요합니다."
            )
        revision = self._strategies.get(reference.strategy_id, reference.revision)
        base_environment = record.run.environment
        if base_environment is None:
            raise ContinuousAccountRejectedError(
                "continuous.unsupported", "기반 실험의 실행 설정이 없습니다."
            )
        if (
            revision.requires_upgrade
            or revision.spec_hash != reference.expected_spec_hash
            or revision.source is None
        ):
            raise ContinuousAccountRejectedError(
                "continuous.source_invalid", "저장 리비전의 원문과 해시를 확인할 수 없습니다."
            )
        picks = {}
        for pick in saved_picks:
            previous = picks.get(pick.window_index)
            if previous is None or pick.attempt > previous.attempt:
                picks[pick.window_index] = pick
        if len(picks) != len(record.design.windows):
            raise ContinuousAccountRejectedError(
                "continuous.unfinished", "모든 검증 창의 선택 기록이 필요합니다."
            )
        trials = {state.trial.index: state for state in self._experiments.trials(experiment_id)}
        choices: list[FrozenWindowChoice] = []
        for index, window in enumerate(record.design.windows):
            pick = picks.get(index)
            if pick is None or pick.trial_index is None:
                raise ContinuousAccountRejectedError(
                    "continuous.no_cell", "선택된 학습 후보가 없는 창이 있어 실행할 수 없습니다."
                )
            training = trials.get(pick.trial_index)
            if (
                training is None
                or training.trial.window != window
                or training.status is not TrialStatus.COMPLETED
                or not training.attempts
            ):
                raise ContinuousAccountRejectedError(
                    "continuous.source_invalid", "선택된 학습 실행과 검증 창이 일치하지 않습니다."
                )
            training_run_id = training.attempts[-1].run_id
            if (
                training_run_id is None
                or pick.run_id is None
                or self._runs.state(pick.run_id).status is not RunStatus.COMPLETED
            ):
                raise ContinuousAccountRejectedError(
                    "continuous.unfinished", "선택된 후보의 학습·검증 실행이 완료되지 않았습니다."
                )
            parameters = resolve_parameter_values(
                revision.spec.parameters, training.trial.parameter_values
            )
            snapshot_id = factor_version = None
            if verify_results:
                manifest = self._runs.result(training_run_id).manifest
                validation = self._runs.result(pick.run_id).manifest
                for actual, start, end in (
                    (manifest, window.train_start, window.train_end),
                    (validation, window.test_start, window.test_end),
                ):
                    if (
                        actual.run_spec.strategy_source != reference
                        or actual.strategy_hash != revision.spec_hash
                        or actual.run_spec.parameter_values != parameters
                        or actual.environment.start != start
                        or actual.environment.end != end
                        or actual.environment != replace(base_environment, start=start, end=end)
                    ):
                        raise ContinuousAccountRejectedError(
                            "continuous.source_invalid",
                            "학습·검증 결과의 출처·파라미터·기간이 선택 기록과 일치하지 않습니다.",
                        )
                    if (
                        actual.run_spec.core is not record.run.core
                        or actual.run_spec.initial_cash != record.run.initial_cash
                        or actual.run_spec.benchmark_security_id != record.run.benchmark_security_id
                        or actual.run_spec.annualization_days != record.run.annualization_days
                        or actual.factor_evaluation_version is None
                    ):
                        raise ContinuousAccountRejectedError(
                            "continuous.source_invalid",
                            "선택 결과의 실행 설정 또는 평가 판본이 기반 실험과 일치하지 않습니다.",
                        )
                if (
                    manifest.data_snapshot_id != validation.data_snapshot_id
                    or manifest.factor_evaluation_version != validation.factor_evaluation_version
                ):
                    raise ContinuousAccountRejectedError(
                        "continuous.source_invalid", "학습과 검증 결과의 자료·평가 판본이 다릅니다."
                    )
                snapshot_id = manifest.data_snapshot_id
                factor_version = manifest.factor_evaluation_version
            choices.append(
                FrozenWindowChoice(
                    index,
                    window,
                    pick.trial_index,
                    training_run_id,
                    pick.run_id,
                    tuple(sorted(parameters.items())),
                    snapshot_id,
                    factor_version,
                )
            )
        return FrozenExperimentInput(
            experiment_id,
            record.run,
            revision.spec,
            revision.source.text,
            reference.strategy_id,
            reference.revision,
            tuple(choices),
        )


class ContinuousPortfolioPipelineAdapter:
    def __init__(
        self, pipeline: PortfolioDesignService, bridge: BacktestEnginePortfolioAdapter
    ) -> None:
        self._pipeline = pipeline
        self._bridge = bridge

    def preflight(self, source: FrozenExperimentInput, environment: RunEnvironment) -> None:
        require_research_window(environment.start, requested_by="continuous_account")
        if not self._bridge.assess(source.strategy, environment).compatible:
            raise ContinuousAccountRejectedError(
                "continuous.unsupported", "선택된 전략의 실행 요구를 현재 커널이 지원하지 않습니다."
            )
        # 현재 실험은 하나의 저장 리비전과 공통 실행 설정에서 파라미터만 바꾼다.
        for choice in source.choices:
            resolve_parameter_values(source.strategy.parameters, dict(choice.parameter_values))

    def prepare(
        self,
        source: FrozenExperimentInput,
        choice: FrozenWindowChoice,
        environment: RunEnvironment,
        *,
        progress: Callable[[float, str], None],
    ) -> PreparedContinuousSignals:
        result = self._pipeline.run_pipeline(
            PortfolioPreviewRequest(source.strategy, environment=environment),
            options=PortfolioPipelineOptions(
                require_engine_compatible=True, parameter_values=dict(choice.parameter_values)
            ),
            progress=progress,
        )
        candidate = ScheduledCandidate(
            spec=source.strategy,
            parameter_values=choice.parameter_values,
            source=source.source,
            factor_plans=tuple(
                (record.factor_id, record.plan) for record in result.factor_evaluations
            ),
            data_snapshot_id=result.data_snapshot_id,
            observations=tuple(
                row
                for row in result.observations
                if environment.start <= row.as_of <= environment.end
            ),
        )

        return PreparedContinuousSignals(candidate, result.preview.warnings)
