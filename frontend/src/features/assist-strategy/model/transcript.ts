import type {
  AssistantChatState,
  AssistantTurnState,
} from "../../../entities/assistant";

/** 화면에 그리는 한 덩어리: 사용자의 질문과 그 질문이 시작한 턴. */
export type AssistTranscriptEntry = {
  turnId: string;
  /** 질문 원문. 이력도 방금 보낸 원문도 없으면 null이다. */
  prompt: string | null;
  turn: AssistantTurnState;
};

/**
 * 턴 목록과 사용자 메시지를 짝지어 대화 순서로 편다.
 *
 * 질문은 사용자 메시지의 `turn_id`로 자기 턴에 붙는다(C-03). 예전에는 "n번째 사용자 메시지 =
 * n번째 턴"이라는 순서 가정으로 짝지었고, 메시지 수와 턴 수가 어긋나면(턴 행 저장 실패로 질문만
 * 남은 이력, 질문 행이 없는 턴) 질문이 다른 턴의 답에 붙었다. 이제 턴 배열의 순서는 늘어놓는
 * 차례만 정한다.
 *
 * `turn_id`가 없는 메시지(어느 턴 뒤에도 오지 않는 옛 이력)는 어느 턴에도 달지 않는다. 한 턴에
 * 사용자 메시지가 둘 이상이면 먼저 쓰인 것이 그 턴의 질문이다. 이력이 아직 그 턴을 모르는
 * 구간(202 직후)은 방금 보낸 원문으로 메운다.
 *
 * 어시스턴트 쪽 본문은 이력의 assistant 메시지가 아니라 턴에 누적된 이벤트에서 읽는다 — 스트리밍
 * 중에도 같은 자리에 같은 방식으로 그려야 하고, 사고 요약·도구·출처·제안은 메시지에 없다.
 */
export const assistTranscript = (
  state: AssistantChatState,
  pendingPrompts: Readonly<Record<string, string>>,
): readonly AssistTranscriptEntry[] => {
  const asked = new Map<string, string>();
  for (const message of state.messages) {
    if (
      message.role === "user" &&
      message.turn_id !== null &&
      !asked.has(message.turn_id)
    ) {
      asked.set(message.turn_id, message.text);
    }
  }
  return state.turns.map((turn) => ({
    turnId: turn.turnId,
    prompt: asked.get(turn.turnId) ?? pendingPrompts[turn.turnId] ?? null,
    turn,
  }));
};
