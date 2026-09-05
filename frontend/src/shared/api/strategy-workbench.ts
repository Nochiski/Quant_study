import { client } from "./generated/client.gen";
import {
  cancelBacktest,
  compileStrategyDocument,
  createStrategy,
  createStrategyDocument,
  deleteStrategyDraft,
  diffStrategyRevisions,
  explainFactorGraph,
  getEquityCatalog,
  getFactorCatalog,
  getBacktestResult,
  getStrategy,
  getStrategyDraft,
  getStrategyDocument,
  getStrategyDocumentContract,
  getStrategyDocumentSchema,
  getBacktestStatus,
  listStrategyRevisions,
  listBacktests,
  listStrategies,
  getStrategyTemplate,
  previewFactorGraph,
  previewPortfolio,
  previewEquityData,
  previewEquityPanel,
  previewEquityUniverse,
  reviseStrategy,
  reviseStrategyDocument,
  saveStrategyDraft,
  startBacktest,
  traceStrategy as postStrategyTrace,
  validateFactorGraph,
  validateStrategy,
} from "./generated/sdk.gen";
import type {
  BacktestRunResult,
  BacktestRunSpec,
  BacktestRunState,
  BacktestRunSummary,
  BacktestStartResponse,
  CompileRequest,
  CompiledDocument,
  DataStep,
  DatasetFieldProfile,
  DiffEntry,
  FactorCatalog,
  FactorDefinition,
  FactorExplanation,
  FactorGraph,
  FactorGraphRequest,
  FactorGraphValidation,
  FactorPreview,
  FactorPreviewRequest,
  FactorSignal,
  FactorValidationIssue,
  FieldContract,
  GetEquityCatalogData,
  InlineDraft,
  GetFactorCatalogData,
  NodeContract,
  MetricDefinition,
  MetricValue,
  PageRevisionSummary,
  PageBacktestRunSummary,
  PageStrategySummary,
  PortfolioPreview,
  PortfolioPreviewRequest,
  ResearchCatalog,
  ResearchPanelCell,
  ResearchPanelPreview,
  ResearchPanelPreviewRequest,
  ResearchPanelQuery,
  ResearchPreview,
  ReviseDocumentRequest,
  RevisionDiff,
  RevisionSummary,
  SaveDocumentRequest,
  SaveStrategyDraftRequest,
  SavedRevisionReference,
  SavedStrategy,
  SourceDiagnostic,
  StrategyDocument,
  StrategyDocumentContractResponse,
  StrategyDocumentSchema,
  StrategyDraft,
  StrategyDraftConflictDetail,
  StrategyRevisionConflictDetail,
  StrategySpec,
  StrategySummary,
  StrategyTraceRequest,
  StrategyTraceResponse,
  StrategyValidation,
  UniverseHistoryQuery,
  UniversePreview,
} from "./generated/types.gen";

export const configureStrategyWorkbenchApi = (baseUrl: string): void => {
  client.setConfig({ baseUrl });
};

configureStrategyWorkbenchApi(
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
);

/**
 * A non-2xx reply the caller can branch on (404 → route not-found, 409 → conflict UI, 422 →
 * invalid document). `detail` is the server's human-readable message, when it sent one.
 */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly detail: string | undefined;
  readonly latestRevision: number | null;
  readonly currentDraft: StrategyDraft | null;

  constructor(
    context: string,
    status: number,
    code?: string,
    detail?: string,
    latestRevision: number | null = null,
    currentDraft: StrategyDraft | null = null,
  ) {
    super(
      `API request failed: ${context} status=${status} code=${code ?? "-"}`,
    );
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.latestRevision = latestRevision;
    this.currentDraft = currentDraft;
  }
}

const requireData = <T>(data: T | undefined, context: string): T => {
  if (data === undefined) {
    throw new Error(`API response did not contain data: ${context}`);
  }
  return data;
};

const errorField = (
  error: unknown,
  field: "code" | "message",
): string | undefined => {
  if (typeof error !== "object" || error === null || !("detail" in error))
    return undefined;
  const detail = (error as { detail: unknown }).detail;
  return typeof detail === "object" && detail !== null && field in detail
    ? String((detail as Record<string, unknown>)[field])
    : undefined;
};

const errorCode = (error: unknown): string | undefined =>
  errorField(error, "code");

