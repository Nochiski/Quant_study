from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import Event
from typing import Annotated, Any, NoReturn, TypeVar

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from strategy_workbench.application.assistant_chat.facade.chat import AssistantChatService
from strategy_workbench.application.assistant_chat.facade.profiles import ProviderProfileService
from strategy_workbench.application.assistant_chat.facade.turns import AssistantTurnRunner
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestArtifactUnreadableError,
    BacktestCancelResult,
    BacktestParameterValueError,
    BacktestResultNotReadyError,
    BacktestRunNotFoundError,
    BacktestRunResult,
    BacktestRunService,
    BacktestRunSpec,
    BacktestRunState,
    BacktestRunSummary,
    BacktestStartResponse,
    RunKind,
    RunStatus,
    TrialLedger,
    TrialLineageAlreadyMergedError,
    TrialPreview,
    rejection_code,
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
from strategy_workbench.application.experiment_run.facade.experiments import (
    ExperimentRunService,
)
from strategy_workbench.application.experiment_run.facade.ports import TrialRunRejectedError
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
    IncompatiblePortfolioRequestError,
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPreview,
    PortfolioPreviewRequest,
    RawObservationContractError,
    RawObservationUnavailableError,
)
from strategy_workbench.application.portfolio_design.facade.trace import (
    InvalidStrategyTraceRequestError,
    StaleStrategyTraceSourceError,
    StrategyTraceCancelledError,
    StrategyTraceCapabilityError,
    StrategyTraceRequest,
    StrategyTraceResponse,
    StrategyTraceService,
    StrategyTraceSourceNotFoundError,
    StrategyTraceSourceRequiresUpgradeError,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompiledDocument,
    CompileRequest,
    DocumentNotUpgradeableError,
    DocumentUpgradeDriftError,
    DocumentUpgradeSyntaxError,
    DocumentUpgradeUnsupportedNodeError,
    InvalidStrategyDocumentError,
    InvalidStrategyDraftError,
    ReviseDocumentRequest,
    RevisionDiff,
    RunEnvironmentSchema,
    SaveDocumentRequest,
    SaveStrategyDraftRequest,
    StrategyAuthoringService,
    StrategyDocument,
    StrategyDocumentContract,
    StrategyDocumentSchema,
    StrategyDocumentService,
    StrategyDraft,
    StrategyDraftService,
    StrategyOperatorCatalog,
    UpgradedDocument,
)
from strategy_workbench.application.strategy_authoring.facade.ports import (
    StrategyDraftConflictError,
    StrategyDraftNotFoundError,
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
    StrategySummary,
)
from strategy_workbench.domain.backtest.facade.environment import ResearchWindowViolationError
from strategy_workbench.domain.equity.facade.research_data import (
    ResearchPanelQuery,
    UniverseHistoryQuery,
)
from strategy_workbench.domain.strategy.facade.explanation import StrategyExplanation
from strategy_workbench.domain.strategy.facade.specification import StrategySpec
from strategy_workbench.domain.strategy.facade.validation import StrategyValidation

from ._assistant_routes import register_assistant_routes
from ._backtest_contract import (
    Backtest422Response,
    BacktestResultNotReadyResponse,
    BacktestResultUnreadableResponse,
    BacktestRunNotFoundResponse,
    BacktestStrategyNotFoundResponse,
    BacktestStrategyStaleResponse,
    CodedBodyValidationRoute,
    StrategyNotFoundResponse,
    TrialLineageAlreadyMergedResponse,
    TrialLineageMergeRequest,
)
from ._execution_error_contract import (
    Portfolio422Response,
    PortfolioRawObservationInvalidDetail,
)
from ._experiment_routes import register_experiment_routes
from ._pagination import CANONICAL_PAGE_INTEGER_VALIDATOR
from ._sse import SSE_KEEPALIVE_FRAME, SSE_KEEPALIVE_SECONDS, SSE_POLL_SECONDS, sse_frame
from ._strategy_document_contract import (
    StrategyDocumentNotUpgradeableDetail,
    StrategyDocumentSave422Response,
    StrategyDocumentUpgrade422Response,
    StrategyDocumentUpgradeDriftDetail,
    StrategyDocumentUpgradeUnsupportedNodeDetail,
)
from ._strategy_draft_contract import (
    StrategyDraft422Response,
    StrategyDraftConflictDetail,
    StrategyDraftConflictResponse,
    StrategyDraftErrorResponse,
)
from ._trace_contract import (
    Trace422Response,
    TraceCancelledDetail,
    TraceCancelledResponse,
    TraceCapabilityUnsupportedDetail,
    TraceEngineIncompatibleDetail,
    TraceRequestInvalidDetail,
    TraceStrategyNotFoundDetail,
    TraceStrategyNotFoundResponse,
    TraceStrategyRequiresUpgradeDetail,
    TraceStrategyStaleDetail,
    TraceStrategyStaleResponse,
    apply_trace_openapi_contract,
)

