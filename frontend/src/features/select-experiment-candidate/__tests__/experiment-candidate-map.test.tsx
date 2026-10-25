import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type {
  Experiment,
  ExperimentTrialState,
  ParameterMap,
  ExperimentSelection,
} from "../../../shared/api/generated";
import { ExperimentCandidateMap } from "../ui/experiment-candidate-map";

const API = "http://localhost:8000/api/v1/experiments";
const MAP: ParameterMap = {
  no_recommendation: null,
  cells: [
    {
      grid_index: [0],
      verdict: "recommended",
      score: 0.2,
      plateau_score: 0.6,
      sensitivity: 0.12,
      sensitivity_cell: [1],
    },
    {
      grid_index: [1],
      verdict: "peak",
      score: 1.2,
      plateau_score: 0.01,
      sensitivity: 0.93,
      sensitivity_cell: [0],
    },
    {
      grid_index: [2],
      verdict: "failed",
      score: null,
      plateau_score: null,
      sensitivity: null,
      sensitivity_cell: null,
    },
    {
      grid_index: [3],
      verdict: "unscored",
      score: null,
      plateau_score: null,
      sensitivity: null,
      sensitivity_cell: null,
    },
  ],
};
const experiment = (id = "a"): Experiment => ({
  record: {
    experiment_id: id,
    created_at: "2026-10-18T00:00:00Z",
    controls: { paused: false, priority: 1 },
    run: {},
    split: null,
    design: {
      search: { axes: [{ parameter_id: "scale", values: [0, 1, 2, 3] }] },
      parameter_values: { fixed: false },
      windows: [],
    },
  },
  finished: true,
  settled: true,
  status: "completed",
  trial_counts: { completed: 2, failed: 1, cancelled: 1 },
  selections: [],
});
const trials = (): ExperimentTrialState[] =>
  MAP.cells.map((cell, index) => ({
    trial: {
      index,
      grid_index: cell.grid_index,
      parameter_values: { scale: index, fixed: false },
      window: null,
    },
    status: index < 2 ? "completed" : index === 2 ? "failed" : "cancelled",
    selectable: index < 2,
    retryable: index === 2,
    awaiting_recovery: false,
    bankrupt: index === 2,
    attempts: [
      {
        experiment_id: "a",
        trial_index: index,
        attempt: 1,
        created_at: "2026-10-18T00:00:00Z",
        run_id: `run-${index}`,
      },
    ],
  }));
const selection = (id = "a", trialIndex = 0): ExperimentSelection => ({
  experiment_id: id,
  trial_index: trialIndex,
  strategy_id: "strategy",
  revision: 1,
  parameter_values: { scale: trialIndex, fixed: false },
  reason: "이웃 점수와 민감도를 비교했습니다.",
  selected_at: "2026-10-18T03:00:00Z",
  trial_count: 21,
  deflated_sharpe: 0.4212345,
  ledger_representative_sharpe: 0.12345,
});
const server = setupServer(
  http.get(`${API}/:id/parameter-map`, () => HttpResponse.json(MAP)),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  vi.useRealTimers();
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

const mount = (initial = experiment(), initialTrials = trials()) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  let change!: (props: {
    experiment: Experiment;
    trials: ExperimentTrialState[];
  }) => void;
  const Surface = () => {
    const [props, setProps] = useState({
      experiment: initial,
      trials: initialTrials,
    });
    change = setProps;
    return <ExperimentCandidateMap {...props} />;
  };
  const root = createRootRoute({ component: () => <Outlet /> });
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: "/",
        component: Surface,
      }),
      createRoute({
        getParentRoute: () => root,
        path: "/research/backtests/$runId",
        component: () => <p>실행 결과 검증 카드</p>,
      }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return {
    client,
    router,
    change: (next: Experiment, states = initialTrials) =>
      act(() => change({ experiment: next, trials: states })),
  };
};
const grid = () => screen.getByRole("region", { name: "파라미터 조합 지도" });
const comparison = () => screen.getByRole("region", { name: "비교 중인 칸" });
const choose = async (name = /scale 0 · 추천 후보/u) => {
  const button = await screen.findByRole("button", { name });
  await userEvent.setup().click(button);
  return comparison();
};

