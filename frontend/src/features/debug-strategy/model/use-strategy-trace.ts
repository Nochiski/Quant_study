import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

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

type ActiveRequest = {
  token: symbol;
  ownerKey: string;
  controller: AbortController;
};

const errorMessage = (error: unknown): string =>
  error instanceof ApiRequestError
    ? (error.detail ?? error.message)
    : error instanceof Error
      ? error.message
      : String(error);

/**
 * Owns a single bounded trace request. Results remain visible only for the exact document,
 * selections and backend fingerprints that submitted them; superseded requests are aborted and
 * cannot publish even if the transport completes late.
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
  const latestOwnerKey = useRef(ownerKey);
  const active = useRef<ActiveRequest | null>(null);
  const [ownedState, setOwnedState] = useState<OwnedState | null>(null);

  useLayoutEffect(() => {
    latestOwnerKey.current = ownerKey;
  }, [ownerKey]);

  useEffect(
    () => () => {
      if (active.current?.ownerKey === ownerKey) {
        active.current.controller.abort();
        active.current = null;
      }
    },
    [ownerKey],
  );

  const run = useCallback(async (): Promise<void> => {
    if (prepared.kind !== "ready") return;
    active.current?.controller.abort();
    const controller = new AbortController();
    const token = Symbol("strategy-trace");
    const request = {
      token,
      ownerKey: prepared.ownerKey,
      controller,
    };
    active.current = request;
    setOwnedState({
      kind: "loading",
      ownerKey: prepared.ownerKey,
      request: prepared.request,
    });
    try {
      const response = await strategyWorkbenchApi.traceStrategy(
        prepared.request,
        controller.signal,
      );
      if (
        active.current?.token !== token ||
        latestOwnerKey.current !== prepared.ownerKey
      )
        return;
      active.current = null;
      if (!responseMatchesStrategyTrace(prepared, response)) {
        setOwnedState({
          kind: "discarded",
          ownerKey: prepared.ownerKey,
          request: prepared.request,
        });
        return;
      }
      setOwnedState({
        kind: "success",
        ownerKey: prepared.ownerKey,
        request: prepared.request,
        response,
      });
    } catch (error) {
      if (
        active.current?.token !== token ||
        latestOwnerKey.current !== prepared.ownerKey
      )
        return;
      active.current = null;
      if (controller.signal.aborted) return;
      setOwnedState({
        kind: "error",
        ownerKey: prepared.ownerKey,
        request: prepared.request,
        message: errorMessage(error),
      });
    }
  }, [prepared]);

  const cancel = useCallback(() => {
    const request = active.current;
    if (request === null) return;
    request.controller.abort();
    active.current = null;
    if (prepared.kind === "ready" && request.ownerKey === prepared.ownerKey) {
      setOwnedState({
        kind: "cancelled",
        ownerKey: prepared.ownerKey,
        request: prepared.request,
      });
    }
  }, [prepared]);

  const state: StrategyTraceState =
    prepared.kind === "blocked"
      ? prepared
      : ownedState === null || ownedState.ownerKey !== prepared.ownerKey
        ? { kind: "idle" }
        : ownedState;

  return { prepared, state, run, cancel };
};