_T = TypeVar("_T")

EQUITY_CATALOG_PATH = "/api/v1/equity/catalog"
FACTOR_CATALOG_PATH = "/api/v1/factors/catalog"


@dataclass(frozen=True)
class ReviseStrategyRequest:
    expected_revision: int
    spec: StrategySpec


@dataclass(frozen=True)
class StrategyRevisionConflictDetail:
    code: str
    message: str
    latest_revision: int | None


@dataclass(frozen=True)
class StrategyRevisionConflictResponse:
    detail: StrategyRevisionConflictDetail


def _revision_conflict(error: StrategyRevisionConflictError) -> HTTPException:
    detail = StrategyRevisionConflictDetail(
        code="strategy.revision_conflict",
        message=str(error),
        latest_revision=error.latest_revision,
    )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=asdict(detail),
    )


# 422 가 아닌 실행 접수 거절. 저장 리비전이 없거나(404) 그사이 바뀌었다(409).
_ADMISSION_STATUS: dict[str, int] = {
    "backtest.strategy.not_found": status.HTTP_404_NOT_FOUND,
    "backtest.strategy.stale": status.HTTP_409_CONFLICT,
}


def _backtest_not_found(error: BacktestRunNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "backtest.run.not_found", "message": str(error)},
    )


def _backtest_run_not_found_responses() -> dict[int | str, dict[str, Any]]:
    return {
        404: {
            "model": BacktestRunNotFoundResponse,
            "description": "The backtest run does not exist",
        }
    }


async def _backtest_event_stream(
    backtest_runs: BacktestRunService,
    run_id: str,
    *,
    after_sequence: int,
    keepalive_seconds: float = SSE_KEEPALIVE_SECONDS,
    poll_seconds: float = SSE_POLL_SECONDS,
) -> AsyncIterator[str]:
    """run 진행 이벤트를 sequence 순으로 흘리고 run 이 끝나면 닫는다.

    비동기 제너레이터라 기다리는 동안 스레드풀 워커를 잡지 않는다. 동기 제너레이터는 다음 이벤트가
    올 때까지 워커 하나를 붙잡아 run 수명(실데이터 수십 초~수 분) 내내 anyio 스레드풀을
    잠식했다(#161). 조용한 구간(자리를 기다리는 `queued`, 긴 tape)에는 keepalive 주석을 보낸다.

    **종결 여부를 이벤트보다 먼저 읽는다.** 반대로 읽으면 두 조회 사이에 기록된 마지막 이벤트를
    보내지 못하고 닫는다. 서비스는 종결 상태와 그 이벤트를 한 잠금 안에서 함께 기록한다.
    """
    sequence = after_sequence
    last_frame_at = time.monotonic()
    while True:
        settled = backtest_runs.state(run_id).status in (
            RunStatus.COMPLETED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        )
        for event in backtest_runs.events(run_id, after_sequence=sequence):
            sequence = event.sequence
            yield sse_frame(sequence=event.sequence, event="progress", data=event)
            last_frame_at = time.monotonic()
        if settled:
            return
        if time.monotonic() - last_frame_at >= keepalive_seconds:
            yield SSE_KEEPALIVE_FRAME
            last_frame_at = time.monotonic()
        await asyncio.sleep(poll_seconds)


def _draft_conflict(error: StrategyDraftConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=jsonable_encoder(
            asdict(
                StrategyDraftConflictDetail(
                    code="strategy.draft.conflict",
                    message=str(error),
                    current=error.current,
                )
            ),
            custom_encoder={
                datetime: lambda value: value.astimezone(UTC).isoformat().replace("+00:00", "Z")
            },
        ),
    )


def _draft_not_found(error: StrategyDraftNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "strategy.draft.not_found", "message": str(error)},
    )


def _invalid_draft(error: InvalidStrategyDraftError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "strategy.draft.invalid", "message": str(error)},
    )


