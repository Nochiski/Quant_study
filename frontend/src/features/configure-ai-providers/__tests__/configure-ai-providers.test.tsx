import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
import type { ReactElement, ReactNode } from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
} from "vitest";

import type {
  ProviderProfileView,
  ProvidersView,
} from "../../../entities/assistant";
import { AiProviderSettings } from "../ui/ai-provider-settings";

const API = "http://localhost:8000";
const SECRET = "sk-ant-secret-value-9876";

const profile = (
  overrides: Partial<ProviderProfileView> = {},
): ProviderProfileView => ({
  profile_id: "p-1",
  kind: "anthropic",
  label: "작업용 Claude",
  model: "claude-sonnet-5",
  base_url: null,
  secret_tail: "9876",
  active: true,
  created_at: "2026-09-20T00:00:00Z",
  ...overrides,
});

/** 핸들러가 함께 읽고 고치는 서버 상태 — 목록은 변이 뒤 다시 조회된다. */
let view: ProvidersView;
let createBodies: Record<string, unknown>[];
let activated: string[];
let deleted: string[];
let createReply: () => Response;

const server = setupServer(
  http.get(`${API}/api/v1/assistant/providers`, () => HttpResponse.json(view)),
  http.post(`${API}/api/v1/assistant/providers`, async ({ request }) => {
    createBodies.push((await request.json()) as Record<string, unknown>);
    return createReply();
  }),
  http.post(
    `${API}/api/v1/assistant/providers/:profileId/activate`,
    ({ params }) => {
      const id = String(params.profileId);
      activated.push(id);
      view = {
        ...view,
        profiles: view.profiles.map((item) => ({
          ...item,
          active: item.profile_id === id,
        })),
      };
      return HttpResponse.json(
        view.profiles.find((item) => item.profile_id === id),
      );
    },
  ),
  http.delete(`${API}/api/v1/assistant/providers/:profileId`, ({ params }) => {
    const id = String(params.profileId);
    deleted.push(id);
    view = {
      ...view,
      profiles: view.profiles.filter((item) => item.profile_id !== id),
    };
    return new HttpResponse(null, { status: 204 });
  }),
);

const probeReply = (body: Record<string, unknown>, status = 200) =>
  server.use(
    http.post(`${API}/api/v1/assistant/providers/:profileId/test`, () =>
      HttpResponse.json(body, { status }),
    ),
  );

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  view = {
    kinds: [
      { kind: "anthropic", installed: true, default_model: "claude-sonnet-5" },
      { kind: "openai", installed: false, default_model: null },
    ],
    profiles: [profile()],
  };
  createBodies = [];
  activated = [];
  deleted = [];
  createReply = () =>
    HttpResponse.json(
      profile({ profile_id: "p-2", label: "두 번째", active: false }),
      { status: 201 },
    );
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

const renderSettings = (ui: ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
};

const cardOf = async (label: string) => {
  const heading = await screen.findByRole("heading", { name: label });
  const card = heading.closest("li");
  if (card === null) throw new Error(`카드를 찾지 못했습니다: ${label}`);
  return within(card);
};

const fillNewProvider = async (
  user: ReturnType<typeof userEvent.setup>,
  label = "새 연결",
) => {
  await user.type(await screen.findByLabelText("표시 이름"), label);
  await user.type(screen.getByLabelText("API 키"), SECRET);
};

