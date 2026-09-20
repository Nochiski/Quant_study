import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type {
  AssistantEventEnvelopeView,
  SessionHistoryView,
  TurnStatus,
} from "../../../shared/api";
import {
  assistantChatReducer,
  emptyAssistantChatState,
} from "../model/chat-state";
import {
  assistantStreamTarget,
  useAssistantEventStream,
  type AssistantStreamClose,
  type AssistantStreamTarget,
} from "../model/use-assistant-event-stream";

const API = "http://localhost:8000";
const SESSION = "session-1";
const TURN = "turn-1";
const encoder = new TextEncoder();

const envelope = (
  sequence: number,
  text: string,
): AssistantEventEnvelopeView => ({
  sequence,
  turn_id: TURN,
  event: { type: "text_delta", text },
});

/** 백엔드가 쓰는 프레이밍 그대로 — `id`는 sequence, 이벤트 이름은 `assistant` 하나다(spec D6). */
const frame = (item: AssistantEventEnvelopeView): Uint8Array =>
  encoder.encode(
    `id: ${item.sequence}\nevent: assistant\ndata: ${JSON.stringify(item)}\n\n`,
  );

const history = (
  status: TurnStatus,
  sessionId: string = SESSION,
): SessionHistoryView => ({
  session: {
    session_id: sessionId,
    title: "세션",
    provider_profile_id: "p-1",
    document_ref: { draft_id: "draft-1", strategy_id: null, revision: null },
    created_at: "2026-09-20T00:00:00Z",
  },
  messages: [],
  turns: [
    {
      turn_id: TURN,
      session_id: sessionId,
      status,
      accepted_sequence: -1,
      started_at: "2026-09-20T00:00:00Z",
      finished_at: status === "running" ? null : "2026-09-20T00:01:00Z",
    },
  ],
  events: [],
});

type Connection = {
  push: (item: AssistantEventEnvelopeView) => void;
  /** 봉투로 좁혀지지 않는 프레임까지 그대로 밀어 넣는다. */
  pushFrame: (raw: string) => void;
  /** 프레임을 임의의 자리에서 자른 청크. 한글이 코드포인트 중간에서 갈리는 경우를 포함한다. */
  pushChunks: (item: AssistantEventEnvelopeView, size: number) => void;
  close: () => void;
  /** 응답 도중 끊긴 연결. 생성 클라이언트는 여기서 재연결한다. */
  drop: () => void;
};

/** 앞에서부터 하나씩 소비하는 응답 계획. 비면 스트림을 연다. */
type Reply =
  | "stream"
  | "network-error"
  | { status: number; body: Record<string, unknown> };

let attempts: Request[];
let connections: Connection[];
let replies: Reply[];
let historyRequests: number;
let historyStatus: TurnStatus;
/** 이력 조회가 실패하는 경우(세션이 지워진 뒤)를 켠다. */
let historyMissing: boolean;

