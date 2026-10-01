import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
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
} from "vitest";

import { App } from "../app";
import type { BacktestRunResult } from "../../entities/backtest";

/**
 * 결과 화면의 AI 패널(결과 설명 spec R1·R4). 진입점은 완료된 결과에만 있고, 대화는 실행에 붙으며
 * 문서 컨텍스트를 싣지 않는다. 핵심 지표는 영어 이름 옆에 쉬운 이름과 뜻을 보인다.
 */
const API = "http://localhost:8000";
const RUN_ID = "run-1";

const metric = (
  metricId: string,
  label: string,
  unit: "percent" | "ratio" | "count",
  value: number,
) => ({
  definition: {
    metric_id: metricId,
    label,
    category: "return" as const,
    unit,
    higher_is_better: true,
    nullable: false,
  },
  value: {
    metric_id: metricId,
    value,
    scope: "full" as const,
    sample_count: 40,
  },
});

const METRICS = [
  metric("total_return", "Total return", "percent", 0.3412),
  metric("sharpe", "Sharpe ratio", "ratio", 0.87),
  metric("max_drawdown", "Maximum drawdown", "percent", -0.2231),
  metric("calmar", "Calmar ratio", "ratio", 0.41),
  metric("turnover", "Turnover", "ratio", 0.35),
  metric("trade_count", "Closed trades", "count", 4),
];

const resultFixture = (): BacktestRunResult => ({
  manifest: {
    run_id: RUN_ID,
    created_at: "2026-09-27T00:00:00Z",
    completed_at: "2026-09-27T00:00:05Z",
    engine_core: "rust",
    engine_version: "test",
    run_fingerprint: "f".repeat(32),
    run_spec: { benchmark_security_id: "005930", metric_windows: [] },
    strategy_hash: "a".repeat(64),
    data_snapshot_id: "snapshot",
    target_tape_hash: "b".repeat(64),
    metric_registry_version: "metric-registry-v1",
    initial_cash: 100_000_000,
    annualization_days: 252,
    fee_bps: 15,
    slippage_bps: 10,
    participation_rate: 0.1,
    environment: {
      start: "2023-01-02",
      end: "2026-04-30",
      universe_id: "krx.common-stock",
    },
    environment_hash: "c".repeat(64),
    strategy_provenance: {
      kind: "inline_draft",
      spec_hash: "a".repeat(64),
      schema_version: "1.1",
    },
    warnings: [],
  },
  metric_definitions: METRICS.map((item) => item.definition),
  metrics: METRICS.map((item) => item.value),
  series: {
    equity: [],
    drawdown: [],
    monthly_returns: [],
    rolling_sharpe: [],
    rolling_sharpe_window_sessions: 126,
  },
  artifacts: {
    snapshots: [],
    positions: [],
    orders: [],
    fills: [],
    costs: [],
    trades: [],
  },
});

let runStatus: "completed" | "running";
let createdSessions: Record<string, unknown>[];
let startedTurns: Record<string, unknown>[];
let listedRunIds: (string | null)[];
let turnSessionIds: string[];

