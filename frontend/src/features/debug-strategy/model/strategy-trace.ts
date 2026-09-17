import type {
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
  start: string;
  end: string;
  factors: StrategyDebuggerFactor[];
};

export type StrategyDebuggerUnavailableReason =
  "document" | "preparing" | "no-factors" | "execution-plan";

export type StrategyTraceSelection = {
  asOf: string;
  security: string;
  factorId: string;
  nodeId: string;
  startingHoldings?: string;
};

export type PreparedStrategyTrace =
  | {
      kind: "blocked";
      reason: "document" | "date" | "security" | "factor" | "node" | "holdings";
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
  if (context === null) return { kind: "blocked", reason: "document" };
  if (selection.asOf !== "" && !validIsoDate(selection.asOf))
    return { kind: "blocked", reason: "date" };
  const securityIds = parseSecurityIds(selection.security);
  if (securityIds.length === 0 || securityIds.length > 100)
    return { kind: "blocked", reason: "security" };
  const factor = context.factors.find(
    (candidate) => candidate.factorId === selection.factorId,
  );
  if (factor === undefined) return { kind: "blocked", reason: "factor" };
  if (!factor.nodes.some((node) => node.nodeId === selection.nodeId))
    return { kind: "blocked", reason: "node" };
  const startingHoldings = parseStartingHoldings(selection.startingHoldings);
  if (startingHoldings.kind === "invalid")
    return { kind: "blocked", reason: "holdings" };

  const nodeIds = factor.nodes.map((node) => node.nodeId);
  const commonRequest: Omit<
    StrategyTraceRequest,
    "node_ids" | "include_raw" | "offset" | "limit"
  > = {
    strategy_source: context.strategySource,
    security_ids: securityIds,
    factor_id: factor.factorId,
    ...(selection.asOf === "" ? {} : { as_of: selection.asOf }),
    ...(startingHoldings.kind === "explicit"
      ? { starting_holdings: startingHoldings.holdings }
      : {}),
  };
  const linkedRequests: StrategyTraceRequest[] = [];
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
  const selectedRequest: StrategyTraceRequest = {
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
    ownerKey: JSON.stringify([
      context.documentEpoch,
      context.sourceVersion,
      context.specHash,
      context.expectedSnapshotId,
      context.expectedRegistryVersion,
      factor.expectedPlanHash,
      sourceOwner,
      request.as_of,
      request.security_ids,
      request.factor_id,
      nodeIds,
      selection.nodeId,
      request.starting_holdings ?? null,
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
      start: context.start,
      end: context.end,
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
  if (
    !validIsoDate(response.as_of) ||
    response.as_of < prepared.expected.start ||
    response.as_of > prepared.expected.end ||
    (request.as_of != null && response.as_of !== request.as_of)
  )
    return false;
  if (response.target === null) return request.as_of != null;
  return (
    response.target.signal_as_of === response.as_of &&
    validIsoDate(response.target.execution_on) &&
    response.target.execution_on > response.as_of &&
    response.target.execution_on <= prepared.expected.end
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
        row.as_of === response.as_of && requestedSecurities.has(row.security_id),
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
