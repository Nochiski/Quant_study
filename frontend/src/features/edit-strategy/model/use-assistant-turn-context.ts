import { useMemo, useRef } from "react";

import type { TurnContextPayload } from "../../../entities/assistant";
import { useCommittedRef } from "../../../shared/lib/react";
import type { SourceFormat } from "../../../shared/lib/yaml12";
import { assistantTurnContext } from "./assistant-turn-context";
import type { DocumentState } from "./document-state";

/**
 * 사이드바에 넘길 `readContext` 손잡이를 만든다(WORKFLOW B-04).
 *
 * 턴은 사용자가 보낸 순간이 아니라 **세션 생성 왕복 뒤**에 시작될 수 있다(`use-assist-chat.ts`의
 * `beginTurn`). 그래서 이 함수는 호출 시점의 값을 읽어야 한다. 함수 정체성은 고정하고(사이드바가
 * 다시 그려지지 않는다) 읽는 값만 최신이 되도록 commit된 값을 비추는 ref를 쓴다
 * (`.claude/rules/frontend-react-effects.md`).
 *
 * 텍스트는 reducer가 아니라 **편집기**에서 읽는다(`readSource`): 편집기 change가 reducer에 닿기 전
 * 한 프레임 동안에도 사용자가 보는 텍스트가 실려야 한다. 포맷은 그 텍스트와 짝이어야 하므로, 편집기
 * 텍스트가 reducer와 같았던 마지막 순간의 포맷을 기억해 둔다 — 포맷 전환(`load`)은 reducer를 먼저
 * 갱신하고 편집기는 effect로 뒤따르므로, 그 틈에는 reducer의 새 포맷이 편집기 텍스트의 포맷이 아니다.
 *
 * 두 페이지가 같은 값을 만들어야 하므로 이 조립을 문서 상태 owner가 소유한다.
 */
export const useAssistantTurnContext = (
  state: DocumentState,
  readSource: () => string | null,
  environment: Record<string, unknown> | null,
): (() => TurnContextPayload) => {
  const committed = useCommittedRef(
    useMemo(
      () =>
        (live: { source: string; format: SourceFormat } | null) =>
          assistantTurnContext(state, environment, live),
      [environment, state],
    ),
  );
  const formats = useCommittedRef(
    useMemo(
      () => ({ source: state.source, format: state.format }),
      [state.format, state.source],
    ),
  );
  // 편집기 포맷의 기억. 콜백 안에서만 읽고 쓴다(렌더 중 ref 접근 금지).
  const editorFormat = useRef<{ source: string; format: SourceFormat } | null>(
    null,
  );
  // `useCallback`으로 감싸지 않는다: 이 함수는 ref 기억을 읽고 쓰므로 컴파일러가 수동 메모를 보존하지
  // 못한다. 사이드바는 이 값을 effect 의존성으로 쓰지 않고 전송 시점에 그대로 부른다.
  return (): TurnContextPayload => {
    const source = readSource();
    const reducer = formats.current;
    if (source === null || source === reducer.source) {
      // 편집기와 reducer가 같은 텍스트다 — 그 포맷이 곧 편집기의 포맷이다.
      editorFormat.current = reducer;
      return committed.current(null);
    }
    return committed.current({
      source,
      // 기억이 없으면(첫 호출이 이미 어긋난 상태) reducer 포맷으로 떨어진다.
      format: editorFormat.current?.format ?? reducer.format,
    });
  };
};
