import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import { resolveDiagnosticDestination } from "./diagnostic-navigation";
import {
  currentDiagnostics,
  type DocumentDiagnostic,
  type DocumentState,
} from "./document-state";
import type { FormProjection } from "./form-projection";
import { deduplicateDiagnostics } from "./problem-list";
import type { StrategyView } from "./strategy-views";

export type DiagnosticNavigationOptions = {
  state: DocumentState;
  /** 지금 선택된 탭. */
  view: StrategyView;
  /** 편집기가 사는 탭(저장된 문서 포맷). */
  sourceView: "yaml" | "json";
  /** Form 투영(runtime schema가 없으면 null). */
  form: FormProjection | null;
  /** Form·Graph가 함께 읽는 parse tree. */
  tree: unknown;
  /** runtime schema가 도착했는가(Form·Graph 편집 표면의 렌더 조건). */
  schemaLoaded: boolean;
  /** 지금 탭을 지킨 채 pointer를 선택한다. */
  onSelectPointer: (pointer: string) => void;
  /** 원문 탭으로 전환한다. pointer가 문서 전체면 undefined가 온다. */
  onOpenSource: (pointer: string | undefined) => void;
};

export type DiagnosticNavigation = {
  /** Problems panel에 넘길 진단. */
  diagnostics: readonly DocumentDiagnostic[];
  /** 진단이 현재 텍스트보다 오래되었는가(행이 흐려지고 선택되지 않는다). */
  stale: boolean;
  /**
   * 문제 행을 현재 탭에서 고를 때마다 올라가는 값. 같은 행을 다시 눌러도 pointer는 그대로라
   * URL이 안 바뀌므로, Form·Graph 패널의 `useRevealSelection`이 이 값을 같이 보고 다시 끌어온다
   * (2차 리뷰 R2-2).
   */
  revealSignal: number;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  selectDiagnostic: (diagnostic: DocumentDiagnostic) => void;
};

/**
 * 문제 행 클릭의 목적지(WORKFLOW P1-01). 문제 목록은 탭 밖에 있으므로 클릭은 세 갈래다.
 *
 * 1. 원문 탭(YAML·JSON)이면 그 자리에서 진단 범위를 선택하고 스크롤한다.
 * 2. Form·Graph가 그 pointer의 카드·노드를 그리면 탭을 지키고 선택만 옮긴다.
 * 3. 아니면 원문 탭으로 전환하고, 전환된 다음 진단 범위로 간다.
 *
 * 3번의 이동은 탭이 `hidden`을 벗은 commit 이후라야 편집기가 스크롤·focus를 받을 수 있어
 * `view`가 원문 탭이 되는 렌더의 effect에서 실행한다.
 */
export const useDiagnosticNavigation = ({
  state,
  view,
  sourceView,
  form,
  tree,
  schemaLoaded,
  onSelectPointer,
  onOpenSource,
}: DiagnosticNavigationOptions): DiagnosticNavigation => {
  const editor = useRef<CodeEditorHandle | null>(null);
  const [revealSignal, setRevealSignal] = useState(0);
  const onEditorReady = useCallback((next: CodeEditorHandle | null): void => {
    editor.current = next;
  }, []);
  // 탭 전환을 기다리는 진단 범위. 원문 탭이 보이는 첫 effect에서 소비한다.
  const pending = useRef<DocumentDiagnostic["range"]>(null);

  const documentDiagnostics = useMemo(
    () => deduplicateDiagnostics(currentDiagnostics(state)),
    [state],
  );
  const diagnostics =
    documentDiagnostics.length > 0 || state.compiled === null
      ? documentDiagnostics
      : state.compiled.diagnostics;
  const stale =
    documentDiagnostics.length === 0 &&
    state.compiled !== null &&
    state.compiledVersion !== state.sourceVersion;

  // 오래된 offset이 짧아진 현재 텍스트를 넘지 않도록 방어적으로 자른다.
  const reveal = useCallback((range: DocumentDiagnostic["range"]): void => {
    const handle = editor.current;
    if (handle === null || range === null) return;
    const length = handle.getText().length;
    const from = Math.min(range.start.offset, length);
    const to = Math.min(Math.max(range.end.offset, from), length);
    handle.setSelection(from, to);
    handle.scrollTo(from);
    handle.focus();
  }, []);

  const selectDiagnostic = useCallback(
    (diagnostic: DocumentDiagnostic): void => {
      if (diagnostic.range === null) return;
      const destination = resolveDiagnosticDestination({
        view,
        sourceView,
        pointer: diagnostic.pointer,
        form,
        tree,
        schemaLoaded,
      });
      if (destination === "current-view") {
        pending.current = null;
        setRevealSignal((previous) => previous + 1);
        onSelectPointer(diagnostic.pointer);
        return;
      }
      if (view === sourceView) {
        pending.current = null;
        reveal(diagnostic.range);
        return;
      }
      pending.current = diagnostic.range;
      onOpenSource(diagnostic.pointer === "" ? undefined : diagnostic.pointer);
    },
    [
      form,
      onOpenSource,
      onSelectPointer,
      reveal,
      schemaLoaded,
      sourceView,
      tree,
      view,
    ],
  );

  useEffect(() => {
    if (view !== sourceView) return;
    const range = pending.current;
    if (range === null) return;
    pending.current = null;
    reveal(range);
  }, [reveal, sourceView, view]);

  return { diagnostics, stale, revealSignal, onEditorReady, selectDiagnostic };
};
