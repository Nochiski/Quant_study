import { client } from "./generated/client.gen";
import {
  cancelBacktest,
  compileStrategyDocument,
  createStrategyDocument,
  deleteStrategyDraft,
  diffStrategyRevisions,
  explainFactorGraph,
  getEquityCatalog,
  getFactorCatalog,
  getBacktestRequest,
  getBacktestResult,
  getStrategyDraft,
  getStrategyDocument,
  getStrategyDocumentContract,
  getStrategyDocumentSchema,
  getStrategyOperatorCatalog,
  getBacktestStatus,
  getRunEnvironmentSchema,
  getTrialLedger,
  listBacktests,
  listStrategies,
  listStrategyRevisions,
  mergeTrialLineage,
  previewBacktestTrial,
  reviseStrategyDocument,
  saveStrategyDraft,
  startBacktest,
  traceStrategy as postStrategyTrace,
  upgradeStrategyDocument,
} from "./generated/sdk.gen";
import type {
  ApplicableWhen,
  BacktestRunResult,
  BacktestRunSpec,
  BacktestRunState,
  BacktestRunSummary,
  BacktestStartResponse,
  CompileRequest,
  CompiledDocument,
  DatasetFieldProfile,
  DiffEntry,
  FactorCatalog,
  FactorDefinition,
  FactorExplanation,
  FactorGraph,
  FactorGraphRequest,
  FactorValidationIssue,
  FieldContract,
  GetEquityCatalogData,
  GetFactorCatalogData,
  InlineDraft,
  MetricDefinition,
  MetricValue,
  NodeContract,
  OperatorDefinition,
  PageBacktestRunSummary,
  PageRevisionSummary,
  PageStrategySummary,
  ResearchCatalog,
  ReviseDocumentRequest,
  RevisionDiff,
  RevisionSummary,
  RunEnvironment,
  RunEnvironmentSchema,
  RunKind,
  SaveDocumentRequest,
  SaveStrategyDraftRequest,
  SavedRevisionReference,
  SourceDiagnostic,
  StrategyDocument,
  StrategyDocumentContractResponse,
  StrategyDocumentInvalidDetail,
  StrategyDocumentSchema,
  StrategyDraft,
  StrategyDraftConflictDetail,
  StrategyOperatorCatalog,
  StrategyRevisionConflictDetail,
  StrategySpec,
  StrategySummary,
  StrategyTraceRequest,
  StrategyTraceResponse,
  TrialLedger,
  TrialPreview,
  UpgradedDocument,
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
  /** 거절이 가리킨 요청 본문의 칸(점 경로, 예 `initial_cash`). detail 에 `field` 가 없으면 undefined. */
  readonly field: string | undefined;
  /**
   * 코드가 없는 FastAPI 기본 422(배열 `detail`)의 진단 요약. `detail` 과 달리 화면 본문에 쓰지 않고 접힌 진단
   * 상세에만 쓴다 — 저장 상태 줄(409·422)은 `detail` 을 본문으로 그린다(#268 리뷰 P3-4).
   */
  readonly diagnostic: string | undefined;
  /** detail 의 문자열 칸(`message` 제외). 거절 문장의 `{이름}` 자리표시자를 채운다(예: 연구 구간 날짜). */
  readonly values: Readonly<Record<string, string>>;

  constructor(
    context: string,
    status: number,
    code?: string,
    detail?: string,
    latestRevision: number | null = null,
    currentDraft: StrategyDraft | null = null,
    field?: string,
    diagnostic?: string,
    values: Readonly<Record<string, string>> = {},
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
    this.field = field;
    this.diagnostic = diagnostic;
    this.values = values;
  }
}

/**
 * 실패한 요청의 서버 사유 — 화면이 접힌 진단 상세("서버 사유")에 두는 원문의 유일한 출처다. backend가 보낸
 * 문장(`detail`)이나 코드 없는 422 요약(`diagnostic`)만 싣고, `ApiRequestError.message`(`API request
 * failed: …`)는 개발자 진단이라 싣지 않는다. 응답 없이 난 오류(네트워크 등)는 그 오류 문장을 남긴다. 화면
 * 본문은 코드 번역이 맡는다(`.claude/rules/frontend-api-state.md`, #270).
 */
export const failureReason = (error: unknown): string | null =>
  error instanceof ApiRequestError
    ? (error.detail ?? error.diagnostic ?? null)
    : error instanceof Error
      ? error.message
      : null;

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

/** detail 의 `field`(문자열일 때만). `errorField` 는 null 을 "null" 문자열로 바꾸므로 따로 읽는다. */
const detailFieldPath = (error: unknown): string | undefined => {
  if (typeof error !== "object" || error === null || !("detail" in error))
    return undefined;
  const detail = (error as { detail: unknown }).detail;
  if (typeof detail !== "object" || detail === null || !("field" in detail))
    return undefined;
  const value = (detail as { field: unknown }).field;
  return typeof value === "string" ? value : undefined;
};

