import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
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
  ProvidersView,
  SessionHistoryView,
  SessionView,
  StrategyProposalView,
  TurnContextPayload,
} from "../../../entities/assistant";
import {
  AssistStrategySidebar,
  type AssistProposalAction,
} from "../ui/assist-strategy-sidebar";

const API = "http://localhost:8000";
const DOCUMENT = { strategy_id: null, revision: null, draft_id: "draft-1" };
const SOURCE = "schema_version: '1.1'\nname: 초기 문서\n";
const encoder = new TextEncoder();

const providersView = (active: boolean): ProvidersView => ({
  kinds: [
    { kind: "anthropic", installed: true, default_model: "claude-sonnet-5" },
    { kind: "openai", installed: false, default_model: null },
  ],
  profiles: [
    {
      profile_id: "p-1",
      kind: "anthropic",
      label: "작업용 Claude",
      model: "claude-sonnet-5",
      base_url: null,
      secret_tail: "9876",
      active,
      created_at: "2026-09-20T00:00:00Z",
    },
  ],
});

const session = (sessionId: string, title: string): SessionView => ({
  session_id: sessionId,
  title,
  provider_profile_id: "p-1",
  document_ref: DOCUMENT,
  created_at: "2026-09-20T00:00:00Z",
});

const emptyHistory = (
  sessionId: string,
  title: string,
): SessionHistoryView => ({
  session: session(sessionId, title),
  messages: [],
  turns: [],
  events: [],
});

const proposal = (): StrategyProposalView => ({
  title: "저변동 모멘텀",
  summary: "변동성이 낮은 모멘텀 상위 종목을 매수합니다.",
  rationale: "최근 국내 시장은 저변동 구간입니다.",
  sources: [
    { title: "시장 보고서", url: "https://example.com/report" },
    { title: "수상한 출처", url: "javascript:alert(1)" },
  ],
  source_format: "yaml",
  source_text: "schema_version: '1.1'\nname: 저변동 모멘텀\n",
  compile: { ok: true, spec_hash: "hash-1", diagnostics: [] },
});

/** 열려 있는 SSE 연결 하나. 테스트가 프레임을 직접 밀어 넣는다. */
type Connection = {
  push: (envelope: AssistantEventEnvelopeView) => void;
  /** 갈래를 좁히지 못하는 프레임. 서버가 이벤트 종류를 늘린 상황이다. */
  pushUnknown: (sequence: number) => void;
  /** 재연결 간격을 서버가 정한다. 테스트는 이것으로 상한 소진을 빠르게 만든다. */
  pushRetryDelay: (ms: number) => void;
  close: () => void;
  /** 응답 도중 끊긴 연결. 생성 클라이언트는 여기서 재연결한다. */
  drop: () => void;
};

/** 이벤트 엔드포인트의 응답 계획. 앞에서부터 하나씩 쓰고, 비면 스트림을 연다. */
type Reply =
  | "stream"
  | "network-error"
  | { status: number; body: Record<string, unknown> };

let providers: ProvidersView;
let sessions: SessionView[];
let histories: Record<string, SessionHistoryView>;
let connections: Connection[];
let eventReplies: Reply[];
let startedTurns: { sessionId: string; body: Record<string, unknown> }[];
let cancelled: { sessionId: string; turnId: string }[];
let createdSessions: Record<string, unknown>[];

