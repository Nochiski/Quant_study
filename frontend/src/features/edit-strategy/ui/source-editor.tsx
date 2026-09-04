import { useCallback, useEffect, useMemo, useRef } from "react";

import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import {
  CodeEditor,
  type CodeEditorHandle,
  type EditorDiagnostic,
} from "../../../shared/ui/code-editor";
import {
  currentDiagnostics,
  isSpecStale,
  type DocumentAction,
  type DocumentState,
} from "../model/document-state";

type SourceEditorProps = {
  state: DocumentState;
  dispatch: (action: DocumentAction) => void;
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
 * IME composition flow into the reducer, current diagnostics flow back as markers, and the undo
 * history is kept per format so switching YAML/JSON views does not lose it (editor ADR D3).
 */
export const SourceEditor = ({ state, dispatch }: SourceEditorProps) => {
  const handle = useRef<CodeEditorHandle>(null);
  const histories = useRef<Partial<Record<DocumentState["format"], unknown>>>(
    {},
  );

  const diagnostics = useMemo<EditorDiagnostic[]>(
    () =>
      currentDiagnostics(state)
        .filter((d) => d.range !== null)
        .map((d) => ({
          from: d.range!.start.offset,
          to: Math.max(d.range!.end.offset, d.range!.start.offset + 1),
          severity: d.severity,
          message: d.message,
          code: d.code,
        })),
    [state],
  );

  const onChange = useCallback(
    (text: string, composing: boolean) => {
      if (composing !== state.composing)
        dispatch({ type: "composing", composing });
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

  useEffect(() => {
    const format = state.format;
    return () => {
      histories.current[format] = handle.current?.getHistoryState();
    };
  }, [state.format]);

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
        key={state.format}
        ref={handle}
        value={state.source}
        language={state.format}
        ariaLabel={t("ide.editor")}
        onChange={onChange}
        onComposingChange={onComposingChange}
        diagnostics={diagnostics}
        initialHistoryState={histories.current[state.format]}
      />
    </div>
  );
};
