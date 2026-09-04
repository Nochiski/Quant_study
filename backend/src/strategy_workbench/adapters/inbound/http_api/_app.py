from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestResultNotReadyError,
    BacktestRunNotFoundError,
    BacktestRunResult,
    BacktestRunService,
    BacktestRunSpec,
    BacktestRunState,
    BacktestStartResponse,
    InvalidBacktestRunError,
    RunStatus,
)
from strategy_workbench.application.equity_workspace.facade.workspace import (
    EquityWorkspaceService,
    FieldCatalogQuery,
    ResearchCatalog,
    ResearchPanelPreview,
    ResearchPanelPreviewRequest,
    ResearchPreview,
    UniversePreview,
)
from strategy_workbench.application.factor_research.facade.research import (
    FactorAvailability,
    FactorCatalog,
    FactorCatalogQuery,
    FactorCategory,
    FactorExplanation,
    FactorGraphRequest,
    FactorGraphValidation,
    FactorPreview,
    FactorPreviewRequest,
    FactorResearchService,
    FactorSnapshotMismatchError,
    InvalidFactorRequestError,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompiledDocument,
    CompileRequest,
    InvalidStrategyDocumentError,
    ReviseDocumentRequest,
    RevisionDiff,
    SaveDocumentRequest,
    StrategyAuthoringService,
    StrategyDocument,
    StrategyDocumentContract,
    StrategyDocumentSchema,
    StrategyDocumentService,
)
from strategy_workbench.application.strategy_design.facade.design import (
    InvalidStrategyError,
    SavedStrategy,
    StrategyDesignService,
)
from strategy_workbench.application.strategy_design.facade.ports import (
    Page,
    PageRequest,
    RevisionSummary,
    StrategyNotFoundError,
    StrategyRevisionConflictError,
)
from strategy_workbench.domain.equity.facade.research_data import (
    ResearchPanelQuery,
    UniverseHistoryQuery,
)
from strategy_workbench.domain.strategy.facade.explanation import StrategyExplanation
from strategy_workbench.domain.strategy.facade.specification import StrategySpec
from strategy_workbench.domain.strategy.facade.validation import StrategyValidation


@dataclass(frozen=True)
class ReviseStrategyRequest:
    expected_revision: int
    spec: StrategySpec


def _backtest_not_found(error: BacktestRunNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "backtest.run.not_found", "message": str(error)},
    )