const server = setupServer(
  http.get(`${API}/api/v1/assistant/providers`, () =>
    HttpResponse.json(providers),
  ),
  http.get(`${API}/api/v1/assistant/sessions`, () =>
    HttpResponse.json(sessions),
  ),
  http.post(`${API}/api/v1/assistant/sessions`, async ({ request }) => {
    createdSessions.push((await request.json()) as Record<string, unknown>);
    const created = session("s-new", "새 대화");
    sessions = [...sessions, created];
    histories["s-new"] = emptyHistory("s-new", "새 대화");
    return HttpResponse.json(created, { status: 201 });
  }),
  http.get(`${API}/api/v1/assistant/sessions/:sessionId`, ({ params }) => {
    const found = histories[String(params.sessionId)];
    return found === undefined
      ? HttpResponse.json(
          { detail: { code: "assistant.session.not_found", message: "" } },
          { status: 404 },
        )
      : HttpResponse.json(found);
  }),
  http.post(
    `${API}/api/v1/assistant/sessions/:sessionId/turns`,
    async ({ params, request }) => {
      const sessionId = String(params.sessionId);
      startedTurns.push({
        sessionId,
        body: (await request.json()) as Record<string, unknown>,
      });
      return HttpResponse.json(
        {
          turn_id: "t-1",
          session_id: sessionId,
          status: "running",
          accepted_sequence: -1,
          started_at: "2026-09-20T00:01:00Z",
        },
        { status: 202 },
      );
    },
  ),
  http.post(
    `${API}/api/v1/assistant/sessions/:sessionId/turns/:turnId/cancel`,
    ({ params }) => {
      const sessionId = String(params.sessionId);
      const turnId = String(params.turnId);
      cancelled.push({ sessionId, turnId });
      return HttpResponse.json({
        turn_id: turnId,
        session_id: sessionId,
        status: "cancelled",
        accepted_sequence: -1,
        started_at: "2026-09-20T00:01:00Z",
        finished_at: "2026-09-20T00:02:00Z",
      });
    },
  ),
  http.get(`${API}/api/v1/assistant/sessions/:sessionId/events`, () => {
    const reply = eventReplies.shift() ?? "stream";
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
    const frame = (text: string) => controller.enqueue(encoder.encode(text));
    connections.push({
      push: (envelope) =>
        frame(
          `id: ${envelope.sequence}\nevent: assistant\ndata: ${JSON.stringify(envelope)}\n\n`,
        ),
      pushUnknown: (sequence) =>
        frame(
          `id: ${sequence}\nevent: assistant\ndata: ${JSON.stringify({
            sequence,
            turn_id: "t-1",
            event: { type: "quantum_leap" },
          })}\n\n`,
        ),
      pushRetryDelay: (ms) => frame(`retry: ${ms}\n\n`),
      close: () => controller.close(),
      drop: () => controller.error(new Error("연결이 끊겼습니다")),
    });
    return new HttpResponse(stream, {
      headers: { "Content-Type": "text/event-stream" },
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => server.close());

beforeEach(() => {
  providers = providersView(true);
  sessions = [];
  histories = {};
  connections = [];
  eventReplies = [];
  startedTurns = [];
  cancelled = [];
  createdSessions = [];
});

type MountOptions = {
  onPreviewProposal?: (action: AssistProposalAction) => void;
  onApplyProposal?: (action: AssistProposalAction) => void;
  onApplyProposalAndBacktest?: (action: AssistProposalAction) => void;
  onClose?: () => void;
};

const context = (): TurnContextPayload => ({
  source_text: SOURCE,
  source_format: "yaml",
  diagnostics: ["strategy.universe.missing"],
  environment: { initial_cash: 10_000_000 },
});

const mount = (options: MountOptions = {}) => {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const homeRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => (
      <AssistStrategySidebar
        documentRef={DOCUMENT}
        readContext={context}
        {...options}
      />
    ),
  });
  const settingsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings",
    component: () => <p>설정 화면</p>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute, settingsRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
};

/** 질문 하나를 보내고 서버가 턴을 받을 때까지 기다린다. */
const send = async (
  user: ReturnType<typeof userEvent.setup>,
  text = "지금 시장에 맞는 전략을 제안해 줘",
): Promise<void> => {
  const input = await screen.findByRole("textbox", {
    name: "어시스턴트에게 보낼 메시지",
  });
  await user.type(input, text);
  await user.keyboard("{Enter}");
  await waitFor(() => expect(startedTurns).toHaveLength(1));
};

/** 질문 하나를 보내고 그 턴의 SSE 연결이 열릴 때까지 기다린다. */
const ask = async (
  user: ReturnType<typeof userEvent.setup>,
  text = "지금 시장에 맞는 전략을 제안해 줘",
): Promise<Connection> => {
  await send(user, text);
  await waitFor(() => expect(connections).toHaveLength(1));
  return connections[0];
};

const textDelta = (
  sequence: number,
  text: string,
): AssistantEventEnvelopeView => ({
  sequence,
  turn_id: "t-1",
  event: { type: "text_delta", text },
});

describe("AssistStrategySidebar", () => {
  it("활성 공급자가 없으면 설정으로 안내하고 입력창을 두지 않는다", async () => {
    providers = providersView(false);
    mount();

    expect(
      await screen.findByText("연결된 AI 공급자가 없습니다"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "설정 열기" })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("질문을 보내면 현재 문서 컨텍스트를 실어 턴을 시작하고 텍스트를 이어 붙인다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);

    expect(createdSessions).toHaveLength(1);
    expect(createdSessions[0].document_ref).toEqual(DOCUMENT);
    expect(startedTurns).toHaveLength(1);
    expect(startedTurns[0].sessionId).toBe("s-new");
    expect(startedTurns[0].body).toMatchObject({
      text: "지금 시장에 맞는 전략을 제안해 줘",
      context: {
        source_text: SOURCE,
        diagnostics: ["strategy.universe.missing"],
      },
    });

    const log = screen.getByRole("log");
    expect(log).toHaveAttribute("aria-live", "polite");
    expect(
      within(log).getByText("지금 시장에 맞는 전략을 제안해 줘"),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "답변을 작성하는 중입니다",
    );

    connection.push(textDelta(0, "저변동 구간이라"));
    connection.push(textDelta(1, " 모멘텀을 권합니다."));

    await waitFor(() =>
      expect(
        within(log).getByText("저변동 구간이라 모멘텀을 권합니다."),
      ).toBeInTheDocument(),
    );
  });

  it("Shift+Enter는 줄을 바꾸고 전송하지 않는다", async () => {
    const user = userEvent.setup();
    mount();
    const input = await screen.findByRole("textbox", {
      name: "어시스턴트에게 보낼 메시지",
    });
    await user.type(input, "첫 줄");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(input, "둘째 줄");

    expect(input).toHaveValue("첫 줄\n둘째 줄");
    expect(startedTurns).toHaveLength(0);
  });

  it("사고 요약은 접힌 채로 도착하고 펼쳐야 보인다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);
    connection.push({
      sequence: 0,
      turn_id: "t-1",
      event: { type: "thinking_summary", text: "유니버스를 먼저 확인한다" },
    });

    const summary = await screen.findByText("사고 요약");
    expect(screen.getByText("유니버스를 먼저 확인한다")).not.toBeVisible();
    await user.click(summary);
    expect(screen.getByText("유니버스를 먼저 확인한다")).toBeVisible();
  });

  it("도구 활동은 접힘으로 두고 결과가 오면 상태를 바꾼다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);
    connection.push({
      sequence: 0,
      turn_id: "t-1",
      event: {
        type: "tool_call",
        call_id: "c-1",
        name: "list_equity_fields",
        arguments: {},
      },
    });

    const summary = await screen.findByText(/도구 활동/);
    await user.click(summary);
    expect(screen.getByText("데이터 필드 목록")).toBeVisible();
    expect(screen.getByText("실행 중")).toBeVisible();

    connection.push({
      sequence: 1,
      turn_id: "t-1",
      event: {
        type: "tool_result",
        call_id: "c-1",
        name: "list_equity_fields",
        ok: true,
        summary: "필드 42개",
      },
    });
    expect(await screen.findByText("필드 42개")).toBeVisible();
    expect(screen.queryByText("실행 중")).toBeNull();
  });

  it("검색 활동은 질의를 보이고 http 출처만 링크로 만든다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);
    connection.push({
      sequence: 0,
      turn_id: "t-1",
      event: {
        type: "search_activity",
        query: "코스피 변동성 2026",
        sources: [
          { title: "시장 보고서", url: "https://example.com/report" },
          { title: "수상한 출처", url: "javascript:alert(1)" },
        ],
      },
    });

    expect(await screen.findByText("코스피 변동성 2026")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "시장 보고서" });
    expect(link).toHaveAttribute("href", "https://example.com/report");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("수상한 출처")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "수상한 출처" })).toBeNull();
  });

  it("모델이 보낸 HTML은 문자 그대로 보이고 요소가 되지 않는다", async () => {
    const user = userEvent.setup();
    const { container } = mount();
    const connection = await ask(user);
    const injection = '<img src=x onerror="alert(1)"><b>굵게</b>';
    connection.push(textDelta(0, injection));

    expect(await screen.findByText(injection)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("제안 카드의 버튼은 제안과 턴 시작 시점 원문을 콜백에 넘긴다", async () => {
    const user = userEvent.setup();
    const onPreviewProposal = vi.fn();
    const onApplyProposal = vi.fn();
    const onApplyProposalAndBacktest = vi.fn();
    mount({ onPreviewProposal, onApplyProposal, onApplyProposalAndBacktest });
    const connection = await ask(user);
    const offered = proposal();
    connection.push({
      sequence: 0,
      turn_id: "t-1",
      event: { type: "proposal", proposal: offered },
    });

    expect(await screen.findByText("저변동 모멘텀")).toBeInTheDocument();
    expect(screen.getByText("검증 통과")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "시장 보고서" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "수상한 출처" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "미리보기" }));
    await user.click(screen.getByRole("button", { name: "문서에 적용" }));
    await user.click(screen.getByRole("button", { name: "적용 후 백테스트" }));

    const expected = { proposal: offered, baseSourceText: SOURCE };
    expect(onPreviewProposal).toHaveBeenCalledWith(expected);
    expect(onApplyProposal).toHaveBeenCalledWith(expected);
    expect(onApplyProposalAndBacktest).toHaveBeenCalledWith(expected);
  });

  it("제안 카드 버튼은 키보드로도 조작된다", async () => {
    const user = userEvent.setup();
    const onApplyProposal = vi.fn();
    mount({ onApplyProposal });
    const connection = await ask(user);
    connection.push({
      sequence: 0,
      turn_id: "t-1",
      event: { type: "proposal", proposal: proposal() },
    });

    const apply = await screen.findByRole("button", { name: "문서에 적용" });
    apply.focus();
    await user.keyboard("{Enter}");
    expect(onApplyProposal).toHaveBeenCalledTimes(1);
  });

  it("실패 이벤트는 서버 문장이 아니라 코드에 맞는 한글 문장을 보여 준다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);
    connection.push({
      sequence: 0,
      turn_id: "t-1",
      event: {
        type: "failure",
        code: "rate_limit",
        message: "RateLimitError sk-ant-1234",
      },
    });

    expect(
      await screen.findByText(
        "공급자가 요청 한도를 넘었다고 답했습니다. 잠시 뒤 다시 물어보세요.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/sk-ant-1234/)).toBeNull();
  });

  it("진행 중에는 중지 버튼이 턴 취소를 부른다", async () => {
    const user = userEvent.setup();
    mount();
    await ask(user);

    const stop = await screen.findByRole("button", { name: "중지" });
    await user.click(stop);

    await waitFor(() => expect(cancelled).toHaveLength(1));
    expect(cancelled[0]).toEqual({ sessionId: "s-new", turnId: "t-1" });
    expect(
      await screen.findByRole("button", { name: "보내기" }),
    ).toBeInTheDocument();
  });

  it("진행 중 턴이 있으면 닫기를 막고 무엇을 확인하는지 설명한다", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    mount({ onClose });
    await ask(user);

    await user.click(
      await screen.findByRole("button", { name: "사이드바 닫기" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("진행 중인 답변이 있습니다");
    expect(onClose).not.toHaveBeenCalled();
    // 대화상자 이름만으로는 무엇을 묻는지 알 수 없다 — 본문을 설명으로 잇는다.
    expect(dialog).toHaveAccessibleDescription(/진행 중인 답변이 있습니다/);
    // 처음 초점은 답변을 버리지 않는 쪽에 둔다.
    expect(
      within(dialog).getByRole("button", { name: "계속 두기" }),
    ).toHaveFocus();
  });

  it("Escape로 확인을 물리면 초점이 닫기 버튼으로 돌아온다", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    mount({ onClose });
    await ask(user);

    await user.click(
      await screen.findByRole("button", { name: "사이드바 닫기" }),
    );
    await screen.findByRole("alertdialog");
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(cancelled).toHaveLength(0);
    expect(screen.getByRole("button", { name: "사이드바 닫기" })).toHaveFocus();
  });

  it("계속 두기는 대화상자만 닫고 턴을 취소하지 않는다", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    mount({ onClose });
    await ask(user);

    await user.click(
      await screen.findByRole("button", { name: "사이드바 닫기" }),
    );
    // 대화상자는 조건 렌더라 다시 열 때마다 새 노드다 — 그때그때 다시 잡는다.
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "계속 두기",
      }),
    );

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(cancelled).toHaveLength(0);
    expect(screen.getByRole("button", { name: "사이드바 닫기" })).toHaveFocus();
  });

  it("취소하고 닫기는 턴을 취소한 뒤 닫는다", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    mount({ onClose });
    await ask(user);

    await user.click(
      await screen.findByRole("button", { name: "사이드바 닫기" }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "취소하고 닫기",
      }),
    );

    await waitFor(() => expect(cancelled).toHaveLength(1));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("진행 중 턴이 없으면 닫기가 바로 닫는다", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    mount({ onClose });

    await user.click(
      await screen.findByRole("button", { name: "사이드바 닫기" }),
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("진행 중 턴이 있다고 서버가 거절하면 질문을 입력칸으로 되돌린다", async () => {
    server.use(
      http.post(`${API}/api/v1/assistant/sessions/:sessionId/turns`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "assistant.turn_in_progress",
              message: "",
              turn_id: "t-other",
            },
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    mount();
    const input = await screen.findByRole("textbox", {
      name: "어시스턴트에게 보낼 메시지",
    });
    await user.type(input, "길게 쓴 질문");
    await user.keyboard("{Enter}");

    expect(
      await screen.findByText(/진행 중인 답변이 있어 보내지 못했습니다/),
    ).toBeInTheDocument();
    await waitFor(() => expect(input).toHaveValue("길게 쓴 질문"));
  });

  it("출처 링크는 제목 옆에 실제 도착지 호스트를 보인다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);
    connection.push({
      sequence: 0,
      turn_id: "t-1",
      event: {
        type: "search_activity",
        query: "공시",
        sources: [
          {
            title: "금융감독원 공시 https://dart.fss.or.kr",
            url: "https://dart-fss.example-evil.com/notice",
          },
        ],
      },
    });

    expect(
      await screen.findByText(/dart-fss\.example-evil\.com/),
    ).toBeInTheDocument();
  });

  it("스트리밍 본문은 라이브 영역 밖이고 완료는 상태로 한 번 알린다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);
    connection.push(textDelta(0, "본문"));

    const body = await screen.findByText("본문");
    expect(body).toHaveAttribute("aria-live", "off");
    expect(screen.getByRole("status")).toHaveTextContent(
      "답변을 작성하는 중입니다",
    );

    histories["s-new"] = {
      ...emptyHistory("s-new", "새 대화"),
      turns: [
        {
          turn_id: "t-1",
          session_id: "s-new",
          status: "completed",
          accepted_sequence: -1,
          started_at: "2026-09-20T00:01:00Z",
          finished_at: "2026-09-20T00:02:00Z",
        },
      ],
      events: [],
    };
    connection.close();

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "답변이 완료되었습니다",
      ),
    );
  });

  it("대화를 고르면 그 세션의 이력을 보여 준다", async () => {
    sessions = [session("s-1", "첫 대화"), session("s-2", "둘째 대화")];
    histories["s-1"] = {
      ...emptyHistory("s-1", "첫 대화"),
      messages: [
        {
          role: "user",
          text: "첫 대화의 질문",
          created_at: "2026-09-20T00:00:01Z",
        },
      ],
      turns: [
        {
          turn_id: "t-old",
          session_id: "s-1",
          status: "completed",
          accepted_sequence: -1,
          started_at: "2026-09-20T00:00:01Z",
          finished_at: "2026-09-20T00:00:09Z",
        },
      ],
      events: [
        {
          sequence: 0,
          turn_id: "t-old",
          event: { type: "text_delta", text: "첫 대화의 답" },
        },
      ],
    };
    histories["s-2"] = {
      ...emptyHistory("s-2", "둘째 대화"),
      messages: [
        {
          role: "user",
          text: "둘째 대화의 질문",
          created_at: "2026-09-20T00:00:02Z",
        },
      ],
      turns: [
        {
          turn_id: "t-new",
          session_id: "s-2",
          status: "completed",
          accepted_sequence: -1,
          started_at: "2026-09-20T00:00:02Z",
          finished_at: "2026-09-20T00:00:09Z",
        },
      ],
      events: [
        {
          sequence: 0,
          turn_id: "t-new",
          event: { type: "text_delta", text: "둘째 대화의 답" },
        },
      ],
    };
    const user = userEvent.setup();
    mount();

    // 가장 최근 대화를 먼저 연다.
    expect(await screen.findByText("둘째 대화의 답")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "대화" }),
      "s-1",
    );
    expect(await screen.findByText("첫 대화의 답")).toBeInTheDocument();
    expect(screen.queryByText("둘째 대화의 답")).toBeNull();
    expect(connections).toHaveLength(0);
  });

  it("새 대화를 누르면 앞 대화의 투영을 비운다", async () => {
    sessions = [session("s-1", "첫 대화")];
    histories["s-1"] = {
      ...emptyHistory("s-1", "첫 대화"),
      messages: [
        {
          role: "user",
          text: "앞 대화의 질문",
          created_at: "2026-09-20T00:00:01Z",
        },
      ],
      turns: [
        {
          turn_id: "t-old",
          session_id: "s-1",
          status: "completed",
          accepted_sequence: -1,
          started_at: "2026-09-20T00:00:01Z",
          finished_at: "2026-09-20T00:00:09Z",
        },
      ],
      events: [],
    };
    const user = userEvent.setup();
    mount();

    expect(await screen.findByText("앞 대화의 질문")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "새 대화" }));
    await waitFor(() =>
      expect(screen.queryByText("앞 대화의 질문")).toBeNull(),
    );
    expect(screen.getByText("무엇이든 물어보세요")).toBeInTheDocument();
  });

  it("재시도 상한을 소진하면 다시 연결 손잡이를 보여 준다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);
    // 서버가 재연결 간격을 정한다 — 기본 3초를 기다리지 않고 상한까지 내려간다.
    connection.pushRetryDelay(1);
    connection.drop();
    for (let attempt = 2; attempt <= 5; attempt += 1) {
      await waitFor(() => expect(connections).toHaveLength(attempt));
      connections[attempt - 1].drop();
    }

    const banner = await screen.findByText(/연결이 끊겼습니다/);
    expect(banner).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "다시 연결" }));
    await waitFor(() => expect(connections).toHaveLength(6));
    await waitFor(() =>
      expect(screen.queryByText(/연결이 끊겼습니다/)).toBeNull(),
    );
  });

  it("서버가 스트림을 열어 주지 않으면 배너 없이 이력으로 정착한다", async () => {
    eventReplies = [
      {
        status: 409,
        body: { detail: { code: "assistant.no_running_turn", message: "" } },
      },
    ];
    const user = userEvent.setup();
    mount();
    server.use(
      http.get(`${API}/api/v1/assistant/sessions/:sessionId`, ({ params }) => {
        const sessionId = String(params.sessionId);
        return HttpResponse.json({
          ...emptyHistory(sessionId, "새 대화"),
          turns: [
            {
              turn_id: "t-1",
              session_id: sessionId,
              status: "completed",
              accepted_sequence: -1,
              started_at: "2026-09-20T00:01:00Z",
              finished_at: "2026-09-20T00:02:00Z",
            },
          ],
          events: [
            {
              sequence: 0,
              turn_id: "t-1",
              event: { type: "text_delta", text: "스트림 없이 받은 답" },
            },
          ],
        });
      }),
    );
    await send(user);

    expect(await screen.findByText("스트림 없이 받은 답")).toBeInTheDocument();
    expect(screen.queryByText(/연결이 끊겼습니다/)).toBeNull();
    expect(
      await screen.findByRole("button", { name: "보내기" }),
    ).toBeInTheDocument();
  });

  it("좁히지 못한 프레임이 있으면 화면에 그 사실을 남긴다", async () => {
    const user = userEvent.setup();
    mount();
    const connection = await ask(user);
    connection.pushUnknown(0);
    connection.push(textDelta(1, "나머지는 그대로 보인다"));
    await screen.findByText("나머지는 그대로 보인다");
    connection.close();

    expect(
      await screen.findByText(/표시하지 못한 진행 정보/),
    ).toBeInTheDocument();
  });

  it("서버가 턴 시작을 거부하면 코드에 맞는 문장을 배너로 보여 준다", async () => {
    server.use(
      http.post(`${API}/api/v1/assistant/sessions/:sessionId/turns`, () =>
        HttpResponse.json(
          { detail: { code: "assistant.no_active_provider", message: "" } },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    mount();
    const input = await screen.findByRole("textbox", {
      name: "어시스턴트에게 보낼 메시지",
    });
    await user.type(input, "질문");
    await user.keyboard("{Enter}");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("활성 공급자가 없습니다.");
  });
});
