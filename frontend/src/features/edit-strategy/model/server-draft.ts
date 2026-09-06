import type { StrategyDraft } from "../../../shared/api";
import type { DocumentState } from "./document-state";

export type ServerDraftBase = Pick<
  DocumentState,
  "format" | "strategyId" | "baseRevision" | "baseSpecHash"
> & { schemaVersion: string };

/** A saved base gets one stable register; the canonical hash prevents identity ambiguity. */
export const revisionDraftId = (
  strategyId: string,
  revision: number,
  specHash: string,
): string => `revision:${strategyId}:${revision}:${specHash}`;

/** Only URL-safe, generated new-draft identifiers are accepted back from the route. */
export const isNewDraftId = (value: unknown): value is string =>
  typeof value === "string" && /^draft-[a-f0-9]{32}$/u.test(value);

export const createNewDraftId = (): string => {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return `draft-${Array.from(bytes, (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("")}`;
};

/** Base identity is all-or-none and exact; source bytes are deliberately not part of it. */
export const matchesServerDraftBase = (
  draft: StrategyDraft,
  base: ServerDraftBase,
): boolean =>
  draft.format === base.format &&
  draft.schema_version === base.schemaVersion &&
  draft.strategy_id === base.strategyId &&
  draft.base_revision === base.baseRevision &&
  draft.base_spec_hash === base.baseSpecHash;
