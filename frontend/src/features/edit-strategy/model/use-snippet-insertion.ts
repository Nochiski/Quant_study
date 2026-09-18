import { useCallback, useMemo } from "react";

import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import {
  buildCanonicalSnippetCatalog,
  planSnippetEdit,
  type CanonicalSnippet,
  type SnippetCatalogSource,
  type SnippetEditFailure,
} from "./canonical-snippets";
import { useSourceTransactions } from "./use-source-transactions";

export type SnippetFailure =
  SnippetEditFailure | "editor-unavailable" | "composing";

export type SnippetFeedback =
  | { status: "idle" }
  | { status: "inserted"; label: string }
  | { status: "error"; label: string; reason: SnippetFailure };

export type SnippetInsertion = {
  snippets: readonly CanonicalSnippet[];
  sourceStatus: SnippetCatalogSource["status"];
  feedback: SnippetFeedback;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  insert: (snippet: CanonicalSnippet) => void;
};

/**
 * 스니펫 카탈로그(순수 schema projection)와 source 트랜잭션 훅을 잇는다. 삽입은 `planSnippetEdit`가
 * 커서 문맥을 `insert-key`/`insert-item` 연산으로 번역한 계획을 `useSourceTransactions.run`으로
 * 적용한다(P3-02). 카탈로그 상태가 ready → unavailable → ready로 바뀌면 이전 구간의 feedback이
 * 되살아나지 않도록 상태를 scope에 넣는다.
 */
export const useSnippetInsertion = (
  state: DocumentState,
  source: SnippetCatalogSource,
  editorActive = true,
): SnippetInsertion => {
  const transactions = useSourceTransactions(
    state,
    editorActive,
    source.status,
  );
  const snippets = useMemo(
    () => buildCanonicalSnippetCatalog(source),
    [source],
  );
  const { run } = transactions;
  const insert = useCallback(
    (snippet: CanonicalSnippet): void =>
      run(
        ({ text, selection }) =>
          planSnippetEdit(text, "yaml", selection, snippet),
        snippet.label,
      ),
    [run],
  );

  const feedback = useMemo((): SnippetFeedback => {
    const current = transactions.feedback;
    if (current.status === "applied")
      return { status: "inserted", label: current.label };
    if (current.status === "error") {
      // planSnippetEdit는 PlanFailure를 스니펫 실패 코드로 이미 번역했으므로 남는 코드는 이 집합뿐이다.
      return {
        status: "error",
        label: current.label,
        reason: current.reason as SnippetFailure,
      };
    }
    return current;
  }, [transactions.feedback]);

  return {
    snippets,
    sourceStatus: source.status,
    feedback,
    onEditorReady: transactions.onEditorReady,
    insert,
  };
};
