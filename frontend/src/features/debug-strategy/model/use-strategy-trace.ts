import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import {
  ApiRequestError,
  strategyWorkbenchApi,
  type StrategyTraceRequest,
  type StrategyTraceResponse,
} from "../../../shared/api";
import {
  prepareStrategyTrace,
  responseMatchesStrategyTrace,
  STRATEGY_TRACE_CLIENT_BUDGET,
  type PreparedStrategyTrace,
  type StrategyDebuggerContext,
  type StrategyTraceSelection,
} from "./strategy-trace";

type RequestOwned = {
  ownerKey: string;
  request: StrategyTraceRequest;
};

export type StrategyTraceState =
  | {
      kind: "blocked";
      reason: Extract<PreparedStrategyTrace, { kind: "blocked" }>["reason"];
    }
  | { kind: "idle" }
  | ({ kind: "loading" } & RequestOwned)
  | ({ kind: "cancelled" } & RequestOwned)
  | ({ kind: "discarded" } & RequestOwned)
  | ({ kind: "error"; message: string } & RequestOwned)
  | ({
      kind: "success";
      response: StrategyTraceResponse;
      linkedRows: StrategyTraceResponse["trace"]["rows"];
      selectedRows: StrategyTraceResponse["trace"]["rows"];
      linkedTruncated: boolean;
    } & RequestOwned);

type OwnedState = Exclude<StrategyTraceState, { kind: "blocked" | "idle" }>;

type CancelledRequest = {
  kind: "cancelled";
  ownerKey: string;
  request: StrategyTraceRequest;
};

class DiscardedStrategyTraceResponse extends Error {
  constructor() {
    super("strategy trace response does not match its request owner");
    this.name = "DiscardedStrategyTraceResponse";
  }
}

type ReadyTrace = Extract<PreparedStrategyTrace, { kind: "ready" }>;
type TraceRow = StrategyTraceResponse["trace"]["rows"][number];

const sameWireValue = (left: unknown, right: unknown): boolean =>
  JSON.stringify(left) === JSON.stringify(right);

const sameCalculation = (
  anchor: StrategyTraceResponse,
  candidate: StrategyTraceResponse,
): boolean =>
  anchor.as_of === candidate.as_of &&
  sameWireValue(anchor.provenance, candidate.provenance) &&
  sameWireValue(anchor.target, candidate.target) &&
  sameWireValue(anchor.warnings ?? [], candidate.warnings ?? []);

const rowIdentity = (row: TraceRow): string =>
  JSON.stringify([row.node_id, row.as_of, row.security_id]);

const requestTrace = async (
  prepared: ReadyTrace,
  request: StrategyTraceRequest,
  signal: AbortSignal,
): Promise<StrategyTraceResponse> => {
  const response = await strategyWorkbenchApi.traceStrategy(request, signal);
  if (!responseMatchesStrategyTrace(prepared, response, request))
    throw new DiscardedStrategyTraceResponse();
  return response;
};

const selectedRowsAreComplete = (
  rows: readonly TraceRow[],
  request: StrategyTraceRequest,
): boolean => {
  const selectedNode = request.node_ids?.[0];
  if (selectedNode === undefined || rows.length !== request.security_ids.length)
    return false;
  const securities = new Set(
    rows
      .filter((row) => row.node_id === selectedNode)
      .map((row) => row.security_id),
  );
  return (
    securities.size === request.security_ids.length &&
    request.security_ids.every((securityId) => securities.has(securityId))
  );
};

