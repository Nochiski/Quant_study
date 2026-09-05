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
  /** The exact source this recovery was compared with. Autosave owns the diff baseline. */
  original: string;
  /** Restore is fail-closed until the complete draft/base contract is proven compatible. */
  compatibility: "compatible" | "unverified" | "incompatible";
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
  const [lastWrite, setLastWrite] = useState<{
    key: string;
    savedAt: string;
  } | null>(null);
  const [writeAvailable, setWriteAvailable] = useState(storage !== null);

  // Recovery is judged once per base, against the text the base was loaded with. The judgement
  // is derived state keyed by the base (re-derived during render when the key changes), and a
  // restore/discard dismisses it for that base.
  const [judged, setJudged] = useState(() => ({
    key,
    record: readDraft(storage, key),
    original: state.savedSource ?? state.source,
    dismissed: false,
  }));
  if (judged.key !== key) {
    setJudged({
      key,
      record: readDraft(storage, key),
      original: state.savedSource ?? state.source,
      dismissed: false,
    });
  }
  const candidate =
    judged.key === key &&
    judged.record !== null &&
    judged.record.source !== judged.original &&
    !judged.dismissed
      ? judged.record
      : null;

  const compatibility: Recovery["compatibility"] =
    options.schemaVersion === null || candidate?.schemaVersion === null
      ? "unverified"
      : candidate === null
        ? "incompatible"
        : candidate.schemaVersion === options.schemaVersion &&
            candidate.format === state.format &&
            candidate.strategyId === state.strategyId &&
            candidate.baseRevision === state.baseRevision &&
            candidate.baseSpecHash === state.baseSpecHash
          ? "compatible"
          : "incompatible";

  // Autosave the dirty text; clear the record when the base is saved successfully.
  const previous = useRef({
    key,
    documentEpoch: state.documentEpoch,
    savedVersion: state.savedVersion,
  });
  useEffect(() => {
    const before = previous.current;
    previous.current = {
      key,
      documentEpoch: state.documentEpoch,
      savedVersion: state.savedVersion,
    };
    if (
      before.documentEpoch === state.documentEpoch &&
      state.savedVersion > before.savedVersion
    ) {
      // A successful save can advance the base revision, so remove the record stored under the
      // pre-save base key even if later edits keep the document dirty. A document load changes
      // the epoch and must keep its recovery record.
      clearDraft(storage, before.key);
      setLastWrite((current) =>
        current?.key === before.key ? null : current,
      );
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
      setWriteAvailable(written);
      setLastWrite(written ? { key, savedAt } : null);
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
    state.savedVersion,
    state.documentEpoch,
    options.schemaVersion,
  ]);

  const restore = useCallback(() => {
    if (!candidate || compatibility !== "compatible") return;
    dispatch({ type: "edit", source: candidate.source });
    setJudged((current) =>
      current.key === key ? { ...current, dismissed: true } : current,
    );
  }, [candidate, compatibility, dispatch, key]);
  const discard = useCallback(() => {
    if (!candidate) return;
    clearDraft(storage, key);
    setJudged((current) =>
      current.key === key ? { ...current, dismissed: true } : current,
    );
  }, [candidate, key, storage]);

  return useMemo<Autosave>(
    () => ({
      recovery: candidate
        ? {
            record: candidate,
            original: judged.original,
            compatibility,
            restore,
            discard,
          }
        : null,
      lastSavedAt: lastWrite?.key === key ? lastWrite.savedAt : null,
      available: writeAvailable,
    }),
    [
      candidate,
      judged.original,
      compatibility,
      restore,
      discard,
      lastWrite,
      key,
      writeAvailable,
    ],
  );
};
