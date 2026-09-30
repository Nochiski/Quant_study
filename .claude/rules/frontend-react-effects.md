---
paths:
  - "frontend/src/**/*.ts"
  - "frontend/src/**/*.tsx"
---

# React 밖에서 읽는 상태는 commit과 같은 시점에 동기화한다

`window`/`document` 리스너, 라우터 blocker(`useBlocker`의 `shouldBlockFn`·`enableBeforeUnload`),
`beforeunload`, 편집기(CodeMirror) 콜백, 타이머처럼 **React 렌더 밖에서 불리는 콜백**이 화면 상태
(게이트·dirty 플래그·현재 문서)를 읽을 때 적용한다.

- 그 값은 `shared/lib/react`의 `useCommittedRef(value)`로 비춘 ref에서 읽거나, 리스너 자체를
  `useLayoutEffect`로 설치한다. `useEffect`로 ref를 갱신하거나 리스너를 교체하지 않는다 — passive
  effect는 commit과 별개의 스케줄러 작업이라 화면은 새 상태인데 콜백은 옛 값을 읽는 틈이 생긴다
  (Phase 5 backlog 20: 버튼이 켜진 직후의 단축키 소실, 21: 저장 안 된 편집을 경고 없이 버림).
- 렌더 중에 ref의 `.current`를 읽거나 쓰지 않는다(`react-hooks/refs`). 읽는 쪽은 언제나 콜백이다.
- 외부 등록(`history.block`, `addEventListener`)은 한 번만 하고 판정은 콜백 안에서 committed ref로
  한다. 등록 자체를 `disabled`·의존성 변경으로 껐다 켜면 그 전환도 passive effect 틈을 만든다.
- `useImperativeHandle`로 부모에게 핸들을 넘기는 컴포넌트는 핸들이 부리는 외부 객체(CodeMirror view 등)를
  그보다 먼저 layout effect에서 만든다. 핸들은 layout 단계에 붙으므로, passive effect에서 만든 객체는
  부모가 핸들을 받는 순간 아직 없고 그때 보낸 명령(선택·포커스)이 조용히 사라진다(#324,
  `code-editor.test.tsx`의 핸들 준비 테스트).
- 이 계열의 회귀 테스트는 act 밖의 갱신(promise)으로 상태를 바꾸고, `MutationObserver`가 DOM 변화를
  본 microtask에서 콜백을 불러 새 값을 읽는지 단언한다(`use-committed-ref.test.tsx`,
  `dirty-leave-guard.test.tsx`, `strategy-ide.shortcut-gate.test.tsx`).
