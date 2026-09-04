import type { StrategyDocument } from "../../../shared/api";
import type { SourceFormat } from "../../../shared/lib/yaml12";
import type { DocumentAction } from "./document-state";

/**
 * Where the text in the editor came from (WORKFLOW P2-04): a fresh draft with no base, or one
 * saved revision that becomes the draft base (`baseRevision`, `baseSpecHash`, `savedSource`).
 * The revision entry carries the exact stored document, never a re-serialised projection.
 */
export type DocumentSource =
  | { kind: "new"; format: SourceFormat; source: string }
  | { kind: "revision"; document: StrategyDocument };

/** Identity of a source; when it changes the reducer is re-loaded from the new base. */
export const documentSourceKey = (source: DocumentSource): string =>
  source.kind === "new"
    ? "new"
    : `${source.document.strategy_id}@${source.document.revision}`;

export const loadAction = (source: DocumentSource): DocumentAction =>
  source.kind === "new"
    ? {
        type: "load",
        format: source.format,
        source: source.source,
        strategyId: null,
        baseRevision: null,
        baseSpecHash: null,
      }
    : {
        type: "load",
        format: source.document.format,
        source: source.document.source,
        strategyId: source.document.strategy_id,
        baseRevision: source.document.revision,
        baseSpecHash: source.document.spec_hash,
      };