def create_app(
    *,
    strategy_design: StrategyDesignService,
    strategy_authoring: StrategyAuthoringService,
    strategy_documents: StrategyDocumentService,
    equity_workspace: EquityWorkspaceService,
    factor_research: FactorResearchService,
    portfolio_design: PortfolioDesignService,
    backtest_runs: BacktestRunService,
    allowed_origins: tuple[str, ...] = ("http://localhost:5173",),
) -> FastAPI:
    app = FastAPI(
        title="Quant Strategy Workbench API",
        version="0.1.0",
        description=(
            "YAML-first factor strategy authoring, validation and backtest orchestration API. "
            "StrategySpec is the execution source of truth; YAML/JSON documents are compiled by "
            "the server."
        ),
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

    @app.post(
        "/api/v1/backtests",
        operation_id="startBacktest",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_backtest(spec: BacktestRunSpec) -> BacktestStartResponse:
        try:
            return backtest_runs.start(spec)
        except InvalidBacktestRunError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "backtest.run.invalid", "message": str(error)},
            ) from error

    @app.get(
        "/api/v1/backtests/{run_id}",
        operation_id="getBacktestStatus",
    )
    def get_backtest_status(run_id: str) -> BacktestRunState:
        try:
            return backtest_runs.state(run_id)
        except BacktestRunNotFoundError as error:
            raise _backtest_not_found(error) from error

    @app.get(
        "/api/v1/backtests/{run_id}/result",
        operation_id="getBacktestResult",
    )
    def get_backtest_result(run_id: str) -> BacktestRunResult:
        try:
            return backtest_runs.result(run_id)
        except BacktestRunNotFoundError as error:
            raise _backtest_not_found(error) from error
        except BacktestResultNotReadyError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "backtest.result.not_ready", "message": str(error)},
            ) from error

    @app.post(
        "/api/v1/backtests/{run_id}/cancel",
        operation_id="cancelBacktest",
    )
    def cancel_backtest(run_id: str) -> BacktestRunState:
        try:
            return backtest_runs.cancel(run_id)
        except BacktestRunNotFoundError as error:
            raise _backtest_not_found(error) from error

    @app.get(
        "/api/v1/backtests/{run_id}/events",
        operation_id="streamBacktestEvents",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    def stream_backtest_events(
        run_id: str,
        after_sequence: int = Query(default=-1, ge=-1),
    ) -> StreamingResponse:
        try:
            backtest_runs.state(run_id)
        except BacktestRunNotFoundError as error:
            raise _backtest_not_found(error) from error

        def event_stream():
            sequence = after_sequence
            while True:
                events = backtest_runs.events(run_id, after_sequence=sequence)
                for event in events:
                    sequence = event.sequence
                    payload = json.dumps(
                        jsonable_encoder(asdict(event)),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    yield f"id: {event.sequence}\nevent: progress\ndata: {payload}\n\n"
                state = backtest_runs.state(run_id)
                if state.status in (
                    RunStatus.COMPLETED,
                    RunStatus.CANCELLED,
                    RunStatus.FAILED,
                ):
                    break
                time.sleep(0.05)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/v1/portfolio/preview",
        operation_id="previewPortfolio",
    )
    def portfolio_preview(request: PortfolioPreviewRequest) -> PortfolioPreview:
        try:
            return portfolio_design.preview(request)
        except InvalidPortfolioRequestError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "portfolio.strategy.invalid",
                    "validation": jsonable_encoder(asdict(error.validation)),
                },
            ) from error

    @app.get(
        "/api/v1/equity/catalog",
        operation_id="getEquityCatalog",
    )
    def equity_catalog(
        search: str | None = Query(default=None, min_length=1),
        dataset_id: Annotated[list[str] | None, Query()] = None,
        unit: Annotated[list[str] | None, Query()] = None,
        frequency: Annotated[list[str] | None, Query()] = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> ResearchCatalog:
        return equity_workspace.catalog(
            FieldCatalogQuery(
                search=search,
                dataset_ids=tuple(dataset_id or ()),
                units=tuple(unit or ()),
                frequencies=tuple(frequency or ()),
                page=page,
                page_size=page_size,
            )
        )

    @app.post(
        "/api/v1/equity/universe/preview",
        operation_id="previewEquityUniverse",
    )
    def equity_universe_preview(query: UniverseHistoryQuery) -> UniversePreview:
        return equity_workspace.preview_universe(query)

    @app.post(
        "/api/v1/equity/panel/preview",
        operation_id="previewEquityPanel",
    )
    def equity_panel_preview(request: ResearchPanelPreviewRequest) -> ResearchPanelPreview:
        return equity_workspace.preview_panel(request)

    @app.post(
        "/api/v1/equity/preview",
        operation_id="previewEquityData",
    )
    def equity_preview(query: ResearchPanelQuery, venue: str = "XKRX") -> ResearchPreview:
        return equity_workspace.preview(query, venue=venue)

    @app.get(
        "/api/v1/factors/catalog",
        operation_id="getFactorCatalog",
    )
    def factor_catalog(
        search: str | None = Query(default=None, min_length=1),
        category: Annotated[list[FactorCategory] | None, Query()] = None,
        availability: Annotated[list[FactorAvailability] | None, Query()] = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> FactorCatalog:
        return factor_research.catalog(
            FactorCatalogQuery(
                search=search,
                categories=tuple(category or ()),
                availability=tuple(availability or ()),
                page=page,
                page_size=page_size,
            )
        )

    @app.post(
        "/api/v1/factors/validate",
        operation_id="validateFactorGraph",
    )
    def validate_factor_graph(request: FactorGraphRequest) -> FactorGraphValidation:
        return factor_research.validate(request)

    @app.post(
        "/api/v1/factors/explain",
        operation_id="explainFactorGraph",
    )
    def explain_factor_graph(request: FactorGraphRequest) -> FactorExplanation:
        return factor_research.explain(request)

    @app.post(
        "/api/v1/factors/preview",
        operation_id="previewFactorGraph",
    )
    def preview_factor_graph(request: FactorPreviewRequest) -> FactorPreview:
        try:
            return factor_research.preview(request)
        except FactorSnapshotMismatchError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "factor.snapshot_mismatch",
                    "expected_data_snapshot_id": error.expected,
                    "actual_data_snapshot_id": error.actual,
                    "message": str(error),
                },
            ) from error
        except InvalidFactorRequestError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "factor.graph.invalid",
                    "validation": jsonable_encoder(asdict(error.validation)),
                },
            ) from error

    @app.post(
        "/api/v1/strategy-documents/compile",
        operation_id="compileStrategyDocument",
    )
    def compile_strategy_document(request: CompileRequest) -> CompiledDocument:
        """Compile YAML/JSON source into a StrategySpec with syntax/structural/semantic diagnostics.

        200 for every well-formed request envelope: the outcome is the diagnostic list, and
        `spec`/`spec_hash` are null while any error-severity diagnostic exists. Only a malformed
        envelope (missing `source`, unknown `format`) is a 422.
        """
        return strategy_authoring.compile(request)

    @app.post(
        "/api/v1/strategy-documents",
        operation_id="createStrategyDocument",
        status_code=status.HTTP_201_CREATED,
    )
    def create_strategy_document(request: SaveDocumentRequest) -> StrategyDocument:
        """Store a cleanly compiled exact source as revision 1 of a new strategy."""
        try:
            return strategy_documents.save(request)
        except InvalidStrategyDocumentError as error:
            raise _invalid_document(error) from error

    @app.post(
        "/api/v1/strategy-documents/{strategy_id}/revisions",
        operation_id="reviseStrategyDocument",
        status_code=status.HTTP_201_CREATED,
    )
    def revise_strategy_document(
        strategy_id: str, request: ReviseDocumentRequest
    ) -> StrategyDocument:
        """Store the next revision; 409 when `expected_revision` is stale."""
        try:
            return strategy_documents.revise(strategy_id, request)
        except InvalidStrategyDocumentError as error:
            raise _invalid_document(error) from error
        except StrategyNotFoundError as error:
            raise _strategy_not_found(error) from error
        except StrategyRevisionConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "strategy.revision_conflict", "message": str(error)},
            ) from error

    @app.get(
        "/api/v1/strategies/{strategy_id}/revisions",
        operation_id="listStrategyRevisions",
    )
    def list_strategy_revisions(
        strategy_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=PageRequest.MAX_LIMIT),
    ) -> Page[RevisionSummary]:
        """Revision history, ascending by revision, paginated deterministically."""
        try:
            return strategy_documents.history(strategy_id, PageRequest(offset, limit))
        except StrategyNotFoundError as error:
            raise _strategy_not_found(error) from error

    @app.get(
        "/api/v1/strategies/{strategy_id}/diff",
        operation_id="diffStrategyRevisions",
    )
    def diff_strategy_revisions(
        strategy_id: str,
        base: int = Query(ge=1),
        target: int = Query(ge=1),
    ) -> RevisionDiff:
        """Semantic diff of two revisions over their canonical payloads (identity excluded)."""
        try:
            return strategy_documents.diff(strategy_id, base, target)
        except StrategyNotFoundError as error:
            raise _strategy_not_found(error) from error

    @app.get(
        "/api/v1/strategies/{strategy_id}/revisions/{revision}/document",
        operation_id="getStrategyDocument",
    )
    def get_strategy_document(strategy_id: str, revision: int) -> StrategyDocument:
        """Exact stored source of one revision (a generated projection for legacy ones)."""
        try:
            return strategy_documents.get(strategy_id, revision)
        except StrategyNotFoundError as error:
            raise _strategy_not_found(error) from error

    @app.get(
        "/api/v1/strategy-documents/schema",
        operation_id="getStrategyDocumentSchema",
        response_model=StrategyDocumentSchema,
    )
    def strategy_document_schema(
        response: Response, if_none_match: Annotated[str | None, Header()] = None
    ) -> StrategyDocumentSchema | Response:
        """Runtime JSON Schema of the authoring document. ETag = schema hash (304 on match)."""
        schema = strategy_authoring.schema()
        etag = _etag(schema.schema_hash)
        if if_none_match is not None and etag in _etags(if_none_match):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return schema

    @app.get(
        "/api/v1/strategy-documents/contract",
        operation_id="getStrategyDocumentContract",
    )
    def strategy_document_contract(response: Response) -> StrategyDocumentContractResponse:
        """Per-field authoring contract (type, enum, range, unit, default, example, stage)
        with the factor/dataset registry versions and catalog links it pairs with.
        """
        contract = strategy_authoring.contract()
        response.headers["ETag"] = _etag(contract.schema_hash)
        return StrategyDocumentContractResponse(
            contract=contract,
            factor_catalog_url="/api/v1/factors/catalog",
            equity_catalog_url="/api/v1/equity/catalog",
        )

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


@dataclass(frozen=True)
class StrategyDocumentContractResponse:
    """Wire envelope: the application contract plus the catalog links this API serves."""

    contract: StrategyDocumentContract
    factor_catalog_url: str
    equity_catalog_url: str


def _etag(schema_hash: str) -> str:
    return f'"{schema_hash}"'


def _etags(header: str) -> set[str]:
    return {item.strip().removeprefix("W/") for item in header.split(",")}


def _strategy_not_found(error: StrategyNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "strategy.not_found", "message": str(error)},
    )


def _invalid_document(error: InvalidStrategyDocumentError) -> HTTPException:
    compiled = error.compiled
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "strategy_document.invalid",
            "source_hash": compiled.source_hash,
            "schema_version": compiled.schema_version,
            "diagnostics": jsonable_encoder([asdict(d) for d in compiled.diagnostics]),
        },
    )