const server = setupServer(
  http.get(`${API}/api/v1/assistant/sessions/:sessionId/events`, ({ request }) => {
    attempts.push(request);
    const reply = replies.shift() ?? "stream";
    if (reply === "network-error") return HttpResponse.error();
    if (reply !== "stream") {
      return HttpResponse.json(reply.body, { status: reply.status });
    }
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start: (value) => {
        controller = value;
      },
    });
    connections.push({
      push: (item) => controller.enqueue(frame(item)),
      pushFrame: (raw) => controller.enqueue(encoder.encode(raw)),
      pushChunks: (item, size) => {
        const bytes = frame(item);
        for (let at = 0; at < bytes.length; at += size) {
          controller.enqueue(bytes.slice(at, at + size));
        }
      },
      close: () => controller.close(),
      drop: () => controller.error(new Error("연결이 끊겼습니다")),
    });
    return new HttpResponse(stream, {
      headers: { "Content-Type": "text/event-stream" },
    });
  }),
  http.get(`${API}/api/v1/assistant/sessions/:sessionId`, ({ params }) => {
    historyRequests += 1;
    if (historyMissing) {
      return HttpResponse.json(
        {
          detail: {
            code: "assistant.session.not_found",
            message: "세션을 찾을 수 없습니다",
          },
        },
        { status: 404 },
      );
    }
    return HttpResponse.json(history(historyStatus, String(params.sessionId)));
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

beforeEach(() => {
  attempts = [];
  connections = [];
  replies = [];
  historyRequests = 0;
  historyStatus = "completed";
  historyMissing = false;
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

type Harness = {
  target: AssistantStreamTarget | null;
  lastSequence: number;
  /** 재시도 대기만 바꿔 다시 여는 경로를 확인할 때 쓴다. */
  retryDelayMs?: number;
};

const onEvent = vi.fn<(item: AssistantEventEnvelopeView) => void>();
const onHistory = vi.fn<(item: SessionHistoryView) => void>();
const onClose = vi.fn<(close: AssistantStreamClose) => void>();

beforeEach(() => {
  onEvent.mockReset();
  onHistory.mockReset();
  onClose.mockReset();
});

const target: AssistantStreamTarget = { sessionId: SESSION, turnId: TURN };

const mount = (initial: Harness) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(
    (props: Harness) =>
      useAssistantEventStream({
        target: props.target,
        lastSequence: props.lastSequence,
        onEvent,
        onHistory,
        onClose,
        // 재시도 대기를 1ms로 줄인다 — 기본 3초는 재연결 경로를 테스트할 수 없게 만든다.
        retryDelayMs: props.retryDelayMs ?? 1,
      }),
    { wrapper, initialProps: initial },
  );
};

const settle = async (): Promise<void> => {
  await act(async () => {
    await Promise.resolve();
  });
};

describe("어시스턴트 SSE 리더", () => {
  it("진행 중 턴이 없으면 스트림을 열지 않는다", async () => {
    mount({ target: null, lastSequence: -1 });
    await settle();

    expect(attempts).toHaveLength(0);
    expect(historyRequests).toBe(0);
  });

  it("반영한 자리부터 열고, 프레임을 넘긴 뒤 닫히면 이력으로 턴 상태를 확정한다", async () => {
    mount({ target, lastSequence: 3 });
    await waitFor(() => expect(connections).toHaveLength(1));

    expect(new URL(attempts[0].url).searchParams.get("after_sequence")).toBe(
      "3",
    );

    connections[0].push(envelope(4, "모멘텀 "));
    connections[0].push(envelope(5, "전략"));
    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(2));
    expect(onEvent.mock.calls.map(([item]) => item.event)).toEqual([
      { type: "text_delta", text: "모멘텀 " },
      { type: "text_delta", text: "전략" },
    ]);

    connections[0].close();
    await waitFor(() => expect(onHistory).toHaveBeenCalledTimes(1));
    expect(onHistory.mock.calls[0][0].turns[0].status).toBe("completed");
    expect(onClose).toHaveBeenCalledWith({
      reason: "ended",
      rejection: null,
      turnStatus: "completed",
      recovered: true,
      droppedFrames: 0,
    });
    expect(attempts).toHaveLength(1);
  });

  it("끊긴 연결은 `Last-Event-ID`로 이어 연다", async () => {
    mount({ target, lastSequence: -1 });
    await waitFor(() => expect(connections).toHaveLength(1));

    connections[0].push(envelope(0, "가"));
    connections[0].push(envelope(1, "나"));
    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(2));
    connections[0].drop();

    await waitFor(() => expect(attempts).toHaveLength(2));
    expect(attempts[1].headers.get("Last-Event-ID")).toBe("1");
    expect(onHistory).not.toHaveBeenCalled();

    connections[1].push(envelope(2, "다"));
    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(3));
    expect(onEvent.mock.calls[2][0].sequence).toBe(2);
  });

  it("프레임이 임의의 자리에서 잘려 와도 한 이벤트로 읽는다", async () => {
    mount({ target, lastSequence: -1 });
    await waitFor(() => expect(connections).toHaveLength(1));

    connections[0].pushChunks(envelope(0, "모멘텀 팩터를 쓰는 전략"), 5);
    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(1));
    expect(onEvent.mock.calls[0][0]).toEqual(envelope(0, "모멘텀 팩터를 쓰는 전략"));
  });

  it("409 `no_running_turn`은 다시 열지 않고 이력으로 복구한다", async () => {
    replies = [
      {
        status: 409,
        body: {
          detail: {
            code: "assistant.no_running_turn",
            message: "이 세션에는 진행 중인 턴이 없습니다",
          },
        },
      },
    ];
    historyStatus = "failed";
    mount({ target, lastSequence: 7 });

    await waitFor(() => expect(onHistory).toHaveBeenCalledTimes(1));
    expect(attempts).toHaveLength(1);
    expect(onClose).toHaveBeenCalledWith({
      reason: "rejected",
      rejection: { status: 409, code: "assistant.no_running_turn" },
      turnStatus: "failed",
      recovered: true,
      droppedFrames: 0,
    });
    expect(onHistory.mock.calls[0][0].turns[0].status).toBe("failed");
  });

  it("재시도 상한을 다 쓰면 멈추고 이력으로 복구한다", async () => {
    replies = Array.from({ length: 12 }, () => "network-error" as const);
    const view = mount({ target, lastSequence: -1 });

    await waitFor(() => expect(onHistory).toHaveBeenCalledTimes(1));
    expect(attempts).toHaveLength(5);
    expect(onClose.mock.calls[0][0]).toMatchObject({ reason: "exhausted" });
    await waitFor(() => expect(view.result.current.status).toBe("exhausted"));
  });

  it("마지막 시도가 붙었다 끊긴 경우도 상한 소진으로 보고한다", async () => {
    historyStatus = "running";
    const view = mount({ target, lastSequence: -1 });

    // 연결은 매번 열리고 본문만 끊긴다 — fetch 성공만 세면 이 경로가 정상 종료로 보인다.
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      await waitFor(() => expect(connections).toHaveLength(attempt));
      connections[attempt - 1].drop();
    }

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onClose.mock.calls[0][0]).toMatchObject({
      reason: "exhausted",
      turnStatus: "running",
      recovered: true,
    });
    expect(view.result.current.status).toBe("exhausted");
  });

  it("상한을 소진해도 스스로 다시 열지 않고 `retry()`에만 다시 연다", async () => {
    historyStatus = "running";
    replies = Array.from({ length: 5 }, () => "network-error" as const);
    const view = mount({ target, lastSequence: -1 });

    await waitFor(() => expect(view.result.current.status).toBe("exhausted"));
    expect(attempts).toHaveLength(5);

    // 턴은 서버에서 계속 돌지만(이력이 running) 훅은 스스로 되살아나지 않는다.
    await settle();
    expect(attempts).toHaveLength(5);

    act(() => view.result.current.retry());
    await waitFor(() => expect(connections).toHaveLength(1));
    expect(attempts).toHaveLength(6);
    expect(view.result.current.status).toBe("open");
  });

  it("이력 복구까지 실패하면 복구 실패를 알린다", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    replies = [
      {
        status: 404,
        body: {
          detail: {
            code: "assistant.session.not_found",
            message: "세션을 찾을 수 없습니다",
          },
        },
      },
    ];
    historyMissing = true;
    mount({ target, lastSequence: -1 });

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onClose.mock.calls[0][0]).toMatchObject({
      reason: "rejected",
      recovered: false,
      turnStatus: null,
    });
    expect(onHistory).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("좁히지 못한 프레임은 버리되 흔적을 남긴다", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mount({ target, lastSequence: -1 });
    await waitFor(() => expect(connections).toHaveLength(1));

    for (const sequence of [0, 1, 2]) {
      connections[0].pushFrame(
        `id: ${sequence}
event: assistant
data: ${JSON.stringify({
          sequence,
          turn_id: TURN,
          event: { type: "brand_new_event" },
        })}

`,
      );
    }
    connections[0].push(envelope(3, "정상 프레임"));
    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(1));

    connections[0].close();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onClose.mock.calls[0][0]).toMatchObject({ droppedFrames: 3 });
    // 같은 갈래는 한 번만 남긴다 — 토큰 단위로 오면 콘솔이 묻힌다.
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain("brand_new_event");
    warn.mockRestore();
  });

  it("언마운트하면 연결을 정리하고 늦게 온 프레임을 버린다", async () => {
    const view = mount({ target, lastSequence: -1 });
    await waitFor(() => expect(connections).toHaveLength(1));

    view.unmount();
    await waitFor(() => expect(attempts[0].signal.aborted).toBe(true));

    connections[0].push(envelope(0, "늦게 온 프레임"));
    await settle();
    expect(onEvent).not.toHaveBeenCalled();
    expect(onHistory).not.toHaveBeenCalled();
  });

  it("턴이 종료 상태가 되면 대상이 사라져 스트림을 닫는다", async () => {
    const running = assistantChatReducer(
      assistantChatReducer(emptyAssistantChatState, {
        type: "session",
        sessionId: SESSION,
      }),
      { type: "turn", turn: history("running").turns[0] },
    );
    const view = mount({
      target: assistantStreamTarget(running),
      lastSequence: -1,
    });
    await waitFor(() => expect(connections).toHaveLength(1));

    const settled = assistantChatReducer(running, {
      type: "history",
      history: history("cancelled"),
    });
    view.rerender({
      target: assistantStreamTarget(settled),
      lastSequence: settled.lastSequence,
    });

    await waitFor(() => expect(attempts[0].signal.aborted).toBe(true));
    expect(attempts).toHaveLength(1);
  });

  it("떠났다 같은 턴으로 돌아오면 앞 연결의 종료 사유가 되살아나지 않는다", async () => {
    const view = mount({ target, lastSequence: -1 });
    await waitFor(() => expect(connections).toHaveLength(1));
    expect(view.result.current.status).toBe("open");

    connections[0].close();
    await waitFor(() => expect(view.result.current.status).toBe("ended"));

    // 투영이 비면 대상이 사라지고(세션 이탈), 돌아와 이력을 다시 읽으면 같은 턴이 다시 선다.
    view.rerender({ target: null, lastSequence: -1 });
    expect(view.result.current.status).toBe("idle");

    view.rerender({ target, lastSequence: -1 });
    await waitFor(() => expect(connections).toHaveLength(2));
    expect(view.result.current.status).toBe("open");
  });

  it("재시도 설정만 바뀌어 다시 열려도 앞 사유가 남지 않는다", async () => {
    const view = mount({ target, lastSequence: -1 });
    await waitFor(() => expect(connections).toHaveLength(1));

    connections[0].close();
    await waitFor(() => expect(view.result.current.status).toBe("ended"));

    view.rerender({ target, lastSequence: -1, retryDelayMs: 2 });
    await waitFor(() => expect(connections).toHaveLength(2));
    expect(view.result.current.status).toBe("open");
  });

  it("세션을 바꾸면 앞 세션 연결을 끊고 새 세션으로 연다", async () => {
    const view = mount({ target, lastSequence: -1 });
    await waitFor(() => expect(connections).toHaveLength(1));

    view.rerender({
      target: { sessionId: "session-2", turnId: "turn-2" },
      lastSequence: -1,
    });

    await waitFor(() => expect(attempts).toHaveLength(2));
    expect(attempts[0].signal.aborted).toBe(true);
    expect(attempts[1].url).toContain("/sessions/session-2/events");

    connections[0].push(envelope(0, "앞 세션 프레임"));
    await settle();
    expect(onEvent).not.toHaveBeenCalled();
  });
});
