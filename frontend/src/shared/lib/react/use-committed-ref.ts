import { useLayoutEffect, useRef, type RefObject } from "react";

/**
 * commit된 값을 그대로 비추는 ref. React 밖에서 불리는 콜백 — `window`/`document` 리스너, 라우터 blocker,
 * `beforeunload`, 편집기(CodeMirror) 콜백, 타이머 — 가 "지금 화면에 보이는 상태"를 읽을 때 쓴다.
 *
 * `useEffect`로 ref를 갱신하면 commit(화면 갱신)과 ref 갱신 사이에 틈이 생긴다: passive effect는 commit과
 * 별개의 스케줄러 작업이라, 그 사이에 온 키 입력·이동·타이머는 옛 값을 읽고 조용히 잘못 판정한다(Phase 5
 * backlog 20: 단축키 소실, 21: 저장 안 된 편집을 경고 없이 버림). layout effect는 commit 안에서 동기로 돌므로
 * DOM이 새 상태를 보이는 어떤 시점에도 ref는 같은 상태다.
 *
 * 렌더 중에 `.current`를 읽지 않는다(`react-hooks/refs`). 읽는 쪽은 언제나 이벤트·콜백이다.
 */
export const useCommittedRef = <T>(value: T): RefObject<T> => {
  const ref = useRef<T>(value);
  useLayoutEffect(() => {
    ref.current = value;
  }, [value]);
  return ref;
};
