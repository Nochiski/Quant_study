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

/** 턴 하나의 토큰 누적. 캐시 토큰은 과금이 다른 갈래라 입력 토큰에 섞지 않는다. */
export type AssistantTurnTokens = {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
};

export type AssistantTurnState = {
  turnId: string;
  /**
   * 저장된 턴 상태. 이벤트로는 바꾸지 않는다 — `Done`은 공급자 스트림이 끝났다는 표시일 뿐이고
   * 그 뒤에 `Failure`가 올 수 있다(spec D3). 아직 서버가 알려 준 상태가 없으면 null이다.
   */
  status: TurnStatus | null;
  /**
   * 서버가 이 턴을 정착(settled)시킨 것을 **클라이언트가 확인했는가.**
   *
   * 저장된 `status`가 종료로 바뀌어도 서버는 그 뒤에 마지막 이벤트를 더 붙인다 — 취소 응답을 받은
   * 직후의 `Failure(CANCELLED)`와 남은 텍스트 조각이 그렇다. status로 스트림을 닫으면 그 이벤트를
   * 영영 못 받아 화면에 취소 사유가 뜨지 않는다(B-05 e2e가 찾은 DEFECT-B05-001).
   *
   * 정착을 확인해 주는 것은 이력 응답 하나뿐이다. 이력이 종료 상태를 답했다면 서버는 그 턴의
   * 이벤트를 모두 flush한 뒤이고 그 이벤트들이 같은 응답에 실려 온다(backend의 SSE 종료 판정도
   * 같은 `is_settled`다). 턴 시작·취소 응답은 그 보장이 없으므로 여기에 쓰지 않는다.
   */
  settled: boolean;
  acceptedSequence: number | null;
  /** 서버가 적은 턴 시작 시각. `acceptedSequence`가 같을 때의 순서 기준이다. */
  startedAt: string | null;
  /**
   * 이 턴에서 이미 반영한 마지막 sequence. 멱등 판정은 세션 전체가 아니라 턴마다 한다.
   *
   * 세션 하나짜리 watermark로 판정하면 스트림이 이력보다 먼저 붙었을 때 그 watermark가 앞으로
   * 밀리고, 뒤늦게 온 이력의 앞선 턴 이벤트(더 낮은 번호)가 통째로 버려진다. backend SSE는 재개
   * 범위에 걸린 앞선 턴 이벤트를 보내지 않으므로 그 이벤트의 유일한 출처가 이력이다 — 버리면
   * 앞선 턴의 답변과 제안이 빈 채로 굳는다(리뷰 P2-1).
   */
  lastSequence: number;
  text: string;
  thinking: readonly string[];
  tools: readonly AssistantToolActivity[];
  searches: readonly AssistantSearchActivity[];
  proposal: StrategyProposalView | null;
  /**
   * 이 턴이 쓴 토큰. `Usage` 이벤트를 접은 값이라 세션 누적은 여기서 더하지 않는다 —
   * 세션 합계·공급자 호출 수의 owner는 서버가 같은 이벤트에서 접어 주는 `SessionHistoryView.usage`다.
   */
  usage: AssistantTurnTokens | null;
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
  /**
   * 세션 전체에서 반영한 가장 큰 sequence. 재개 요청의 `after_sequence`로만 쓴다.
   *
   * 멱등 판정은 `AssistantTurnState.lastSequence`가 턴마다 한다.
   */
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
  settled: false,
  acceptedSequence: null,
  startedAt: null,
  lastSequence: -1,
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

/**
 * 턴 배열의 순서 불변식: **서버가 아는 생성 순서**.
 *
 * 클라이언트가 처음 본 순서로 두면 턴 시작 202가 이력보다 먼저 도착한 세션에서 배열이
 * `[t-3, t-1, t-2]`가 된다. 소비자(`features/assist-strategy`의 transcript)는 n번째 사용자
 * 메시지를 n번째 턴의 질문으로 짝지으므로, 그 어긋남이 답변을 다른 질문에 붙이고 마지막 턴의
 * 질문을 잃는다. 한 번 어긋나면 이후 이력 재조회도 제자리 갱신뿐이라 스스로 복구되지 않는다
 * (B-03 리뷰 P1).
 *
 * 기준은 `accepted_sequence`(턴 시작 직전 세션의 마지막 번호라 세션 안에서 단조), 같으면
 * `started_at`, 그것도 모르면(이벤트로만 본 턴) 지금 자리를 지킨다.
 */
const TURN_RANK_UNKNOWN = Number.MAX_SAFE_INTEGER;

/** 시작 시각을 모르는 턴(이벤트로만 본 턴)은 뒤로 보낸다. */
const compareStartedAt = (
  left: string | null,
  right: string | null,
): number => {
  if (left === right) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return left < right ? -1 : 1;
};

const orderTurns = (
  turns: readonly AssistantTurnState[],
): readonly AssistantTurnState[] => {
  const ordered = turns
    .map((turn, index) => ({ turn, index }))
    .sort(
      (left, right) =>
        (left.turn.acceptedSequence ?? TURN_RANK_UNKNOWN) -
          (right.turn.acceptedSequence ?? TURN_RANK_UNKNOWN) ||
        compareStartedAt(left.turn.startedAt, right.turn.startedAt) ||
        left.index - right.index,
    );
  // 자리가 그대로면 같은 배열을 돌려준다 — 순서만 보고 다시 그리는 소비자를 깨우지 않는다.
  return ordered.every((item, at) => item.index === at)
    ? turns
    : ordered.map((item) => item.turn);
};

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
  return { ...state, turns: orderTurns(turns) };
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
          cacheReadTokens:
            (turn.usage?.cacheReadTokens ?? 0) + event.cache_read_tokens,
          cacheWriteTokens:
            (turn.usage?.cacheWriteTokens ?? 0) + event.cache_write_tokens,
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
  const current = state.turns.find((turn) => turn.turnId === envelope.turn_id);
  if (current !== undefined && envelope.sequence <= current.lastSequence) {
    return state;
  }
  const applied = withTurn(state, envelope.turn_id, (turn) => ({
    ...applyEvent(turn, envelope.event),
    lastSequence: envelope.sequence,
  }));
  return {
    ...applied,
    // 재개 위치는 뒤로 가지 않는다 — 이력이 스트림보다 낮은 번호를 늦게 들고 와도 마찬가지다.
    lastSequence: Math.max(state.lastSequence, envelope.sequence),
  };
};

