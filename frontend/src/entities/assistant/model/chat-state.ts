import type {
  AssistantEventEnvelopeView,
  ChatMessageView,
  FailureCode,
  SessionHistoryView,
  SourceView,
  StrategyProposalView,
  TurnAcceptedView,
  TurnStatus,
  TurnView,
} from "../../../shared/api";

export type AssistantToolActivity = {
  callId: string;
  name: string;
  arguments: Readonly<Record<string, unknown>>;
  /** 결과가 아직 안 온 호출은 null이다. */
  ok: boolean | null;
  summary: string | null;
};

export type AssistantSearchActivity = {
  query: string;
  sources: readonly SourceView[];
};

export type AssistantFailure = { code: FailureCode; message: string };

export type AssistantTurnState = {
  turnId: string;
  /**
   * 저장된 턴 상태. 이벤트로는 바꾸지 않는다 — `Done`은 공급자 스트림이 끝났다는 표시일 뿐이고
   * 그 뒤에 `Failure`가 올 수 있다(spec D3). 아직 서버가 알려 준 상태가 없으면 null이다.
   */
  status: TurnStatus | null;
  acceptedSequence: number | null;
  text: string;
  thinking: readonly string[];
  tools: readonly AssistantToolActivity[];
  searches: readonly AssistantSearchActivity[];
  proposal: StrategyProposalView | null;
  usage: { inputTokens: number; outputTokens: number } | null;
  failure: AssistantFailure | null;
  stopReason: string | null;
};

/**
 * 사이드바가 그리는 한 세션의 투영.
 *
 * 서버 이력(`GET /sessions/{id}`)은 query cache가 소유하고 이 상태는 그 위에 스트리밍 이벤트를 얹은
 * 파생 투영이다. 토큰마다 캐시를 다시 쓰는 대신 local 상태로 접는 것은 spec D9가 "마지막 반영
 * sequence"를 frontend local UI state로 정한 것과 같은 이유다.
 */
export type AssistantChatState = {
  sessionId: string | null;
  messages: readonly ChatMessageView[];
  turns: readonly AssistantTurnState[];
  /** 이미 반영한 마지막 sequence. 재개 요청의 `after_sequence`이자 멱등 판정 기준이다(spec D7). */
  lastSequence: number;
};

export type AssistantChatAction =
  /** 세션 전환·초기화. 앞 세션의 투영을 버린다. */
  | { type: "session"; sessionId: string | null }
  /** 서버 이력 병합. 스트림을 열기 전과 닫은 뒤의 복구 경로다. */
  | { type: "history"; history: SessionHistoryView }
  /** 턴 시작·취소 응답이 알려 준 저장된 턴 상태. */
  | { type: "turn"; turn: TurnView | TurnAcceptedView }
  | { type: "event"; envelope: AssistantEventEnvelopeView };

export const emptyAssistantChatState: AssistantChatState = {
  sessionId: null,
  messages: [],
  turns: [],
  lastSequence: -1,
};

const emptyTurn = (turnId: string): AssistantTurnState => ({
  turnId,
  status: null,
  acceptedSequence: null,
  text: "",
  thinking: [],
  tools: [],
  searches: [],
  proposal: null,
  usage: null,
  failure: null,
  stopReason: null,
});

/** 턴 상태는 running에서 종료로 한 방향으로만 간다 — 낡은 스냅샷이 그 결말을 되돌리지 못한다. */
const settledStatus = (
  current: TurnStatus | null,
  incoming: TurnStatus,
): TurnStatus =>
  current !== null && current !== "running" ? current : incoming;

const withTurn = (
  state: AssistantChatState,
  turnId: string,
  update: (turn: AssistantTurnState) => AssistantTurnState,
): AssistantChatState => {
  const index = state.turns.findIndex((turn) => turn.turnId === turnId);
  const previous = index === -1 ? emptyTurn(turnId) : state.turns[index];
  const next = update(previous);
  if (next === previous) return state;
  const turns =
    index === -1
      ? [...state.turns, next]
      : state.turns.map((turn, at) => (at === index ? next : turn));
  return { ...state, turns };
};

const applyToolResult = (
  tools: readonly AssistantToolActivity[],
  result: { call_id: string; name: string; ok: boolean; summary: string },
): readonly AssistantToolActivity[] => {
  const index = tools.findIndex(
    (tool) => tool.callId === result.call_id && tool.ok === null,
  );
  const resolved = {
    callId: result.call_id,
    name: result.name,
    arguments: index === -1 ? {} : tools[index].arguments,
    ok: result.ok,
    summary: result.summary,
  };
  // 호출을 못 본 결과(재개 경계에서 호출 프레임을 흘려보낸 경우)도 활동으로 남긴다.
  return index === -1
    ? [...tools, resolved]
    : tools.map((tool, at) => (at === index ? resolved : tool));
};

