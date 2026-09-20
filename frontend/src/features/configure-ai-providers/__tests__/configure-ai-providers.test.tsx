import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
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

/** 테스트가 `queryClient`를 들고 있어야 캐시에 키가 남는지 직접 볼 수 있다(리뷰 P1-1). */
const renderSettings = (ui: ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
  return queryClient;
};

/** query·mutation 캐시 전체를 직렬화한다 — mutation `variables`까지 포함해서 본다. */
const cacheDump = (queryClient: QueryClient): string =>
  JSON.stringify([
    queryClient
      .getMutationCache()
      .getAll()
      .map((entry) => entry.state),
    queryClient
      .getQueryCache()
      .getAll()
      .map((entry) => entry.state),
  ]);

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

  it("키를 요청 본문으로만 보내고 제출 뒤 화면·캐시에 남기지 않는다", async () => {
    const user = userEvent.setup();
    const queryClient = renderSettings(<AiProviderSettings />);
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
    await waitFor(() =>
      expect(screen.getByLabelText("표시 이름")).toHaveValue(""),
    );
    expect(cacheDump(queryClient)).not.toContain(SECRET);
  });

  it("거부로 끝난 제출도 키를 캐시에 남기지 않는다", async () => {
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
    const queryClient = renderSettings(<AiProviderSettings />);
    await fillNewProvider(user);

    await user.click(
      screen.getByRole("button", { name: "연결 테스트 후 저장" }),
    );

    await screen.findByText(/API 키가 거부/);
    expect(cacheDump(queryClient)).not.toContain(SECRET);
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

  it("고급 설정을 접으면 base_url을 보내지 않고 숨은 값을 드러낸다", async () => {
    const user = userEvent.setup();
    renderSettings(<AiProviderSettings />);
    await fillNewProvider(user);
    await user.click(screen.getByRole("button", { name: "고급 설정" }));
    await user.type(screen.getByLabelText("base_url"), "https://proxy.example");
    await user.click(screen.getByRole("button", { name: "고급 설정" }));

    expect(screen.queryByLabelText("base_url")).toBeNull();
    expect(screen.getByText("base_url 미적용")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "연결 테스트 후 저장" }),
    );

    await waitFor(() => expect(createBodies).toHaveLength(1));
    expect(createBodies[0]).toMatchObject({ base_url: null });
  });

  // 배지를 컨트롤에 매달지 않으면 스크린리더 사용자는 "고급 설정" 버튼만 듣고, 적어 둔 base_url이
  // 전송되지 않는다는 사실을 놓친다(B-01 리뷰 이관 항목).
  it("미적용 배지를 고급 설정 토글의 설명으로 단다", async () => {
    const user = userEvent.setup();
    renderSettings(<AiProviderSettings />);
    await fillNewProvider(user);
    const toggle = screen.getByRole("button", { name: "고급 설정" });
    expect(toggle).not.toHaveAttribute("aria-describedby");

    await user.click(toggle);
    await user.type(screen.getByLabelText("base_url"), "https://proxy.example");
    await user.click(toggle);

    const badge = screen.getByText("base_url 미적용");
    expect(toggle).toHaveAttribute("aria-describedby", badge.id);
    expect(badge.id).not.toBe("");

    // 다시 펼치면 값이 전송되므로 설명도 사라진다.
    await user.click(toggle);
    expect(toggle).not.toHaveAttribute("aria-describedby");
  });

  // 접힌 채 제출하면 base_url을 보내지 않으므로 이 거부는 보통 "제출 뒤 접었는데 그 사이 거부가
  // 도착한" 경우다. 서버가 어떤 이유로 base_url을 지목하든 고칠 칸이 화면에 있어야 한다.
  it("base_url 거부가 오면 칸이 접혀 있어도 다시 펼친다", async () => {
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
    await user.click(screen.getByRole("button", { name: "고급 설정" }));
    expect(screen.queryByLabelText("base_url")).toBeNull();

    await user.click(
      screen.getByRole("button", { name: "연결 테스트 후 저장" }),
    );
    await waitFor(() => expect(createBodies).toHaveLength(1));

    const reopened = await screen.findByLabelText("base_url");
    await waitFor(() =>
      expect(reopened).toHaveAttribute("aria-invalid", "true"),
    );
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
    // 200 경로에도 서버 원문 문구를 그리지 않는 잠금을 건다(spec D6, 리뷰 P3-3).
    expect(card.queryByText(/rate limited/)).toBeNull();
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
    const confirmButton = card.getByRole("button", { name: "삭제 확인" });
    expect(confirmButton).toHaveFocus();
    const announced = card.getByRole("alert");
    expect(announced).toHaveTextContent(/키도 함께 지워집니다/);
    expect(confirmButton).toHaveAttribute("aria-describedby", announced.id);

    await user.click(confirmButton);

    await waitFor(() => expect(deleted).toEqual(["p-1"]));
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "작업용 Claude" }),
      ).toBeNull(),
    );
  });

  // 확인 단계 진입·취소는 포커스를 옮기는데 성공 경로만 비어 있었다(B-01 리뷰 이관 항목).
  // 누른 버튼이 카드째 사라지므로 포커스는 body로 떨어지고, 키보드 사용자는 파괴적 동작 직후에
  // 목록의 어디에 있었는지 잃는다.
  it("삭제가 끝나면 포커스를 섹션 제목으로 되돌린다", async () => {
    const user = userEvent.setup();
    renderSettings(<AiProviderSettings />);

    const card = await cardOf("작업용 Claude");
    await user.click(card.getByRole("button", { name: "삭제" }));
    await user.click(card.getByRole("button", { name: "삭제 확인" }));

    await waitFor(() => expect(deleted).toEqual(["p-1"]));
    expect(
      screen.getByRole("heading", { name: "AI 어시스턴트 공급자" }),
    ).toHaveFocus();
    expect(document.body).not.toHaveFocus();
  });

  it("삭제 확인은 취소할 수 있다", async () => {
    const user = userEvent.setup();
    renderSettings(<AiProviderSettings />);

    const card = await cardOf("작업용 Claude");
    await user.click(card.getByRole("button", { name: "삭제" }));
    await user.click(card.getByRole("button", { name: "삭제 취소" }));

    expect(deleted).toEqual([]);
    expect(card.queryByRole("button", { name: "삭제 확인" })).toBeNull();
    expect(card.getByRole("button", { name: "삭제" })).toHaveFocus();
  });

  it("먼저 끝난 연결 테스트가 느린 카드의 잠금을 풀지 않는다", async () => {
    const user = userEvent.setup();
    view = {
      ...view,
      profiles: [
        profile({ label: "첫째" }),
        profile({ profile_id: "p-2", label: "둘째", active: false }),
      ],
    };
    server.use(
      http.post(
        `${API}/api/v1/assistant/providers/:profileId/test`,
        async ({ params }) => {
          await delay(String(params.profileId) === "p-1" ? 400 : 20);
          return HttpResponse.json({
            ok: true,
            failure: null,
            latency_ms: 10,
            message: "ok",
          });
        },
      ),
    );
    renderSettings(<AiProviderSettings />);

    const slow = await cardOf("첫째");
    const fast = await cardOf("둘째");
    await user.click(slow.getByRole("button", { name: "연결 테스트" }));
    await user.click(fast.getByRole("button", { name: "연결 테스트" }));

    expect(await fast.findByText(/연결 확인됨/)).toBeInTheDocument();
    expect(slow.queryByText(/연결 확인됨/)).toBeNull();
    expect(slow.getByRole("button", { name: "연결 테스트" })).toBeDisabled();
  });

  it("404가 오면 목록을 다시 읽어 유령 카드를 지운다", async () => {
    const user = userEvent.setup();
    view = {
      ...view,
      profiles: [
        profile(),
        profile({ profile_id: "p-2", label: "예비 Claude", active: false }),
      ],
    };
    server.use(
      http.post(
        `${API}/api/v1/assistant/providers/:profileId/activate`,
        ({ params }) => {
          // 다른 곳에서 이미 지워진 프로파일
          view = {
            ...view,
            profiles: view.profiles.filter(
              (item) => item.profile_id !== String(params.profileId),
            ),
          };
          return HttpResponse.json(
            {
              detail: {
                code: "assistant.provider.not_found",
                message: "gone",
              },
            },
            { status: 404 },
          );
        },
      ),
    );
    renderSettings(<AiProviderSettings />);

    const spare = await cardOf("예비 Claude");
    await user.click(spare.getByRole("button", { name: "활성으로 사용" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "예비 Claude" })).toBeNull(),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/찾을 수 없습니다/);
  });

  it("서버 거부와 입력 누락 모두 고칠 칸으로 포커스를 옮긴다", async () => {
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

    // 표시 이름이 비어 있으면 요청 없이 그 칸으로 간다.
    await user.click(
      await screen.findByRole("button", { name: "연결 테스트 후 저장" }),
    );
    expect(screen.getByLabelText("표시 이름")).toHaveFocus();
    expect(createBodies).toEqual([]);

    await user.type(screen.getByLabelText("표시 이름"), "새 연결");
    await user.type(screen.getByLabelText("API 키"), SECRET);
    await user.click(
      screen.getByRole("button", { name: "연결 테스트 후 저장" }),
    );

    await waitFor(() => expect(screen.getByLabelText("API 키")).toHaveFocus());
  });

  it("프로파일이 없으면 빈 상태를 보여준다", async () => {
    view = { ...view, profiles: [] };
    renderSettings(<AiProviderSettings />);

    expect(
      await screen.findByText("연결된 공급자가 없습니다"),
    ).toBeInTheDocument();
  });
});
