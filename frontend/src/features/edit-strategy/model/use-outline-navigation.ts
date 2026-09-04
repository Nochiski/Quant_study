import { useCallback, useEffect, useRef } from "react";

import { locatePointer, locateRange } from "../../../shared/lib/yaml12";
import type {
  CodeEditorHandle,
  EditorSelection,
} from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import type { JsonSchema } from "./schema-navigator";
import {
  findOutlineNode,
  type StrategyOutlineNode,
} from "./strategy-outline";
import {
  useStrategyOutline,
  type StrategyOutlineSnapshot,
} from "./use-strategy-outline";

type OutlineNavigationOptions = {
  state: DocumentState;
  schema: JsonSchema | null;
  /** URL-owned JSON Pointer. Undefined is the root/default and must not be written to the URL. */
  selectedPointer: string | undefined;
  onSelectedPointer: (
    pointer: string | undefined,
    origin: "cursor" | "outline",
  ) => void;
};

export type StrategyOutlineNavigation = {
  snapshot: StrategyOutlineSnapshot | null;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  onEditorSelectionChange: (selection: EditorSelection) => void;
  onSelectOutlineNode: (node: StrategyOutlineNode) => void;
};

const normalizedPointer = (pointer: string | undefined): string => pointer ?? "";

/** Owns the bidirectional source ↔ outline interaction; the page only persists path in the URL. */
export const useOutlineNavigation = ({
  state,
  schema,
  selectedPointer,
  onSelectedPointer,
}: OutlineNavigationOptions): StrategyOutlineNavigation => {
  const snapshot = useStrategyOutline(state, schema);
  const editor = useRef<CodeEditorHandle | null>(null);
  const latestSnapshot = useRef(snapshot);
  const latestSelected = useRef(selectedPointer);
  const pendingReveal = useRef<string | null>(null);
  const cursorPublished = useRef<string | null>(null);
  const programmaticSelection = useRef<string | null>(null);
  const routeSelectionKey = useRef<string | null>(null);
  useEffect(() => {
    latestSnapshot.current = snapshot;
    latestSelected.current = selectedPointer;
  }, [selectedPointer, snapshot]);

  const reveal = useCallback((pointer: string): boolean => {
    const currentEditor = editor.current;
    const currentSnapshot = latestSnapshot.current;
    if (!currentEditor || !currentSnapshot) return false;
    const node = findOutlineNode(currentSnapshot.nodes, pointer);
    const range =
      node?.range ?? locateRange(currentSnapshot.parsed, node?.pointer ?? pointer);
    if (range === null) return false;
    const length = currentEditor.getText().length;
    const from = Math.min(range.start.offset, length);
    const to = Math.min(Math.max(range.end.offset, from), length);
    programmaticSelection.current = pointer;
    currentEditor.setSelection(from, to);
    currentEditor.scrollTo(from);
    currentEditor.focus();
    return true;
  }, []);

  const onEditorReady = useCallback(
    (next: CodeEditorHandle | null): void => {
      editor.current = next;
      if (next === null) return;
      const pointer = pendingReveal.current;
      if (pointer !== null && reveal(pointer)) pendingReveal.current = null;
    },
    [reveal],
  );

  const onEditorSelectionChange = useCallback(
    (selection: EditorSelection): void => {
      const current = latestSnapshot.current;
      if (current === null || current.stale) return;
      const pointer = locatePointer(current.parsed, selection.from);
      if (pointer === null) return;
      if (programmaticSelection.current !== null) {
        const expected = programmaticSelection.current;
        programmaticSelection.current = null;
        if (expected === pointer) return;
      }
      if (pointer === normalizedPointer(latestSelected.current)) return;
      cursorPublished.current = pointer;
      onSelectedPointer(pointer === "" ? undefined : pointer, "cursor");
    },
    [onSelectedPointer],
  );

  const onSelectOutlineNode = useCallback(
    (node: StrategyOutlineNode): void => {
      const pointer = node.pointer;
      pendingReveal.current = pointer;
      onSelectedPointer(pointer === "" ? undefined : pointer, "outline");
      if (reveal(pointer)) pendingReveal.current = null;
    },
    [onSelectedPointer, reveal],
  );

  // Direct links and browser back/forward also reveal their URL path. A path just published by
  // the cursor is already at the right place and must not expand its whole source range.
  useEffect(() => {
    const pointer = normalizedPointer(selectedPointer);
    const key = `${state.documentEpoch}:${pointer}`;
    if (routeSelectionKey.current !== key) {
      routeSelectionKey.current = key;
      if (cursorPublished.current === pointer) {
        cursorPublished.current = null;
        pendingReveal.current = null;
        return;
      }
      pendingReveal.current = pointer;
    }
    const pending = pendingReveal.current;
    if (pending !== null && reveal(pending)) pendingReveal.current = null;
  }, [selectedPointer, state.documentEpoch, snapshot, reveal]);

  return {
    snapshot,
    onEditorReady,
    onEditorSelectionChange,
    onSelectOutlineNode,
  };
};
