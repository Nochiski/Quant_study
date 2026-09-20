import { describe, expect, it } from "vitest";

import {
  assistantChatReducer,
  emptyAssistantChatState,
  type AssistantChatState,
  type SessionHistoryView,
} from "../../../entities/assistant";
import { assistTranscript } from "../model/transcript";
import { safeExternalUrl } from "../model/render-safety";

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

describe("safeExternalUrl", () => {
  it("keeps http and https targets", () => {
    expect(safeExternalUrl("https://example.com/report?a=1")).toBe(
      "https://example.com/report?a=1",
    );
    expect(safeExternalUrl("http://example.com/")).toBe("http://example.com/");
  });

  it("rejects every other scheme so a model cannot ship an executable link", () => {
    expect(safeExternalUrl("javascript:alert(1)")).toBeNull();
    expect(safeExternalUrl("  javascript:alert(1)")).toBeNull();
    expect(safeExternalUrl("JaVaScRiPt:alert(1)")).toBeNull();
    expect(
      safeExternalUrl("data:text/html,<script>alert(1)</script>"),
    ).toBeNull();
    expect(safeExternalUrl("file:///etc/passwd")).toBeNull();
    expect(safeExternalUrl("vbscript:msgbox(1)")).toBeNull();
  });

  it("rejects values that are not absolute URLs at all", () => {
    expect(safeExternalUrl("")).toBeNull();
    expect(safeExternalUrl("/settings")).toBeNull();
    expect(safeExternalUrl("example.com")).toBeNull();
  });
});
