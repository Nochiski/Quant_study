import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  useStartBacktest,
  type BacktestRunSpec,
} from "../../../entities/backtest";
import { ApiRequestError, failureReason } from "../../../shared/api";
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
  /**
   * `code`는 backend 거절 detail의 코드(예: `backtest.strategy.requires_upgrade`), 없으면 null.
   * `detail`은 접힌 진단 상세에 둘 서버 사유다. 화면 본문은 `code`의 번역이 맡는다(이슈 #260).
   */
  | {
      kind: "failed";
      detail: string | null;
      code: string | null;
      /** 거절이 가리킨 요청 본문의 칸(점 경로). 없으면 null. */
      field: string | null;
      /** 거절 문장의 자리표시자를 채울 detail 값(`ApiRequestError.values`). */
      values: Readonly<Record<string, string>>;
    };

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
            detail: failureReason(error),
            code:
              error instanceof ApiRequestError ? (error.code ?? null) : null,
            field:
              error instanceof ApiRequestError ? (error.field ?? null) : null,
            values: error instanceof ApiRequestError ? error.values : {},
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
