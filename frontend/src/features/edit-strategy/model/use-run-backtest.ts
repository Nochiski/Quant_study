import { useCommittedRef } from "../../../shared/lib/react";
import type { BacktestRequestOptions as BacktestRunOptions } from "../../../entities/backtest";
import { useCallback, useMemo, useRef, useState } from "react";

import {
  useStartBacktest,
  type BacktestRunSpec,
} from "../../../entities/backtest";
import { useNavigate, useRouter } from "../../../shared/lib/router";
import {
  decideBacktestSource,
  gateBacktestSourceWithFactorPlans,
  isBacktestSettling,
  type BacktestSourceDecision,
} from "./backtest-source";
import type { DocumentState } from "./document-state";
import type { ExecutionPlansState } from "./use-execution-plans";

export type RunBacktestStatus =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "accepted"; runId: string }
  /** 거절 원문 객체를 보존하고 표시·교정 소비자가 해당 책임의 공용 함수를 쓴다. */
  | { kind: "failed"; error: unknown };



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
  options: BacktestRunOptions | null,
  parameterSourceRunId?: string,
) => {
  const navigate = useNavigate();
  const router = useRouter();
  const start = useStartBacktest();
  const [ownedStatus, setOwnedStatus] = useState<OwnedRunStatus | null>(null);
  const optionsKey = useMemo(() => JSON.stringify(parameterSourceRunId === undefined ? options : [options, parameterSourceRunId]), [options, parameterSourceRunId]);
  const currentOwner = useMemo<RunOwner>(
    () => ({
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      optionsKey,
    }),
    [optionsKey, state.documentEpoch, state.sourceVersion],
  );
  const latestOwner = useCommittedRef(currentOwner);
  const activeRequest = useRef<symbol | null>(null);
  const decision = useMemo(
    () =>
      gateBacktestSourceWithFactorPlans(
        decideBacktestSource(state),
        executionPlans,
      ),
    [executionPlans, state],
  );
  // 실행 버튼이 보낼 요청. 실행 전 시도 미리 계산(검증 랩 V5-05)도 같은 요청을 묻는다.
  const request = useMemo<BacktestRunSpec | null>(
    () =>
      decision.kind === "blocked" || options === null
        ? null
        : decision.kind === "saved_revision"
          ? { ...options, strategy_source: decision.reference }
          : {
              ...options,
              strategy_source: decision.draft,
              // 저장된 전략을 고친 초안도 그 전략 계열의 시도로 센다(검증 랩 spec D2). 저장 리비전은
              // backend가 리비전에서 계열을 알아서 싣지 않는다.
              ...(state.strategyId === null
                ? {}
                : { lineage_strategy_id: state.strategyId }),
            },
    [decision, options, state.strategyId],
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
      request === null ||
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
      const accepted = await mutateAsync(request);
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
            error,
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
    isPending,
    mutateAsync,
    navigate,
    latestOwner,
    optionsKey,
    request,
    router,
    state,
    status,
  ]);

  return {
    run,
    decision,
    /** 실행 버튼이 보낼 요청. 실행할 수 없으면 null. */
    request,
    status,
    /** 결정이 닫혀 있지만 팩터 계획 조회가 아직 끝나지 않았다(`isBacktestSettling`). */
    settling: isBacktestSettling(decision, executionPlans),
    canRun:
      (status.kind === "accepted" || decision.kind !== "blocked") &&
      options !== null &&
      status.kind !== "starting" &&
      !isPending,
  };
};

export type { BacktestSourceDecision };
