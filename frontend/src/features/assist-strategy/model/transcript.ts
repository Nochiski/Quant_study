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
 * 리듀서는 메시지(이력)와 턴(이벤트 누적)을 각각 들고 있고 둘을 잇는 id가 계약에 없다. 턴 하나가
 * 사용자 메시지 하나로 시작하고 둘 다 생성 순서대로 쌓이므로 n번째 사용자 메시지가 n번째 턴의
 * 질문이다. 이력이 아직 그 턴을 모르는 구간(202 직후)은 방금 보낸 원문으로 메운다.
 *
 * 어시스턴트 쪽 본문은 이력의 assistant 메시지가 아니라 턴에 누적된 이벤트에서 읽는다 — 스트리밍
 * 중에도 같은 자리에 같은 방식으로 그려야 하고, 사고 요약·도구·출처·제안은 메시지에 없다.
 */
export const assistTranscript = (
  state: AssistantChatState,
  pendingPrompts: Readonly<Record<string, string>>,
): readonly AssistTranscriptEntry[] => {
  const asked = state.messages.filter((message) => message.role === "user");
  return state.turns.map((turn, index) => ({
    turnId: turn.turnId,
    prompt: asked[index]?.text ?? pendingPrompts[turn.turnId] ?? null,
    turn,
  }));
};
