import { useCallback, useSyncExternalStore } from "react";
import type { EditorHistoryDepth } from "../../../shared/ui/code-editor";
import type { EditorHandleOwner } from "./use-editor-handle";

export type DocumentHistory = {
  depth: EditorHistoryDepth;
  undo: () => void;
  redo: () => void;
};

const EMPTY: EditorHistoryDepth = { undo: 0, redo: 0 };
const unsubscribeEmpty = () => {};

/** 편집기 이력을 구독한다. 문서 버전으로 깊이를 추정하거나 별도 undo 스택을 만들지 않는다. */
export const useDocumentHistory = (owner: EditorHandleOwner): DocumentHistory => {
  const readEditor = owner.read;
  const mounted = owner.mounted;
  const subscribe = useCallback((listener: () => void) =>
    mounted?.subscribeHistory(listener) ?? unsubscribeEmpty, [mounted]);
  const snapshot = useCallback(() => mounted?.historyDepth() ?? EMPTY, [mounted]);
  const depth = useSyncExternalStore(subscribe, snapshot, snapshot);
  const undo = useCallback(() => { readEditor()?.undo(); }, [readEditor]);
  const redo = useCallback(() => { readEditor()?.redo(); }, [readEditor]);
  return { depth, undo, redo };
};
