import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CodeEditorHandle,
  EditorHistoryDepth,
} from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";

export type DocumentHistory = {
  /** 지금 남은 되돌리기·다시 실행 단계 수. 버튼 비활성 표시에만 쓴다. */
  depth: EditorHistoryDepth;
  /** 편집 한 단계를 되돌린다. 되돌릴 것이 없으면 아무 일도 하지 않는다. */
  undo: () => void;
  /** 되돌린 편집 한 단계를 다시 적용한다. */
  redo: () => void;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
};

const EMPTY: EditorHistoryDepth = { undo: 0, redo: 0 };

const same = (a: EditorHistoryDepth, b: EditorHistoryDepth): boolean =>
  a.undo === b.undo && a.redo === b.redo;

/**
 * 되돌리기·다시 실행의 단일 경로(WORKFLOW P1-02, spec D9). 이력의 owner는 편집기(CodeMirror history)
 * 하나이고 이 훅은 그 명령을 탭 밖의 버튼·전역 단축키가 부를 수 있게 handle로 중계한다. 별도 undo 스택을
 * 만들지 않는다 — Form·Graph 트랜잭션은 `replaceRange` 한 번(history 격리)이라 편집기 이력에서 이미
 * 한 단계다.
 *
 * `depth`는 표시용이다. 편집 결과로 `sourceVersion`이 바뀔 때마다 다시 읽고(폴링 없음), 버튼이 직접 부른
 * 되돌리기·다시 실행 뒤에는 그 자리에서 갱신한다. 깊이가 낡아도 동작은 틀리지 않는다: 버튼은 `disabled`가
 * 아니라 `aria-disabled`라 클릭이 막히지 않고, 단축키는 깊이를 게이트로 쓰지 않으며, 할 일이 없으면
 * 편집기 명령이 스스로 아무 일도 하지 않는다(게이트 경합 없음 — Phase 5 backlog 20과 같은 이유).
 */
export const useDocumentHistory = (state: DocumentState): DocumentHistory => {
  const editor = useRef<CodeEditorHandle | null>(null);
  const [ready, setReady] = useState(false);
  const [depth, setDepth] = useState<EditorHistoryDepth>(EMPTY);

  const onEditorReady = useCallback((next: CodeEditorHandle | null): void => {
    editor.current = next;
    setReady(next !== null);
  }, []);

  const read = useCallback((): void => {
    const next = editor.current?.historyDepth() ?? EMPTY;
    setDepth((current) => (same(current, next) ? current : next));
  }, []);

  const undo = useCallback((): void => {
    editor.current?.undo();
    read();
  }, [read]);

  const redo = useCallback((): void => {
    editor.current?.redo();
    read();
  }, [read]);

  // 편집기가 붙는 순간과 문서가 바뀌는 순간(키 입력·Form·Graph 트랜잭션·업그레이드 적용 모두
  // `sourceVersion`을 올린다)마다 다시 읽는다. 다른 문서를 열면 `documentEpoch`가 바뀐다.
  useEffect(read, [read, ready, state.documentEpoch, state.sourceVersion]);

  return { depth, undo, redo, onEditorReady };
};
