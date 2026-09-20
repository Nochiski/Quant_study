import { useCallback, useMemo } from "react";

import type { TurnContextPayload } from "../../../entities/assistant";
import { useCommittedRef } from "../../../shared/lib/react";
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
 * 한 프레임 동안에도 사용자가 보는 텍스트가 실려야 한다. 두 페이지가 같은 값을 만들어야 하므로 이
 * 조립을 문서 상태 owner가 소유한다.
 */
export const useAssistantTurnContext = (
  state: DocumentState,
  readSource: () => string | null,
  environment: Record<string, unknown> | null,
): (() => TurnContextPayload) => {
  const build = useCommittedRef(
    useMemo(
      () => (liveSource: string | null) =>
        assistantTurnContext(state, environment, liveSource),
      [environment, state],
    ),
  );
  return useCallback(() => build.current(readSource()), [build, readSource]);
};
