import fc from "fast-check";
import { describe, expect, it } from "vitest";

import type {
  AssistantEventEnvelopeView,
  SessionHistoryView,
  SessionUsageView,
  StrategyProposalView,
  TurnStatus,
  TurnView,
} from "../../../shared/api";
import {
  assistantChatReducer,
  assistantTurn,
  emptyAssistantChatState,
  runningAssistantTurn,
  type AssistantChatAction,
  type AssistantChatState,
} from "../model/chat-state";

const TURN = "turn-1";
const SESSION = "session-1";

type ChatEvent = AssistantEventEnvelopeView["event"];

const envelope = (
  sequence: number,
  event: ChatEvent,
  turnId: string = TURN,
): AssistantEventEnvelopeView => ({ sequence, turn_id: turnId, event });

const text = (value: string): ChatEvent => ({ type: "text_delta", text: value });

const proposal = (title: string): StrategyProposalView => ({
  title,
  summary: "한 문장 요약",
  rationale: "근거",
  sources: [{ title: "출처", url: "https://example.test/a" }],
  source_format: "yaml",
  source_text: "schema_version: '1.1'\n",
  compile: { ok: true, spec_hash: "hash", diagnostics: [] },
});

const turnView = (status: TurnStatus, turnId: string = TURN): TurnView => ({
  turn_id: turnId,
  session_id: SESSION,
  status,
  accepted_sequence: -1,
  started_at: "2026-09-20T00:00:00Z",
  finished_at: status === "running" ? null : "2026-09-20T00:01:00Z",
});

/** 서버가 이벤트에서 접어 주는 사용량. 리듀서는 이 값을 들지 않는다(owner는 이력 응답이다). */
const sessionUsage = (): SessionUsageView => ({
  provider_calls: 0,
  search_uses: 0,
  tokens: {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    total_input_tokens: 0,
  },
  turns: [],
});

const history = (
  events: AssistantEventEnvelopeView[],
  turns: TurnView[],
): SessionHistoryView => ({
  session: {
    session_id: SESSION,
    title: "세션",
    provider_profile_id: "p-1",
    document_ref: { draft_id: "draft-1", strategy_id: null, revision: null },
    created_at: "2026-09-20T00:00:00Z",
  },
  messages: [
    { role: "user", text: "모멘텀 전략을 제안해줘", created_at: "2026-09-20T00:00:00Z" },
  ],
  turns,
  events,
  usage: sessionUsage(),
});

const opened = (): AssistantChatState =>
  assistantChatReducer(emptyAssistantChatState, {
    type: "session",
    sessionId: SESSION,
  });

const fold = (
  state: AssistantChatState,
  actions: AssistantChatAction[],
): AssistantChatState => actions.reduce(assistantChatReducer, state);

const events = (envelopes: AssistantEventEnvelopeView[]): AssistantChatAction[] =>
  envelopes.map((item) => ({ type: "event", envelope: item }) as const);