describe("파라미터 지도와 명시적 후보 선택", () => {
  it("최고 칸을 다시 고르지 않고 서버의 추천·봉우리·실패·점수 없음과 원수치를 읽는다", async () => {
    mount();
    const detail = await choose();
    expect(
      within(grid()).getByRole("button", { name: /scale 1 · 봉우리/u }),
    ).toHaveTextContent("0.0100");
    expect(detail).toHaveTextContent("0.2");
    expect(detail).toHaveTextContent("0.6");
    expect(detail).toHaveTextContent("0.12");
    expect(screen.getByRole("heading", { name: "비교 중인 칸" })).toHaveFocus();
    await userEvent
      .setup()
      .click(within(detail).getByRole("button", { name: "비교 칸 보기" }));
    expect(detail).toHaveTextContent("scale 1 · 봉우리");
    expect(detail).toHaveTextContent("0.93");
    await choose(/scale 2 · 실패/u);
    expect(detail).toHaveTextContent("파산한 학습 실행");
    expect(within(detail).queryByRole("form")).toBeNull();
    expect(
      within(grid()).getByRole("button", { name: /scale 3 · 점수 없음/u }),
    ).toBeEnabled();
  });

  it("점수가 0 이하인 지도는 서버의 추천 없음 사유를 보이고 0을 점수 없음으로 바꾸지 않는다", async () => {
    server.use(
      http.get(`${API}/:id/parameter-map`, () =>
        HttpResponse.json({
          no_recommendation: "non_positive_score",
          cells: [
            {
              ...MAP.cells[0],
              verdict: "scored",
              score: 0,
              plateau_score: -0.2,
              sensitivity: null,
              sensitivity_cell: null,
            },
          ],
        }),
      ),
    );
    mount();
    await choose(/scale 0 · 점수 있음/u);
    expect(
      screen.getByText(/추천할 설정이 없어요.*모두 0 이하예요/u),
    ).toBeVisible();
    expect(
      within(grid()).queryByRole("button", { name: /추천 후보/u }),
    ).toBeNull();
    expect(
      within(comparison()).getByText("0", { selector: "dd" }),
    ).toBeVisible();
  });

  it("학습 창과 이유를 명시해 기록하고 당시 통계에서 해당 결과로 이동한다", async () => {
    const submitted: unknown[] = [];
    server.use(
      http.post(`${API}/a/selections`, async ({ request }) => {
        submitted.push(await request.json());
        return HttpResponse.json(selection());
      }),
    );
    const old = {
      ...selection(),
      selected_at: "2026-10-17T03:00:00Z",
      reason: "과거 선택 원문",
      trial_count: 7,
      deflated_sharpe: 0.2,
    };
    const initial = { ...experiment(), selections: [old] };
    const view = mount(initial);
    const detail = await choose();
    const form = within(detail).getByRole("form", { name: "후보 선택 기록" });
    const submit = within(form).getByRole("button", {
      name: "이유와 후보 기록",
    });
    expect(submit).toBeDisabled();
    const user = userEvent.setup();
    await user.selectOptions(
      within(form).getByLabelText("후보로 기록할 학습 창"),
      "0",
    );
    // 기록 버튼 바로 위에 되돌릴 수 없음과 실행·자금 투입 없음을 말한다(RD-10-B).
    expect(form).toHaveTextContent(
      "기록은 되돌릴 수 없어요. 비교한 점수와 한계를 이유에 남기세요. 자동매매 실행이나 자금 투입은 하지 않아요.",
    );
    // 이유가 비면 기록하지 않고 이유 칸 아래에 알린다.
    await user.click(submit);
    expect(within(form).getByRole("alert")).toHaveTextContent("선택 이유를 적어 주세요.");
    expect(within(form).getByLabelText("선택 이유")).toHaveAttribute("aria-invalid", "true");
    expect(submitted).toEqual([]);
    await user.type(
      within(form).getByLabelText("선택 이유"),
      "이웃 점수와 민감도를 비교했습니다.",
    );
    await user.click(submit);
    await waitFor(() =>
      expect(submitted).toEqual([
        { trial_index: 0, reason: "이웃 점수와 민감도를 비교했습니다." },
      ]),
    );
    await screen.findByText("후보 선택이 기록되었습니다.");
    expect(detail).toHaveTextContent("21");
    expect(detail).toHaveTextContent("0.4212345");
    expect(detail).toHaveTextContent("현재 계열 원장 조회값과 다릅니다");
    await user.click(
      screen.getByText(/후보 선택 기록과 당시 통계/u, { selector: "summary" }),
    );
    expect(screen.getByText("과거 선택 원문")).toBeVisible();
    expect(screen.getByText("7", { selector: "dd" })).toBeVisible();
    expect(old).toEqual(initial.selections[0]);
    await user.click(
      within(detail).getByRole("link", {
        name: "선택한 실행의 결과 검증 카드 보기",
      }),
    );
    await waitFor(() =>
      expect(view.router.state.location.pathname).toBe(
        "/research/backtests/run-0",
      ),
    );
  });

  it.each([
    "experiment.selection.not_finished",
    "experiment.selection.not_completed",
  ])(
    "표시 뒤 서버의 %s 거절은 이유를 보존하고 코드 문장과 접힌 원문을 보인다",
    async (code) => {
      server.use(
        http.post(`${API}/a/selections`, () =>
          HttpResponse.json(
            { detail: { code, message: "unfinished_trials=1" } },
            { status: 409 },
          ),
        ),
      );
      mount();
      await choose();
      const user = userEvent.setup();
      await user.selectOptions(
        screen.getByLabelText("후보로 기록할 학습 창"),
        "0",
      );
      await user.type(screen.getByLabelText("선택 이유"), "비교한 이유 원문");
      await user.click(
        screen.getByRole("button", { name: "이유와 후보 기록" }),
      );
      const alert = await screen.findByRole("alert");
      expect(alert).not.toHaveTextContent("잠시 뒤");
      expect(alert).toHaveTextContent(
        code.endsWith("not_finished") ? "모두 끝난 뒤" : "완료된 trial",
      );
      expect(screen.getByLabelText("선택 이유")).toHaveValue(
        "비교한 이유 원문",
      );
      expect(
        within(alert).getByText("서버 사유").closest("details"),
      ).not.toHaveAttribute("open");
      expect(alert).toHaveTextContent("unfinished_trials=1");
    },
  );

  it("아직 도는 실험은 완료한 칸도 후보 기록 폼 대신 남은 trial을 보여준다", async () => {
    const running = {
      ...experiment(),
      status: "running" as const,
      finished: false,
      settled: false,
    };
    const states = trials().map((state) => ({ ...state, selectable: false }));
    states[1] = { ...states[1], status: "running", selectable: false };
    mount(running, states);
    await choose();
    expect(comparison()).toHaveTextContent("남은 trial 1개");
    expect(within(comparison()).queryByRole("form")).toBeNull();
  });

  it("다축은 고정값을 바꾸며 각 축 6개씩 읽고 다른 실험으로 전환하면 칸·이유를 넘기지 않는다", async () => {
    const many = experiment();
    many.record.design.search.axes = [
      { parameter_id: "scale", values: [0, 1, 2, 3, 4, 5, 6] },
      { parameter_id: "mode", values: [false, true] },
      { parameter_id: "window", values: [3, 5] },
    ];
    server.use(
      http.get(`${API}/:id/parameter-map`, () =>
        HttpResponse.json({
          cells: [
            { ...MAP.cells[0], grid_index: [0, 0, 0] },
            { ...MAP.cells[1], grid_index: [6, 0, 0] },
            { ...MAP.cells[0], grid_index: [0, 0, 1], verdict: "scored" },
          ],
        }),
      ),
    );
    const view = mount(many, []);
    await choose(/scale 0 · mode false · window 3/u);
    const legend = screen.getByText(/^색 범례 · 실험 전체 상대 범위/u, {
      selector: "summary",
    });
    const globalRange = legend.textContent;
    expect(
      within(grid()).queryByRole("button", { name: /scale 6/u }),
    ).toBeNull();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "가로축 다음 6개" }));
    expect(
      within(grid()).getByRole("button", {
        name: /scale 6 · mode false · window 3/u,
      }),
    ).toBeVisible();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "가로축 이전 6개" }));
    await userEvent
      .setup()
      .selectOptions(screen.getByLabelText("window · 고정값"), "1");
    expect(legend.textContent).toBe(globalRange);
    expect(
      within(grid()).getByRole("button", {
        name: /scale 0 · mode false · window 5 · 점수 있음/u,
      }),
    ).toBeVisible();
    view.change(experiment("b"));
    await waitFor(() =>
      expect(comparison()).toHaveTextContent("지도에서 비교할 칸을 고르세요"),
    );
  });

  it("서버가 허용한 완료 학습 창은 뒤따르는 검증 실행의 진행 상태로 다시 차단하지 않는다", async () => {
    mount({
      ...experiment(),
      status: "running",
      finished: false,
      settled: false,
    });
    await choose();
    expect(
      within(comparison()).getByRole("form", { name: "후보 선택 기록" }),
    ).toBeVisible();
  });

  it("진행 중에도 조회 실패 뒤 폴링을 멈추고 축 없는 한 칸을 명시 재조회로 복구한다", async () => {
    const single = experiment();
    single.settled = false;
    single.finished = false;
    single.status = "running";
    single.record.design.search.axes = [];
    let reads = 0;
    server.use(
      http.get(`${API}/:id/parameter-map`, () =>
        ++reads === 1
          ? HttpResponse.json({}, { status: 503 })
          : HttpResponse.json({ cells: [{ ...MAP.cells[0], grid_index: [] }] }),
      ),
    );
    vi.useFakeTimers();
    const { client } = mount(single, []);
    await act(async () => {
      await vi.waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    });
    await act(() => vi.advanceTimersByTimeAsync(6_000));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["experiments"] });
    });
    expect(reads).toBe(1);
    vi.useRealTimers();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "지도 다시 조회" }));
    await choose(/기본 설정 한 칸 · 추천 후보/u);
    expect(comparison()).toHaveTextContent(
      "지금 선택할 수 있는 완료 학습 창이 없습니다",
    );
    expect(reads).toBe(2);
  });

  it("같은 유효값은 한 색이라고 안내하고 결측은 0으로 바꾸지 않으며 색 비교 기준을 명시 전환한다", async () => {
    server.use(
      http.get(`${API}/:id/parameter-map`, () =>
        HttpResponse.json({
          cells: [{ ...MAP.cells[0], plateau_score: null }],
        }),
      ),
    );
    mount();
    await choose();
    expect(
      screen.getByText(/색으로 표시할 유효한 점수가 없습니다/u),
    ).toBeVisible();
    expect(within(grid()).getByRole("button")).toHaveTextContent("—");
    const user = userEvent.setup();
    await user.selectOptions(
      screen.getByLabelText("색으로 비교할 값"),
      "score",
    );
    expect(within(grid()).getByRole("button")).toHaveTextContent("0.2000");
    await user.click(
      screen.getByText(/^색 범례 · 실험 전체 상대 범위/u, {
        selector: "summary",
      }),
    );
    expect(
      screen.getByText(/유효값이 모두 같아 한 색으로 표시합니다/u),
    ).toBeVisible();
    expect(
      screen.getByText(
        /진한 색은 양수 성과·미래 수익·추천 여부를 뜻하지 않습니다/u,
      ),
    ).toBeVisible();
  });

  it("이전 실험에서 진행한 선택 응답이 도착해도 새 실험의 이유와 성공 기록을 덮지 않는다", async () => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    let submitted = false,
      returned = false;
    server.use(
      http.post(`${API}/a/selections`, async () => {
        submitted = true;
        await pending;
        returned = true;
        return HttpResponse.json(selection());
      }),
    );
    const view = mount();
    await choose();
    const user = userEvent.setup();
    await user.selectOptions(
      screen.getByLabelText("후보로 기록할 학습 창"),
      "0",
    );
    await user.type(
      screen.getByLabelText("선택 이유"),
      "이전 실험의 선택 이유",
    );
    await user.click(screen.getByRole("button", { name: "이유와 후보 기록" }));
    await waitFor(() => expect(submitted).toBe(true));
    view.change(experiment("b"));
    await choose();
    expect(screen.getByLabelText("선택 이유")).toHaveValue("");
    release();
    await waitFor(() => expect(returned).toBe(true));
    expect(screen.queryByText("후보 선택이 기록되었습니다.")).toBeNull();
    expect(screen.getByLabelText("선택 이유")).toHaveValue("");
  });

  it("옛 실험의 늦은 지도 응답을 새 실험의 칸과 점수로 표시하지 않는다", async () => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    let requested = false,
      returned = false;
    server.use(
      http.get(`${API}/:id/parameter-map`, async ({ params }) => {
        if (params.id === "a") {
          requested = true;
          await pending;
          returned = true;
          return HttpResponse.json(MAP);
        }
        return HttpResponse.json({ cells: [{ ...MAP.cells[0], score: 0.85 }] });
      }),
    );
    const view = mount();
    await waitFor(() => expect(requested).toBe(true));
    expect(screen.getByRole("status")).toHaveTextContent(
      "파라미터 지도를 읽고 있습니다",
    );
    view.change(experiment("b"));
    await choose();
    expect(comparison()).toHaveTextContent("0.85");
    release();
    await waitFor(() => expect(returned).toBe(true));
    expect(
      within(comparison()).getByText("0.85", { selector: "dd" }),
    ).toBeVisible();
    expect(
      within(comparison()).queryByText("0.2", { selector: "dd" }),
    ).toBeNull();
  });
});
