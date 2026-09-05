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
  | ({ kind: "success"; response: StrategyTraceResponse } & RequestOwned);

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
      const response = await strategyWorkbenchApi.traceStrategy(
        prepared.request,
        signal,
      );
      if (!responseMatchesStrategyTrace(prepared, response))
        throw new DiscardedStrategyTraceResponse();
      return response;
    },
    enabled: false,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
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
          response: query.data,
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