const server = setupServer(
  http.get(`${API}/api/v1/backtests/:runId`, ({ params }) =>
    HttpResponse.json({
      run_id: params.runId,
      status: runStatus,
      progress: runStatus === "completed" ? 1 : 0.5,
      stage: runStatus === "completed" ? "completed" : "engine",
      message: runStatus === "completed" ? "Run completed" : "Running",
      created_at: "2026-09-27T00:00:00Z",
      updated_at: "2026-09-27T00:00:01Z",
    }),
  ),
  // 결과 화면은 실행 종류를 서버 판정으로 읽는다(단일 실행에만 실험 만들기 링크).
  http.get(`${API}/api/v1/backtests/:runId/summary`, () =>
    HttpResponse.json({ kind: "single" }),
  ),
  http.get(`${API}/api/v1/backtests/:runId/request`, () =>
    HttpResponse.json({ core: "rust", metric_windows: [] }),
  ),
  http.get(`${API}/api/v1/backtests/:runId/result`, () =>
    HttpResponse.json(resultFixture()),
  ),
  http.get(`${API}/api/v1/assistant/providers`, () =>
    HttpResponse.json({
      kinds: [{ kind: "anthropic", installed: true, default_model: "m" }],
      profiles: [
        {
          profile_id: "p-1",
          kind: "anthropic",
          label: "Claude",
          model: "m",
          base_url: null,
          secret_tail: "0001",
          active: true,
          created_at: "2026-09-27T00:00:00Z",
        },
      ],
    }),
  ),
  // 실행 id가 빠진 목록 조회는 서버가 422로 거절한다(참조가 비어 있다). 그 모양을 그대로 흉내 낸다.
  http.get(`${API}/api/v1/assistant/sessions`, ({ request }) => {
    const runId = new URL(request.url).searchParams.get("run_id");
    listedRunIds.push(runId);
    return runId === null
      ? HttpResponse.json(
          { detail: { code: "assistant.document_ref_invalid", message: "" } },
          { status: 422 },
        )
      : HttpResponse.json([]);
  }),
  http.post(`${API}/api/v1/assistant/sessions`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    createdSessions.push(body);
    return HttpResponse.json(
      {
        // 실행마다 다른 대화가 만들어져야 한다. 같은 id를 돌려주면 대화가 섞인 것을 가려낼 수 없다.
        session_id: `s-${createdSessions.length}`,
        title: "결과",
        provider_profile_id: "p-1",
        document_ref: body.document_ref,
        created_at: "2026-09-27T00:00:00Z",
      },
      { status: 201 },
    );
  }),
  http.post(
    `${API}/api/v1/assistant/sessions/:sessionId/turns`,
    async ({ params, request }) => {
      startedTurns.push((await request.json()) as Record<string, unknown>);
      turnSessionIds.push(String(params.sessionId));
      return HttpResponse.json(
        {
          turn_id: `t-${startedTurns.length}`,
          session_id: String(params.sessionId),
          status: "running",
          accepted_sequence: -1,
          started_at: "2026-09-27T00:00:01Z",
        },
        { status: 202 },
      );
    },
  ),
  http.get(`${API}/api/v1/assistant/sessions/:sessionId`, () =>
    HttpResponse.json({
      session: {
        session_id: "s-1",
        title: "결과",
        provider_profile_id: "p-1",
        document_ref: { run_id: RUN_ID },
        created_at: "2026-09-27T00:00:00Z",
      },
      messages: [],
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
  http.get(`${API}/api/v1/assistant/sessions/:sessionId/events`, () =>
    HttpResponse.json(
      { detail: { code: "assistant.no_running_turn", message: "" } },
      { status: 409 },
    ),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
beforeEach(() => {
  runStatus = "completed";
  createdSessions = [];
  startedTurns = [];
  listedRunIds = [];
  turnSessionIds = [];
});

const mount = () => {
  const history = createMemoryHistory({
    initialEntries: [`/research/backtests/${RUN_ID}`],
  });
  render(
    <App
      history={history}
      queryClient={
        new QueryClient({ defaultOptions: { queries: { retry: 0 } } })
      }
      operationsEnabled={false}
    />,
  );
  return history;
};

const ask = async (
  user: ReturnType<typeof userEvent.setup>,
  panel: HTMLElement,
  text: string,
) => {
  await user.type(
    within(panel).getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" }),
    `${text}{Enter}`,
  );
};

describe("백테스트 결과 화면의 AI 패널", () => {
  it("핵심 지표마다 영어 이름 옆에 쉬운 이름과 한 줄 뜻을 보인다", async () => {
    mount();

    const highlights = await screen.findByRole("region", {
      name: "핵심 성과 지표",
    });
    const sharpe = within(highlights)
      .getByText("Sharpe ratio", { exact: true })
      .closest("div");
    expect(sharpe).not.toBeNull();
    expect(sharpe).toHaveTextContent(/^Sharpe ratio0\.8700샤프 비율 흔들림/u);
    expect(
      within(highlights).getByText("최대 낙폭", { selector: "dfn" }),
    ).toBeInTheDocument();
  });

  it("완료된 결과에서 AI에게 물으면 실행에 붙은 대화가 문서 없이 시작된다", async () => {
    const user = userEvent.setup();
    mount();

    const toggle = await screen.findByRole("button", {
      name: "AI에게 결과 묻기",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // 열기 전에는 패널이 없고 어시스턴트 질의도 나가지 않는다.
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeNull();

    await user.click(toggle);
    const panel = await screen.findByRole("complementary", {
      name: "AI 어시스턴트",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      await within(panel).findByText(/이 실행의 숫자를 쉬운 말로 풀어 줍니다/u),
    ).toBeInTheDocument();

    await user.type(
      within(panel).getByRole("textbox", {
        name: "어시스턴트에게 보낼 메시지",
      }),
      "이 결과 좋은 거야?{Enter}",
    );

    await waitFor(() => expect(startedTurns).toHaveLength(1));
    expect(createdSessions[0].document_ref).toEqual({ run_id: RUN_ID });
    expect(listedRunIds.length).toBeGreaterThan(0);
    expect(listedRunIds.every((runId) => runId === RUN_ID)).toBe(true);
    expect(startedTurns[0]).toEqual({ text: "이 결과 좋은 거야?" });
    // 결과 세션은 설명 전용이라 제안 적용 버튼이 없다(spec R5).
    expect(
      within(panel).queryByRole("button", { name: "문서에 적용" }),
    ).toBeNull();
  });

  it("사이드바를 닫으면 패널이 숨고 초점이 여는 버튼으로 돌아온다", async () => {
    const user = userEvent.setup();
    mount();
    const toggle = await screen.findByRole("button", {
      name: "AI에게 결과 묻기",
    });
    await user.click(toggle);
    const panel = await screen.findByRole("complementary", {
      name: "AI 어시스턴트",
    });

    await user.click(
      await within(panel).findByRole("button", { name: "사이드바 닫기" }),
    );

    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeNull();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("아직 끝나지 않은 실행에는 AI 진입점이 없다", async () => {
    runStatus = "running";
    mount();

    expect(
      await screen.findByRole("status", { name: "실행 상태" }),
    ).toHaveTextContent("running");
    expect(
      screen.queryByRole("button", { name: "AI에게 결과 묻기" }),
    ).toBeNull();
  });

  it("다른 실행으로 옮기면 패널이 닫히고 다시 열면 그 실행의 새 대화로 묻는다", async () => {
    // 재실행은 같은 라우트라 페이지가 재사용된다. 패널 상태를 되돌리지 않으면 새 실행 화면의 질문이
    // 앞 실행의 대화로 가서 다른 실행의 결과를 설명한다(D 스택 리뷰 P3-6).
    const user = userEvent.setup();
    const history = mount();
    await user.click(
      await screen.findByRole("button", { name: "AI에게 결과 묻기" }),
    );
    await ask(
      user,
      await screen.findByRole("complementary", { name: "AI 어시스턴트" }),
      "첫 실행은 어때?",
    );
    await waitFor(() => expect(turnSessionIds).toEqual(["s-1"]));

    history.push("/research/backtests/run-2");

    await waitFor(() =>
      expect(
        screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
      ).toBeNull(),
    );
    const toggle = await screen.findByRole("button", {
      name: "AI에게 결과 묻기",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await user.click(toggle);
    const panel = await screen.findByRole("complementary", {
      name: "AI 어시스턴트",
    });
    expect(
      await within(panel).findByText(/이 실행의 숫자를 쉬운 말로 풀어 줍니다/u),
    ).toBeInTheDocument();
    await ask(user, panel, "두 번째 실행은 어때?");

    await waitFor(() => expect(turnSessionIds).toEqual(["s-1", "s-2"]));
    expect(createdSessions.map((body) => body.document_ref)).toEqual([
      { run_id: RUN_ID },
      { run_id: "run-2" },
    ]);
    expect(listedRunIds).toContain("run-2");
  });
});
