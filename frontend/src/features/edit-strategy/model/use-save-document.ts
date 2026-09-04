import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import {
  strategyDocumentQuery,
  strategyRevisionsQuery,
} from "../../../entities/strategy";
import {
  ApiRequestError,
  strategyWorkbenchApi,
  type StrategyDocument,
} from "../../../shared/api";
import type { SourceFormat } from "../../../shared/lib/yaml12";
import {
  currentSpec,
  type DocumentAction,
  type DocumentState,
} from "./document-state";

export type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; document: StrategyDocument }
  /** Someone else stored a newer revision than the draft base (HTTP 409). */
  | { kind: "conflict"; detail: string }
  /** The backend refused the text as a document (HTTP 422); the draft stays as typed. */
  | { kind: "invalid"; detail: string }
  | { kind: "failed"; detail: string };

type SaveSnapshot = {
  source: string;
  format: SourceFormat;
  strategyId: string | null;
  baseRevision: number | null;
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
    onMutate: () => setStatus({ kind: "saving" }),
    onSuccess: (document) => {
      queryClient.setQueryData(
        strategyDocumentQuery(document.strategy_id, document.revision).queryKey,
        document,
      );
      void queryClient.invalidateQueries({
        queryKey: strategyRevisionsQuery(document.strategy_id).queryKey,
      });
      dispatch({
        type: "saved",
        strategyId: document.strategy_id,
        revision: document.revision,
        specHash: document.spec_hash,
        source: document.source,
      });
      setStatus({ kind: "saved", document });
    },
    onError: (error) => {
      if (error instanceof ApiRequestError && error.status === 409) {
        setStatus({ kind: "conflict", detail: error.detail ?? "" });
      } else if (error instanceof ApiRequestError && error.status === 422) {
        setStatus({ kind: "invalid", detail: error.detail ?? "" });
      } else {
        setStatus({
          kind: "failed",
          detail: error instanceof Error ? error.message : String(error),
        });
      }
    },
  });

  const { mutate, isPending } = mutation;
  const save = useCallback(() => {
    if (!canSaveDocument(state) || isPending) return;
    mutate({
      source: state.source,
      format: state.format,
      strategyId: state.strategyId,
      baseRevision: state.baseRevision,
    });
  }, [isPending, mutate, state]);

  return { save, status, canSave: canSaveDocument(state) && !isPending };
};
