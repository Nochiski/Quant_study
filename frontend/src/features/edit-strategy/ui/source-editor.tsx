import { useCallback, useEffect, useMemo, useRef } from "react";

import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import {
  CodeEditor,
  type CodeEditorHandle,
  type EditorCompletionSource,
  type EditorDiagnostic,
  type EditorHoverSource,
} from "../../../shared/ui/code-editor";
import {
  currentDiagnostics,
  isSpecStale,
  type DocumentAction,
  type DocumentDiagnostic,
  type DocumentState,
} from "../model/document-state";
import { DiagnosticsPanel } from "./diagnostics-panel";

type SourceEditorProps = {
  state: DocumentState;
  dispatch: (action: DocumentAction) => void;
  /** Schema-driven completion and hover (P3-03); absent in tests and before the schema loads. */
  assist?: {
    completionSource?: EditorCompletionSource;
    hoverSource?: EditorHoverSource;
  };
};

const PHASE_TONE = {
  editing: "neutral",
  parsing: "neutral",
  "syntax-invalid": "error",
  "structure-invalid": "error",
  "semantic-invalid": "error",
  "structurally-valid": "info",
  "semantically-valid": "ok",
  saved: "ok",
} as const;

/**
 * Binds the domain-neutral editor to the document state machine (P3-01 → P3-02): text edits and
 * IME composition flow into the reducer and current diagnostics flow back as markers. The editor
 * is never remounted on a format switch — the language is reconfigured in place — so the undo
 * history survives the switch (editor ADR D3).
 */
export const SourceEditor = ({
  state,
  dispatch,
  assist,
}: SourceEditorProps) => {
  const handle = useRef<CodeEditorHandle>(null);
  const documentDiagnostics = useMemo(() => currentDiagnostics(state), [state]);
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

  // Selecting a problem moves the editor to its range (WORKFLOW P3-04 acceptance). Offsets are
  // clamped to the current text: a range from an older text must never throw.
  const selectDiagnostic = useCallback((diagnostic: DocumentDiagnostic) => {
    const editor = handle.current;
    if (!editor || diagnostic.range === null) return;
    const length = editor.getText().length;
    const from = Math.min(diagnostic.range.start.offset, length);
    const to = Math.min(Math.max(diagnostic.range.end.offset, from), length);
    editor.setSelection(from, to);
    editor.scrollTo(from);
    editor.focus();
  }, []);

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
      <div className="source-editor__status">
        <Badge tone={PHASE_TONE[state.phase]}>
          {t(`document.phase.${state.phase}`)}
        </Badge>
        {isSpecStale(state) ? (
          <Badge tone="warn">{t("document.stale")}</Badge>
        ) : null}
        {state.composing ? (
          <Badge tone="neutral">{t("document.composing")}</Badge>
        ) : null}
      </div>
      <CodeEditor
        ref={handle}
        value={state.source}
        language={state.format}
        ariaLabel={t("ide.editor")}
        onChange={onChange}
        onComposingChange={onComposingChange}
        diagnostics={diagnostics}
        completionSource={assist?.completionSource}
        hoverSource={assist?.hoverSource}
      />
      <DiagnosticsPanel
        diagnostics={
          documentDiagnostics.length > 0 || state.compiled === null
            ? documentDiagnostics
            : state.compiled.diagnostics
        }
        // The last compile result belongs to another version of the text (whether or not it
        // produced a spec): its list is shown dimmed and cannot be navigated.
        stale={
          documentDiagnostics.length === 0 &&
          state.compiled !== null &&
          state.compiledVersion !== state.sourceVersion
        }
        onSelect={selectDiagnostic}
      />
    </div>
  );
};
