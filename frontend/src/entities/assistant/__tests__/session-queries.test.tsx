import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
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
} from "vitest";

import { AssistantRequestError, type SessionView } from "../../../shared/api";
import {
  assistantSessionQuery,
  assistantSessionsQuery,
  useCancelAssistantTurn,
  useCreateAssistantSession,
  useStartAssistantTurn,
} from "../model/session-queries";

const API = "http://localhost:8000";
const SESSION = "session-1";

const session: SessionView = {
  session_id: SESSION,
  title: "모멘텀 상담",
  provider_profile_id: "p-1",
  document_ref: { draft_id: "draft-1", strategy_id: null, revision: null },
  created_at: "2026-09-20T00:00:00Z",
};

let createBodies: Record<string, unknown>[];
let turnBodies: Record<string, unknown>[];
let turnReply: () => Response;
let listQueries: URLSearchParams[];

const server = setupServer(
  http.post(`${API}/api/v1/assistant/sessions`, async ({ request }) => {
    createBodies.push((await request.json()) as Record<string, unknown>);
    return HttpResponse.json(session, { status: 201 });
  }),
  http.get(`${API}/api/v1/assistant/sessions`, ({ request }) => {
    listQueries.push(new URL(request.url).searchParams);
    return HttpResponse.json([session]);
  }),
  http.get(`${API}/api/v1/assistant/sessions/:sessionId`, ({ params }) =>
    HttpResponse.json({
      session: { ...session, session_id: String(params.sessionId) },
      messages: [
        { role: "user", text: "질문", created_at: "2026-09-20T00:00:00Z" },
      ],
      turns: [],
      events: [],
      usage: {
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
      },
    }),
  ),
  http.post(
    `${API}/api/v1/assistant/sessions/:sessionId/turns`,
    async ({ request }) => {
      turnBodies.push((await request.json()) as Record<string, unknown>);
      return turnReply();
    },
  ),
  http.post(
    `${API}/api/v1/assistant/sessions/:sessionId/turns/:turnId/cancel`,
    ({ params }) =>
      HttpResponse.json({
        turn_id: String(params.turnId),
        session_id: String(params.sessionId),
        status: "cancelled",
        accepted_sequence: 4,
        started_at: "2026-09-20T00:00:00Z",
        finished_at: "2026-09-20T00:00:30Z",
      }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

beforeEach(() => {
  createBodies = [];
  turnBodies = [];
  listQueries = [];
  turnReply = () =>
    HttpResponse.json(
      {
        turn_id: "turn-1",
        session_id: SESSION,
        status: "running",
        accepted_sequence: 4,
        started_at: "2026-09-20T00:00:00Z",
      },
      { status: 202 },
    );
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

const client = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

const wrapperFor = (queryClient: QueryClient) =>
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };

describe("어시스턴트 세션·턴 query", () => {
  it("문서 참조로 세션을 만들고 그 문서의 목록을 다시 읽는다", async () => {
    const queryClient = client();
    const view = renderHook(
      () => ({
        sessions: useQuery(assistantSessionsQuery({ draft_id: "draft-1" })),
        create: useCreateAssistantSession(),
      }),
      { wrapper: wrapperFor(queryClient) },
    );

    await waitFor(() => expect(view.result.current.sessions.data).toHaveLength(1));
    expect(listQueries[0].get("draft_id")).toBe("draft-1");

    await view.result.current.create.mutateAsync({
      document_ref: { draft_id: "draft-1" },
      title: "모멘텀 상담",
    });

    expect(createBodies).toEqual([
      { document_ref: { draft_id: "draft-1" }, title: "모멘텀 상담" },
    ]);
    // 생성 성공은 그 문서의 세션 목록만 무효화한다.
    await waitFor(() => expect(listQueries).toHaveLength(2));
  });

  it("턴 시작은 `accepted_sequence`를 그대로 돌려준다", async () => {
    const queryClient = client();
    const view = renderHook(() => useStartAssistantTurn(), {
      wrapper: wrapperFor(queryClient),
    });

    const accepted = await view.result.current.mutateAsync({
      sessionId: SESSION,
      request: {
        text: "모멘텀 전략을 제안해줘",
        context: { source_text: "schema_version: '1.1'\n", source_format: "yaml" },
      },
    });

    expect(accepted).toMatchObject({ turn_id: "turn-1", accepted_sequence: 4 });
    expect(turnBodies[0]).toMatchObject({ text: "모멘텀 전략을 제안해줘" });
  });

  it("진행 중 턴이 있으면 409 코드와 그 턴 id를 실어 거부한다", async () => {
    turnReply = () =>
      HttpResponse.json(
        {
          detail: {
            code: "assistant.turn_in_progress",
            message: "이미 진행 중인 턴이 있습니다",
            turn_id: "turn-9",
          },
        },
        { status: 409 },
      );
    const queryClient = client();
    const view = renderHook(() => useStartAssistantTurn(), {
      wrapper: wrapperFor(queryClient),
    });

    const rejection = await view.result.current
      .mutateAsync({
        sessionId: SESSION,
        request: { text: "질문", context: { source_text: "" } },
      })
      .catch((error: unknown) => error);

    expect(rejection).toBeInstanceOf(AssistantRequestError);
    const error = rejection as AssistantRequestError;
    expect(error.status).toBe(409);
    expect(error.code).toBe("assistant.turn_in_progress");
    expect(error.turnId).toBe("turn-9");
    // 서버 문장은 화면으로 새지 않는다 — 문구의 owner는 frontend i18n이다.
    expect(error.message).not.toContain("이미 진행 중인 턴이 있습니다");
  });

  it("취소는 저장된 턴 상태를 돌려준다", async () => {
    const queryClient = client();
    const view = renderHook(() => useCancelAssistantTurn(), {
      wrapper: wrapperFor(queryClient),
    });

    const turn = await view.result.current.mutateAsync({
      sessionId: SESSION,
      turnId: "turn-1",
    });

    expect(turn).toMatchObject({ turn_id: "turn-1", status: "cancelled" });
  });

  it("이력 query는 메시지·턴·이벤트를 한 번에 읽는다", async () => {
    const queryClient = client();
    const history = await queryClient.fetchQuery(assistantSessionQuery(SESSION));

    await waitFor(() => expect(history.session.session_id).toBe(SESSION));
    expect(history.messages).toHaveLength(1);
  });
});
