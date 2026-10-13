import { currentParse, currentParseOutcome } from "./document-state";
import { useMemo } from "react";

import type { DocumentState } from "./document-state";
import type { JsonSchema } from "./schema-navigator";
import {
  projectStrategyOutline,
  type StrategyOutlineNode,
  type ValidParsedSource,
} from "./strategy-outline";

export type StrategyOutlineSnapshot = {
  documentEpoch: number;
  sourceVersion: number;
  nodes: StrategyOutlineNode[];
  parsed: ValidParsedSource;
  /** True while this document shows its last valid structure during parse/update failure. */
  stale: boolean;
  staleReason: "updating" | "syntax-error" | null;
};

/**
 * Keeps exactly one projection fallback, scoped to a document epoch. Parse failures cannot erase
 * a useful tree, and route changes cannot leak the previous document's structure.
 */
export const useStrategyOutline = (
  state: DocumentState,
  schema: JsonSchema | null,
): StrategyOutlineSnapshot | null => {
  const current = currentParse(state);
  const syntaxFailed = currentParseOutcome(state)?.status === "rejected";
  const parsed = current ?? state.lastValidParse?.result ?? null;
  const parsedVersion = current
    ? state.sourceVersion
    : (state.lastValidParse?.version ?? -1);
  return useMemo<StrategyOutlineSnapshot | null>(
    () =>
      parsed
        ? {
            documentEpoch: state.documentEpoch,
            sourceVersion: parsedVersion,
            nodes: projectStrategyOutline(parsed, schema),
            parsed,
            stale: current === null,
            staleReason:
              current !== null
                ? null
                : syntaxFailed
                  ? "syntax-error"
                  : "updating",
          }
        : null,
    [
      current,
      parsed,
      parsedVersion,
      schema,
      state.documentEpoch,
      syntaxFailed,
    ],
  );
};
