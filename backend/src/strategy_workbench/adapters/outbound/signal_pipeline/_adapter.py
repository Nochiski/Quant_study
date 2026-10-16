from __future__ import annotations

from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.signal_research.facade.ports import PreparedSignals
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.strategy.facade.specification import StrategySpec


class PortfolioSignalPipelineAdapter:
    """실제 선정 파이프라인을 실행하고 판본·평가값의 경계를 검사해 연구 전용 DTO로 바꾼다."""

    def __init__(self, pipeline: PortfolioDesignService) -> None:
        self._pipeline = pipeline

    def prepare(self, strategy: StrategySpec, environment: RunEnvironment) -> PreparedSignals:
        result = self._pipeline.run_pipeline(
            PortfolioPreviewRequest(strategy, environment=environment)
        )
        if result.data_snapshot_id != result.preview.tape.data_snapshot_id:
            raise ValueError("pipeline and target tape snapshots differ")
        raw_keys = {(row.as_of, row.security_id) for row in result.raw_observations}
        evaluations = {
            record.factor_id: {
                (value.as_of, value.security_id): value.value for value in record.values
            }
            for record in result.factor_evaluations
        }
        for row in result.observations:
            key = (row.as_of, row.security_id)
            if key not in raw_keys:
                raise ValueError(f"evaluated signal has no raw observation — key={key!r}")
            for factor in row.factor_values:
                if (
                    factor.factor_id not in evaluations
                    or key not in evaluations[factor.factor_id]
                    or evaluations[factor.factor_id][key] != factor.value
                ):
                    raise ValueError(
                        f"pipeline signal differs from actual factor evaluation — key={key!r} "
                        f"factor_id={factor.factor_id!r}"
                    )
        return PreparedSignals(
            result.data_snapshot_id,
            result.observations,
            result.preview.tape,
            result.preview.warnings,
        )