const reduceTurn = (
  state: AssistantChatState,
  turn: TurnView | TurnAcceptedView,
  /** 이력 응답이 답한 상태인가. 이력만이 "서버가 다 흘렸다"를 보장한다. */
  fromHistory: boolean,
): AssistantChatState =>
  withTurn(state, turn.turn_id, (previous) => ({
    ...previous,
    status: settledStatus(previous.status, turn.status),
    settled: previous.settled || (fromHistory && turn.status !== "running"),
    acceptedSequence: turn.accepted_sequence,
    startedAt: turn.started_at,
  }));

/**
 * 이력을 덮어쓰지 않고 병합한다.
 *
 * 이력은 조회 시점의 스냅샷이라 응답이 도착했을 때는 스트림이 더 앞서 있을 수 있다. 통째로 갈아
 * 끼우면 그 사이에 반영한 이벤트가 사라지므로, 이벤트는 같은 멱등 경로로 넣고 턴 상태는 한 방향으로만
 * 옮긴다. 반대 방향(이력만 아는 옛 이벤트가 사라지는 것)은 턴별 watermark가 막는다.
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
  const seeded: AssistantChatState = {
    ...state,
    sessionId: history.session.session_id,
    messages: history.messages,
  };
  const withTurns = history.turns.reduce<AssistantChatState>(
    (merged, turn) => reduceTurn(merged, turn, true),
    seeded,
  );
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
      return reduceTurn(state, action.turn, false);
    case "event":
      return reduceEvent(state, action.envelope);
  }
};

export const assistantTurn = (
  state: AssistantChatState,
  turnId: string,
): AssistantTurnState | null =>
  state.turns.find((turn) => turn.turnId === turnId) ?? null;

/** 저장된 상태가 `running`인 턴. 화면의 "답변 중" 표시가 읽는 값이다. */
export const runningAssistantTurn = (
  state: AssistantChatState,
): AssistantTurnState | null =>
  state.turns.find((turn) => turn.status === "running") ?? null;

/**
 * 정착을 아직 확인하지 못한 마지막 턴. 스트림을 열고 닫는 판단은 이 값이 한다(spec D7).
 *
 * `runningAssistantTurn`이 아닌 이유는 취소 경로다 — 취소 응답으로 status가 먼저 CANCELLED가 되고
 * 서버의 취소 사유는 그 뒤에 온다. 반대 방향(정착했는데 아직 확인 못 한 턴을 대상으로 두는 것)은
 * 서버가 409 `no_running_turn`으로 막고 그 거절이 이력 복구를 부르므로, 한 번의 헛된 요청으로
 * 스스로 수렴한다.
 */
export const unsettledAssistantTurn = (
  state: AssistantChatState,
): AssistantTurnState | null => {
  for (let at = state.turns.length - 1; at >= 0; at -= 1) {
    const turn = state.turns[at];
    if (!turn.settled) return turn;
  }
  return null;
};
