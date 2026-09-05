import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import {
  strategyDocumentQuery,
  strategyRevisionsKey,
} from "../../../entities/strategy";
import {
  ApiRequestError,
  strategyWorkbenchApi,
  type StrategyDocument,
} from "../../../shared/api";
import type { SourceFormat } from "../../../shared/lib/yaml12";
import {
  currentCompile,
  currentSpec,
  type DocumentAction,
  type DocumentState,
} from "./document-state";

export type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving"; documentEpoch: number }
  | { kind: "saved"; document: StrategyDocument; documentEpoch: number }
  /** Someone else stored a newer revision than the draft base (HTTP 409). */
  | {
      kind: "conflict";
      detail: string;
      latestRevision: number | null;
      strategyId: string | null;
      baseRevision: number | null;
      documentEpoch: number;
    }
  /** The backend refused the text as a document (HTTP 422); the draft stays as typed. */
  | { kind: "invalid"; detail: string; documentEpoch: number }
  | { kind: "failed"; detail: string; documentEpoch: number };

type SaveSnapshot = {
  source: string;
  format: SourceFormat;
  strategyId: string | null;
  baseRevision: number | null;
  documentEpoch: number;
  sourceVersion: number;
  canonicalJson: string;
  specHash: string;
};

/**
 * Save is meaningful only for a dirty, non-empty text whose current version the backend compiled
 * without errors (WORKFLOW P3-05: Save is disabled while the document is invalid or stale).
 */
export const canSaveDocument = (state: DocumentState): boolean =>
  state.dirty &&
  !state.composing &&
  state.source.trim().length > 0 &&
  currentSpec(state) !== null;

/**
 * Save the draft as a document: a draft without a base creates a strategy (revision 1), a draft
 * on a base appends the next revision with optimistic concurrency (`expected_revision`). The
 * exact text the server stored becomes the new base; the pages own what happens to the URL.
 * Only the backend decides validity, so a 422 comes back as a status, never as a thrown error.
 */
export const useSaveDocument = (
  state: DocumentState,
  dispatch: (action: DocumentAction) => void,
) => {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });

  const mutation = useMutation({
    mutationFn: (snapshot: SaveSnapshot) =>
      snapshot.strategyId === null || snapshot.baseRevision === null
        ? strategyWorkbenchApi.createStrategyDocument({
            format: snapshot.format,
            source: snapshot.source,
          })
        : strategyWorkbenchApi.reviseStrategyDocument(snapshot.strategyId, {
            format: snapshot.format,
            source: snapshot.source,
            expected_revision: snapshot.baseRevision,
          }),
    onMutate: (snapshot) =>
      setStatus({ kind: "saving", documentEpoch: snapshot.documentEpoch }),
    onSuccess: (document, snapshot) => {
      queryClient.setQueryData(
        strategyDocumentQuery(document.strategy_id, document.revision).queryKey,
        document,
      );
      void queryClient.invalidateQueries({
        queryKey: strategyRevisionsKey(document.strategy_id),
      });
      dispatch({
        type: "saved",
        strategyId: document.strategy_id,
        revision: document.revision,
        specHash: document.spec_hash,
        // Save recompiles the exact source. Reuse the earlier canonical bytes only when the
        // response proves that both source and semantic hash are still the same snapshot.
        // Otherwise the reducer marks the saved baseline for a backend recompile.
        canonicalJson:
          document.source === snapshot.source &&
          document.spec_hash === snapshot.specHash
            ? snapshot.canonicalJson
            : null,
        source: document.source,
        documentEpoch: snapshot.documentEpoch,
        sourceVersion: snapshot.sourceVersion,
      });
      setStatus({
        kind: "saved",
        document,
        documentEpoch: snapshot.documentEpoch,
      });
    },
    onError: (error, snapshot) => {
      if (error instanceof ApiRequestError && error.status === 409) {
        setStatus({
          kind: "conflict",
          detail: error.detail ?? "",
          latestRevision: error.latestRevision,
          strategyId: snapshot.strategyId,
          baseRevision: snapshot.baseRevision,
          documentEpoch: snapshot.documentEpoch,
        });
      } else if (error instanceof ApiRequestError && error.status === 422) {
        setStatus({
          kind: "invalid",
          detail: error.detail ?? "",
          documentEpoch: snapshot.documentEpoch,
        });
      } else {
        setStatus({
          kind: "failed",
          detail: error instanceof Error ? error.message : String(error),
          documentEpoch: snapshot.documentEpoch,
        });
      }
    },
  });

  const { mutate, isPending } = mutation;
  const save = useCallback(() => {
    const compile = currentCompile(state);
    if (!canSaveDocument(state) || compile === null || isPending) return;
    mutate({
      source: state.source,
      format: state.format,
      strategyId: state.strategyId,
      baseRevision: state.baseRevision,
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      canonicalJson: compile.canonicalJson,
      specHash: compile.specHash,
    });
  }, [isPending, mutate, state]);

  const visibleStatus = useMemo<SaveStatus>(
    () =>
      status.kind === "idle" || status.documentEpoch === state.documentEpoch
        ? status
        : { kind: "idle" },
    [state.documentEpoch, status],
  );

  /**
   * Explicitly publish the preserved current document after the server's latest revision. This
   * is not a merge: immutable server history remains intact and the button warns that the new
   * revision uses the current whole document. A second concurrent save simply returns another
   * structured conflict through the same mutation.
   */
  const createRevisionFromConflict = useCallback(() => {
    const compile = currentCompile(state);
    if (
      visibleStatus.kind !== "conflict" ||
      visibleStatus.strategyId === null ||
      visibleStatus.latestRevision === null ||
      visibleStatus.strategyId !== state.strategyId ||
      compile === null ||
      isPending
    )
      return;
    mutate({
      source: state.source,
      format: state.format,
      strategyId: visibleStatus.strategyId,
      baseRevision: visibleStatus.latestRevision,
      documentEpoch: state.documentEpoch,
      sourceVersion: state.sourceVersion,
      canonicalJson: compile.canonicalJson,
      specHash: compile.specHash,
    });
  }, [isPending, mutate, state, visibleStatus]);

  return {
    save,
    status: visibleStatus,
    canSave: canSaveDocument(state) && !isPending,
    createRevisionFromConflict,
    canCreateRevisionFromConflict:
      visibleStatus.kind === "conflict" &&
      visibleStatus.latestRevision !== null &&
      currentCompile(state) !== null &&
      !isPending,
  };
};
