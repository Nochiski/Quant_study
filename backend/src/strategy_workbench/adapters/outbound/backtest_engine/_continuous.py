"""연속 일정의 별도 실행 신원. 커널 회계와 피드 배관은 기존 owner를 재사용한다."""

from collections.abc import Callable, Mapping
from datetime import date

from backtest_engine.engine.tape import evaluate_tape
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.requirements import StrategyRequirements
from backtest_engine.types.strategy import StrategyContext
from backtest_engine.types.tape import DeclarativeTapeStrategy, TapeFrame
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.application.continuous_account.facade.ports import (
    ContinuousExecutionOutput,
    ContinuousExecutionRequest,
)
from strategy_workbench.domain.analytics.facade.metrics import (
    AnalysisPoint,
    build_default_metric_registry,
    compute_analytics,
)
from strategy_workbench.domain.backtest.facade.environment import impact_input_warnings
from strategy_workbench.domain.backtest.facade.runs import ENGINE_RULES_VERSION
from strategy_workbench.domain.backtest.facade.trade_analytics import analytics_input

from ._adapter import _artifacts, _base_rate_warnings, _participation_warnings, _run_kernel


class ScheduledTapeStrategy(DeclarativeTapeStrategy):
    def __init__(
        self, frames: Mapping[date, TapeFrame], requirements: StrategyRequirements
    ) -> None:
        self._frames = frames
        self._requirements = requirements

    @property
    def idle_reason(self) -> str:
        return "continuous_account_idle"

    def requirements(self) -> StrategyRequirements:
        return self._requirements

    def tape_frames(self) -> Mapping[date, TapeFrame]:
        return self._frames

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        return evaluate_tape(self._frames, self.idle_reason, ctx, event)


class ContinuousBacktestExecutorAdapter:
    def __init__(self) -> None:
        self._registry = build_default_metric_registry()

    @property
    def engine_version(self) -> str:
        return ENGINE_RULES_VERSION

    @property
    def metric_registry_version(self) -> str:
        return self._registry.version

    def execute(
        self, request: ContinuousExecutionRequest, *, progress: Callable[[float, str], None]
    ) -> ContinuousExecutionOutput:
        environment = request.manifest.environment
        bridge = BacktestEnginePortfolioAdapter()
        if not request.strategies:
            raise ValueError("continuous execution requires candidate strategies")
        requirements = bridge.requirements(request.strategies[0], environment)
        gross = request.strategies[0].risk.gross_exposure
        side = request.strategies[0].portfolio.side
        for spec in request.strategies:
            if (
                not bridge.assess(spec, environment).compatible
                or bridge.requirements(spec, environment) != requirements
                or spec.risk.gross_exposure != gross
                or spec.portfolio.side is not side
            ):
                raise ValueError(
                    "continuous candidates must share supported execution risk requirements"
                )
        if request.dataset.data_snapshot_id != request.tape.data_snapshot_id:
            raise ValueError("continuous dataset differs from the scheduled snapshot")
        frames: dict[date, TapeFrame] = {}
        sessions = request.dataset.sessions
        for frame in request.tape.frames:
            if frame.signal_as_of in frames:
                raise ValueError("duplicate continuous signal session")
            index = sessions.index(frame.signal_as_of)
            if index + 1 >= len(sessions) or sessions[index + 1] != frame.execution_on:
                raise ValueError("continuous execution is not the next common calendar session")
            frames[frame.signal_as_of] = TapeFrame(
                bridge.to_target_action(frame, max_participation=environment.participation_rate),
                f"continuous_account:{frame.signal_as_of}",
            )
        progress(0.0, "engine")
        engine, costs = _run_kernel(
            dataset=request.dataset,
            environment=environment,
            run_id=request.manifest.evaluation_id,
            initial_cash=request.manifest.initial_cash,
            annualization_days=request.manifest.annualization_days,
            gross_exposure=gross,
            core=request.core.value,
            tape_strategy=ScheduledTapeStrategy(frames, requirements),
            cancelled=lambda: False,
        )
        artifacts, outcomes = _artifacts(
            engine.event_store.result_tables(), engine.event_store.order_updates()
        )
        points = tuple(
            AnalysisPoint(
                session=row.session,
                equity=row.equity,
                gross_exposure=row.gross_exposure,
                net_exposure=row.net_exposure,
            )
            for row in artifacts.snapshots
        )
        analytics = compute_analytics(
            analytics_input(points, artifacts.fills, artifacts.costs, outcomes),
            self._registry,
            annualization_days=request.manifest.annualization_days,
        )
        progress(1.0, "engine")
        return ContinuousExecutionOutput(
            artifacts,
            ENGINE_RULES_VERSION,
            (
                *_participation_warnings(environment),
                *impact_input_warnings(costs, artifacts.fills),
                *_base_rate_warnings(analytics.base_rate_carried_sessions),
            ),
            self._registry.definitions(),
            analytics.metrics,
        )
