import type { InlineDraft, SavedRevisionReference } from "../../../shared/api";
import { currentCompile, type DocumentState } from "./document-state";
import type { ExecutionPlansState } from "./use-execution-plans";

/**
 * What a backtest started from this editor would run (WORKFLOW P3-05):
 *
 * - `saved_revision` only when the text is exactly the saved base (`dirty == false`,
 *   `baseRevision != null`) and the backend's current spec hash equals the base spec hash —
 *   the run is then reproducible from the stored revision alone;
 * - `inline_draft` when the current text compiled cleanly but is not that base (a new
 *   strategy, edits after a save, a base whose hash no longer matches): the run carries the
 *   spec plus source/spec provenance;
 * - `blocked` when the document is invalid, unparsed, still compiling, or the compile result
 *   belongs to an older text. There is no fallback to the saved revision in that case.
 */
export type BacktestSourceDecision =
  | { kind: "saved_revision"; reference: SavedRevisionReference }
  | { kind: "inline_draft"; draft: InlineDraft }
  | {
      kind: "blocked";
      reason: "empty" | "invalid" | "stale" | "composing" | "factor-plan";
    };

export const decideBacktestSource = (
  state: DocumentState,
): BacktestSourceDecision => {
  if (state.composing) return { kind: "blocked", reason: "composing" };
  if (state.source.trim().length === 0)
    return { kind: "blocked", reason: "empty" };
  const compiledIsCurrent =
    state.compiled !== null && state.compiledVersion === state.sourceVersion;
  if (!compiledIsCurrent) return { kind: "blocked", reason: "stale" };
  const compiled = currentCompile(state);
  if (compiled === null) {
    return { kind: "blocked", reason: "invalid" };
  }
  if (
    !state.dirty &&
    state.strategyId !== null &&
    state.baseRevision !== null &&
    state.baseSpecHash !== null &&
    compiled.specHash === state.baseSpecHash
  ) {
    return {
      kind: "saved_revision",
      reference: {
        kind: "saved_revision",
        strategy_id: state.strategyId,
        revision: state.baseRevision,
        expected_spec_hash: state.baseSpecHash,
      },
    };
  }
  return {
    kind: "inline_draft",
    draft: {
      kind: "inline_draft",
      spec: compiled.spec,
      source_hash: compiled.sourceHash || null,
    },
  };
};

/**
 * Adds the metadata-aware FactorGraph gate to a document-owned source decision. A strategy with
 * factors runs only after the backend explain contract returns one valid execution plan per
 * factor. Empty-factor documents preserve the existing source decision.
 */
export const gateBacktestSourceWithFactorPlans = (
  decision: BacktestSourceDecision,
  plans: ExecutionPlansState,
): BacktestSourceDecision => {
  if (decision.kind === "blocked" || plans.status === "empty") return decision;
  if (
    plans.status === "ready" &&
    plans.factors.every(
      (factor) =>
        factor.explanation.validation.valid && factor.explanation.plan !== null,
    )
  )
    return decision;
  return { kind: "blocked", reason: "factor-plan" };
};

/**
 * 실행 결정이 닫혀 있지만 아직 문서 검증이 끝나지 않은 상태인지.
 *
 * 팩터가 있는 문서는 compile 뒤에 backend explain으로 팩터마다 실행 계획을 받아야 게이트가 열린다.
 * 그 조회(또는 그 전제인 메타데이터 조회)가 도는 동안의 `factor-plan` 닫힘은 "실행할 수 없음"이 아니라
 * "아직 모름"이다. "적용 후 백테스트"는 이 동안 기다려야 한다(C-02 리뷰 P1-1). 계획 오류·호환 불가·
 * 메타데이터 없음은 끝난 판정이라 여기에 들지 않는다.
 */
export const isBacktestSettling = (
  decision: BacktestSourceDecision,
  plans: ExecutionPlansState,
): boolean =>
  decision.kind === "blocked" &&
  decision.reason === "factor-plan" &&
  (plans.status === "loading" || plans.status === "metadata-loading");
