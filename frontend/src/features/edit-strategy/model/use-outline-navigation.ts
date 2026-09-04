import { useCallback, useEffect, useRef } from "react";

import { locatePointer, locateRange } from "../../../shared/lib/yaml12";
import type {
  CodeEditorHandle,
  EditorSelection,
} from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import type { JsonSchema } from "./schema-navigator";
import { findOutlineNode, type StrategyOutlineNode } from "./strategy-outline";
import {
  useStrategyOutline,
  type StrategyOutlineSnapshot,
} from "./use-strategy-outline";

type OutlineNavigationOptions = {
  state: DocumentState;
  schema: JsonSchema | null;
  /** False while a read-only projection is visible; hidden editors must never steal focus. */
  revealSelectedPointer?: boolean;
  /** URL-owned JSON Pointer. Undefined is the root/default and must not be written to the URL. */
  selectedPointer: string | undefined;
  onSelectedPointer: (
    pointer: string | undefined,
    origin: "cursor" | "outline" | "outline-collapse",
  ) => void;
};

export type StrategyOutlineNavigation = {
  snapshot: StrategyOutlineSnapshot | null;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  onEditorSelectionChange: (selection: EditorSelection) => void;
  onSelectOutlineNode: (node: StrategyOutlineNode) => void;
  onCollapseOutlineNode: (node: StrategyOutlineNode) => void;
  requestSourceReveal: (pointer: string) => void;
};

const normalizedPointer = (pointer: string | undefined): string =>
  pointer ?? "";