/** Page/chunk server rows by identity only; no factor or portfolio value is calculated here. */
const fetchTraceBundle = async (
  prepared: ReadyTrace,
  signal: AbortSignal,
) => {
  let anchor: StrategyTraceResponse | null = null;
  let remainingBudget: number = STRATEGY_TRACE_CLIENT_BUDGET.totalRows;
  let linkedTruncated = false;
  const linkedRows: TraceRow[] = [];
  const seenRows = new Set<string>();

  outer: for (const baseRequest of prepared.linkedRequests) {
    const nodeCount = baseRequest.node_ids?.length ?? 0;
    const expectedRows = nodeCount * baseRequest.security_ids.length;
    let offset = 0;
    while (offset < expectedRows) {
      if (remainingBudget === 0) {
        linkedTruncated = true;
        break outer;
      }
      const limit = Math.min(
        STRATEGY_TRACE_CLIENT_BUDGET.pageRows,
        expectedRows - offset,
        remainingBudget,
      );
      const pageRequest: StrategyTraceRequest = {
        ...baseRequest,
        include_raw: anchor === null,
        offset,
        limit,
      };
      const page = await requestTrace(prepared, pageRequest, signal);
      if (anchor === null) anchor = page;
      else if (!sameCalculation(anchor, page))
        throw new DiscardedStrategyTraceResponse();
      if (!pageRequest.include_raw && (page.raw.length > 0 || page.raw_truncated))
        throw new DiscardedStrategyTraceResponse();
      if (page.trace.returned !== limit)
        throw new DiscardedStrategyTraceResponse();
      for (const row of page.trace.rows) {
        const identity = rowIdentity(row);
        if (seenRows.has(identity)) throw new DiscardedStrategyTraceResponse();
        seenRows.add(identity);
        linkedRows.push(row);
      }
      offset += page.trace.returned;
      remainingBudget -= page.trace.returned;
      if (!page.trace.has_more) {
        if (offset !== expectedRows)
          throw new DiscardedStrategyTraceResponse();
        break;
      }
    }
  }
  if (anchor === null) throw new DiscardedStrategyTraceResponse();

  let selectedRows = linkedRows.filter(
    (row) => row.node_id === prepared.selectedRequest.node_ids?.[0],
  );
  if (
    linkedTruncated ||
    !selectedRowsAreComplete(selectedRows, prepared.selectedRequest)
  ) {
    const selected = await requestTrace(
      prepared,
      prepared.selectedRequest,
      signal,
    );
    if (
      !sameCalculation(anchor, selected) ||
      selected.raw.length > 0 ||
      selected.raw_truncated ||
      selected.trace.has_more ||
      !selectedRowsAreComplete(selected.trace.rows, prepared.selectedRequest)
    )
      throw new DiscardedStrategyTraceResponse();
    selectedRows = selected.trace.rows;
  }
  return { anchor, linkedRows, selectedRows, linkedTruncated };
};

const errorMessage = (error: unknown): string =>
  error instanceof ApiRequestError
    ? (error.detail ?? error.message)
    : error instanceof Error
      ? error.message
      : String(error);

/**
 * Observes one exact trace query. The query cache owns REST data while this hook owns only the
 * explicit cancelled UI state. Source identity, selections and backend fingerprints are all part
 * of the key, so a superseded response can populate only its old cache entry, never the current UI.
 */
export const useStrategyTrace = (
  context: StrategyDebuggerContext | null,
  selection: StrategyTraceSelection,
) => {
  const prepared = useMemo(
    () => prepareStrategyTrace(context, selection),
    [context, selection],
  );
  const ownerKey = prepared.kind === "ready" ? prepared.ownerKey : null;
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => ["strategy", "debug-trace", ownerKey ?? "blocked"] as const,
    [ownerKey],
  );
  const [cancelled, setCancelled] = useState<CancelledRequest | null>(null);
  const query = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      if (prepared.kind !== "ready")
        throw new Error("blocked strategy trace query cannot execute");
      return fetchTraceBundle(prepared, signal);
    },
    enabled: false,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    // Linked traces are deliberately larger than the P5-02 single-node response.
    gcTime: 60_000,
  });

  const run = useCallback(async (): Promise<void> => {
    if (prepared.kind !== "ready") return;
    setCancelled(null);
    await query.refetch({ cancelRefetch: true });
  }, [prepared, query]);

  const cancel = useCallback(() => {
    if (prepared.kind !== "ready" || !query.isFetching) return;
    setCancelled({
      kind: "cancelled",
      ownerKey: prepared.ownerKey,
      request: prepared.request,
    });
    void queryClient.cancelQueries({ queryKey, exact: true });
  }, [prepared, query.isFetching, queryClient, queryKey]);

  const ownedState = useMemo<OwnedState | null>(() => {
    if (prepared.kind !== "ready") return null;
    if (cancelled?.ownerKey === prepared.ownerKey) return cancelled;
    const requestOwner = {
      ownerKey: prepared.ownerKey,
      request: prepared.request,
    };
    if (query.isFetching) return { kind: "loading", ...requestOwner };
    if (query.isError)
      return query.error instanceof DiscardedStrategyTraceResponse
        ? { kind: "discarded", ...requestOwner }
        : {
            kind: "error",
            ...requestOwner,
            message: errorMessage(query.error),
          };
    return query.data === undefined
      ? null
      : {
          kind: "success",
          ...requestOwner,
          response: query.data.anchor,
          linkedRows: query.data.linkedRows,
          selectedRows: query.data.selectedRows,
          linkedTruncated: query.data.linkedTruncated,
        };
  }, [
    cancelled,
    prepared,
    query.data,
    query.error,
    query.isError,
    query.isFetching,
  ]);

  const state: StrategyTraceState =
    prepared.kind === "blocked"
      ? prepared
      : ownedState === null
        ? { kind: "idle" }
        : ownedState;

  return { prepared, state, run, cancel };
};
