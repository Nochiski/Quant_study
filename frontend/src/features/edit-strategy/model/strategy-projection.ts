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