const applyEvent = (
  turn: AssistantTurnState,
  event: AssistantEventEnvelopeView["event"],
): AssistantTurnState => {
  switch (event.type) {
    case "text_delta":
      return { ...turn, text: turn.text + event.text };
    case "thinking_summary":
      return { ...turn, thinking: [...turn.thinking, event.text] };
    case "tool_call":
      return {
        ...turn,
        tools: [
          ...turn.tools,
          {
            callId: event.call_id,
            name: event.name,
            arguments: event.arguments,
            ok: null,
            summary: null,
          },
        ],
      };
    case "tool_result":
      return { ...turn, tools: applyToolResult(turn.tools, event) };
    case "search_activity":
      return {
        ...turn,
        searches: [
          ...turn.searches,
          { query: event.query, sources: event.sources },
        ],
      };
    case "proposal":
      // 모델이 고쳐 다시 제출하면 마지막 제안이 그 턴의 제안이다.
      return { ...turn, proposal: event.proposal };
    case "usage":
      return {
        ...turn,
        usage: {
          inputTokens: (turn.usage?.inputTokens ?? 0) + event.input_tokens,
          outputTokens: (turn.usage?.outputTokens ?? 0) + event.output_tokens,
        },
      };
    case "done":
      // 턴을 완료로 표시하지 않는다. 종료 판정은 저장된 턴 상태만 한다(spec D3).
      return { ...turn, stopReason: event.stop_reason };
    case "failure":
      // 턴 상태가 되는 Failure는 먼저 확정된 하나뿐이다(spec D3 Failure 우선순위).
      return turn.failure === null
        ? { ...turn, failure: { code: event.code, message: event.message } }
        : turn;
  }
};

const reduceEvent = (
  state: AssistantChatState,
  envelope: AssistantEventEnvelopeView,
): AssistantChatState => {
  if (envelope.sequence <= state.lastSequence) return state;
  const applied = withTurn(state, envelope.turn_id, (turn) =>
    applyEvent(turn, envelope.event),
  );
  return { ...applied, lastSequence: envelope.sequence };
};

const reduceTurn = (
  state: AssistantChatState,
  turn: TurnView | TurnAcceptedView,
): AssistantChatState =>
  withTurn(state, turn.turn_id, (previous) => ({
    ...previous,
    status: settledStatus(previous.status, turn.status),
    acceptedSequence: turn.accepted_sequence,
  }));

/**
 * 이력을 덮어쓰지 않고 병합한다.
 *
 * 이력은 조회 시점의 스냅샷이라 응답이 도착했을 때는 스트림이 더 앞서 있을 수 있다. 통째로 갈아
 * 끼우면 그 사이에 반영한 이벤트가 사라지므로, 이벤트는 같은 멱등 경로로 넣고 턴 상태는 한 방향으로만
 * 옮긴다.
 */
const reduceHistory = (
  state: AssistantChatState,
  history: SessionHistoryView,
): AssistantChatState => {
  if (
    state.sessionId !== null &&
    state.sessionId !== history.session.session_id
  ) {
    // 세션을 바꾼 뒤 도착한 앞 세션의 응답이다.
    return state;
  }
  const withTurns = history.turns.reduce(reduceTurn, {
    ...state,
    sessionId: history.session.session_id,
    messages: history.messages,
  });
  return history.events.reduce(reduceEvent, withTurns);
};

export const assistantChatReducer = (
  state: AssistantChatState,
  action: AssistantChatAction,
): AssistantChatState => {
  switch (action.type) {
    case "session":
      return action.sessionId === state.sessionId
        ? state
        : { ...emptyAssistantChatState, sessionId: action.sessionId };
    case "history":
      return reduceHistory(state, action.history);
    case "turn":
      return reduceTurn(state, action.turn);
    case "event":
      return reduceEvent(state, action.envelope);
  }
};

export const assistantTurn = (
  state: AssistantChatState,
  turnId: string,
): AssistantTurnState | null =>
  state.turns.find((turn) => turn.turnId === turnId) ?? null;

/** 진행 중 턴. 스트림을 열지 말지는 이 값이 정한다(spec D7). */
export const runningAssistantTurn = (
  state: AssistantChatState,
): AssistantTurnState | null =>
  state.turns.find((turn) => turn.status === "running") ?? null;