/** Owns the bidirectional source ↔ outline interaction; the page only persists path in the URL. */
export const useOutlineNavigation = ({
  state,
  schema,
  revealSelectedPointer = true,
  selectedPointer,
  onSelectedPointer,
}: OutlineNavigationOptions): StrategyOutlineNavigation => {
  const snapshot = useStrategyOutline(state, schema);
  const editor = useRef<CodeEditorHandle | null>(null);
  const latestSnapshot = useRef(snapshot);
  const latestSelected = useRef(selectedPointer);
  const pendingReveal = useRef<string | null>(null);
  const cursorPublished = useRef<string | null>(null);
  const collapsePublished = useRef<string | null>(null);
  const programmaticSelection = useRef(false);
  const pendingCursor = useRef<{
    documentEpoch: number;
    targetSourceVersion: number;
    routePointer: string;
    selection: EditorSelection;
  } | null>(null);
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
      node?.range ??
      locateRange(currentSnapshot.parsed, node?.pointer ?? pointer);
    if (range === null) return false;
    const length = currentEditor.getText().length;
    const from = Math.min(range.start.offset, length);
    const to = Math.min(Math.max(range.end.offset, from), length);
    programmaticSelection.current = true;
    currentEditor.setSelection(from, to);
    // CodeMirror dispatch is synchronous. Clear the guard here as well in case setting an
    // already-equal range produces no selection transaction; the next real cursor move must
    // never be swallowed.
    programmaticSelection.current = false;
    currentEditor.scrollTo(from);
    currentEditor.focus();
    return true;
  }, []);

  const onEditorReady = useCallback(
    (next: CodeEditorHandle | null): void => {
      editor.current = next;
      if (next === null || !revealSelectedPointer) return;
      const pointer = pendingReveal.current;
      if (pointer !== null && reveal(pointer)) pendingReveal.current = null;
    },
    [reveal, revealSelectedPointer],
  );

  const onEditorSelectionChange = useCallback(
    (selection: EditorSelection): void => {
      const current = latestSnapshot.current;
      if (programmaticSelection.current) {
        programmaticSelection.current = false;
        return;
      }
      if (selection.documentChanged || current === null || current.stale) {
        pendingCursor.current = {
          documentEpoch: state.documentEpoch,
          targetSourceVersion:
            state.sourceVersion + (selection.documentChanged ? 1 : 0),
          routePointer: normalizedPointer(selectedPointer),
          selection,
        };
        return;
      }
      pendingCursor.current = null;
      const pointer = locatePointer(current.parsed, selection.from);
      if (pointer === null) return;
      if (pointer === normalizedPointer(latestSelected.current)) return;
      cursorPublished.current = pointer;
      onSelectedPointer(pointer === "" ? undefined : pointer, "cursor");
    },
    [
      onSelectedPointer,
      selectedPointer,
      state.documentEpoch,
      state.sourceVersion,
    ],
  );

  const onSelectOutlineNode = useCallback(
    (node: StrategyOutlineNode): void => {
      const pointer = node.pointer;
      pendingCursor.current = null;
      pendingReveal.current = pointer;
      onSelectedPointer(pointer === "" ? undefined : pointer, "outline");
      if (revealSelectedPointer && reveal(pointer)) {
        pendingReveal.current = null;
      }
    },
    [onSelectedPointer, reveal, revealSelectedPointer],
  );

  const onCollapseOutlineNode = useCallback(
    (node: StrategyOutlineNode): void => {
      const pointer = node.pointer;
      pendingCursor.current = null;
      pendingReveal.current = null;
      if (pointer === normalizedPointer(latestSelected.current)) return;
      collapsePublished.current = pointer;
      onSelectedPointer(
        pointer === "" ? undefined : pointer,
        "outline-collapse",
      );
    },
    [onSelectedPointer],
  );

  const requestSourceReveal = useCallback((pointer: string): void => {
    pendingCursor.current = null;
    pendingReveal.current = pointer;
  }, []);

  // Text edits move the cursor before their parser-owned source map exists. Publish only after
  // the matching document has a current successful parse; the newest pending cursor wins.
  useEffect(() => {
    const pending = pendingCursor.current;
    if (pending === null || snapshot === null || snapshot.stale) return;
    if (pending.documentEpoch !== snapshot.documentEpoch) {
      pendingCursor.current = null;
      return;
    }
    if (pending.routePointer !== normalizedPointer(selectedPointer)) {
      pendingCursor.current = null;
      return;
    }
    if (snapshot.sourceVersion < pending.targetSourceVersion) return;
    if (snapshot.sourceVersion !== pending.targetSourceVersion) {
      pendingCursor.current = null;
      return;
    }
    pendingCursor.current = null;
    const pointer = locatePointer(snapshot.parsed, pending.selection.from);
    if (
      pointer === null ||
      pointer === normalizedPointer(latestSelected.current)
    )
      return;
    cursorPublished.current = pointer;
    onSelectedPointer(pointer === "" ? undefined : pointer, "cursor");
  }, [onSelectedPointer, selectedPointer, snapshot]);

  // Direct links and browser back/forward also reveal their URL path. A path just published by
  // the cursor is already at the right place and must not expand its whole source range.
  useEffect(() => {
    if (!revealSelectedPointer) {
      pendingReveal.current = null;
      return;
    }
    const pointer = normalizedPointer(selectedPointer);
    const key = `${state.documentEpoch}:${pointer}`;
    if (routeSelectionKey.current !== key) {
      routeSelectionKey.current = key;
      // An absent path is the URL default, not an instruction to select the entire document.
      // Outline clicks on the virtual root reveal it directly in onSelectOutlineNode.
      if (selectedPointer === undefined) {
        pendingReveal.current = null;
        cursorPublished.current = null;
        collapsePublished.current = null;
        return;
      }
      if (cursorPublished.current === pointer) {
        cursorPublished.current = null;
        pendingReveal.current = null;
        return;
      }
      if (collapsePublished.current === pointer) {
        collapsePublished.current = null;
        pendingReveal.current = null;
        return;
      }
      pendingCursor.current = null;
      pendingReveal.current = pointer;
    }
    const pending = pendingReveal.current;
    if (pending !== null && reveal(pending)) pendingReveal.current = null;
  }, [
    revealSelectedPointer,
    selectedPointer,
    state.documentEpoch,
    snapshot,
    reveal,
  ]);

  return {
    snapshot,
    onEditorReady,
    onEditorSelectionChange,
    onSelectOutlineNode,
    onCollapseOutlineNode,
    requestSourceReveal,
  };
};