describe("어시스턴트 채팅 리듀서", () => {
  it("스트리밍 텍스트를 순서대로 이어 붙이고 도구 결과를 그 호출에 붙인다", () => {
    const state = fold(
      opened(),
      events([
        envelope(0, text("모멘텀 ")),
        envelope(1, {
          type: "tool_call",
          call_id: "c-1",
          name: "validate_strategy_yaml",
          arguments: { source_text: "..." },
        }),
        envelope(2, {
          type: "tool_result",
          call_id: "c-1",
          name: "validate_strategy_yaml",
          ok: true,
          summary: "진단 없음",
        }),
        envelope(3, text("전략입니다")),
        envelope(4, {
          type: "search_activity",
          query: "momentum factor",
          sources: [{ title: "논문", url: "https://example.test/p" }],
        }),
        envelope(5, {
          type: "usage",
          input_tokens: 10,
          output_tokens: 4,
          cache_read_tokens: 6,
          cache_write_tokens: 1,
        }),
        envelope(6, {
          type: "usage",
          input_tokens: 3,
          output_tokens: 2,
          cache_read_tokens: 0,
          cache_write_tokens: 5,
        }),
        envelope(7, { type: "proposal", proposal: proposal("모멘텀 v1") }),
      ]),
    );

    const turn = state.turns[0];
    expect(turn.text).toBe("모멘텀 전략입니다");
    expect(turn.tools).toEqual([
      {
        callId: "c-1",
        name: "validate_strategy_yaml",
        arguments: { source_text: "..." },
        ok: true,
        summary: "진단 없음",
      },
    ]);
    expect(turn.searches).toEqual([
      { query: "momentum factor", sources: [{ title: "논문", url: "https://example.test/p" }] },
    ]);
    expect(turn.usage).toEqual({
      inputTokens: 13,
      outputTokens: 6,
      cacheReadTokens: 6,
      cacheWriteTokens: 6,
    });
    expect(turn.proposal?.title).toBe("모멘텀 v1");
    expect(state.lastSequence).toBe(7);
  });

  it("`Done`으로 턴을 완료로 표시하지 않고 그 뒤의 `Failure`도 받아 둔다", () => {
    const streamed = fold(
      fold(opened(), [{ type: "turn", turn: turnView("running") }]),
      events([
        envelope(0, text("부분 답변")),
        envelope(1, { type: "done", stop_reason: "end_turn" }),
        envelope(2, { type: "failure", code: "timeout", message: "제한 시간 초과" }),
        envelope(3, { type: "failure", code: "cancelled", message: "취소" }),
      ]),
    );

    // `Done`은 공급자 스트림이 끝났다는 표시일 뿐이다(spec D3).
    expect(streamed.turns[0].stopReason).toBe("end_turn");
    expect(streamed.turns[0].status).toBe("running");
    // 턴 상태가 되는 Failure는 먼저 확정된 하나뿐이다.
    expect(streamed.turns[0].failure).toEqual({
      code: "timeout",
      message: "제한 시간 초과",
    });
    expect(streamed.turns[0].text).toBe("부분 답변");

    const settled = assistantChatReducer(streamed, {
      type: "turn",
      turn: turnView("failed"),
    });
    expect(settled.turns[0].status).toBe("failed");
    expect(runningAssistantTurn(settled)).toBeNull();
  });

  it("이미 반영한 sequence 이하를 무시한다", () => {
    const state = fold(opened(), events([envelope(0, text("가")), envelope(1, text("나"))]));
    const replayed = fold(
      state,
      events([envelope(0, text("가")), envelope(1, text("나"))]),
    );

    expect(replayed).toBe(state);
    expect(replayed.turns[0].text).toBe("가나");
  });

  it("세션을 바꾸면 앞 세션의 투영을 버린다", () => {
    const state = fold(opened(), events([envelope(0, text("가"))]));
    const switched = assistantChatReducer(state, {
      type: "session",
      sessionId: "session-2",
    });

    expect(switched.turns).toEqual([]);
    expect(switched.lastSequence).toBe(-1);
    expect(switched.sessionId).toBe("session-2");
  });

  it("이력을 겹쳐 받아도 이벤트를 두 번 세지 않고 다른 세션 이력은 버린다", () => {
    const live = fold(
      opened(),
      events([envelope(0, text("가")), envelope(1, text("나"))]),
    );
    const merged = assistantChatReducer(live, {
      type: "history",
      history: history(
        [envelope(0, text("가")), envelope(1, text("나")), envelope(2, text("다"))],
        [turnView("completed")],
      ),
    });

    expect(merged.turns[0].text).toBe("가나다");
    expect(merged.turns[0].status).toBe("completed");
    expect(merged.messages).toHaveLength(1);
    expect(merged.lastSequence).toBe(2);

    const otherSession: SessionHistoryView = {
      ...history([envelope(3, text("라"))], []),
      session: { ...history([], []).session, session_id: "session-2" },
    };
    expect(
      assistantChatReducer(merged, { type: "history", history: otherSession }),
    ).toBe(merged);
  });

  it("종료된 턴 상태를 낡은 이력 스냅샷의 running으로 되돌리지 않는다", () => {
    const settled = fold(opened(), [
      { type: "turn", turn: turnView("cancelled") },
    ]);
    const stale = assistantChatReducer(settled, {
      type: "history",
      history: history([], [turnView("running")]),
    });

    expect(stale.turns[0].status).toBe("cancelled");
  });

  it("스트림이 이력보다 먼저 붙어도 앞선 턴의 이벤트를 잃지 않는다", () => {
    // 이력 query가 아직 오는 중에 턴 시작 202가 먼저 도착해 스트림이 붙은 경우다.
    const live = fold(
      opened(),
      events([
        envelope(10, text("새 턴 "), "turn-2"),
        envelope(11, text("답변"), "turn-2"),
      ]),
    );
    const merged = assistantChatReducer(live, {
      type: "history",
      history: history(
        [
          envelope(0, text("앞선 턴 답변"), "turn-1"),
          envelope(1, { type: "proposal", proposal: proposal("앞선 제안") }, "turn-1"),
          envelope(10, text("새 턴 "), "turn-2"),
          envelope(11, text("답변"), "turn-2"),
        ],
        [turnView("completed", "turn-1"), turnView("running", "turn-2")],
      ),
    });

    // backend SSE는 재개 범위에 걸린 앞선 턴 이벤트를 보내지 않는다 — 이력이 유일한 출처다.
    expect(assistantTurn(merged, "turn-1")?.text).toBe("앞선 턴 답변");
    expect(assistantTurn(merged, "turn-1")?.proposal?.title).toBe("앞선 제안");
    // 같은 턴의 이미 반영한 이벤트는 다시 붙지 않는다.
    expect(assistantTurn(merged, "turn-2")?.text).toBe("새 턴 답변");
    expect(merged.lastSequence).toBe(11);
  });

  it("턴이 여러 개면 이벤트를 각 턴에 나눠 담는다", () => {
    const state = fold(
      opened(),
      events([
        envelope(0, text("첫 턴"), "turn-1"),
        envelope(1, text("둘째 턴"), "turn-2"),
      ]),
    );

    expect(state.turns.map((turn) => turn.text)).toEqual(["첫 턴", "둘째 턴"]);
  });
});

