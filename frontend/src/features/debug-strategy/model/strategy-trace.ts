import type {
  RunEnvironment,
  StrategyTraceRequest,
  StrategyTraceResponse,
} from "../../../shared/api";

export type StrategyDebuggerNode = {
  nodeId: string;
  operation: string;
  pointer: string;
};

export type StrategyDebuggerFactor = {
  factorId: string;
  label: string;
  pointer: string;
  outputNodeId: string;
  expectedPlanHash: string;
  nodes: StrategyDebuggerNode[];
};

/**
 * Immutable, backend-pinned input handed to the debugger by the Strategy IDE composition layer.
 * It deliberately contains no editor state: the debug feature must not become a second owner of
 * dirty/current/source rules.
 */
export type StrategyDebuggerContext = {
  documentEpoch: number;
  sourceVersion: number;
  strategySource: StrategyTraceRequest["strategy_source"];
  specHash: string;
  expectedSnapshotId: string;
  expectedRegistryVersion: string;
  /**
   * 추적이 놓일 실행 설정(schema 1.2 부터 전략 문서 밖, P3-02). 실행 요청과 같은 값을 trace 요청에
   * 싣는다 — 없으면 backend 가 실행 설정이 없다고 거절한다. 응답 날짜 범위 가드와 날짜 입력 범위도 이
   * 값의 기간(`start`·`end`)을 읽는다.
   */
  environment: RunEnvironment;
  factors: StrategyDebuggerFactor[];
};

/** `environment` 는 실행 설정 패널의 기간·유니버스가 아직 정해지지 않았다는 뜻이다. */
export type StrategyDebuggerUnavailableReason =
  "document" | "preparing" | "no-factors" | "execution-plan" | "environment";

export type StrategyTraceSelection = {
  /** 요약은 첫 팩터의 지문으로 검증하며 종목별 추적을 요청하지 않는다. */
  summaryOnly?: boolean;
  asOf: string;
  security: string;
  factorId: string;
  nodeId: string;
  startingHoldings?: string;
};

export type PreparedStrategyTrace =
  | {
      kind: "blocked";
      /**
       * `unavailable` 은 추적 문맥이 없다는 뜻이다. 왜 없는지(문서·준비 중·팩터 없음·실행 계획·실행 설정)는
       * 상위 `StrategyDebuggerUnavailableReason` 이 한 문장으로 말하므로 여기서 사유를 다시 짓지 않는다
       * (이슈 #260: 실행 설정만 비었는데 "실행 가능한 문서가 없다"가 함께 떴다).
       */
      reason:
        "unavailable" | "date" | "security" | "factor" | "node" | "holdings";
    }
  | {
      kind: "ready";
      ownerKey: string;
      /** First request retained for UI ownership; the hook pages every linked request below. */
      request: StrategyTraceRequest;
      linkedRequests: StrategyTraceRequest[];
      selectedRequest: StrategyTraceRequest;
      expected: {
        specHash: string;
        snapshotId: string;
        registryVersion: string;
        planHash: string;
        sourceVersion: number;
        start: string;
        end: string;
      };
    };

/** Client-owned budgets stay below the generated server contract and bound aggregate memory. */
export const STRATEGY_TRACE_CLIENT_BUDGET = {
  nodeChunk: 64,
  pageRows: 400,
  totalRows: 8_000,
} as const;

const validIsoDate = (value: string): boolean => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return (
    !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};

/** Parse the URL-owned editor value without changing order; the backend still validates IDs. */
export const parseSecurityIds = (value: string): string[] => {
  const unique = new Set<string>();
  for (const item of value.split(/[\s,]+/u)) {
    const trimmed = item.trim();
    if (trimmed !== "") unique.add(trimmed);
  }
  return [...unique];
};

export type ParsedStartingHoldings =
  | { kind: "source" }
  | {
      kind: "explicit";
      holdings: NonNullable<StrategyTraceRequest["starting_holdings"]>;
    }
  | { kind: "invalid" };

