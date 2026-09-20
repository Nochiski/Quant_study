import { useCallback, useEffect, useMemo, useRef } from "react";

import { t } from "../../../shared/config";
import {
  CodeEditor,
  type CodeEditorHandle,
  type EditorCompletionSource,
  type EditorDiagnostic,
  type EditorHoverSource,
  type EditorSelection,
} from "../../../shared/ui/code-editor";
import {
  currentDiagnostics,
  type DocumentAction,
  type DocumentState,
} from "../model/document-state";
import { deduplicateDiagnostics } from "../model/problem-list";
import "./source-editor.css";

type SourceEditorProps = {
  state: DocumentState;
  dispatch: (action: DocumentAction) => void;
  /** Schema-driven completion and hover (P3-03); absent in tests and before the schema loads. */
  assist?: {
    completionSource?: EditorCompletionSource;
    hoverSource?: EditorHoverSource;
  };
  /** Exposes only the editor-agnostic command handle for cross-panel navigation. */
  onEditorReady?: (editor: CodeEditorHandle | null) => void;
  /** Reports cursor/selection-only movement for JSON Pointer projection. */
  onSelectionChange?: (selection: EditorSelection) => void;
};

/**
 * Binds the domain-neutral editor to the document state machine (P3-01 → P3-02): text edits and
 * IME composition flow into the reducer and current diagnostics flow back as markers. The editor
 * is never remounted on a format switch — the language is reconfigured in place — so the undo
 * history survives the switch (editor ADR D3).
 *
 * 문서 상태 배지와 문제 목록은 이 컴포넌트 밖에 산다(`DocumentStatus`·`DiagnosticsPanel`) — 탭 패널 안에 있으면
 * Graph·Form 탭에서 보이지 않기 때문이다(WORKFLOW P1-01).
 */
export const SourceEditor = ({
  state,
  dispatch,
  assist,
  onEditorReady,
  onSelectionChange,
}: SourceEditorProps) => {
  const handle = useRef<CodeEditorHandle>(null);
  const bindHandle = useCallback(
    (editor: CodeEditorHandle | null) => {
      handle.current = editor;
      onEditorReady?.(editor);
    },
    [onEditorReady],
  );

  const documentDiagnostics = useMemo(
    () => deduplicateDiagnostics(currentDiagnostics(state)),
    [state],
  );
  const diagnostics = useMemo<EditorDiagnostic[]>(
    () =>
      documentDiagnostics
        .filter((d) => d.range !== null)
        .map((d) => ({
          from: d.range!.start.offset,
          to: Math.max(d.range!.end.offset, d.range!.start.offset + 1),
          severity: d.severity,
          message: d.message,
          code: d.code,
        })),
    [documentDiagnostics],
  );

  // The editor can only raise the composition flag from a change (`view.composing`); the DOM
  // `compositionend` event is what lowers it, so a change delivered while an IME session is open
  // never re-enables parsing early.
  const onChange = useCallback(
    (text: string, composing: boolean) => {
      if (composing && !state.composing)
        dispatch({ type: "composing", composing: true });
      dispatch({ type: "edit", source: text });
    },
    [dispatch, state.composing],
  );
  const onComposingChange = useCallback(
    (composing: boolean) => dispatch({ type: "composing", composing }),
    [dispatch],
  );

  // External source replacement (load / format switch) is pushed into the editor.
  useEffect(() => {
    handle.current?.setText(state.source);
  }, [state.source]);

  return (
    <div className="source-editor">
      <CodeEditor
        ref={bindHandle}
        value={state.source}
        language={state.format}
        ariaLabel={t("ide.editor")}
        onChange={onChange}
        onSelectionChange={onSelectionChange}
        onComposingChange={onComposingChange}
        diagnostics={diagnostics}
        completionSource={assist?.completionSource}
        hoverSource={assist?.hoverSource}
      />
    </div>
  );
};