/** 임의의 이벤트 열 — 갈래마다 상태를 다르게 건드리는 것만 고른다. */
const arbitraryEvent = fc.oneof(
  fc.string({ minLength: 1, maxLength: 4 }).map(
    (value): ChatEvent => ({ type: "text_delta", text: value }),
  ),
  fc.string({ minLength: 1, maxLength: 4 }).map(
    (value): ChatEvent => ({ type: "thinking_summary", text: value }),
  ),
  fc.string({ minLength: 1, maxLength: 3 }).map(
    (value): ChatEvent => ({
      type: "tool_call",
      call_id: value,
      name: "list_factor_catalog",
      arguments: {},
    }),
  ),
  fc.string({ minLength: 1, maxLength: 3 }).map(
    (value): ChatEvent => ({
      type: "tool_result",
      call_id: value,
      name: "list_factor_catalog",
      ok: true,
      summary: "요약",
    }),
  ),
  fc.string({ minLength: 1, maxLength: 4 }).map(
    (value): ChatEvent => ({ type: "search_activity", query: value, sources: [] }),
  ),
  fc.integer({ min: 0, max: 50 }).map(
    (value): ChatEvent => ({
      type: "usage",
      input_tokens: value,
      output_tokens: value,
      cache_read_tokens: value,
      cache_write_tokens: 0,
    }),
  ),
  fc.constant<ChatEvent>({ type: "done", stop_reason: "end_turn" }),
  fc.constant<ChatEvent>({
    type: "failure",
    code: "provider",
    message: "공급자 오류",
  }),
  fc.string({ minLength: 1, maxLength: 3 }).map(
    (value): ChatEvent => ({ type: "proposal", proposal: proposal(value) }),
  ),
);

