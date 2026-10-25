import { useCommittedRef } from "../../../shared/lib/react";
import type { DocumentState } from "./document-state";
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

export type ReplacementDocument = Pick<DocumentState, "documentEpoch" | "savedVersion" | "strategyId" | "baseRevision" | "baseSpecHash" | "format">;

/** 같은 원문이어도 저장·기준 revision·형식이 바뀌면 외부 응답의 적용 권한은 끝난다. */
export const sameReplacementDocument = (requested: ReplacementDocument, current: ReplacementDocument): boolean =>
  requested.documentEpoch === current.documentEpoch &&
  requested.savedVersion === current.savedVersion &&
  requested.strategyId === current.strategyId &&
  requested.baseRevision === current.baseRevision &&
  requested.baseSpecHash === current.baseSpecHash &&
  requested.format === current.format;

/** 표시 상태와 별개로, 실제 편집 명령 직전에 commit된 문서 신원과 live 원문을 확인한다. */
export const useGuardedDocumentReplacement = (owner: EditorHandleOwner, state: DocumentState) => {
  const latest = useCommittedRef(state);
  const read = owner.read;
  // 함수 정체성은 React Compiler 메모이제이션이 맡는다(수동 useCallback은 ref 읽기로 최적화를 막는다).
  return (requested: DocumentState, editor: CodeEditorHandle, expectedSource: string, source: string): boolean => {
    if (!sameReplacementDocument(requested, latest.current) || latest.current.composing ||
        read() !== editor || editor.getText() !== expectedSource) return false;
    replaceWholeDocument(editor, source);
    return true;
  };
};
