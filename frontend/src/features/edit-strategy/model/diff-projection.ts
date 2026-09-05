import type { DiffEntry } from "../../../shared/api";
import { lineDiff, type TextDiff } from "../../../shared/lib/text-diff";
import { currentCompile, type DocumentState } from "./document-state";

export type DraftSemanticDiff =
  | { status: "no-base" }
  | { status: "base-pending" }
  | { status: "current-unavailable" }
  | { status: "incoherent" }
  | {
      status: "ready";
      baseSpecHash: string;
      currentSpecHash: string;
      changes: DiffEntry[];
    };

export type DraftDiffProjection = {
  source: TextDiff;
  sourceBase: "saved-revision" | "empty-draft";
  semantic: DraftSemanticDiff;
};

const pointerToken = (value: string): string =>
  value.replaceAll("~", "~0").replaceAll("/", "~1");

const childPointer = (parent: string, key: string | number): string =>
  `${parent}/${pointerToken(String(key))}`;

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const MISSING = Symbol("missing");

const walkCanonical = (
  before: unknown | typeof MISSING,
  after: unknown | typeof MISSING,
  pointer: string,
  entries: DiffEntry[],
): void => {
  if (before === MISSING) {
    entries.push({ pointer, kind: "added", before: null, after });
    return;
  }
  if (after === MISSING) {
    entries.push({ pointer, kind: "removed", before, after: null });
    return;
  }
  if (isObject(before) && isObject(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    for (const key of [...keys].sort()) {
      walkCanonical(
        Object.hasOwn(before, key) ? before[key] : MISSING,
        Object.hasOwn(after, key) ? after[key] : MISSING,
        childPointer(pointer, key),
        entries,
      );
    }
    return;
  }
  if (Array.isArray(before) && Array.isArray(after)) {
    const length = Math.max(before.length, after.length);
    for (let index = 0; index < length; index += 1) {
      walkCanonical(
        index < before.length ? before[index] : MISSING,
        index < after.length ? after[index] : MISSING,
        childPointer(pointer, index),
        entries,
      );
    }
    return;
  }
  if (!Object.is(before, after)) {
    entries.push({ pointer, kind: "changed", before, after });
  }
};

/**
 * Presentation-only structural diff over two backend-owned canonical JSON payloads. It knows no
 * StrategySpec fields or normalization rules: identity/comment/number semantics remain owned by
 * the backend canonicalizer and its spec hash.
 */
export const diffCanonicalJson = (
  beforeCanonicalJson: string,
  afterCanonicalJson: string,
): DiffEntry[] | null => {
  try {
    const entries: DiffEntry[] = [];
    walkCanonical(
      JSON.parse(beforeCanonicalJson) as unknown,
      JSON.parse(afterCanonicalJson) as unknown,
      "",
      entries,
    );
    return entries;
  } catch {
    return null;
  }
};

/** Draft-vs-base projection. Invalid current source always keeps its exact text diff. */
export const projectDraftDiff = (state: DocumentState): DraftDiffProjection => {
  const sourceBase =
    state.savedSource === null ? "empty-draft" : "saved-revision";
  const source = lineDiff(state.savedSource ?? "", state.source);
  if (
    state.baseRevision === null ||
    state.baseSpecHash === null ||
    state.savedSource === null
  ) {
    return { source, sourceBase, semantic: { status: "no-base" } };
  }
  if (state.savedCanonicalJson === null) {
    return { source, sourceBase, semantic: { status: "base-pending" } };
  }
  const current = currentCompile(state);
  if (current === null) {
    return { source, sourceBase, semantic: { status: "current-unavailable" } };
  }
  if (state.baseSpecHash === current.specHash) {
    return {
      source,
      sourceBase,
      semantic: {
        status: "ready",
        baseSpecHash: state.baseSpecHash,
        currentSpecHash: current.specHash,
        changes: [],
      },
    };
  }
  const changes = diffCanonicalJson(
    state.savedCanonicalJson,
    current.canonicalJson,
  );
  if (changes === null || changes.length === 0) {
    return { source, sourceBase, semantic: { status: "incoherent" } };
  }
  return {
    source,
    sourceBase,
    semantic: {
      status: "ready",
      baseSpecHash: state.baseSpecHash,
      currentSpecHash: current.specHash,
      changes,
    },
  };
};
