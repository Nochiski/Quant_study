import { useCallback, useMemo, useState } from "react";

import { useStartBacktest } from "../../../entities/backtest";
import { useNavigate } from "../../../shared/lib/router";
import {
  decideBacktestSource,
  type BacktestSourceDecision,
} from "./backtest-source";
import type { DocumentState } from "./document-state";

export type RunBacktestStatus =
  { kind: "idle" } | { kind: "starting" } | { kind: "failed"; detail: string };

/**
 * Starts a backtest from the editor and moves to the run page (WORKFLOW P3-05). The request
 * carries `strategy_source` only — a saved-revision reference or an inline draft with its
 * provenance — never a bare spec, so the run manifest always records where the spec came from.
 * A blocked decision never starts a run.
 */
export const useRunBacktest = (state: DocumentState) => {
  const navigate = useNavigate();
  const start = useStartBacktest();
  const [status, setStatus] = useState<RunBacktestStatus>({ kind: "idle" });
  const decision = useMemo(() => decideBacktestSource(state), [state]);

  const { mutateAsync, isPending } = start;
  const run = useCallback(async () => {
    if (decision.kind === "blocked" || isPending) return;
    setStatus({ kind: "starting" });
    try {
      const accepted = await mutateAsync({
        strategy_source:
          decision.kind === "saved_revision"
            ? decision.reference
            : decision.draft,
      });
      setStatus({ kind: "idle" });
      await navigate({
        to: "/research/backtests/$runId",
        params: { runId: accepted.run.run_id },
      });
    } catch (error) {
      setStatus({
        kind: "failed",
        detail: error instanceof Error ? error.message : String(error),
      });
    }
  }, [decision, isPending, mutateAsync, navigate]);

  return {
    run,
    decision,
    status,
    canRun: decision.kind !== "blocked" && !isPending,
  };
};

export type { BacktestSourceDecision };
