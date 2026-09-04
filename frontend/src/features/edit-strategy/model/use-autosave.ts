import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  clearDraft,
  defaultDraftStorage,
  draftKey,
  readDraft,
  writeDraft,
  type DraftRecord,
  type DraftStorage,
} from "./draft-store";
import type { DocumentAction, DocumentState } from "./document-state";

const AUTOSAVE_DELAY_MS = 800;

export type Recovery = {
  record: DraftRecord;
  /** The recovered text targets another schema version: offer the raw text, not a restore. */
  schemaMismatch: boolean;
  restore: () => void;
  discard: () => void;
};

export type Autosave = {
  recovery: Recovery | null;
  /** Timestamp of the last local write for the current base, if any. */
  lastSavedAt: string | null;
  available: boolean;
};

type AutosaveOptions = {
  /** Schema version the editor currently targets (from the runtime schema), when known. */
  schemaVersion: string | null;
  storage?: DraftStorage | null;
  now?: () => string;
};

/**
 * Local autosave and recovery (WORKFLOW P3-06). A dirty text is written to local storage after
 * a short delay under its draft base key; a successful save clears the record for that base.
 * On load, a record for the same base whose text differs from the loaded original becomes a
 * recovery offer; a record written for another schema version is offered as a raw download
 * instead of being restored into the editor.
 */
export const useAutosave = (
  state: DocumentState,
  dispatch: (action: DocumentAction) => void,
  options: AutosaveOptions,
): Autosave => {
  const storage = useMemo(
    () =>
      options.storage === undefined ? defaultDraftStorage() : options.storage,
    [options.storage],
  );
  const now = options.now ?? (() => new Date().toISOString());
  const key = draftKey(state.strategyId, state.baseRevision);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);

  // Recovery is judged once per base, against the text the base was loaded with. The judgement
  // is derived state keyed by the base (re-derived during render when the key changes), and a
  // restore/discard dismisses it for that base.
  const [judged, setJudged] = useState(() => ({
    key,
    record: readDraft(storage, key),
    original: state.savedSource ?? state.source,
  }));
  if (judged.key !== key) {
    setJudged({
      key,
      record: readDraft(storage, key),
      original: state.savedSource ?? state.source,
    });
  }
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const candidate =
    judged.key === key &&
    judged.record !== null &&
    judged.record.source !== judged.original &&
    dismissedKey !== key
      ? judged.record
      : null;

  // Autosave the dirty text; clear the record when the base is saved successfully.
  const previous = useRef({
    key,
    dirty: state.dirty,
    documentEpoch: state.documentEpoch,
  });
  useEffect(() => {
    const before = previous.current;
    previous.current = {
      key,
      dirty: state.dirty,
      documentEpoch: state.documentEpoch,
    };
    if (
      before.documentEpoch === state.documentEpoch &&
      before.dirty &&
      !state.dirty &&
      state.savedSource === state.source
    ) {
      // A successful save can advance the base revision, so remove the record stored under the
      // pre-save base key. A document load changes the epoch and must keep its recovery record.
      clearDraft(storage, before.key);
      setLastSavedAt(null);
      return;
    }
    if (!state.dirty || state.composing) return;
    const timer = setTimeout(() => {
      const savedAt = now();
      const written = writeDraft(storage, {
        key,
        format: state.format,
        source: state.source,
        strategyId: state.strategyId,
        baseRevision: state.baseRevision,
        baseSpecHash: state.baseSpecHash,
        schemaVersion: options.schemaVersion,
        savedAt,
      });
      if (written) setLastSavedAt(savedAt);
    }, AUTOSAVE_DELAY_MS);
    return () => clearTimeout(timer);
    // `now` is a stable option in practice; re-running on its identity would re-arm the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    key,
    storage,
    state.dirty,
    state.composing,
    state.source,
    state.format,
    state.strategyId,
    state.baseRevision,
    state.baseSpecHash,
    state.savedSource,
    state.documentEpoch,
    options.schemaVersion,
  ]);

  const restore = useCallback(() => {
    if (!candidate) return;
    dispatch({ type: "edit", source: candidate.source });
    setDismissedKey(key);
  }, [candidate, dispatch, key]);
  const discard = useCallback(() => {
    if (!candidate) return;
    clearDraft(storage, key);
    setDismissedKey(key);
  }, [candidate, key, storage]);

  return useMemo<Autosave>(
    () => ({
      recovery: candidate
        ? {
            record: candidate,
            schemaMismatch:
              options.schemaVersion !== null &&
              candidate.schemaVersion !== null &&
              candidate.schemaVersion !== options.schemaVersion,
            restore,
            discard,
          }
        : null,
      lastSavedAt,
      available: storage !== null,
    }),
    [candidate, options.schemaVersion, restore, discard, lastSavedAt, storage],
  );
};
