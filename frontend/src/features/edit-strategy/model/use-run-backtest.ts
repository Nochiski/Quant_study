import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

import { useStartBacktest } from "../../../entities/backtest";
import { useNavigate, useRouter } from "../../../shared/lib/router";
import {
  decideBacktestSource,
  type BacktestSourceDecision,
} from "./backtest-source";
import type { DocumentState } from "./document-state";

export type RunBacktestStatus =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "accepted"; runId: string }
  | { kind: "failed"; detail: string };

type DocumentIdentity = Pick<DocumentState, "documentEpoch" | "sourceVersion">;

type RunSnapshot = DocumentIdentity & { pathname: string };
type OwnedRunStatus = DocumentIdentity & { status: RunBacktestStatus };

const IDLE: RunBacktestStatus = { kind: "idle" };

const sameDocument = (
  left: DocumentIdentity,
  right: DocumentIdentity,
): boolean =>
  left.documentEpoch === right.documentEpoch &&
  left.sourceVersion === right.sourceVersion;

/**
 * Starts a backtest from the editor and moves to the run page (WORKFLOW P3-05). The request
 * carries `strategy_source` only — a saved-revision reference or an inline draft with its
 * provenance — never a bare spec, so the run manifest always records where the spec came from.
 * A blocked decision never starts a run.
 */
export const useRunBacktest = (state: DocumentState) => {
  const navigate = useNavigate();
  const router = useRouter();
  const start = useStartBacktest();
  const [ownedStatus, setOwnedStatus] = useState<OwnedRunStatus | null>(null);
  const latestDocument = useRef<DocumentIdentity>({
    documentEpoch: state.documentEpoch,
    sourceVersion: state.sourceVersion,
  });
  const activeRequest = useRef<symbol | null>(null);
  useLayoutEffect(() => {
    latestDocument.current = {
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
    };
  }, [state.documentEpoch, state.sourceVersion]);
  const decision = useMemo(() => decideBacktestSource(state), [state]);
  const status =
    ownedStatus !== null && sameDocument(ownedStatus, state)
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
      status.kind === "starting" ||
      isPending ||
      activeRequest.current !== null
    )
      return;
    const snapshot: RunSnapshot = {
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      pathname: router.state.location.pathname,
    };
    const requestId = Symbol("backtest-request");
    activeRequest.current = requestId;
    setOwnedStatus({ ...snapshot, status: { kind: "starting" } });
    let runId: string;
    try {
      const accepted = await mutateAsync({
        strategy_source:
          decision.kind === "saved_revision"
            ? decision.reference
            : decision.draft,
      });
      runId = accepted.run.run_id;
    } catch (error) {
      if (
        sameDocument(snapshot, latestDocument.current) &&
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
      !sameDocument(snapshot, latestDocument.current) ||
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
  }, [decision, isPending, mutateAsync, navigate, router, state, status]);

  return {
    run,
    decision,
    status,
    canRun:
      (status.kind === "accepted" || decision.kind !== "blocked") &&
      status.kind !== "starting" &&
      !isPending,
  };
};

export type { BacktestSourceDecision };
