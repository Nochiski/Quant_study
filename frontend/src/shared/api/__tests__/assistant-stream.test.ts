import { describe, expect, it } from "vitest";

import { assistantEventEnvelope } from "../assistant-stream";

const proposal = {
  title: "모멘텀 v1",
  summary: "한 문장",
  rationale: "근거",
  sources: [{ title: "출처", url: "https://example.test/a" }],
  source_format: "yaml",
  source_text: "schema_version: '1.1'\n",
  compile: { ok: true, spec_hash: "hash", diagnostics: [] },
};

const frame = (event: unknown): unknown => ({
  sequence: 3,
  turn_id: "turn-1",
  event,
});

describe("SSE 프레임 좁히기", () => {
  it("갈래별 필수 필드가 다 있는 프레임을 통과시킨다", () => {
    const events = [
      { type: "text_delta", text: "가" },
      { type: "thinking_summary", text: "요약" },
      { type: "tool_call", call_id: "c-1", name: "read_current_strategy", arguments: {} },
      {
        type: "tool_result",
        call_id: "c-1",
        name: "read_current_strategy",
        ok: false,
        summary: "실패",
      },
      { type: "search_activity", query: "momentum", sources: [] },
      { type: "proposal", proposal },
      {
        type: "usage",
        input_tokens: 1,
        output_tokens: 2,
        cache_read_tokens: 3,
        cache_write_tokens: 4,
      },
      { type: "done", stop_reason: "end_turn" },
      { type: "failure", code: "timeout", message: "제한 시간" },
    ];

    for (const event of events) {
      expect(assistantEventEnvelope(frame(event))?.event).toEqual(event);
    }
  });

  it("봉투나 갈래를 좁히지 못하는 프레임은 버린다", () => {
    const rejected: unknown[] = [
      null,
      "문자열 프레임",
      { turn_id: "turn-1", event: { type: "done", stop_reason: "end_turn" } },
      { sequence: 1, event: { type: "done", stop_reason: "end_turn" } },
      frame({ type: "unknown_event" }),
      // 갈래는 맞지만 리듀서가 읽는 필드가 없다 — 누적 텍스트에 `undefined`가 섞이는 경로다.
      frame({ type: "text_delta" }),
      frame({
        type: "usage",
        input_tokens: "많음",
        output_tokens: 2,
        cache_read_tokens: 3,
        cache_write_tokens: 4,
      }),
      // A-07이 더한 캐시 토큰이 빠진 프레임 — 누적이 `undefined`로 물드는 경로다.
      frame({ type: "usage", input_tokens: 1, output_tokens: 2 }),
      frame({ type: "proposal", proposal: { ...proposal, compile: undefined } }),
      frame({ type: "search_activity", query: "momentum", sources: [{ url: 1 }] }),
    ];

    for (const value of rejected) {
      expect(assistantEventEnvelope(value)).toBeNull();
    }
  });
});
