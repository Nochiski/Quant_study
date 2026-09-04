/**
 * Editor-agnostic contract of the source editor (editor ADR P0-02, D3). Features hold this
 * handle only; CodeMirror types never leave `code-editor-view.tsx`.
 */
export type EditorLanguage = "yaml" | "json";

export type EditorSeverity = "error" | "warning" | "info";

/** A marker in UTF-16 offsets of the editor text. Advisory or backend-derived alike. */
export type EditorDiagnostic = {
  from: number;
  to: number;
  severity: EditorSeverity;
  message: string;
  code?: string;
};

export type EditorPosition = { line: number; column: number };

export type EditorSelection = {
  from: number;
  to: number;
  /** True when the selection belongs to the same transaction as a document edit. */
  documentChanged: boolean;
};

export type EditorCompletionOption = {
  label: string;
  detail?: string;
  info?: string;
  type?: "property" | "enum" | "keyword" | "value";
  apply?: string;
};

export type EditorCompletionResult = {
  from: number;
  options: EditorCompletionOption[];
};

export type EditorCompletionContext = {
  text: string;
  offset: number;
  explicit: boolean;
};

export type EditorCompletionSource = (
  context: EditorCompletionContext,
) => EditorCompletionResult | null | Promise<EditorCompletionResult | null>;

/** Plain-text hover card for the token spanning `from`..`to`; one line per entry. */
export type EditorHover = { from: number; to: number; lines: string[] };

export type EditorHoverSource = (offset: number) => EditorHover | null;

export type CodeEditorHandle = {
  getText(): string;
  /** Replaces the whole document; history records it as one change. */
  setText(text: string): void;
  /** Applies one range replacement and optional selection as a single undoable transaction. */
  replaceRange(
    from: number,
    to: number,
    text: string,
    selection?: { from: number; to?: number },
  ): void;
  getSelection(): { from: number; to: number };
  setSelection(from: number, to?: number): void;
  offsetToPosition(offset: number): EditorPosition;
  positionToOffset(position: EditorPosition): number;
  scrollTo(offset: number): void;
  focus(): void;
  /** Opaque undo history (plus document) for view switching; never inspected by callers. */
  getHistoryState(): unknown;
  restoreHistoryState(state: unknown): void;
};

export type CodeEditorProps = {
  /** Initial text; later changes flow through `onChange` and `setText`. */
  value: string;
  language: EditorLanguage;
  ariaLabel: string;
  onChange: (text: string, composing: boolean) => void;
  /** Fires for selection transactions in UTF-16 offsets; consumers gate edits on a fresh map. */
  onSelectionChange?: (selection: EditorSelection) => void;
  /** Mirrors the editor's IME composition state (`view.composing`). */
  onComposingChange?: (composing: boolean) => void;
  onEscape?: () => void;
  diagnostics?: EditorDiagnostic[];
  completionSource?: EditorCompletionSource;
  hoverSource?: EditorHoverSource;
  readOnly?: boolean;
  /** Restores a previously captured opaque history state on mount. */
  initialHistoryState?: unknown;
};