/** Runtime check at the HTTP boundary for the generated structured 409 detail. */
const revisionConflictDetail = (
  error: unknown,
): StrategyRevisionConflictDetail | null => {
  if (typeof error !== "object" || error === null || !("detail" in error))
    return null;
  const detail = (error as { detail: unknown }).detail;
  if (typeof detail !== "object" || detail === null) return null;
  const candidate = detail as Partial<StrategyRevisionConflictDetail>;
  return candidate.code === "strategy.revision_conflict" &&
    typeof candidate.message === "string" &&
    (candidate.latest_revision === null ||
      (typeof candidate.latest_revision === "number" &&
        Number.isSafeInteger(candidate.latest_revision) &&
        candidate.latest_revision > 0))
    ? (candidate as StrategyRevisionConflictDetail)
    : null;
};

/** Runtime check for a CAS conflict. Invalid payloads fail closed as an ordinary request error. */
const draftConflictDetail = (
  error: unknown,
): StrategyDraftConflictDetail | null => {
  if (typeof error !== "object" || error === null || !("detail" in error))
    return null;
  const detail = (error as { detail: unknown }).detail;
  if (typeof detail !== "object" || detail === null) return null;
  const candidate = detail as Partial<StrategyDraftConflictDetail>;
  const current = candidate.current;
  const value =
    typeof current === "object" && current !== null
      ? (current as unknown as Record<string, unknown>)
      : null;
  const savedIdentity =
    value === null
      ? false
      : value.strategy_id === null &&
          value.base_revision === null &&
          value.base_spec_hash === null
        ? true
        : typeof value.strategy_id === "string" &&
          value.strategy_id.trim() !== "" &&
          typeof value.base_revision === "number" &&
          Number.isSafeInteger(value.base_revision) &&
          value.base_revision > 0 &&
          typeof value.base_spec_hash === "string" &&
          /^[a-f0-9]{64}$/u.test(value.base_spec_hash);
  const validCurrent =
    current === null ||
    (value !== null &&
      typeof value.draft_id === "string" &&
      value.draft_id.trim() !== "" &&
      value.draft_id.length <= 512 &&
      typeof value.version === "number" &&
      Number.isSafeInteger(value.version) &&
      value.version > 0 &&
      typeof value.source === "string" &&
      (value.format === "yaml" || value.format === "json") &&
      typeof value.source_hash === "string" &&
      /^[a-f0-9]{64}$/u.test(value.source_hash) &&
      typeof value.schema_version === "string" &&
      value.schema_version.trim() !== "" &&
      typeof value.updated_at === "string" &&
      Number.isFinite(Date.parse(value.updated_at)) &&
      savedIdentity);
  return candidate.code === "strategy.draft.conflict" &&
    typeof candidate.message === "string" &&
    validCurrent
    ? (candidate as StrategyDraftConflictDetail)
    : null;
};

const requestError = (
  response: { error?: unknown; response?: { status: number } },
  context: string,
): ApiRequestError => {
  const conflict = revisionConflictDetail(response.error);
  const draftConflict = draftConflictDetail(response.error);
  return new ApiRequestError(
    context,
    response.response?.status ?? 0,
    errorCode(response.error),
    errorField(response.error, "message"),
    conflict?.latest_revision ?? null,
    draftConflict?.current ?? null,
  );
};

/** Turns an SDK reply into data or a typed error; every document call goes through here. */
const unwrap = <T>(
  response: { data?: T; error?: unknown; response?: { status: number } },
  context: string,
): T => {
  if (response.error !== undefined) {
    throw requestError(response, context);
  }
  return requireData(response.data, context);
};

