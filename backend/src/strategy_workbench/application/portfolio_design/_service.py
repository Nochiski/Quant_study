from __future__ import annotations

from strategy_workbench.domain.portfolio.facade.construction import compile_target_tape
from strategy_workbench.domain.strategy.facade.validation import (
    StrategyValidation,
    validate_strategy,
)

from ._models import PortfolioPreview, PortfolioPreviewRequest
from .ports.outgoing.engine_portfolio import EnginePortfolioPort
from .ports.outgoing.portfolio_observations import (
    PortfolioObservationPort,
    PortfolioObservationQuery,
)


class InvalidPortfolioRequestError(ValueError):
    def __init__(self, validation: StrategyValidation) -> None:
        super().__init__("portfolio preview requires a valid StrategySpec")
        self.validation = validation


class PortfolioDesignService:
    def __init__(
        self,
        observation_source: PortfolioObservationPort,
        engine_portfolio: EnginePortfolioPort,
    ) -> None:
        self._observation_source = observation_source
        self._engine_portfolio = engine_portfolio

    def preview(self, request: PortfolioPreviewRequest) -> PortfolioPreview:
        validation = validate_strategy(request.spec)
        if not validation.valid:
            raise InvalidPortfolioRequestError(validation)
        fields = {rule.field_id for rule in request.spec.eligibility.rules}
        fields.update(
            field_id
            for field_id in (
                request.spec.portfolio.liquidity_field_id,
                request.spec.signal.regime_field_id,
                request.spec.risk.risk_field_id,
            )
            if field_id is not None
        )
        observation_set = self._observation_source.load_portfolio_observations(
            PortfolioObservationQuery(
                start=request.spec.data.start,
                end=request.spec.data.end,
                factor_ids=tuple(factor.factor_id for factor in request.spec.factors.factors),
                field_ids=tuple(sorted(fields)),
            )
        )
        return PortfolioPreview(
            tape=compile_target_tape(
                request.spec,
                data_snapshot_id=observation_set.data_snapshot_id,
                sessions=observation_set.sessions,
                observations=observation_set.observations,
            ),
            engine=self._engine_portfolio.assess(request.spec),
        )
