import { useMemo } from "react";

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
 * 한 프레임 동안에도 사용자가 보는 텍스트가 실려야 한다. 그 텍스트와 짝이 되는 포맷은 reducer의
 * `format`이다 — 포맷은 문서 적재(`load`)로만 바뀌고 적재는 텍스트도 함께 갈아 끼우므로, 한 문서를
 * 편집하는 동안 편집기 텍스트의 포맷은 언제나 reducer의 포맷이다.
 *
 * 남는 한계: 적재 직후 편집기가 아직 앞 문서를 들고 있는 틈(commit과 passive effect 사이)에 턴이
 * 시작되면 앞 문서 텍스트에 새 포맷이 붙는다. 사용자가 보내는 순간에는 그 틈에 들어갈 수 없지만,
 * 세션 생성 왕복 뒤의 `beginTurn`은 네트워크 콜백에서 돌아 passive effect가 그 전에 비워진다는 보장이
 * 없다. 도달하려면 세션 생성이 도는 동안 문서 적재가 겹쳐야 해서 실해는 없다고 보고, 기억을 두는
 * 상태 기계 대신 이 한계를 적어 둔다(3차 리뷰 P3-1, 4차 리뷰 주석 정정).
 *
 * 두 페이지가 같은 값을 만들어야 하므로 이 조립을 문서 상태 owner가 소유한다.
 */
export const useAssistantTurnContext = (
  state: DocumentState,
  readSource: () => string | null,
  environment: Record<string, unknown> | null,
): (() => TurnContextPayload) => {
  const build = useCommittedRef(
    useMemo(
      () => (source: string | null) =>
        assistantTurnContext(
          state,
          environment,
          source === null ? null : { source, format: state.format },
        ),
      [environment, state],
    ),
  );
  // `useCallback`으로 감싸지 않는다: ref를 읽는 함수라 컴파일러가 수동 메모를 보존하지 못한다.
  // 사이드바는 이 값을 effect 의존성으로 쓰지 않고 전송 시점에 그대로 부른다.
  return (): TurnContextPayload => build.current(readSource());
};
