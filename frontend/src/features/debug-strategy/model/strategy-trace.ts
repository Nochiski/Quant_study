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
};

export type PreparedStrategyTrace =
  | {
      kind: "blocked";
      reason: "document" | "date" | "security" | "factor" | "node";
    }
  | {
      kind: "ready";
      ownerKey: string;
      request: StrategyTraceRequest;
      expected: {
        specHash: string;
        snapshotId: string;
        registryVersion: string;
        planHash: string;
        sourceVersion: number;
      };
    };

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

export const prepareStrategyTrace = (
  context: StrategyDebuggerContext | null,
  selection: StrategyTraceSelection,
): PreparedStrategyTrace => {
  if (context === null) return { kind: "blocked", reason: "document" };
  if (!validIsoDate(selection.asOf)) return { kind: "blocked", reason: "date" };
  const securityIds = parseSecurityIds(selection.security);
  if (securityIds.length === 0 || securityIds.length > 100)
    return { kind: "blocked", reason: "security" };
  const factor = context.factors.find(
    (candidate) => candidate.factorId === selection.factorId,
  );
  if (factor === undefined) return { kind: "blocked", reason: "factor" };
  if (!factor.nodes.some((node) => node.nodeId === selection.nodeId))
    return { kind: "blocked", reason: "node" };

  const request: StrategyTraceRequest = {
    strategy_source: context.strategySource,
    as_of: selection.asOf,
    security_ids: securityIds,
    factor_id: factor.factorId,
    node_ids: [selection.nodeId],
    include_raw: false,
    offset: 0,
    // One selected node produces at most one row per requested security.
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
      request.node_ids,
    ]),
    request,
    expected: {
      specHash: context.specHash,
      snapshotId: context.expectedSnapshotId,
      registryVersion: context.expectedRegistryVersion,
      planHash: factor.expectedPlanHash,
      sourceVersion: context.sourceVersion,
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

/** Fail closed before a response can replace the current debugger projection. */
export const responseMatchesStrategyTrace = (
  prepared: Extract<PreparedStrategyTrace, { kind: "ready" }>,
  response: StrategyTraceResponse,
): boolean => {
  const request = prepared.request;
  const requestedSecurities = new Set(request.security_ids);
  const requestedNodes = new Set(request.node_ids ?? []);
  return (
    response.spec_hash === prepared.expected.specHash &&
    response.provenance.spec_hash === prepared.expected.specHash &&
    response.snapshot_id === prepared.expected.snapshotId &&
    response.registry_version === prepared.expected.registryVersion &&
    response.plan_hash === prepared.expected.planHash &&
    response.factor_id === request.factor_id &&
    response.as_of === request.as_of &&
    sameSourceProvenance(request, response) &&
    response.trace.offset === (request.offset ?? 0) &&
    response.trace.limit === (request.limit ?? 200) &&
    response.trace.returned === response.trace.rows.length &&
    response.trace.rows.every(
      (row) =>
        row.as_of === request.as_of &&
        requestedSecurities.has(row.security_id) &&
        requestedNodes.has(row.node_id),
    ) &&
    (response.target === null ||
      (response.target.signal_as_of === request.as_of &&
        response.target.candidates.every(
          (row) =>
            requestedSecurities.has(row.security_id) &&
            row.as_of === request.as_of,
        ) &&
        response.target.targets.every((row) =>
          requestedSecurities.has(row.security_id),
        )))
  );
};