const detailValues = (error: unknown): Record<string, string> => {
  if (typeof error !== "object" || error === null || !("detail" in error))
    return {};
  const detail = (error as { detail: unknown }).detail;
  if (typeof detail !== "object" || detail === null) return {};
  return Object.fromEntries(
    // 서버 원문 `message` 는 번역 문장에 새지 않게 뺀다 — 접힌 진단 상세로만 간다.
    Object.entries(detail).filter(
      (entry): entry is [string, string] =>
        entry[0] !== "message" && typeof entry[1] === "string",
    ),
  );
};

/**
 * 저장·revise·upgrade의 `strategy_document.invalid` detail에는 `message`가 없다 — 첫 error 진단(pointer + 문구)을
 * detail 문구로 쓴다(Phase 5 감사 backlog 16: 저장 실패 사유가 화면에 비어 있었다).
 */
export const invalidDocumentSummary = (error: unknown): string | undefined => {
  if (errorCode(error) !== "strategy_document.invalid") return undefined;
  const detail = (error as { detail: Partial<StrategyDocumentInvalidDetail> })
    .detail;
  const diagnostics = Array.isArray(detail.diagnostics)
    ? detail.diagnostics
    : [];
  const first =
    diagnostics.find((item) => item.severity === "error") ?? diagnostics[0];
  if (first === undefined) return undefined;
  const pointer = first.pointer === "" ? "/" : first.pointer;
  const rest = diagnostics.length - 1;
  return `${pointer}: ${first.message}${rest > 0 ? ` (+${rest})` : ""}`;
};

/**
 * FastAPI 기본 422(`detail` 배열)의 진단 문장. 첫 오류의 본문 경로와 문장에 나머지 개수를 붙인다. 코드화된
 * 계약이 없는 라우트에서도 서버 사유를 버리지 않으려는 방어다(이슈 #260: 배열 detail 에서 `code`·`message`
 * 를 꺼내지 못해 사유가 사라졌다). `ApiRequestError.diagnostic` 에만 싣는다 — 화면 본문이 아니라 접힌 진단 상세에
 * 쓴다.
 */
export const requestValidationSummary = (
  error: unknown,
): string | undefined => {
  if (typeof error !== "object" || error === null || !("detail" in error))
    return undefined;
  const detail = (error as { detail: unknown }).detail;
  if (!Array.isArray(detail) || detail.length === 0) return undefined;
  const first = detail[0] as { loc?: unknown; msg?: unknown };
  const location = Array.isArray(first.loc) ? first.loc.map(String) : [];
  const path =
    (location[0] === "body" && location.length > 1
      ? location.slice(1)
      : location
    ).join(".") || "-";
  const rest = detail.length - 1;
  return `${path}: ${String(first.msg ?? "")}${rest > 0 ? ` (+${rest})` : ""}`;
};

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

const strategyDraftRecord = (
  candidate: unknown,
  expectedDraftId: string,
): StrategyDraft | null => {
  if (typeof candidate !== "object" || candidate === null) return null;
  const value = candidate as unknown as Record<string, unknown>;
  const savedIdentity =
    value.strategy_id === null &&
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
  return value.draft_id === expectedDraftId &&
    expectedDraftId.trim() !== "" &&
    expectedDraftId.length <= 512 &&
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
    value.updated_at.endsWith("Z") &&
    Number.isFinite(Date.parse(value.updated_at)) &&
    savedIdentity
    ? (candidate as StrategyDraft)
    : null;
};

/** Runtime check for a CAS conflict, bound to the route identity that produced it. */
const draftConflictDetail = (
  error: unknown,
  expectedDraftId: string | undefined,
): StrategyDraftConflictDetail | null => {
  if (expectedDraftId === undefined) return null;
  if (typeof error !== "object" || error === null || !("detail" in error))
    return null;
  const detail = (error as { detail: unknown }).detail;
  if (typeof detail !== "object" || detail === null) return null;
  const candidate = detail as Partial<StrategyDraftConflictDetail>;
  const current = candidate.current;
  const validCurrent =
    current === null || strategyDraftRecord(current, expectedDraftId) !== null;
  return candidate.code === "strategy.draft.conflict" &&
    typeof candidate.message === "string" &&
    validCurrent
    ? (candidate as StrategyDraftConflictDetail)
    : null;
};

