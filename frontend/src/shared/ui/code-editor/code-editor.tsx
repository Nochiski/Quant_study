import { forwardRef, lazy, Suspense } from "react";

import { t } from "../../config";
import type { CodeEditorHandle, CodeEditorProps } from "./handle";
import "./code-editor.css";

/** The CodeMirror bundle is its own chunk (editor ADR D1: gzip budget 200 KB). */
const LazyCodeEditorView = lazy(() =>
  import("./code-editor-view").then((module) => ({
    default: module.CodeEditorView,
  })),
);

/**
 * Domain-neutral source editor. Shows a text fallback while the editor chunk loads and a hint
 * that `Tab` indents (focus is not trapped: `Escape` leaves the editor).
 */
export const CodeEditor = forwardRef<CodeEditorHandle, CodeEditorProps>(
  function CodeEditor(props, ref) {
    return (
      <div className="code-editor-shell">
        <Suspense
          fallback={
            <div className="code-editor-fallback" role="status">
              {t("editor.loading")}
            </div>
          }
        >
          <LazyCodeEditorView ref={ref} {...props} />
        </Suspense>
        <p className="code-editor-hint">{t("editor.keyboardHint")}</p>
      </div>
    );
  },
);
