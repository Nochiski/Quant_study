import type { StrategySpec } from "../../../entities/strategy";
import {
  currentCompile,
  type CompleteCompileOutcome,
  type DocumentState,
} from "./document-state";

export type StrategyProjection =
  | { status: "unavailable" }
  | {
      status: "ready";
      spec: StrategySpec;
      canonicalJson: string;
      specHash: string;
      schemaVersion: string;
      stale: boolean;
      /** backend compile이 `strategy.field.inapplicable`로 경고한 pointer(명시 기재·기본값과 다름·`owned_by_error` 억제는 backend 판정). */
      inapplicablePointers: ReadonlySet<string>;
    };

const fromCompile = (
  outcome: CompleteCompileOutcome,
  stale: boolean,
): StrategyProjection => ({
  status: "ready",
  spec: outcome.spec,
  canonicalJson: outcome.canonicalJson,
  specHash: outcome.specHash,
  schemaVersion: outcome.schemaVersion,
  stale,
  inapplicablePointers: new Set(
    outcome.diagnostics
      .filter((diagnostic) => diagnostic.code === "strategy.field.inapplicable")
      .map((diagnostic) => diagnostic.pointer),
  ),
});

/**
 * Selects only complete backend compile snapshots for read-only views. Current wins, then the
 * same-document last valid compile; no client-generated canonical representation is accepted.
 */
export const projectStrategySpec = (
  state: DocumentState,
): StrategyProjection => {
  const current = currentCompile(state);
  if (current !== null) return fromCompile(current, false);

  if (state.lastValidCompiled !== null)
    return fromCompile(state.lastValidCompiled.outcome, true);
  return { status: "unavailable" };
};
