import { useCallback, useMemo, useRef, useState } from "react";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";

export type EditorHandleOwner = {
  read: () => CodeEditorHandle | null;
  /** 붙음·분리를 React에 알리는 신호다. 명령은 read로 현재 핸들을 읽는다. */
  mounted: CodeEditorHandle | null;
  onEditorReady: (handle: CodeEditorHandle | null) => void;
};

/** 문서 화면마다 편집기 수명과 명령 핸들을 한 번만 소유한다. */
export const useEditorHandle = (): EditorHandleOwner => {
  const ref = useRef<CodeEditorHandle | null>(null);
  const [mounted, setMounted] = useState<CodeEditorHandle | null>(null);
  const onEditorReady = useCallback((handle: CodeEditorHandle | null) => {
    ref.current = handle;
    setMounted(handle);
  }, []);
  const read = useCallback(() => ref.current, []);
  return useMemo(() => ({ read, mounted, onEditorReady }), [read, mounted, onEditorReady]);
};
