import { describe, expect, it } from "vitest";

import {
  assistantChatReducer,
  emptyAssistantChatState,
  type AssistantChatState,
  type SessionHistoryView,
} from "../../../entities/assistant";
import { assistTranscript } from "../model/transcript";
import { safeExternalLink } from "../model/render-safety";

const history = (): SessionHistoryView => ({
  session: {
    session_id: "s-1",
    title: "세션",
    provider_profile_id: "p-1",
    document_ref: { draft_id: "draft-1", strategy_id: null, revision: null },
    created_at: "2026-09-20T00:00:00Z",
  },
  messages: [
    { role: "user", text: "첫 질문", created_at: "2026-09-20T00:00:01Z" },
    { role: "assistant", text: "첫 답", created_at: "2026-09-20T00:00:02Z" },
    { role: "user", text: "둘째 질문", created_at: "2026-09-20T00:00:03Z" },
  ],
  turns: [
    {
      turn_id: "t-1",
      session_id: "s-1",
      status: "completed",
      accepted_sequence: -1,
      started_at: "2026-09-20T00:00:01Z",
      finished_at: "2026-09-20T00:00:02Z",
    },
    {
      turn_id: "t-2",
      session_id: "s-1",
      status: "running",
      accepted_sequence: 4,
      started_at: "2026-09-20T00:00:03Z",
      finished_at: null,
    },
  ],
  events: [],
});

const loaded = (): AssistantChatState =>
  assistantChatReducer(emptyAssistantChatState, {
    type: "history",
    history: history(),
  });

describe("assistTranscript", () => {
  it("pairs each turn with the user message that started it", () => {
    const entries = assistTranscript(loaded(), {});
    expect(entries.map((entry) => entry.turnId)).toEqual(["t-1", "t-2"]);
    expect(entries.map((entry) => entry.prompt)).toEqual([
      "첫 질문",
      "둘째 질문",
    ]);
  });

  it("falls back to the text just sent when the history has not caught up", () => {
    const state = assistantChatReducer(emptyAssistantChatState, {
      type: "turn",
      turn: {
        turn_id: "t-9",
        session_id: "s-1",
        status: "running",
        accepted_sequence: 0,
        started_at: "2026-09-20T00:10:00Z",
      },
    });
    const entries = assistTranscript(state, { "t-9": "방금 보낸 질문" });
    expect(entries).toHaveLength(1);
    expect(entries[0].prompt).toBe("방금 보낸 질문");
  });

  it("이력이 늦게 와도 턴 순서는 서버가 아는 생성 순서를 지킨다", () => {
    // 턴 시작 202가 이력보다 먼저 도착한 세션. 리듀서가 관측 순서로 쌓으면 [t-3, t-1, t-2]가 되고
    // 질문이 한 칸씩 밀린다(B-03 리뷰 P1).
    const afterStart = assistantChatReducer(
      { ...emptyAssistantChatState, sessionId: "s-1" },
      {
        type: "turn",
        turn: {
          turn_id: "t-3",
          session_id: "s-1",
          status: "running",
          accepted_sequence: 7,
          started_at: "2026-09-20T00:00:05Z",
        },
      },
    );
    const merged = assistantChatReducer(afterStart, {
      type: "history",
      history: history(),
    });

    expect(merged.turns.map((turn) => turn.turnId)).toEqual([
      "t-1",
      "t-2",
      "t-3",
    ]);
    expect(
      assistTranscript(merged, { "t-3": "셋째 질문" }).map(
        (entry) => entry.prompt,
      ),
    ).toEqual(["첫 질문", "둘째 질문", "셋째 질문"]);
  });

  it("leaves the prompt empty when neither source knows it", () => {
    const state = assistantChatReducer(emptyAssistantChatState, {
      type: "turn",
      turn: {
        turn_id: "t-9",
        session_id: "s-1",
        status: "completed",
        accepted_sequence: 0,
        started_at: "2026-09-20T00:10:00Z",
      },
    });
    expect(assistTranscript(state, {})[0].prompt).toBeNull();
  });
});

describe("safeExternalLink", () => {
  it("keeps http and https targets and names the real host", () => {
    expect(safeExternalLink("https://example.com/report?a=1")).toEqual({
      href: "https://example.com/report?a=1",
      host: "example.com",
    });
    expect(safeExternalLink("http://example.com/")).toEqual({
      href: "http://example.com/",
      host: "example.com",
    });
    // 제목이 다른 곳을 사칭해도 호스트는 실제 도착지를 가리킨다(B-03 리뷰 P2).
    expect(safeExternalLink("https://dart-fss.example-evil.com/x")?.host).toBe(
      "dart-fss.example-evil.com",
    );
  });

  it("rejects every other scheme so a model cannot ship an executable link", () => {
    expect(safeExternalLink("javascript:alert(1)")).toBeNull();
    expect(safeExternalLink("  javascript:alert(1)")).toBeNull();
    expect(safeExternalLink("JaVaScRiPt:alert(1)")).toBeNull();
    expect(
      safeExternalLink("data:text/html,<script>alert(1)</script>"),
    ).toBeNull();
    expect(safeExternalLink("file:///etc/passwd")).toBeNull();
    expect(safeExternalLink("vbscript:msgbox(1)")).toBeNull();
  });

  it("rejects values that are not absolute URLs at all", () => {
    expect(safeExternalLink("")).toBeNull();
    expect(safeExternalLink("/settings")).toBeNull();
    expect(safeExternalLink("example.com")).toBeNull();
  });
});
