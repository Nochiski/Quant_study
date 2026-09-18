import { useCallback, useMemo, useState } from "react";

import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import {
  buildCanonicalSnippetCatalog,
  planSnippetEdit,
  type CanonicalSnippet,
  type SnippetCatalogSource,
  type SnippetEditFailure,
} from "./canonical-snippets";
import {
  useSourceTransactions,
  type SourceTransactions,
} from "./use-source-transactions";

export type SnippetFailure =
  SnippetEditFailure | "editor-unavailable" | "editor-inactive" | "composing";

const SNIPPET_OWNER = "snippet";
const IDLE: SnippetFeedback = { status: "idle" };

const SNIPPET_FAILURES: ReadonlySet<string> = new Set<SnippetFailure>([
  "yaml-only",
  "selection",
  "cursor-context",
  "duplicate",
  "parse",
  "editor-unavailable",
  "editor-inactive",
  "composing",
]);

/** `planSnippetEdit`가 PlanFailure를 스니펫 코드로 번역하므로 다른 코드는 오지 않지만, 좁히기는 가드로 한다. */
const isSnippetFailure = (reason: string): reason is SnippetFailure =>
  SNIPPET_FAILURES.has(reason);

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
  shared?: SourceTransactions,
): SnippetInsertion => {
  // page가 만든 인스턴스를 공유하면(P4-04) 그것을 쓰고, 아니면 자기 것을 만든다.
  const own = useSourceTransactions(state, editorActive);
  const transactions = shared ?? own;
  const snippets = useMemo(
    () => buildCanonicalSnippetCatalog(source),
    [source],
  );
  const { run } = transactions;
  // 카탈로그 상태가 ready → unavailable → ready로 바뀌면 이전 구간의 feedback을 되살리지 않는다:
  // 상태가 한 번이라도 바뀌면 렌더 중 파생 상태 조정으로 슬롯을 비운다.
  const [statusAtInsert, setStatusAtInsert] = useState<
    SnippetCatalogSource["status"] | null
  >(null);
  if (statusAtInsert !== null && statusAtInsert !== source.status)
    setStatusAtInsert(null);
  const insert = useCallback(
    (snippet: CanonicalSnippet): void => {
      setStatusAtInsert(source.status);
      // page 인스턴스를 공유해도 스니펫은 source view가 활성일 때만 커서에 넣는다(hidden 편집기의 커서는
      // 사용자가 보지 못한다). 공유 인스턴스의 editorActive는 "handle이 살아 있는가"라 따로 막는다.
      if (shared !== undefined && !editorActive) {
        run(
          () => ({ status: "error", reason: "editor-inactive" }),
          snippet.label,
          SNIPPET_OWNER,
        );
        return;
      }
      run(
        ({ text, selection }) =>
          planSnippetEdit(text, "yaml", selection, snippet),
        snippet.label,
        SNIPPET_OWNER,
      );
    },
    [editorActive, run, shared, source.status],
  );

  const feedback = useMemo((): SnippetFeedback => {
    const current = transactions.feedback;
    if (current.status === "idle" || current.owner !== SNIPPET_OWNER)
      return IDLE;
    if (statusAtInsert === null) return IDLE;
    if (current.status === "applied")
      return { status: "inserted", label: current.label };
    return {
      status: "error",
      label: current.label,
      reason: isSnippetFailure(current.reason)
        ? current.reason
        : "cursor-context",
    };
  }, [statusAtInsert, transactions.feedback]);

  return {
    snippets,
    sourceStatus: source.status,
    feedback,
    onEditorReady: transactions.onEditorReady,
    insert,
  };
};