def create_app(
    *,
    strategy_design: StrategyDesignService,
    strategy_authoring: StrategyAuthoringService,
    strategy_documents: StrategyDocumentService,
    strategy_drafts: StrategyDraftService,
    equity_workspace: EquityWorkspaceService,
    factor_research: FactorResearchService,
    portfolio_design: PortfolioDesignService,
    strategy_traces: StrategyTraceService,
    backtest_runs: BacktestRunService,
    experiments: ExperimentRunService | None = None,
    assistant_profiles: ProviderProfileService | None = None,
    assistant_chat: AssistantChatService | None = None,
    assistant_turns: AssistantTurnRunner | None = None,
    allowed_origins: tuple[str, ...],
) -> FastAPI:
    """Compose the HTTP surface; the assistant routes appear only when their services arrive.

    어시스턴트 서비스 셋은 항상 같이 만들어진다 — 하나를 세우는 컨테이너는 셋을 다 세운다.
    `/api/v1/assistant`를 건드리지 않는 기존 테스트는 셋 다 넘기지 않고, 그러면 라우트가
    "있는데 실패"가 아니라 아예 없는 상태가 된다.

    `allowed_origins`는 기본값을 두지 않는다. 기본 origin의 owner는 조립 지점
    (`bootstrap/_http.py`의 `DEFAULT_ALLOWED_ORIGINS`)이고, 여기에 같은 리터럴을 또 두면 개발 서버
    포트를 옮길 때 한쪽만 바뀐다(Phase B 감사 NB-7).
    """
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

    @app.get(
        "/api/v1/backtests",
        operation_id="listBacktests",
    )
    def list_backtests(
        offset: Annotated[
            int,
            Query(ge=0, le=PageRequest.MAX_OFFSET),
            CANONICAL_PAGE_INTEGER_VALIDATOR,
        ] = 0,
        limit: Annotated[
            int,
            Query(ge=1, le=PageRequest.MAX_LIMIT),
            CANONICAL_PAGE_INTEGER_VALIDATOR,
        ] = 50,
        strategy_id: str | None = Query(default=None, min_length=1),
        kind: RunKind | None = None,
    ) -> Page[BacktestRunSummary]:
        return backtest_runs.list_runs(
            PageRequest(offset=offset, limit=limit),
            strategy_id=strategy_id,
            kind=kind,
        )

    def admitted(call: Callable[[], _T]) -> _T:
        """시작·미리 계산·실험 기반 검사의 거절을 실행 요청 거절 코드로 낸다.

        시작 요청은 데이터를 읽지 않는 사전 검사만 해서(이슈 #158) 관측 데이터 부재·계약 위반을
        여기서 옮기지 않는다 — run 스레드의 tape 단계에서 run 상태 `failed` + `error` 로 기록된다.
        """
        try:
            return call()
        except Exception as error:
            _raise_run_request_rejection(error)

    def start_backtest(spec: BacktestRunSpec) -> BacktestStartResponse:
        return admitted(lambda: backtest_runs.start(spec))

    def preview_backtest_trial(spec: BacktestRunSpec) -> TrialPreview:
        """실행 전 미리 계산 — 이 요청이 결과를 내면 계열 N 에 새로 드는가(검증 랩 spec D2)."""
        return admitted(lambda: backtest_runs.preview_trial(spec))

    # 실행 요청 라우트(시작·미리 계산·미리보기·추적)는 본문 검증 실패를 코드화된 422 로 내려고
    # `CodedBodyValidationRoute` 로 등록한다(이슈 #260, #351). `app.post` 데코레이터는 라우트
    # 클래스를 받지 않는다.
    admission_responses: dict[int | str, dict[str, Any]] = {
        404: {
            "model": BacktestStrategyNotFoundResponse,
            "description": "The immutable strategy revision does not exist",
        },
        409: {
            "model": BacktestStrategyStaleResponse,
            "description": "The saved revision hash differs from the expected hash",
        },
        422: {
            "model": Backtest422Response,
            "description": "A coded backtest preflight or request-body diagnostic",
        },
    }
    app.router.add_api_route(
        "/api/v1/backtests",
        start_backtest,
        methods=["POST"],
        operation_id="startBacktest",
        status_code=status.HTTP_202_ACCEPTED,
        route_class_override=CodedBodyValidationRoute,
        responses=admission_responses,
    )
    app.router.add_api_route(
        "/api/v1/backtests/trial-preview",
        preview_backtest_trial,
        methods=["POST"],
        operation_id="previewBacktestTrial",
        route_class_override=CodedBodyValidationRoute,
        responses=admission_responses,
    )

    # 실험 라우트도 어시스턴트처럼 서비스가 올 때만 생긴다. 기반 실행 요청은 시작과 같은 판정이다.
    if experiments is not None:
        register_experiment_routes(app, experiments, admitted=admitted)

    strategy_not_found: dict[int | str, dict[str, Any]] = {
        404: {"model": StrategyNotFoundResponse, "description": "The strategy does not exist"}
    }

    @app.get(
        "/api/v1/strategies/{strategy_id}/trials",
        operation_id="getTrialLedger",
        responses=strategy_not_found,
    )
    def get_trial_ledger(strategy_id: str) -> TrialLedger:
        """계열 시도 원장(검증 랩 spec D2). 합쳐진 계열이면 남은 계열의 원장이다."""
        try:
            return backtest_runs.trial_ledger(strategy_id)
        except StrategyNotFoundError as error:
            raise _strategy_not_found(error) from error

    @app.post(
        "/api/v1/strategies/{strategy_id}/trials/merge",
        operation_id="mergeTrialLineage",
        responses={
            **strategy_not_found,
            409: {
                "model": TrialLineageAlreadyMergedResponse,
                "description": "The two lineages are already one",
            },
        },
    )
    def merge_trial_lineage(strategy_id: str, request: TrialLineageMergeRequest) -> TrialLedger:
        """`source_strategy_id` 계열을 이 계열에 합친다. 되돌릴 수 없다."""
        try:
            return backtest_runs.merge_lineages(request.source_strategy_id, strategy_id)
        except StrategyNotFoundError as error:
            raise _strategy_not_found(error) from error
        except TrialLineageAlreadyMergedError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "backtest.lineage.already_merged", "message": str(error)},
            ) from error

    @app.get(
        "/api/v1/backtests/{run_id}",
        operation_id="getBacktestStatus",
        responses=_backtest_run_not_found_responses(),
    )
    def get_backtest_status(run_id: str) -> BacktestRunState:
        try:
            return backtest_runs.state(run_id)
        except BacktestRunNotFoundError as error:
            raise _backtest_not_found(error) from error

    @app.get(
        "/api/v1/backtests/{run_id}/result",
        operation_id="getBacktestResult",
        responses={
            **_backtest_run_not_found_responses(),
            409: {
                "model": BacktestResultNotReadyResponse,
                "description": "The run has not completed with a result",
            },
            410: {
                "model": BacktestResultUnreadableResponse,
                "description": "The completed run's result file is missing, altered or unreadable",
            },
        },
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
        except BacktestArtifactUnreadableError as error:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"code": "backtest.result.unreadable", "message": str(error)},
            ) from error

    @app.get(
        "/api/v1/backtests/{run_id}/request",
        operation_id="getBacktestRequest",
        responses=_backtest_run_not_found_responses(),
    )
    def get_backtest_request(run_id: str) -> BacktestRunSpec:
        """Expose the server-owned accepted assumptions for audit and exact reruns."""

        try:
            return backtest_runs.request(run_id)
        except BacktestRunNotFoundError as error:
            raise _backtest_not_found(error) from error

    @app.post(
        "/api/v1/backtests/{run_id}/cancel",
        operation_id="cancelBacktest",
        responses=_backtest_run_not_found_responses(),
    )
    def cancel_backtest(run_id: str) -> BacktestCancelResult:
        """사용자가 run 에서 빠진다. 실험이 써서 계속 돌면 `kept_by_owners` 가 참이다."""
        try:
            return backtest_runs.cancel(run_id)
        except BacktestRunNotFoundError as error:
            raise _backtest_not_found(error) from error

    @app.get(
        "/api/v1/backtests/{run_id}/events",
        operation_id="streamBacktestEvents",
        response_class=StreamingResponse,
        responses={
            **_backtest_run_not_found_responses(),
            200: {"content": {"text/event-stream": {}}},
        },
    )
    def stream_backtest_events(
        run_id: str,
        after_sequence: int = Query(default=-1, ge=-1),
    ) -> StreamingResponse:
        try:
            backtest_runs.state(run_id)
        except BacktestRunNotFoundError as error:
            raise _backtest_not_found(error) from error
        return StreamingResponse(
            _backtest_event_stream(backtest_runs, run_id, after_sequence=after_sequence),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def portfolio_preview(request: PortfolioPreviewRequest) -> PortfolioPreview:
        try:
            return portfolio_design.preview(request)
        except (RawObservationUnavailableError, RawObservationContractError) as error:
            raise _portfolio_http_error(error) from error
        except Exception as error:
            _raise_run_request_rejection(error)

    app.router.add_api_route(
        "/api/v1/portfolio/preview",
        portfolio_preview,
        methods=["POST"],
        operation_id="previewPortfolio",
        route_class_override=CodedBodyValidationRoute,
        responses={
            422: {
                "model": Portfolio422Response,
                "description": "A coded portfolio preflight or request-body diagnostic",
            }
        },
    )

    async def trace_strategy(
        trace_request: StrategyTraceRequest, request: Request
    ) -> StrategyTraceResponse:
        """Bounded node/raw/target projection from the same calculation that builds TargetTape."""
        stop = Event()
        task = asyncio.create_task(
            asyncio.to_thread(strategy_traces.trace, trace_request, cancelled=stop.is_set)
        )
        try:
            while not task.done():
                if await request.is_disconnected():
                    stop.set()
                await asyncio.sleep(0.01)
            return await task
        except InvalidStrategyTraceRequestError as error:
            detail = TraceRequestInvalidDetail("trace.request.invalid", str(error))
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=asdict(detail),
            ) from error
        except StrategyTraceSourceNotFoundError as error:
            detail = TraceStrategyNotFoundDetail("trace.strategy.not_found", str(error))
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=asdict(detail),
            ) from error
        except StaleStrategyTraceSourceError as error:
            detail = TraceStrategyStaleDetail("trace.strategy.stale", str(error))
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=asdict(detail),
            ) from error
        except StrategyTraceSourceRequiresUpgradeError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=asdict(
                    TraceStrategyRequiresUpgradeDetail(
                        "trace.strategy.requires_upgrade", str(error)
                    )
                ),
            ) from error
        except IncompatiblePortfolioRequestError as error:
            detail = TraceEngineIncompatibleDetail("trace.engine.incompatible", error.compatibility)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=jsonable_encoder(asdict(detail)),
            ) from error
        except StrategyTraceCapabilityError as error:
            detail = TraceCapabilityUnsupportedDetail(
                "trace.capability.unsupported", error.capability, str(error)
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=asdict(detail),
            ) from error
        except (RawObservationUnavailableError, RawObservationContractError) as error:
            raise _portfolio_http_error(error) from error
        except StrategyTraceCancelledError as error:
            detail = TraceCancelledDetail("trace.cancelled", str(error))
            raise HTTPException(
                status_code=499,
                detail=asdict(detail),
            ) from error
        except Exception as error:
            # 문서 검증·실행 설정 거절은 실행 요청 판정이라 백테스트 시작과 같은 코드·detail
            # 이다(#351).
            _raise_run_request_rejection(error)
        finally:
            stop.set()

    app.router.add_api_route(
        "/api/v1/strategies/debug/trace",
        trace_strategy,
        methods=["POST"],
        operation_id="traceStrategy",
        route_class_override=CodedBodyValidationRoute,
        responses={
            404: {
                "model": TraceStrategyNotFoundResponse,
                "description": "The immutable strategy revision does not exist",
            },
            409: {
                "model": TraceStrategyStaleResponse,
                "description": "The saved revision hash differs from the expected hash",
            },
            422: {
                "model": Trace422Response,
                "description": "A coded trace preflight or request-body diagnostic",
            },
            499: {
                "model": TraceCancelledResponse,
                "description": "The client cancelled the trace request",
            },
        },
    )

    @app.get(
        EQUITY_CATALOG_PATH,
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
        try:
            return equity_workspace.preview_panel(request)
        except ResearchWindowViolationError as error:
            raise _research_window_rejection(error, ("body", "query", "start")) from error

    @app.post(
        "/api/v1/equity/preview",
        operation_id="previewEquityData",
    )
    def equity_preview(query: ResearchPanelQuery, venue: str = "XKRX") -> ResearchPreview:
        try:
            return equity_workspace.preview(query, venue=venue)
        except ResearchWindowViolationError as error:
            raise _research_window_rejection(error, ("body", "start")) from error

    @app.get(
        FACTOR_CATALOG_PATH,
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

    @app.get(
        "/api/v1/strategy-drafts/{draft_id}",
        operation_id="getStrategyDraft",
        responses={
            404: {
                "model": StrategyDraftErrorResponse,
                "description": "Draft does not exist",
            },
            422: {
                "model": StrategyDraft422Response,
                "description": "Malformed request or invalid draft identity/base",
            },
        },
    )
    def get_strategy_draft(draft_id: str) -> StrategyDraft:
        try:
            return strategy_drafts.get(draft_id)
        except StrategyDraftNotFoundError as error:
            raise _draft_not_found(error) from error
        except InvalidStrategyDraftError as error:
            raise _invalid_draft(error) from error

    @app.put(
        "/api/v1/strategy-drafts/{draft_id}",
        operation_id="saveStrategyDraft",
        responses={
            409: {
                "model": StrategyDraftConflictResponse,
                "description": "A different client advanced this draft version",
            },
            422: {
                "model": StrategyDraft422Response,
                "description": "Malformed request or invalid draft identity/base",
            },
        },
    )
    def save_strategy_draft(draft_id: str, request: SaveStrategyDraftRequest) -> StrategyDraft:
        try:
            return strategy_drafts.save(draft_id, request)
        except StrategyDraftConflictError as error:
            raise _draft_conflict(error) from error
        except InvalidStrategyDraftError as error:
            raise _invalid_draft(error) from error

    @app.delete(
        "/api/v1/strategy-drafts/{draft_id}",
        operation_id="deleteStrategyDraft",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            404: {
                "model": StrategyDraftErrorResponse,
                "description": "Draft does not exist",
            },
            409: {
                "model": StrategyDraftConflictResponse,
                "description": "A different client advanced this draft version",
            },
            422: {
                "model": StrategyDraft422Response,
                "description": "Malformed request or invalid draft identity/base",
            },
        },
    )
    def delete_strategy_draft(
        draft_id: str,
        expected_version: int = Query(ge=1),
    ) -> Response:
        try:
            strategy_drafts.delete(draft_id, expected_version=expected_version)
        except StrategyDraftNotFoundError as error:
            raise _draft_not_found(error) from error
        except StrategyDraftConflictError as error:
            raise _draft_conflict(error) from error
        except InvalidStrategyDraftError as error:
            raise _invalid_draft(error) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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
        "/api/v1/strategy-documents/upgrade",
        operation_id="upgradeStrategyDocument",
        responses={
            422: {
                "model": StrategyDocumentUpgrade422Response,
                "description": (
                    "Syntax errors, a document with no upgrade chain, a saved-reference node, "
                    "or upgrade rule drift"
                ),
            },
        },
    )
    def upgrade_strategy_document(request: CompileRequest) -> UpgradedDocument:
        """Rewrite a retired-schema source as the current version and compile the result.

        Comments and order are kept. The rewrite must parse to exactly what the domain dict
        transform yields; otherwise the service refuses with `strategy_document.upgrade_drift`
        rather than returning text that would silently mean something else (spec D3). The
        execution settings the retired document carried come back as `environment`, and facts
        the user should know (a folded missing policy, a changed weighting rule, settings that
        could not be moved) as `warnings` (spec D7).
        """
        try:
            return strategy_authoring.upgrade(request)
        except DocumentUpgradeSyntaxError as error:
            raise _invalid_document_compiled(error.compiled) from error
        except DocumentNotUpgradeableError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=asdict(
                    StrategyDocumentNotUpgradeableDetail(
                        "strategy_document.not_upgradeable",
                        None if error.schema_version is None else str(error.schema_version),
                        str(error),
                    )
                ),
            ) from error
        except DocumentUpgradeDriftError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=asdict(
                    StrategyDocumentUpgradeDriftDetail(
                        "strategy_document.upgrade_drift", error.pointer, str(error)
                    )
                ),
            ) from error
        except DocumentUpgradeUnsupportedNodeError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=asdict(
                    StrategyDocumentUpgradeUnsupportedNodeDetail(
                        "strategy_document.upgrade_unsupported_node", error.pointer, str(error)
                    )
                ),
            ) from error

    @app.post(
        "/api/v1/strategy-documents",
        operation_id="createStrategyDocument",
        status_code=status.HTTP_201_CREATED,
        responses={
            422: {
                "model": StrategyDocumentSave422Response,
                "description": "Source has error-severity diagnostics, or malformed envelope",
            },
        },
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
        responses={
            409: {
                "model": StrategyRevisionConflictResponse,
                "description": "The expected revision is stale",
            },
            422: {
                "model": StrategyDocumentSave422Response,
                "description": "Source has error-severity diagnostics, or malformed envelope",
            },
        },
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
            raise _revision_conflict(error) from error

    @app.get(
        "/api/v1/strategies/{strategy_id}/revisions",
        operation_id="listStrategyRevisions",
    )
    def list_strategy_revisions(
        strategy_id: str,
        offset: Annotated[
            int,
            Query(ge=0, le=PageRequest.MAX_OFFSET),
            CANONICAL_PAGE_INTEGER_VALIDATOR,
        ] = 0,
        limit: Annotated[
            int,
            Query(ge=1, le=PageRequest.MAX_LIMIT),
            CANONICAL_PAGE_INTEGER_VALIDATOR,
        ] = 50,
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
        responses={304: {"description": "Not modified (ETag matched If-None-Match)"}},
    )
    def strategy_document_schema(
        response: Response, if_none_match: Annotated[str | None, Header()] = None
    ) -> StrategyDocumentSchema | Response:
        """Runtime JSON Schema of the authoring document. ETag = schema hash (304 on match)."""
        schema = strategy_authoring.schema()
        etag = _etag(schema.schema_hash)
        if if_none_match is not None and _matches(if_none_match, etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return schema

    @app.get(
        "/api/v1/run-environments/schema",
        operation_id="getRunEnvironmentSchema",
        response_model=RunEnvironmentSchema,
        responses={304: {"description": "Not modified (ETag matched If-None-Match)"}},
    )
    def run_environment_schema(
        response: Response, if_none_match: Annotated[str | None, Header()] = None
    ) -> RunEnvironmentSchema | Response:
        """실행 설정의 런타임 JSON Schema. ETag = 스키마 해시(일치하면 304)."""
        schema = strategy_authoring.run_environment_schema()
        etag = _etag(schema.schema_hash)
        if if_none_match is not None and _matches(if_none_match, etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return schema

    @app.get(
        "/api/v1/strategy-documents/operators",
        operation_id="getStrategyOperatorCatalog",
        response_model=StrategyOperatorCatalog,
        responses={304: {"description": "Not modified (ETag matched If-None-Match)"}},
    )
    def strategy_operator_catalog(
        response: Response, if_none_match: Annotated[str | None, Header()] = None
    ) -> StrategyOperatorCatalog | Response:
        """그래프 노드 연산자 정의 전부.

        입력 개수, 읽는 파라미터, 출력 타입·단위 규칙, 가용성, i18n 키를 담는다.

        팔레트·노드 라벨이 보일 수 있는 연산자 목록의 유일한 출처다. 소비자는 목록을 다시 적지
        않는다. ETag는 카탈로그 해시이며 If-None-Match가 맞으면 304로 답한다.
        """
        catalog = strategy_authoring.operators()
        etag = _etag(catalog.catalog_hash)
        if if_none_match is not None and _matches(if_none_match, etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return catalog

    @app.get(
        "/api/v1/strategy-documents/contract",
        operation_id="getStrategyDocumentContract",
        response_model=StrategyDocumentContractResponse,
        responses={304: {"description": "Not modified (ETag matched If-None-Match)"}},
    )
    def strategy_document_contract(
        response: Response, if_none_match: Annotated[str | None, Header()] = None
    ) -> StrategyDocumentContractResponse | Response:
        """Per-field authoring contract (type, enum, range, unit, default, example, stage)
        with the factor/dataset registry versions and catalog links it pairs with.
        """
        contract = strategy_authoring.contract()
        etag = _etag(contract.contract_hash)
        if if_none_match is not None and _matches(if_none_match, etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return StrategyDocumentContractResponse(
            contract=contract,
            factor_catalog_url=FACTOR_CATALOG_PATH,
            equity_catalog_url=EQUITY_CATALOG_PATH,
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
        "/api/v1/strategies",
        operation_id="listStrategies",
    )
    def list_strategies(
        offset: Annotated[
            int,
            Query(ge=0, le=PageRequest.MAX_OFFSET),
            CANONICAL_PAGE_INTEGER_VALIDATOR,
        ] = 0,
        limit: Annotated[
            int,
            Query(ge=1, le=PageRequest.MAX_LIMIT),
            CANONICAL_PAGE_INTEGER_VALIDATOR,
        ] = 50,
    ) -> Page[StrategySummary]:
        """Latest immutable revision of every strategy, ordered by strategy id."""
        return strategy_documents.list_strategies(PageRequest(offset, limit))

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
            raise _strategy_not_found(error) from error

    @app.post(
        "/api/v1/strategies/{strategy_id}/revisions",
        operation_id="reviseStrategy",
        status_code=status.HTTP_201_CREATED,
        responses={
            409: {
                "model": StrategyRevisionConflictResponse,
                "description": "The expected revision is stale or the authoring mode conflicts",
            }
        },
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
            raise _strategy_not_found(error) from error
        except StrategyRevisionConflictError as error:
            raise _revision_conflict(error) from error

    if (
        assistant_profiles is not None
        and assistant_chat is not None
        and assistant_turns is not None
    ):
        register_assistant_routes(
            app, profiles=assistant_profiles, chat=assistant_chat, turns=assistant_turns
        )

    # FastAPI sees plain dataclasses, while this inbound adapter owns wire-only constraints and
    # discriminator metadata. Mutate the cached schema once after every route is registered.
    apply_trace_openapi_contract(app.openapi())
    return app


@dataclass(frozen=True)
class StrategyDocumentContractResponse:
    """Wire envelope: the application contract plus the catalog links this API serves."""

    contract: StrategyDocumentContract
    factor_catalog_url: str
    equity_catalog_url: str


def _etag(schema_hash: str) -> str:
    return f'"{schema_hash}"'


def _matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 §13.1.2: `*` matches any current representation; weak tags compare by value."""
    if if_none_match.strip() == "*":
        return True
    return etag in {item.strip().removeprefix("W/") for item in if_none_match.split(",")}


def _strategy_not_found(error: StrategyNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "strategy.not_found", "message": str(error)},
    )


def _invalid_document(error: InvalidStrategyDocumentError) -> HTTPException:
    return _invalid_document_compiled(error.compiled)


def _invalid_document_compiled(compiled: CompiledDocument) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "strategy_document.invalid",
            "source_hash": compiled.source_hash,
            "schema_version": compiled.schema_version,
            "diagnostics": jsonable_encoder([asdict(d) for d in compiled.diagnostics]),
        },
    )


def _research_window_rejection(
    error: ResearchWindowViolationError, loc: tuple[str, ...]
) -> RequestValidationError:
    """equity 미리보기의 연구 구간 거절을 이 경로의 기존 422(본문 검증 실패 목록)로 싣는다(spec D1).

    두 경로는 코드화된 422 detail 이 없고 본문 검증 실패 `HTTPValidationError` 만 선언한다. 같은
    모양의 항목 하나로 싣고 `type` 에 진단 코드, `msg` 에 backend 가 날짜까지 넣어 완성한 문장을
    둔다.
    """
    return RequestValidationError([{"type": error.code, "loc": loc, "msg": str(error)}])


def _raise_run_request_rejection(error: Exception) -> NoReturn:
    """실행 요청 판정의 거절이면 코드화된 HTTP 오류로, 아니면 원래 오류를 그대로 올린다.

    시작·미리 계산·실험 기반 검사·미리보기·추적은 같은 요청 판정(문서 검증·실행 설정 관문)을 타므로
    거절도 같은 코드·detail 로 낸다(#351). 거절 목록과 코드는 실행 유스케이스의 `rejection_code`
    하나가 소유하고, 여기서는 HTTP 상태와 코드별 detail 칸만 붙인다. 관측 데이터 부재·계약 위반은
    요청 판정이 아니라 부르는 쪽이 옮긴다. 실험 포트가 코드를 실어 감싼 거절
    (`TrialRunRejectedError`)은 감싼 원래 거절로 옮긴다.
    """
    if isinstance(error, TrialRunRejectedError) and isinstance(error.__cause__, Exception):
        error = error.__cause__
    if isinstance(error, InvalidPortfolioRequestError):
        raise _portfolio_http_error(error) from error
    code = rejection_code(error)
    if code is None:
        raise error
    detail: dict[str, object] = {"code": code, "message": str(error)}
    if isinstance(error, ResearchWindowViolationError):
        detail |= {
            "sealed_start": error.sealed_start.isoformat(),
            "sealed_end": error.sealed_end.isoformat(),
            "research_start": error.research_start.isoformat(),
        }
    elif isinstance(error, BacktestParameterValueError):
        detail["parameter_id"] = error.parameter_id
    raise HTTPException(
        status_code=_ADMISSION_STATUS.get(code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        detail=detail,
    ) from error


def _portfolio_http_error(
    error: InvalidPortfolioRequestError
    | RawObservationUnavailableError
    | RawObservationContractError,
) -> HTTPException:
    """Same coded 422 for the preview and backtest routes: the pipeline rejected the request."""
    if isinstance(error, InvalidPortfolioRequestError):
        detail: dict[str, object] = {
            "code": "portfolio.strategy.invalid",
            "validation": jsonable_encoder(asdict(error.validation)),
        }
    elif isinstance(error, RawObservationUnavailableError):
        detail = {
            "code": "portfolio.data.unavailable",
            "status": error.status.value,
            "detail": error.detail,
        }
    else:
        detail = asdict(
            PortfolioRawObservationInvalidDetail(
                "portfolio.raw_observation.invalid",
                str(error),
            )
        )
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)