describe("AI 공급자 설정 섹션", () => {
  it("프로파일 카드에 모델·키 꼬리·활성 배지를 그린다", async () => {
    renderSettings(<AiProviderSettings />);

    const card = await cardOf("작업용 Claude");
    expect(card.getByText("Claude (Anthropic)")).toBeInTheDocument();
    expect(card.getByText("claude-sonnet-5")).toBeInTheDocument();
    expect(card.getByText(/9876/)).toBeInTheDocument();
    expect(card.getByTestId("provider-active")).toBeInTheDocument();
  });

  it("설치되지 않은 공급자는 사유와 함께 폼에서 고를 수 없다", async () => {
    renderSettings(<AiProviderSettings />);

    const select = await screen.findByLabelText("공급자");
    const option = within(select).getByRole("option", {
      name: /Codex \(OpenAI\)/,
    });
    expect(option).toBeDisabled();

    const status = screen.getByTestId("provider-kind-openai");
    expect(within(status).getByText("설치 필요")).toBeInTheDocument();
    expect(
      within(status).getByRole("tooltip", { hidden: true }),
    ).toHaveTextContent(/설치/);
  });

  it("키를 요청 본문으로만 보내고 제출 뒤 화면에 남기지 않는다", async () => {
    const user = userEvent.setup();
    renderSettings(<AiProviderSettings />);
    await fillNewProvider(user);

    await user.click(
      screen.getByRole("button", { name: "연결 테스트 후 저장" }),
    );

    await waitFor(() => expect(createBodies).toHaveLength(1));
    expect(createBodies[0]).toMatchObject({
      kind: "anthropic",
      label: "새 연결",
      secret: SECRET,
    });
    const secretField = screen.getByLabelText("API 키");
    expect(secretField).toHaveValue("");
    expect(secretField).toHaveAttribute("type", "password");
    expect(secretField).toHaveAttribute("autocomplete", "off");
    expect(document.body.innerHTML).not.toContain(SECRET);
  });

  it("probe 실패 422를 키 필드의 한글 사유로 보여준다", async () => {
    const user = userEvent.setup();
    createReply = () =>
      HttpResponse.json(
        {
          detail: {
            code: "assistant.probe_failed",
            failure: "auth",
            message: "authentication rejected",
          },
        },
        { status: 422 },
      );
    renderSettings(<AiProviderSettings />);
    await fillNewProvider(user);

    await user.click(
      screen.getByRole("button", { name: "연결 테스트 후 저장" }),
    );

    const secretField = await screen.findByLabelText("API 키");
    await waitFor(() =>
      expect(secretField).toHaveAttribute("aria-invalid", "true"),
    );
    expect(screen.getByText(/API 키가 거부/)).toBeInTheDocument();
    expect(screen.queryByText(/authentication rejected/)).toBeNull();
    expect(document.body.innerHTML).not.toContain(SECRET);
  });

  it("base_url 거부 422를 base_url 필드 오류로 보여준다", async () => {
    const user = userEvent.setup();
    createReply = () =>
      HttpResponse.json(
        {
          detail: {
            code: "assistant.base_url_rejected",
            message: "loopback host rejected",
          },
        },
        { status: 422 },
      );
    renderSettings(<AiProviderSettings />);
    await fillNewProvider(user);
    await user.click(screen.getByRole("button", { name: "고급 설정" }));
    await user.type(screen.getByLabelText("base_url"), "https://proxy.example");

    await user.click(
      screen.getByRole("button", { name: "연결 테스트 후 저장" }),
    );

    const baseUrlField = await screen.findByLabelText("base_url");
    await waitFor(() =>
      expect(baseUrlField).toHaveAttribute("aria-invalid", "true"),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/루프백·사설 대역/);
    expect(createBodies[0]).toMatchObject({
      base_url: "https://proxy.example",
    });
  });

  it("연결 테스트 실패를 카드에 인라인 사유로 보여준다", async () => {
    const user = userEvent.setup();
    probeReply({
      ok: false,
      failure: "rate_limit",
      latency_ms: null,
      message: "rate limited",
    });
    renderSettings(<AiProviderSettings />);

    const card = await cardOf("작업용 Claude");
    await user.click(card.getByRole("button", { name: "연결 테스트" }));

    expect(await card.findByText(/요청 한도/)).toBeInTheDocument();
  });

  it("연결 테스트 성공은 지연 시간과 함께 보여준다", async () => {
    const user = userEvent.setup();
    probeReply({ ok: true, failure: null, latency_ms: 412, message: "ok" });
    renderSettings(<AiProviderSettings />);

    const card = await cardOf("작업용 Claude");
    await user.click(card.getByRole("button", { name: "연결 테스트" }));

    expect(await card.findByText(/412ms/)).toBeInTheDocument();
  });

  it("활성 전환은 다른 프로파일의 배지를 옮긴다", async () => {
    const user = userEvent.setup();
    view = {
      ...view,
      profiles: [
        profile(),
        profile({
          profile_id: "p-2",
          label: "예비 Claude",
          active: false,
          secret_tail: "1111",
        }),
      ],
    };
    renderSettings(<AiProviderSettings />);

    const spare = await cardOf("예비 Claude");
    await user.click(spare.getByRole("button", { name: "활성으로 사용" }));

    await waitFor(() => expect(activated).toEqual(["p-2"]));
    const updated = await cardOf("예비 Claude");
    expect(await updated.findByTestId("provider-active")).toBeInTheDocument();
  });

  it("삭제는 확인을 거친 뒤에만 요청한다", async () => {
    const user = userEvent.setup();
    renderSettings(<AiProviderSettings />);

    const card = await cardOf("작업용 Claude");
    await user.click(card.getByRole("button", { name: "삭제" }));
    expect(deleted).toEqual([]);
    expect(card.getByText(/키도 함께 지워집니다/)).toBeInTheDocument();

    await user.click(card.getByRole("button", { name: "삭제 확인" }));

    await waitFor(() => expect(deleted).toEqual(["p-1"]));
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "작업용 Claude" }),
      ).toBeNull(),
    );
  });

  it("삭제 확인은 취소할 수 있다", async () => {
    const user = userEvent.setup();
    renderSettings(<AiProviderSettings />);

    const card = await cardOf("작업용 Claude");
    await user.click(card.getByRole("button", { name: "삭제" }));
    await user.click(card.getByRole("button", { name: "삭제 취소" }));

    expect(deleted).toEqual([]);
    expect(card.queryByRole("button", { name: "삭제 확인" })).toBeNull();
  });

  it("프로파일이 없으면 빈 상태를 보여준다", async () => {
    view = { ...view, profiles: [] };
    renderSettings(<AiProviderSettings />);

    expect(
      await screen.findByText("연결된 공급자가 없습니다"),
    ).toBeInTheDocument();
  });
});