/**
 * 재개가 겹쳐 보낸 프레임을 흉내 낸다 — 끊긴 자리부터 다시 보내면서 이미 받은 것을 얼마쯤 되풀이한다.
 *
 * SSE 재개는 `Last-Event-ID`가 가리키는 자리부터 다시 흘러오고, 서버가 그 경계를 어떻게 잡든
 * 클라이언트가 이미 반영한 번호는 다시 온다. 그 중복이 상태를 바꾸면 텍스트가 두 번 붙는다.
 */
const resent = (
  envelopes: AssistantEventEnvelopeView[],
  overlaps: number[],
): AssistantEventEnvelopeView[] => {
  const delivered: AssistantEventEnvelopeView[] = [];
  let cursor = 0;
  for (const overlap of overlaps) {
    const from = Math.max(0, cursor - overlap);
    delivered.push(...envelopes.slice(from, cursor + 1));
    cursor += 1;
    if (cursor >= envelopes.length) break;
  }
  delivered.push(...envelopes.slice(cursor));
  return delivered;
};

/** 턴별 순서는 지키고 턴 사이의 도착 순서만 뒤섞는다 — 스트림이 보장하는 것은 한 턴 안의 순서다. */
const interleave = (
  left: AssistantEventEnvelopeView[],
  right: AssistantEventEnvelopeView[],
  picks: boolean[],
): AssistantEventEnvelopeView[] => {
  const delivered: AssistantEventEnvelopeView[] = [];
  let atLeft = 0;
  let atRight = 0;
  let pick = 0;
  while (atLeft < left.length || atRight < right.length) {
    const wantLeft = picks[pick] ?? true;
    pick += 1;
    if (atLeft < left.length && (wantLeft || atRight >= right.length)) {
      delivered.push(left[atLeft]);
      atLeft += 1;
    } else {
      delivered.push(right[atRight]);
      atRight += 1;
    }
  }
  return delivered;
};

/** 턴 목록의 순서는 도착 순서를 따르므로, 투영을 비교할 때만 정렬해 맞춘다. */
const byTurnId = (state: AssistantChatState): AssistantChatState => ({
  ...state,
  turns: [...state.turns].sort((left, right) =>
    left.turnId.localeCompare(right.turnId),
  ),
});

describe("어시스턴트 채팅 리듀서 property", () => {
  it("턴 사이 도착 순서가 뒤바뀌어도 같은 투영을 만든다", () => {
    fc.assert(
      fc.property(
        fc.array(fc.tuple(fc.boolean(), arbitraryEvent), {
          minLength: 1,
          maxLength: 20,
        }),
        fc.array(fc.boolean(), { maxLength: 40 }),
        (plan, picks) => {
          const envelopes = plan.map(([second, event], index) =>
            envelope(index, event, second ? "turn-2" : "turn-1"),
          );
          const ordered = fold(opened(), events(envelopes));
          const shuffled = fold(
            opened(),
            events(
              interleave(
                envelopes.filter((item) => item.turn_id === "turn-1"),
                envelopes.filter((item) => item.turn_id === "turn-2"),
                picks,
              ),
            ),
          );
          expect(byTurnId(shuffled)).toEqual(byTurnId(ordered));
        },
      ),
    );
  });

  it("중복 재전송과 재개 분할에도 한 번 적용한 상태와 같다", () => {
    fc.assert(
      fc.property(
        fc.array(arbitraryEvent, { minLength: 1, maxLength: 24 }),
        fc.array(fc.integer({ min: 0, max: 6 }), { maxLength: 24 }),
        (chatEvents, overlaps) => {
          const envelopes = chatEvents.map((event, index) =>
            envelope(index, event),
          );
          const once = fold(opened(), events(envelopes));
          const resumed = fold(opened(), events(resent(envelopes, overlaps)));
          expect(resumed).toEqual(once);
        },
      ),
    );
  });
});
