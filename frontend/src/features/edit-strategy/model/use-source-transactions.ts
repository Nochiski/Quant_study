import { useCallback, useMemo, useRef, useState } from "react";

import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import type { SnippetEditFailure } from "./canonical-snippets";
import {
  planSourceOperation,
  type PlanFailure,
  type PlannedEdit,
  type SourceOperation,
} from "./source-transactions";

export type TransactionFailure =
  PlanFailure | SnippetEditFailure | "composing" | "editor-unavailable";

export type TransactionFeedback =
  | { status: "idle" }
  | { status: "applied"; label: string }
  | { status: "error"; label: string; reason: TransactionFailure };

/** 편집기 현재 텍스트·선택으로 편집 하나를 계획하는 함수. 기본은 `planSourceOperation`이다. */
export type SourcePlanner = (editor: {
  text: string;
  selection: { from: number; to: number };
}) =>
  | { status: "ok"; edit: PlannedEdit }
  | { status: "error"; reason: TransactionFailure };

export type SourceTransactions = {
  /** 원시 연산 하나를 편집기 현재 텍스트에 계획·적용한다. 실패하면 텍스트는 그대로고 feedback만 바뀐다. */
  apply: (op: SourceOperation, label: string) => void;
  /** 커서 문맥이 필요한 편집(스니펫)처럼 연산 하나로 표현되지 않는 계획을 같은 경로로 적용한다. */
  run: (planner: SourcePlanner, label: string) => void;
  feedback: TransactionFeedback;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  /** yaml 문서이고, IME 조합 중이 아니며, 편집기가 붙어 있고, 현재 텍스트의 parse가 ok인 상태. */
  enabled: boolean;
};

type ScopedFeedback = { scope: unknown; value: TransactionFeedback };

const IDLE: TransactionFeedback = { status: "idle" };

/**
 * source 트랜잭션을 편집기 handle에 적용하는 훅(WORKFLOW P3-02, spec D5). page가 한 번 만들어
 * 스니펫·Form·Graph가 공유한다(P4-04). 적용은 `CodeEditorHandle.replaceRange` 한 번(history 격리)이고,
 * 편집기 change가 reducer `edit`로 흘러 parse·compile이 이어진다.
 *
 * 계획은 항상 **편집기의 현재 텍스트**(`getText`)로 세운다: reducer의 `state.source`는 debounce 전에
 * 잠시 뒤처질 수 있고, 트랜잭션은 사용자가 보는 텍스트에 적용되어야 한다. 그래서 `planSourceOperation`이
 * 문서를 두 번 parse한다(계획 + preflight). 연산 한 번에 붙는 비용이라 호출자는 keystroke마다가 아니라
 * 확정(blur·Enter·버튼)마다 호출한다.
 *
 * feedback은 `documentEpoch`(다른 문서를 열면 사라진다)와 호출자가 주는 `scope` 값(같은 문서 안에서
 * 기능이 꺼졌다 켜지는 전이)에 묶인다.
 *
 * `enabled`는 UI 비활성화용 요약값이다. `apply`/`run`은 그것으로 막지 않고 편집기의 live 텍스트를 다시
 * parse하므로, reducer의 parse가 stale이거나 실패했어도 편집기 텍스트가 유효하면 적용된다(fail-closed는
 * `planSourceOperation`의 preflight가 한다).
 */
export const useSourceTransactions = (
  state: DocumentState,
  editorActive = true,
  scope: unknown = null,
): SourceTransactions => {
  const editor = useRef<CodeEditorHandle | null>(null);
  const [editorReady, setEditorReady] = useState(false);
  const [scoped, setScoped] = useState<ScopedFeedback | null>(null);
  const feedbackScope = useMemo(
    () => ({ documentEpoch: state.documentEpoch, scope }),
    [scope, state.documentEpoch],
  );
  const onEditorReady = useCallback((next: CodeEditorHandle | null): void => {
    editor.current = next;
    setEditorReady(next !== null);
  }, []);
  const enabled =
    state.format === "yaml" &&
    editorActive &&
    !state.composing &&
    editorReady &&
    state.parse !== null &&
    state.parse.status === "ok" &&
    state.parsedVersion === state.sourceVersion;
  const setFeedback = useCallback(
    (value: TransactionFeedback): void =>
      setScoped({ scope: feedbackScope, value }),
    [feedbackScope],
  );

  const run = useCallback(
    (planner: SourcePlanner, label: string): void => {
      if (state.format !== "yaml" || !editorActive) {
        setFeedback({ status: "error", label, reason: "yaml-only" });
        return;
      }
      const current = editor.current;
      if (current === null) {
        setFeedback({ status: "error", label, reason: "editor-unavailable" });
        return;
      }
      if (state.composing) {
        setFeedback({ status: "error", label, reason: "composing" });
        return;
      }
      const result = planner({
        text: current.getText(),
        selection: current.getSelection(),
      });
      if (result.status === "error") {
        setFeedback({ status: "error", label, reason: result.reason });
        current.focus();
        return;
      }
      const { edit } = result;
      current.replaceRange(edit.from, edit.to, edit.insert, edit.selection);
      current.scrollTo(edit.selection.from);
      current.focus();
      setFeedback({ status: "applied", label });
    },
    [editorActive, setFeedback, state.composing, state.format],
  );

  const apply = useCallback(
    (op: SourceOperation, label: string): void =>
      run(({ text }) => planSourceOperation(text, "yaml", op), label),
    [run],
  );

  const feedback =
    scoped !== null && scoped.scope === feedbackScope ? scoped.value : IDLE;

  return { apply, run, feedback, onEditorReady, enabled };
};
