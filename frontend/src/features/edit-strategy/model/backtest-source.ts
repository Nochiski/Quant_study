import type { InlineDraft, SavedRevisionReference } from "../../../shared/api";
import { currentSpec, type DocumentState } from "./document-state";

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
  | { kind: "blocked"; reason: "empty" | "invalid" | "stale" | "composing" };

export const decideBacktestSource = (
  state: DocumentState,
): BacktestSourceDecision => {
  if (state.composing) return { kind: "blocked", reason: "composing" };
  if (state.source.trim().length === 0)
    return { kind: "blocked", reason: "empty" };
  const compiledIsCurrent =
    state.compiled !== null && state.compiledVersion === state.sourceVersion;
  if (!compiledIsCurrent) return { kind: "blocked", reason: "stale" };
  const spec = currentSpec(state);
  if (
    spec === null ||
    state.compiled === null ||
    state.compiled.specHash === null
  ) {
    return { kind: "blocked", reason: "invalid" };
  }
  if (
    !state.dirty &&
    state.strategyId !== null &&
    state.baseRevision !== null &&
    state.baseSpecHash !== null &&
    state.compiled.specHash === state.baseSpecHash
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
      spec,
      source_hash: state.compiled.sourceHash || null,
    },
  };
};
