import type { StrategySpec } from "../../../entities/strategy";
import type { DocumentState } from "./document-state";

export type StrategyProjectionSeed = {
  strategyId: string;
  revision: number;
  spec: StrategySpec;
  specHash: string;
  schemaVersion: string;
};

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

const prettyCanonicalJson = (canonicalJson: string): string => {
  try {
    return JSON.stringify(JSON.parse(canonicalJson), null, 2);
  } catch {
    // The backend contract promises JSON. Preserve its exact response if that invariant breaks;
    // a read-only projection must not silently substitute a client-generated payload.
    return canonicalJson;
  }
};

const fromCompile = (
  outcome: NonNullable<DocumentState["compiled"]>,
  stale: boolean,
): StrategyProjection =>
  outcome.spec !== null &&
  outcome.canonicalJson !== null &&
  outcome.specHash !== null &&
  outcome.schemaVersion !== null
    ? {
        status: "ready",
        spec: outcome.spec,
        canonicalJson: prettyCanonicalJson(outcome.canonicalJson),
        specHash: outcome.specHash,
        schemaVersion: outcome.schemaVersion,
        stale,
      }
    : { status: "unavailable" };

/**
 * Selects only backend-owned StrategySpec snapshots for read-only views. Current error-free
 * compile wins, then the same-document last valid compile, then the immutable saved revision.
 */
export const projectStrategySpec = (
  state: DocumentState,
  seed: StrategyProjectionSeed | null = null,
): StrategyProjection => {
  const current =
    state.compiledVersion === state.sourceVersion &&
    state.compiled !== null &&
    state.compiled.spec !== null &&
    !state.compiled.diagnostics.some(
      (diagnostic) => diagnostic.severity === "error",
    )
      ? state.compiled
      : null;
  if (current !== null) return fromCompile(current, false);

  if (state.lastValidCompiled !== null)
    return fromCompile(state.lastValidCompiled.outcome, true);

  if (
    seed === null ||
    state.strategyId !== seed.strategyId ||
    state.baseRevision !== seed.revision
  )
    return { status: "unavailable" };

  const seedIsCurrent =
    !state.dirty &&
    state.source === state.savedSource &&
    state.baseSpecHash === seed.specHash;
  return {
    status: "ready",
    spec: seed.spec,
    canonicalJson: JSON.stringify(seed.spec, null, 2),
    specHash: seed.specHash,
    schemaVersion: seed.schemaVersion,
    stale: !seedIsCurrent,
  };
};