/** Blank preserves the adapter book; `flat`/`[]` explicitly declares an empty opening book. */
export const parseStartingHoldings = (
  value: string | undefined,
): ParsedStartingHoldings => {
  const source = value?.trim() ?? "";
  if (source === "") return { kind: "source" };
  if (source.toLowerCase() === "flat" || source === "[]")
    return { kind: "explicit", holdings: [] };
  const holdings: NonNullable<StrategyTraceRequest["starting_holdings"]> = [];
  const seen = new Set<string>();
  for (const entry of source.split(/[\s,]+/u)) {
    const match =
      /^([^=]+)=([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)$/iu.exec(entry);
    if (match === null) return { kind: "invalid" };
    const securityId = match[1]?.trim() ?? "";
    const weight = Number(match[2]);
    if (securityId === "" || seen.has(securityId) || !Number.isFinite(weight))
      return { kind: "invalid" };
    seen.add(securityId);
    holdings.push({ security_id: securityId, weight });
  }
  if (holdings.length > 100) return { kind: "invalid" };
  return { kind: "explicit", holdings };
};

export const prepareStrategyTrace = (
  context: StrategyDebuggerContext | null,
  selection: StrategyTraceSelection,
): PreparedStrategyTrace => {
  if (context === null) return { kind: "blocked", reason: "unavailable" };
  if (selection.asOf !== "" && !validIsoDate(selection.asOf))
    return { kind: "blocked", reason: "date" };
  const securityIds = selection.summaryOnly
    ? []
    : parseSecurityIds(selection.security);
  if (
    (!selection.summaryOnly && securityIds.length === 0) ||
    securityIds.length > 100
  )
    return { kind: "blocked", reason: "security" };
  const factor =
    selection.summaryOnly && selection.factorId === ""
      ? context.factors[0]
      : context.factors.find(
          (candidate) => candidate.factorId === selection.factorId,
        );
  if (factor === undefined) return { kind: "blocked", reason: "factor" };
  if (
    !selection.summaryOnly &&
    !factor.nodes.some((node) => node.nodeId === selection.nodeId)
  )
    return { kind: "blocked", reason: "node" };
  const startingHoldings = parseStartingHoldings(selection.startingHoldings);
  if (startingHoldings.kind === "invalid")
    return { kind: "blocked", reason: "holdings" };

  const nodeIds = selection.summaryOnly
    ? []
    : factor.nodes.map((node) => node.nodeId);
  const commonRequest: Omit<
    StrategyTraceRequest,
    "node_ids" | "include_raw" | "offset" | "limit"
  > = {
    strategy_source: context.strategySource,
    environment: context.environment,
    security_ids: securityIds,
    factor_id: factor.factorId,
    ...(selection.asOf === "" ? {} : { as_of: selection.asOf }),
    ...(startingHoldings.kind === "explicit"
      ? { starting_holdings: startingHoldings.holdings }
      : {}),
  };
  const linkedRequests: StrategyTraceRequest[] = [];
  if (selection.summaryOnly)
    linkedRequests.push({
      ...commonRequest,
      node_ids: [],
      include_raw: false,
      offset: 0,
      limit: 1,
    });
  for (
    let index = 0;
    index < nodeIds.length;
    index += STRATEGY_TRACE_CLIENT_BUDGET.nodeChunk
  ) {
    const chunk = nodeIds.slice(
      index,
      index + STRATEGY_TRACE_CLIENT_BUDGET.nodeChunk,
    );
    linkedRequests.push({
      ...commonRequest,
      node_ids: chunk,
      include_raw: index === 0,
      offset: 0,
      limit: Math.min(
        chunk.length * securityIds.length,
        STRATEGY_TRACE_CLIENT_BUDGET.pageRows,
      ),
    });
  }
  const request = linkedRequests[0];
  if (request === undefined) return { kind: "blocked", reason: "node" };
  const selectedRequest: StrategyTraceRequest = selection.summaryOnly
    ? request
    : {
        ...commonRequest,
        node_ids: [selection.nodeId],
        include_raw: false,
        offset: 0,
        limit: securityIds.length,
      };
  const sourceOwner =
    request.strategy_source.kind === "saved_revision"
      ? [
          request.strategy_source.kind,
          request.strategy_source.strategy_id,
          request.strategy_source.revision,
          request.strategy_source.expected_spec_hash,
        ]
      : [
          request.strategy_source.kind,
          request.strategy_source.source_hash ?? null,
        ];
  return {
    kind: "ready",
    // 키는 요청에 싣는 값 전부(inline 원문은 그 신원 `sourceOwner` 로 줄인다)와 backend 지문이다. 요청
    // 칸을 골라 다시 적으면 요청에 새 칸이 생길 때 키에서 빠진다 — 실행 설정이 그렇게 빠져, 설정을 바꿔도
    // 옛 추적이 새 설정의 결과로 보였다(#351).
    ownerKey: JSON.stringify([
      context.documentEpoch,
      context.sourceVersion,
      context.specHash,
      context.expectedSnapshotId,
      context.expectedRegistryVersion,
      factor.expectedPlanHash,
      { ...commonRequest, strategy_source: sourceOwner },
      nodeIds,
      selection.nodeId,
      STRATEGY_TRACE_CLIENT_BUDGET,
    ]),
    request,
    linkedRequests,
    selectedRequest,
    expected: {
      specHash: context.specHash,
      snapshotId: context.expectedSnapshotId,
      registryVersion: context.expectedRegistryVersion,
      planHash: factor.expectedPlanHash,
      sourceVersion: context.sourceVersion,
      start: context.environment.start,
      end: context.environment.end,
    },
  };
};

const sameSourceProvenance = (
  request: StrategyTraceRequest,
  response: StrategyTraceResponse,
): boolean => {
  const source = request.strategy_source;
  const provenance = response.provenance;
  if (provenance.kind !== source.kind) return false;
  if (source.kind === "saved_revision") {
    return (
      provenance.strategy_id === source.strategy_id &&
      provenance.revision === source.revision
    );
  }
  return (
    (provenance.source_hash ?? null) === (source.source_hash ?? null) &&
    provenance.strategy_id == null &&
    provenance.revision == null
  );
};

const indexUniqueBySecurity = <Row extends { security_id: string }>(
  rows: Row[],
): Map<string, Row> | null => {
  const indexed = new Map<string, Row>();
  for (const row of rows) {
    if (indexed.has(row.security_id)) return null;
    indexed.set(row.security_id, row);
  }
  return indexed;
};

const sameExclusionReasons = (left: string[], right: string[]): boolean =>
  left.length === right.length &&
  left.every((reason, index) => reason === right[index]);

const targetProjectionMatches = (
  target: NonNullable<StrategyTraceResponse["target"]>,
  requestedSecurities: Set<string>,
  expectsOrderDelta: boolean,
): boolean => {
  const candidates = indexUniqueBySecurity(target.candidates);
  const construction = indexUniqueBySecurity(target.construction);
  const targets = indexUniqueBySecurity(target.targets);
  if (candidates === null || construction === null || targets === null)
    return false;
  if (
    candidates.size !== requestedSecurities.size ||
    construction.size !== requestedSecurities.size
  )
    return false;

  for (const securityId of requestedSecurities) {
    const candidate = candidates.get(securityId);
    const audit = construction.get(securityId);
    if (
      candidate === undefined ||
      audit === undefined ||
      candidate.as_of !== target.signal_as_of ||
      audit.as_of !== target.signal_as_of ||
      candidate.composite_score !== audit.composite_score ||
      candidate.rank !== audit.rank ||
      candidate.eligible !== audit.eligible ||
      candidate.selected !== audit.selected ||
      candidate.side !== audit.side ||
      candidate.target_weight !== audit.constrained_target_weight ||
      !sameExclusionReasons(
        candidate.exclusion_reasons,
        audit.exclusion_reasons,
      ) ||
      (expectsOrderDelta
        ? audit.previous_weight === null || audit.estimated_order_delta === null
        : audit.previous_weight !== null ||
          audit.estimated_order_delta !== null)
    )
      return false;

    const targetPosition = targets.get(securityId);
    const shouldHaveTarget =
      audit.selected && audit.constrained_target_weight !== 0;
    if (shouldHaveTarget !== (targetPosition !== undefined)) return false;
    if (
      targetPosition !== undefined &&
      (targetPosition.weight !== audit.constrained_target_weight ||
        targetPosition.composite_score !== audit.composite_score ||
        targetPosition.rank !== audit.rank ||
        targetPosition.side !== audit.side)
    )
      return false;
  }
  return targets.size <= requestedSecurities.size;
};

const responseDateMatches = (
  prepared: Extract<PreparedStrategyTrace, { kind: "ready" }>,
  request: StrategyTraceRequest,
  response: StrategyTraceResponse,
): boolean => {
  const { start, end } = prepared.expected;
  if (
    !validIsoDate(response.as_of) ||
    response.as_of < start ||
    response.as_of > end ||
    (request.as_of != null && response.as_of !== request.as_of)
  )
    return false;
  if (response.target === null) return request.as_of != null;
  return (
    response.target.signal_as_of === response.as_of &&
    validIsoDate(response.target.execution_on) &&
    response.target.execution_on > response.as_of &&
    response.target.execution_on <= end
  );
};

const traceRowsAreUnique = (response: StrategyTraceResponse): boolean => {
  const identities = new Set<string>();
  for (const row of response.trace.rows) {
    const identity = JSON.stringify([row.node_id, row.as_of, row.security_id]);
    if (identities.has(identity)) return false;
    identities.add(identity);
  }
  return true;
};

/** Fail closed before a response can replace the current debugger projection. */
export const responseMatchesStrategyTrace = (
  prepared: Extract<PreparedStrategyTrace, { kind: "ready" }>,
  response: StrategyTraceResponse,
  request: StrategyTraceRequest = prepared.request,
): boolean => {
  const requestedSecurities = new Set(request.security_ids);
  const requestedNodes = new Set(request.node_ids ?? []);
  const expectsOrderDelta = request.starting_holdings != null;
  return (
    response.spec_hash === prepared.expected.specHash &&
    response.provenance.spec_hash === prepared.expected.specHash &&
    response.snapshot_id === prepared.expected.snapshotId &&
    response.registry_version === prepared.expected.registryVersion &&
    response.plan_hash === prepared.expected.planHash &&
    response.factor_id === request.factor_id &&
    responseDateMatches(prepared, request, response) &&
    (response.summary == null ||
      (response.summary.signal_as_of === response.as_of &&
        response.summary.execution_on === response.target?.execution_on)) &&
    sameSourceProvenance(request, response) &&
    response.trace.offset === (request.offset ?? 0) &&
    response.trace.limit === (request.limit ?? 200) &&
    response.trace.returned === response.trace.rows.length &&
    traceRowsAreUnique(response) &&
    response.trace.rows.every(
      (row) =>
        row.as_of === response.as_of &&
        requestedSecurities.has(row.security_id) &&
        requestedNodes.has(row.node_id),
    ) &&
    response.raw.every(
      (row) =>
        row.as_of === response.as_of &&
        requestedSecurities.has(row.security_id),
    ) &&
    (response.target === null ||
      (response.target.signal_as_of === response.as_of &&
        targetProjectionMatches(
          response.target,
          requestedSecurities,
          expectsOrderDelta,
        )))
  );
};
