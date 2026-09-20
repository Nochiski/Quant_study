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

/**
 * 되돌리기·다시 실행으로 소비할 수 있는 편집 단계 수(P1-02). 0이면 그 방향으로 할 일이 없다.
 * `replaceRange` 한 번(Form·Graph 트랜잭션 하나)은 언제나 한 단계다.
 */
export type EditorHistoryDepth = { undo: number; redo: number };

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
  /** 편집 한 단계를 되돌린다. 되돌릴 것이 없으면 아무 일도 없이 false. */
  undo(): boolean;
  /** 되돌린 편집 한 단계를 다시 적용한다. 다시 실행할 것이 없으면 false. */
  redo(): boolean;
  /**
   * 지금 남은 되돌리기·다시 실행 깊이. 편집기가 포커스를 갖지 않아도 읽을 수 있어, 편집기가 hidden인
   * 탭에서도 툴바 버튼이 비활성 여부를 판정한다(spec D9).
   */
  historyDepth(): EditorHistoryDepth;
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