export const strategyWorkbenchApi = {
  async listBacktests(
    page: { offset?: number; limit?: number; strategyId?: string } = {},
  ): Promise<PageBacktestRunSummary> {
    const response = await listBacktests({
      query: {
        offset: page.offset,
        limit: page.limit,
        strategy_id: page.strategyId,
      },
    });
    return unwrap(response, "listBacktests");
  },

  async startBacktest(spec: BacktestRunSpec): Promise<BacktestStartResponse> {
    const response = await startBacktest({ body: spec });
    return requireData(response.data, "startBacktest");
  },

  async getBacktestStatus(runId: string): Promise<BacktestRunState> {
    const response = await getBacktestStatus({ path: { run_id: runId } });
    return requireData(response.data, "getBacktestStatus");
  },

  async getBacktestResult(runId: string): Promise<BacktestRunResult> {
    const response = await getBacktestResult({ path: { run_id: runId } });
    return requireData(response.data, "getBacktestResult");
  },

  async cancelBacktest(runId: string): Promise<BacktestRunState> {
    const response = await cancelBacktest({ path: { run_id: runId } });
    return requireData(response.data, "cancelBacktest");
  },

  /** Bounded projection from the same calculation that produces TargetTape/backtest input. */
  async traceStrategy(
    request: StrategyTraceRequest,
    signal?: AbortSignal,
  ): Promise<StrategyTraceResponse> {
    const response = await postStrategyTrace({ body: request, signal });
    return unwrap(response, "traceStrategy");
  },

  async getTemplate(): Promise<StrategySpec> {
    const response = await getStrategyTemplate();
    return requireData(response.data, "getStrategyTemplate");
  },

  async getEquityCatalog(
    query: EquityCatalogQuery = {},
  ): Promise<ResearchCatalog> {
    const response = await getEquityCatalog({ query });
    return requireData(response.data, "getEquityCatalog");
  },

  async previewUniverse(query: UniverseHistoryQuery): Promise<UniversePreview> {
    const response = await previewEquityUniverse({ body: query });
    return requireData(response.data, "previewEquityUniverse");
  },

  async previewPanel(
    request: ResearchPanelPreviewRequest,
  ): Promise<ResearchPanelPreview> {
    const response = await previewEquityPanel({ body: request });
    return requireData(response.data, "previewEquityPanel");
  },

  async previewEquity(
    query: ResearchPanelQuery,
    venue = "XKRX",
  ): Promise<ResearchPreview> {
    const response = await previewEquityData({ body: query, query: { venue } });
    return requireData(response.data, "previewEquityData");
  },

  async getFactorCatalog(
    query: FactorCatalogQuery = {},
  ): Promise<FactorCatalog> {
    const response = await getFactorCatalog({ query });
    return requireData(response.data, "getFactorCatalog");
  },

  async validateFactorGraph(
    request: FactorGraphRequest,
  ): Promise<FactorGraphValidation> {
    const response = await validateFactorGraph({ body: request });
    return requireData(response.data, "validateFactorGraph");
  },

  async explainFactorGraph(
    request: FactorGraphRequest,
    signal?: AbortSignal,
  ): Promise<FactorExplanation> {
    const response = await explainFactorGraph({ body: request, signal });
    return requireData(response.data, "explainFactorGraph");
  },

  async previewFactorGraph(
    request: FactorPreviewRequest,
  ): Promise<FactorPreview> {
    const response = await previewFactorGraph({ body: request });
    return requireData(response.data, "previewFactorGraph");
  },

  async previewPortfolio(
    request: PortfolioPreviewRequest,
  ): Promise<PortfolioPreview> {
    const response = await previewPortfolio({ body: request });
    return requireData(response.data, "previewPortfolio");
  },

  async validate(spec: StrategySpec): Promise<StrategyValidation> {
    const response = await validateStrategy({ body: spec });
    return requireData(response.data, "validateStrategy");
  },

  async getStrategy(
    strategyId: string,
    revision?: number,
  ): Promise<SavedStrategy> {
    const response = await getStrategy({
      path: { strategy_id: strategyId },
      query: revision === undefined ? undefined : { revision },
    });
    if (response.error !== undefined) {
      throw new ApiRequestError(
        "getStrategy",
        response.response?.status ?? 0,
        errorCode(response.error),
      );
    }
    return requireData(response.data, "getStrategy");
  },

  async listStrategies(
    page: { offset?: number; limit?: number } = {},
  ): Promise<PageStrategySummary> {
    const response = await listStrategies({ query: page });
    return unwrap(response, "listStrategies");
  },

  async getStrategyDraft(draftId: string): Promise<StrategyDraft> {
    const response = await getStrategyDraft({ path: { draft_id: draftId } });
    return unwrap(response, "getStrategyDraft");
  },

  async saveStrategyDraft(
    draftId: string,
    request: SaveStrategyDraftRequest,
  ): Promise<StrategyDraft> {
    const response = await saveStrategyDraft({
      path: { draft_id: draftId },
      body: request,
    });
    return unwrap(response, "saveStrategyDraft");
  },

  async deleteStrategyDraft(
    draftId: string,
    expectedVersion: number,
  ): Promise<void> {
    const response = await deleteStrategyDraft({
      path: { draft_id: draftId },
      query: { expected_version: expectedVersion },
    });
    if (response.error !== undefined)
      throw requestError(response, "deleteStrategyDraft");
  },

  async getStrategyDocument(
    strategyId: string,
    revision: number,
  ): Promise<StrategyDocument> {
    const response = await getStrategyDocument({
      path: { strategy_id: strategyId, revision },
    });
    return unwrap(response, "getStrategyDocument");
  },

  /** Compile exact text; `signal` aborts a request the editor has already superseded. */
  async compileStrategyDocument(
    request: CompileRequest,
    signal?: AbortSignal,
  ): Promise<CompiledDocument> {
    const response = await compileStrategyDocument({ body: request, signal });
    return unwrap(response, "compileStrategyDocument");
  },

  async getStrategyDocumentSchema(): Promise<StrategyDocumentSchema> {
    const response = await getStrategyDocumentSchema();
    return unwrap(response, "getStrategyDocumentSchema");
  },

  async getStrategyDocumentContract(): Promise<StrategyDocumentContractResponse> {
    const response = await getStrategyDocumentContract();
    return unwrap(response, "getStrategyDocumentContract");
  },

  async createStrategyDocument(
    request: SaveDocumentRequest,
  ): Promise<StrategyDocument> {
    const response = await createStrategyDocument({ body: request });
    return unwrap(response, "createStrategyDocument");
  },

  async reviseStrategyDocument(
    strategyId: string,
    request: ReviseDocumentRequest,
  ): Promise<StrategyDocument> {
    const response = await reviseStrategyDocument({
      path: { strategy_id: strategyId },
      body: request,
    });
    return unwrap(response, "reviseStrategyDocument");
  },

  /** Semantic diff between two stored revisions (identity, comments, formatting invisible). */
  async diffStrategyRevisions(
    strategyId: string,
    base: number,
    target: number,
  ): Promise<RevisionDiff> {
    const response = await diffStrategyRevisions({
      path: { strategy_id: strategyId },
      query: { base, target },
    });
    return unwrap(response, "diffStrategyRevisions");
  },

  async listStrategyRevisions(
    strategyId: string,
    page: { offset?: number; limit?: number } = {},
  ): Promise<PageRevisionSummary> {
    const response = await listStrategyRevisions({
      path: { strategy_id: strategyId },
      query: page,
    });
    return unwrap(response, "listStrategyRevisions");
  },

  async create(spec: StrategySpec): Promise<SavedStrategy> {
    const response = await createStrategy({ body: spec });
    return requireData(response.data, "createStrategy");
  },

  async revise(
    identity: { strategyId: string; revision: number },
    spec: StrategySpec,
  ): Promise<SavedStrategy> {
    const response = await reviseStrategy({
      body: {
        expected_revision: identity.revision,
        spec,
      },
      path: { strategy_id: identity.strategyId },
    });
    return requireData(response.data, "reviseStrategy");
  },
};

