from __future__ import annotations

from dataclasses import asdict, dataclass

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware

from strategy_workbench.application.equity_workspace.facade.workspace import (
    EquityWorkspaceService,
    ResearchCatalog,
    ResearchPreview,
)
from strategy_workbench.application.strategy_design.facade.design import (
    InvalidStrategyError,
    SavedStrategy,
    StrategyDesignService,
)
from strategy_workbench.application.strategy_design.facade.ports import (
    StrategyNotFoundError,
    StrategyRevisionConflictError,
)
from strategy_workbench.domain.equity.facade.research_data import ResearchPanelQuery
from strategy_workbench.domain.strategy.facade.explanation import StrategyExplanation
from strategy_workbench.domain.strategy.facade.specification import StrategySpec
from strategy_workbench.domain.strategy.facade.validation import StrategyValidation


@dataclass(frozen=True)
class ReviseStrategyRequest:
    expected_revision: int
    spec: StrategySpec


def create_app(
    *,
    strategy_design: StrategyDesignService,
    equity_workspace: EquityWorkspaceService,
    allowed_origins: tuple[str, ...] = ("http://localhost:5173",),
) -> FastAPI:
    app = FastAPI(
        title="Quant Strategy Workbench API",
        version="0.1.0",
        description="No-code factor strategy design and backtest orchestration API.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health", operation_id="getHealth")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/v1/equity/catalog",
        operation_id="getEquityCatalog",
    )
    def equity_catalog() -> ResearchCatalog:
        return equity_workspace.catalog()

    @app.post(
        "/api/v1/equity/preview",
        operation_id="previewEquityData",
    )
    def equity_preview(query: ResearchPanelQuery, venue: str = "XKRX") -> ResearchPreview:
        return equity_workspace.preview(query, venue=venue)

    @app.get(
        "/api/v1/strategies/template",
        operation_id="getStrategyTemplate",
    )
    def strategy_template() -> StrategySpec:
        return strategy_design.template()

    @app.post(
        "/api/v1/strategies/validate",
        operation_id="validateStrategy",
    )
    def validate_strategy(spec: StrategySpec) -> StrategyValidation:
        return strategy_design.validate(spec)

    @app.post(
        "/api/v1/strategies/explain",
        operation_id="explainStrategy",
    )
    def explain_strategy(spec: StrategySpec) -> StrategyExplanation:
        return strategy_design.explain(spec)

    @app.post(
        "/api/v1/strategies",
        operation_id="createStrategy",
        status_code=status.HTTP_201_CREATED,
    )
    def create_strategy(spec: StrategySpec) -> SavedStrategy:
        try:
            return strategy_design.create(spec)
        except InvalidStrategyError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "strategy.invalid",
                    "validation": jsonable_encoder(asdict(error.validation)),
                },
            ) from error

    @app.get(
        "/api/v1/strategies/{strategy_id}",
        operation_id="getStrategy",
    )
    def get_strategy(
        strategy_id: str,
        revision: int | None = Query(default=None, ge=1),
    ) -> SavedStrategy:
        try:
            return strategy_design.get(strategy_id, revision)
        except StrategyNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "strategy.not_found", "message": str(error)},
            ) from error

    @app.post(
        "/api/v1/strategies/{strategy_id}/revisions",
        operation_id="reviseStrategy",
        status_code=status.HTTP_201_CREATED,
    )
    def revise_strategy(strategy_id: str, request: ReviseStrategyRequest) -> SavedStrategy:
        try:
            return strategy_design.revise(
                strategy_id,
                request.spec,
                expected_revision=request.expected_revision,
            )
        except InvalidStrategyError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "strategy.invalid",
                    "validation": jsonable_encoder(asdict(error.validation)),
                },
            ) from error
        except StrategyNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "strategy.not_found", "message": str(error)},
            ) from error
        except StrategyRevisionConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "strategy.revision_conflict", "message": str(error)},
            ) from error

    return app
