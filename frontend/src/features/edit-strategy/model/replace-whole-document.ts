import type { EditorHandleOwner } from "./use-editor-handle";
import { useCallback } from "react";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";

/** 같은 문서의 외부 교체는 모두 편집기 change → reducer 경로와 격리된 undo 한 단계를 쓴다. */
export const replaceWholeDocument = (
  editor: CodeEditorHandle,
  source: string,
): void => {
  const before = editor.getText();
  if (before !== source) editor.replaceRange(0, before.length, source);
};

/** 복구 명령이 준비된 편집기만 수정한다. 준비 전에는 복구 레코드를 소비하지 않는다. */
export const useWholeDocumentReplacement = (owner: EditorHandleOwner) => {
  const readEditor = owner.read;
  const replace = useCallback((source: string): boolean => {
    const handle = readEditor();
    if (handle === null) return false;
    replaceWholeDocument(handle, source);
    return true;
  }, [readEditor]);
  return { replace };
};
