import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  useStartBacktest,
  type BacktestRunSpec,
} from "../../../entities/backtest";
import { useNavigate, useRouter } from "../../../shared/lib/router";
import {
  decideBacktestSource,
  gateBacktestSourceWithFactorPlans,
  type BacktestSourceDecision,
} from "./backtest-source";
import type { DocumentState } from "./document-state";
import type { ExecutionPlansState } from "./use-execution-plans";

export type RunBacktestStatus =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "accepted"; runId: string }
  | { kind: "failed"; detail: string };

export type BacktestRunOptions = Omit<
  BacktestRunSpec,
  "strategy" | "strategy_source"
>;

type DocumentIdentity = Pick<DocumentState, "documentEpoch" | "sourceVersion">;
type RunOwner = DocumentIdentity & { optionsKey: string };

type RunSnapshot = RunOwner & { pathname: string };
type OwnedRunStatus = RunOwner & { status: RunBacktestStatus };

const IDLE: RunBacktestStatus = { kind: "idle" };

const sameOwner = (left: RunOwner, right: RunOwner): boolean =>
  left.documentEpoch === right.documentEpoch &&
  left.sourceVersion === right.sourceVersion &&
  left.optionsKey === right.optionsKey;

/**
 * Starts a backtest from the editor and moves to the run page (WORKFLOW P3-05). The request
 * carries `strategy_source` only — a saved-revision reference or an inline draft with its
 * provenance — never a bare spec, so the run manifest always records where the spec came from.
 * A blocked decision never starts a run.
 */
export const useRunBacktest = (
  state: DocumentState,
  executionPlans: ExecutionPlansState,
  options: BacktestRunOptions | null = {},
) => {
  const navigate = useNavigate();
  const router = useRouter();
  const start = useStartBacktest();
  const [ownedStatus, setOwnedStatus] = useState<OwnedRunStatus | null>(null);
  const optionsKey = useMemo(() => JSON.stringify(options), [options]);
  const currentOwner = useMemo<RunOwner>(
    () => ({
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      optionsKey,
    }),
    [optionsKey, state.documentEpoch, state.sourceVersion],
  );
  const latestOwner = useRef<RunOwner>(currentOwner);
  const activeRequest = useRef<symbol | null>(null);
  useLayoutEffect(() => {
    latestOwner.current = {
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      optionsKey,
    };
  }, [optionsKey, state.documentEpoch, state.sourceVersion]);
  const decision = useMemo(
    () =>
      gateBacktestSourceWithFactorPlans(
        decideBacktestSource(state),
        executionPlans,
      ),
    [executionPlans, state],
  );
  const status =
    ownedStatus !== null && sameOwner(ownedStatus, currentOwner)
      ? ownedStatus.status
      : IDLE;

  const { mutateAsync, isPending } = start;
  const run = useCallback(async () => {
    if (status.kind === "accepted") {
      await navigate({
        to: "/research/backtests/$runId",
        params: { runId: status.runId },
      });
      return;
    }
    if (
      decision.kind === "blocked" ||
      options === null ||
      status.kind === "starting" ||
      isPending ||
      activeRequest.current !== null
    )
      return;
    const snapshot: RunSnapshot = {
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      optionsKey,
      pathname: router.state.location.pathname,
    };
    const requestId = Symbol("backtest-request");
    activeRequest.current = requestId;
    setOwnedStatus({ ...snapshot, status: { kind: "starting" } });
    let runId: string;
    try {
      const accepted = await mutateAsync({
        ...options,
        strategy_source:
          decision.kind === "saved_revision"
            ? decision.reference
            : decision.draft,
      });
      runId = accepted.run.run_id;
    } catch (error) {
      if (
        sameOwner(snapshot, latestOwner.current) &&
        router.state.location.pathname === snapshot.pathname
      ) {
        setOwnedStatus({
          ...snapshot,
          status: {
            kind: "failed",
            detail: error instanceof Error ? error.message : String(error),
          },
        });
      }
      return;
    } finally {
      if (activeRequest.current === requestId) activeRequest.current = null;
    }
    // The request belongs to the exact text and route that submitted it. A response arriving
    // after an edit or route change must not replace the user's newer screen or its status.
    if (
      !sameOwner(snapshot, latestOwner.current) ||
      router.state.location.pathname !== snapshot.pathname
    )
      return;
    setOwnedStatus({
      ...snapshot,
      status: { kind: "accepted", runId },
    });
    // Dirty inline drafts intentionally hit the leave guard here. If the user stays, the
    // accepted run id remains owned by this document and pressing Backtest opens that run
    // again without submitting a duplicate request.
    await navigate({
      to: "/research/backtests/$runId",
      params: { runId },
    });
  }, [
    decision,
    isPending,
    mutateAsync,
    navigate,
    options,
    optionsKey,
    router,
    state,
    status,
  ]);

  return {
    run,
    decision,
    status,
    canRun:
      (status.kind === "accepted" || decision.kind !== "blocked") &&
      options !== null &&
      status.kind !== "starting" &&
      !isPending,
  };
};

export type { BacktestSourceDecision };
