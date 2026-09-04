import { useCallback, useMemo, useRef, useState } from "react";

import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import {
  buildCanonicalSnippetCatalog,
  planSnippetEdit,
  type CanonicalSnippet,
  type SnippetCatalogSource,
  type SnippetEditFailure,
} from "./canonical-snippets";

export type SnippetFeedback =
  | { status: "idle" }
  | { status: "inserted"; label: string }
  | {
      status: "error";
      label: string;
      reason: SnippetEditFailure | "editor-unavailable" | "composing";
    };

export type SnippetInsertion = {
  snippets: readonly CanonicalSnippet[];
  sourceStatus: SnippetCatalogSource["status"];
  feedback: SnippetFeedback;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  insert: (snippet: CanonicalSnippet) => void;
};

/** Coordinates the editor command handle with pure schema projection and insertion planning. */
export const useSnippetInsertion = (
  state: DocumentState,
  source: SnippetCatalogSource,
): SnippetInsertion => {
  const editor = useRef<CodeEditorHandle | null>(null);
  const [feedback, setFeedback] = useState<SnippetFeedback>({ status: "idle" });
  const snippets = useMemo(
    () => buildCanonicalSnippetCatalog(source),
    [source],
  );
  const onEditorReady = useCallback((next: CodeEditorHandle | null): void => {
    editor.current = next;
  }, []);
  const insert = useCallback(
    (snippet: CanonicalSnippet): void => {
      const current = editor.current;
      if (current === null) {
        setFeedback({
          status: "error",
          label: snippet.label,
          reason: "editor-unavailable",
        });
        return;
      }
      if (state.composing) {
        setFeedback({
          status: "error",
          label: snippet.label,
          reason: "composing",
        });
        return;
      }
      const result = planSnippetEdit(
        current.getText(),
        state.format,
        current.getSelection(),
        snippet,
      );
      if (result.status === "error") {
        setFeedback({
          status: "error",
          label: snippet.label,
          reason: result.reason,
        });
        current.focus();
        return;
      }
      const { edit } = result;
      current.replaceRange(edit.from, edit.to, edit.insert, edit.selection);
      current.scrollTo(edit.selection.from);
      current.focus();
      setFeedback({ status: "inserted", label: snippet.label });
    },
    [state.composing, state.format],
  );

  return {
    snippets,
    sourceStatus: source.status,
    feedback,
    onEditorReady,
    insert,
  };
};