const requestError = (
  response: { error?: unknown; response?: { status: number } },
  context: string,
  expectedDraftId?: string,
): ApiRequestError => {
  const conflict = revisionConflictDetail(response.error);
  const draftConflict = draftConflictDetail(response.error, expectedDraftId);
  const responseStatus = response.response?.status ?? 0;
  if (
    expectedDraftId !== undefined &&
    responseStatus === 409 &&
    draftConflict === null
  ) {
    return new ApiRequestError(
      context,
      responseStatus,
      "client.response.invalid",
      "Draft conflict response did not match the requested draft identity.",
    );
  }
  return new ApiRequestError(
    context,
    responseStatus,
    errorCode(response.error),
    errorField(response.error, "message") ??
      invalidDocumentSummary(response.error),
    conflict?.latest_revision ?? null,
    draftConflict?.current ?? null,
    detailFieldPath(response.error),
    requestValidationSummary(response.error),
    detailValues(response.error),
  );
};

const invalidDraftResponse = (
  context: string,
  detail: string,
): ApiRequestError =>
  new ApiRequestError(context, 200, "client.response.invalid", detail);

const requireStrategyDraft = (
  candidate: unknown,
  draftId: string,
  context: string,
): StrategyDraft => {
  const draft = strategyDraftRecord(candidate, draftId);
  if (draft === null) {
    throw invalidDraftResponse(
      context,
      "Draft response did not match the requested draft identity or contract.",
    );
  }
  return draft;
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
    page: {
      offset?: number;
      limit?: number;
      strategyId?: string;
      kind?: RunKind;
    } = {},
  ): Promise<PageBacktestRunSummary> {
    const response = await listBacktests({
      query: {
        offset: page.offset,
        limit: page.limit,
        strategy_id: page.strategyId,
        kind: page.kind,
      },
    });
    return unwrap(response, "listBacktests");
  },

  /** 계열 시도 원장 — 시도 묶음·실행 역할·N(검증 랩 spec D2). 합쳐진 계열이면 남은 계열의 원장이다. */
  async getTrialLedger(strategyId: string): Promise<TrialLedger> {
    const response = await getTrialLedger({
      path: { strategy_id: strategyId },
    });
    return unwrap(response, "getTrialLedger");
  },

  /** `sourceStrategyId` 계열을 `strategyId` 계열에 합친다. 되돌릴 수 없다. */
  async mergeTrialLineage(
    strategyId: string,
    sourceStrategyId: string,
  ): Promise<TrialLedger> {
    const response = await mergeTrialLineage({
      path: { strategy_id: strategyId },
      body: { source_strategy_id: sourceStrategyId },
    });
    return unwrap(response, "mergeTrialLineage");
  },

  async startBacktest(spec: BacktestRunSpec): Promise<BacktestStartResponse> {
    const response = await startBacktest({ body: spec });
    return unwrap(response, "startBacktest");
  },

  /** 실행 전 미리 계산 — 같은 요청이 결과를 내면 계열 시도 수가 어떻게 되는가(검증 랩 spec D2). */
  async previewBacktestTrial(spec: BacktestRunSpec): Promise<TrialPreview> {
    const response = await previewBacktestTrial({ body: spec });
    return unwrap(response, "previewBacktestTrial");
  },

  async getBacktestStatus(runId: string): Promise<BacktestRunState> {
    const response = await getBacktestStatus({ path: { run_id: runId } });
    return unwrap(response, "getBacktestStatus");
  },

  async getBacktestRequest(runId: string): Promise<BacktestRunSpec> {
    const response = await getBacktestRequest({ path: { run_id: runId } });
    return unwrap(response, "getBacktestRequest");
  },

  async getBacktestResult(runId: string): Promise<BacktestRunResult> {
    const response = await getBacktestResult({ path: { run_id: runId } });
    return unwrap(response, "getBacktestResult");
  },

  async cancelBacktest(runId: string): Promise<BacktestRunState> {
    const response = await cancelBacktest({ path: { run_id: runId } });
    return unwrap(response, "cancelBacktest");
  },

  /** Bounded projection from the same calculation that produces TargetTape/backtest input. */
  async traceStrategy(
    request: StrategyTraceRequest,
    signal?: AbortSignal,
  ): Promise<StrategyTraceResponse> {
    const response = await postStrategyTrace({ body: request, signal });
    return unwrap(response, "traceStrategy");
  },

  async getEquityCatalog(
    query: EquityCatalogQuery = {},
  ): Promise<ResearchCatalog> {
    const response = await getEquityCatalog({ query });
    return requireData(response.data, "getEquityCatalog");
  },

  async getFactorCatalog(
    query: FactorCatalogQuery = {},
  ): Promise<FactorCatalog> {
    const response = await getFactorCatalog({ query });
    return requireData(response.data, "getFactorCatalog");
  },

  async explainFactorGraph(
    request: FactorGraphRequest,
    signal?: AbortSignal,
  ): Promise<FactorExplanation> {
    const response = await explainFactorGraph({ body: request, signal });
    return requireData(response.data, "explainFactorGraph");
  },

  async listStrategies(
    page: { offset?: number; limit?: number } = {},
  ): Promise<PageStrategySummary> {
    const response = await listStrategies({ query: page });
    return unwrap(response, "listStrategies");
  },

  async getStrategyDraft(draftId: string): Promise<StrategyDraft> {
    const response = await getStrategyDraft({ path: { draft_id: draftId } });
    return requireStrategyDraft(
      unwrap(response, "getStrategyDraft"),
      draftId,
      "getStrategyDraft",
    );
  },

  async saveStrategyDraft(
    draftId: string,
    request: SaveStrategyDraftRequest,
  ): Promise<StrategyDraft> {
    const response = await saveStrategyDraft({
      path: { draft_id: draftId },
      body: request,
    });
    if (response.error !== undefined) {
      throw requestError(response, "saveStrategyDraft", draftId);
    }
    const saved = requireStrategyDraft(
      requireData(response.data, "saveStrategyDraft"),
      draftId,
      "saveStrategyDraft",
    );
    const exactSuccessor =
      saved.version === request.expected_version + 1 &&
      saved.source === request.source &&
      saved.format === request.format &&
      saved.schema_version === request.schema_version &&
      saved.strategy_id === request.strategy_id &&
      saved.base_revision === request.base_revision &&
      saved.base_spec_hash === request.base_spec_hash;
    if (!exactSuccessor) {
      throw invalidDraftResponse(
        "saveStrategyDraft",
        "Saved draft response did not match the submitted CAS snapshot.",
      );
    }
    return saved;
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
      throw requestError(response, "deleteStrategyDraft", draftId);
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

  /** 1.0 텍스트를 1.1로 다시 쓴 source와 그 compile 결과. 실패(422)는 텍스트를 바꾸지 않는다. */
  async upgradeStrategyDocument(
    request: CompileRequest,
    signal?: AbortSignal,
  ): Promise<UpgradedDocument> {
    const response = await upgradeStrategyDocument({ body: request, signal });
    return unwrap(response, "upgradeStrategyDocument");
  },

  async getStrategyDocumentSchema(): Promise<StrategyDocumentSchema> {
    const response = await getStrategyDocumentSchema();
    return unwrap(response, "getStrategyDocumentSchema");
  },

  /**
   * 실행 설정(`RunEnvironment`) 런타임 JSON Schema(P2-01, spec D6). 실행 설정 패널이 필드·기본값·
   * 범위를 여기서 읽는다 — 생성 SDK 타입에는 범위가 없다(pydantic 이 `__post_init__` 를 보지 못한다).
   */
  async getRunEnvironmentSchema(): Promise<RunEnvironmentSchema> {
    const response = await getRunEnvironmentSchema();
    return unwrap(response, "getRunEnvironmentSchema");
  },

  /** 그래프 노드 연산자 정의 전부(P1-03, spec D8). 팔레트·라벨이 읽는 유일한 연산자 목록이다. */
  async getStrategyOperatorCatalog(): Promise<StrategyOperatorCatalog> {
    const response = await getStrategyOperatorCatalog();
    return unwrap(response, "getStrategyOperatorCatalog");
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
};

export type EquityCatalogQuery = NonNullable<GetEquityCatalogData["query"]>;
export type FactorCatalogQuery = NonNullable<GetFactorCatalogData["query"]>;

export type {
  ApplicableWhen,
  BacktestRunResult,
  BacktestRunSpec,
  BacktestRunState,
  BacktestRunSummary,
  BacktestStartResponse,
  CompileRequest,
  CompiledDocument,
  DatasetFieldProfile,
  DiffEntry,
  FactorCatalog,
  FactorDefinition,
  FactorExplanation,
  FactorGraph,
  FactorGraphRequest,
  FactorValidationIssue,
  FieldContract,
  InlineDraft,
  NodeContract,
  MetricDefinition,
  MetricValue,
  OperatorDefinition,
  PageRevisionSummary,
  PageBacktestRunSummary,
  PageStrategySummary,
  ResearchCatalog,
  ReviseDocumentRequest,
  RevisionDiff,
  RevisionSummary,
  RunEnvironment,
  RunEnvironmentSchema,
  RunKind,
  SaveDocumentRequest,
  SaveStrategyDraftRequest,
  SavedRevisionReference,
  SourceDiagnostic,
  StrategyDocument,
  StrategyDocumentContractResponse,
  StrategyDocumentSchema,
  StrategyDraft,
  StrategyOperatorCatalog,
  StrategySpec,
  StrategySummary,
  StrategyTraceRequest,
  StrategyTraceResponse,
  TrialLedger,
  TrialPreview,
  UpgradedDocument,
};