export type EquityCatalogQuery = NonNullable<GetEquityCatalogData["query"]>;
export type FactorCatalogQuery = NonNullable<GetFactorCatalogData["query"]>;

export type {
  BacktestRunResult,
  BacktestRunSpec,
  BacktestRunState,
  BacktestRunSummary,
  BacktestStartResponse,
  CompileRequest,
  CompiledDocument,
  DataStep,
  DatasetFieldProfile,
  DiffEntry,
  FactorCatalog,
  FactorDefinition,
  FactorExplanation,
  FactorGraph,
  FactorGraphRequest,
  FactorGraphValidation,
  FactorPreview,
  FactorPreviewRequest,
  FactorSignal,
  FactorValidationIssue,
  FieldContract,
  InlineDraft,
  NodeContract,
  MetricDefinition,
  MetricValue,
  PageRevisionSummary,
  PageBacktestRunSummary,
  PageStrategySummary,
  PortfolioPreview,
  PortfolioPreviewRequest,
  ResearchCatalog,
  ResearchPanelCell,
  ResearchPanelPreview,
  ResearchPanelPreviewRequest,
  ResearchPanelQuery,
  ResearchPreview,
  ReviseDocumentRequest,
  RevisionDiff,
  RevisionSummary,
  SaveDocumentRequest,
  SaveStrategyDraftRequest,
  SavedRevisionReference,
  SavedStrategy,
  SourceDiagnostic,
  StrategyDocument,
  StrategyDocumentContractResponse,
  StrategyDocumentSchema,
  StrategyDraft,
  StrategySpec,
  StrategySummary,
  StrategyTraceRequest,
  StrategyTraceResponse,
  StrategyValidation,
  UniverseHistoryQuery,
  UniversePreview,
};
