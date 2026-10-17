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
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type {
  Experiment,
  ExperimentPreview,
  ParameterMap,
  WalkForwardReport,
} from "../../../shared/api";
import { RobustnessCheck } from "../ui/robustness-check";

/**
 * 결과 검증 카드의 "튼튼한지 확인하기"(US-DM-11·US-CS-09). 실험 설계·시도 수·창 선택·고원 판정은 서버
 * 응답이고, 화면은 그 값을 그대로 보이는지와 확인 창의 흐름을 본다.
 */
const API = "http://localhost:8000/api/v1";
const RUN_ID = "run-1";

const AXES = [
  { parameter_id: "scale", values: [0, 1] },
  { parameter_id: "mode", values: ["a", "b"] },
];
const WINDOW = {
  train_start: "2020-01-02",
  train_end: "2022-12-22",
  test_start: "2023-01-02",
  test_end: "2023-12-28",
};

const experiment = (
  overrides: Partial<Experiment> = {},
  axes = AXES,
): Experiment => ({
  record: {
    experiment_id: "exp-1",
    created_at: "2026-10-17T00:00:00Z",
    run: { environment: null },
    split: {
      mode: "rolling",
      train_years: 3,
      test_years: 1,
      embargo_sessions: 5,
      selection_rule: "neighbor_mean_sharpe_max",
    },
    design: { search: { axes }, parameter_values: {}, windows: [WINDOW] },
    controls: { paused: false, priority: 1 },
    source_run_id: RUN_ID,
  },
  status: "completed",
  trial_counts: {},
  selections: [],
  finished: true,
  settled: true,
  ...overrides,
});

const PREVIEW: ExperimentPreview = {
  design: { search: { axes: AXES }, parameter_values: {}, windows: [WINDOW] },
  combination_count: 4,
  run_count: 5,
  lineage_id: "s-1",
  trial_count: 1,
  new_trial_count: 3,
  trial_count_after: 4,
  time_estimate: null,
};

const WALK_FORWARD: WalkForwardReport = {
  windows: [],
  curve: [
    { session: "2023-01-02", equity: 1, benchmark_equity: 1 },
    { session: "2023-01-03", equity: 1.02, benchmark_equity: 1.01 },
  ],
  out_of_sample_sharpe: 0.0412,
  retention: 0.663,
  gap: null,
  interpretation: "창마다 같은 초기자본으로 다시 시작해 이은 곡선입니다.",
};

const PARAMETER_MAP: ParameterMap = {
  cells: [
    {
      grid_index: [0, 0],
      verdict: "scored",
      score: 0.03,
      plateau_score: 0.02,
      sensitivity: null,
      sensitivity_cell: null,
    },
    {
      grid_index: [1, 0],
      verdict: "recommended",
      score: 0.05,
      plateau_score: 0.04,
      sensitivity: 0.12,
      sensitivity_cell: [0, 0],
    },
  ],
};

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

const mount = (single = true) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const routes = [
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: () => <RobustnessCheck runId={RUN_ID} single={single} />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/research/experiments/$experimentId",
      component: () => <p>실험 화면</p>,
    }),
  ];
  const router = createRouter({
    routeTree: rootRoute.addChildren(routes),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
};

const listChecks = (items: () => Experiment[]) =>
  http.get(`${API}/backtests/:runId/robustness-checks`, () =>
    HttpResponse.json(items()),
  );

describe("튼튼한지 확인하기", () => {
  it("실험이 없으면 두 항목이 실험이 필요하다고 말하고, 확인 창이 새 시도 수부터 보인 뒤 실험을 시작한다", async () => {
    let created = false;
    const posted: string[] = [];
    server.use(
      listChecks(() =>
        created
          ? [experiment({ status: "running", finished: false, settled: false })]
          : [],
      ),
      http.post(`${API}/backtests/:runId/robustness-check/preview`, () =>
        HttpResponse.json(PREVIEW),
      ),
      http.post(`${API}/backtests/:runId/robustness-check`, ({ params }) => {
        posted.push(String(params.runId));
        created = true;
        return HttpResponse.json(
          experiment({ status: "queued", finished: false, settled: false }),
          { status: 202 },
        );
      }),
    );
    mount();
    const outOfSample = await screen.findByRole("region", {
      name: "표본 밖 성과 · 워크포워드",
    });
    expect(outOfSample).toHaveTextContent("실험이 필요해요");
    expect(
      screen.getByRole("region", { name: "파라미터 고원" }),
    ).toHaveTextContent("실험이 필요해요");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "튼튼한지 확인하기" }));
    const dialog = await screen.findByRole("dialog", {
      name: "튼튼한지 확인하기",
    });
    // 확인 창은 새로 세는 시도 수를 맨 먼저 보인다.
    const trials = await within(dialog).findByText(
      "새로 세는 시도 3개 · 계열 시도 수 1 → 4",
    );
    expect(dialog.querySelector("p")).toBe(trials);
    expect(dialog).toHaveTextContent("조합 4개 · 실행 5개");
    expect(dialog).toHaveTextContent(
      "지금 값과 양옆을 볼 파라미터: scale 0, 1 · mode a, b",
    );
    const windows = within(dialog).getByRole("table", { name: "학습·검증 창" });
    expect(within(windows).getByText("2023-01-02 ~ 2023-12-28")).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "닫기" })).toHaveFocus();
    // 포커스는 창 안 두 버튼 사이에서만 돈다.
    await user.tab();
    expect(within(dialog).getByRole("button", { name: "시작" })).toHaveFocus();
    await user.tab();
    expect(within(dialog).getByRole("button", { name: "닫기" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "시작" })).toHaveFocus();

    await user.click(within(dialog).getByRole("button", { name: "시작" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted).toEqual([RUN_ID]);
    // 시작한 뒤에는 버튼이 사라지므로 포커스가 항목 제목으로 간다.
    expect(
      screen.getByRole("heading", { name: "튼튼한지 확인" }),
    ).toHaveFocus();
    expect(
      await screen.findAllByText(
        "확인 실험이 아직 끝나지 않았어요 · 실행 중. 끝나면 이 항목이 채워져요.",
      ),
    ).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "튼튼한지 확인하기" }),
    ).toBeNull();
  });

  it("끝난 실험의 표본 밖 곡선·유지율과 추천 후보를 서버 값 그대로 채운다", async () => {
    server.use(
      listChecks(() => [experiment()]),
      http.get(`${API}/experiments/:id/walk-forward`, () =>
        HttpResponse.json(WALK_FORWARD),
      ),
      http.get(`${API}/experiments/:id/parameter-map`, () =>
        HttpResponse.json(PARAMETER_MAP),
      ),
    );
    mount();
    const outOfSample = await screen.findByRole("region", {
      name: "표본 밖 성과 · 워크포워드",
    });
    expect(
      await within(outOfSample).findByText(
        "표본 밖 샤프(세션 단위) 0.0412 · 유지율 66.3%",
      ),
    ).toBeVisible();
    expect(
      within(outOfSample).getByRole("img", {
        name: /검증 구간만 이어 붙인 곡선/u,
      }),
    ).toBeVisible();
    expect(outOfSample).toHaveTextContent(
      "창마다 같은 초기자본으로 다시 시작해 이은 곡선입니다.",
    );
    const plateau = screen.getByRole("region", { name: "파라미터 고원" });
    expect(
      await within(plateau).findByText("추천 후보: scale 1 · mode a"),
    ).toBeVisible();
    expect(plateau).toHaveTextContent(
      "고원 점수(이웃 평균 샤프, 세션 단위) 0.0400 · 칸 점수 0.0500 · 민감도(설정을 조금 옮긴 칸의 점수 하락) 12.0%",
    );
    expect(
      screen.getByRole("link", { name: "확인 실험 자세히 보기" }),
    ).toHaveAttribute("href", "/research/experiments/exp-1");
    expect(
      screen.queryByRole("button", { name: "튼튼한지 확인하기" }),
    ).toBeNull();
  });

  it('유지율이 비면 "—" 옆에 서버가 준 이유와 학습 샤프 평균을 말한다', async () => {
    server.use(
      listChecks(() => [experiment()]),
      http.get(`${API}/experiments/:id/walk-forward`, () =>
        HttpResponse.json({
          ...WALK_FORWARD,
          retention: null,
          train_sharpe_mean: -0.0123,
          retention_gap: "non_positive_train_sharpe",
        }),
      ),
      http.get(`${API}/experiments/:id/parameter-map`, () =>
        HttpResponse.json(PARAMETER_MAP),
      ),
    );
    mount();
    const outOfSample = await screen.findByRole("region", {
      name: "표본 밖 성과 · 워크포워드",
    });
    expect(
      await within(outOfSample).findByText(
        "표본 밖 샤프(세션 단위) 0.0412 · 유지율 —",
      ),
    ).toBeVisible();
    expect(outOfSample).toHaveTextContent(
      "유지율 없음: 고른 설정의 학습 샤프 평균이 -0.0123(0 이하)이라",
    );
    expect(outOfSample).not.toHaveTextContent("1에 가까울수록");
  });

  it("추천할 칸이 없으면 서버가 준 이유(손실 나는 설정)를 말한다", async () => {
    server.use(
      listChecks(() => [experiment()]),
      http.get(`${API}/experiments/:id/walk-forward`, () =>
        HttpResponse.json(WALK_FORWARD),
      ),
      http.get(`${API}/experiments/:id/parameter-map`, () =>
        HttpResponse.json({
          cells: PARAMETER_MAP.cells.map((cell) => ({
            ...cell,
            verdict: "scored",
            score: -0.06,
          })),
          no_recommendation: "non_positive_score",
        }),
      ),
    );
    mount();
    const plateau = await screen.findByRole("region", {
      name: "파라미터 고원",
    });
    expect(
      await within(plateau).findByText(/추천할 설정이 없어요/u),
    ).toBeVisible();
    expect(plateau).toHaveTextContent(
      "모든 칸의 학습 샤프나 그 이웃 평균이 0 이하예요",
    );
    expect(plateau).not.toHaveTextContent("추천 후보");
  });

  it("팩터 계산이 읽지 않는 파라미터는 확인 창이 지금 값에 둔다고 말한다", async () => {
    server.use(
      listChecks(() => []),
      http.post(`${API}/backtests/:runId/robustness-check/preview`, () =>
        HttpResponse.json({
          ...PREVIEW,
          design: { ...PREVIEW.design, search: { axes: [] } },
          combination_count: 1,
          unread_parameters: ["scale"],
        }),
      ),
    );
    mount();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "튼튼한지 확인하기" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(
        "팩터 계산이 읽지 않아 지금 값에 둔 파라미터: scale. 바꿔도 결과가 같아 시도 수만 늘어요.",
      ),
    ).toBeVisible();
    expect(dialog).toHaveTextContent(
      "결과를 바꾸는 숫자 파라미터가 없어 조합 하나로 표본 밖 성과만 봐요.",
    );
  });

  it("파라미터가 없으면 고원 대신 그 사정을 말하고 지도를 묻지 않는다", async () => {
    server.use(
      listChecks(() => [experiment({}, [])]),
      http.get(`${API}/experiments/:id/walk-forward`, () =>
        HttpResponse.json({
          ...WALK_FORWARD,
          curve: [],
          gap: "pending",
          retention: null,
          out_of_sample_sharpe: null,
        }),
      ),
    );
    mount();
    expect(
      await screen.findByText(
        "이 전략에는 결과를 바꾸는 숫자 파라미터가 없어 고원을 볼 수 없어요. 표본 밖 성과만 확인해요.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("미리 계산이 거절되면 번역한 이유를 보이고 시작 버튼이 없으며 Esc 로 닫힌다", async () => {
    server.use(
      listChecks(() => []),
      http.post(`${API}/backtests/:runId/robustness-check/preview`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "experiment.robustness.period_too_short",
              message: "start=2021-01-04",
            },
          },
          { status: 422 },
        ),
      ),
    );
    mount();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "튼튼한지 확인하기" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(
        /튼튼한지 확인하려면 실행 기간이 2년 이상이어야 합니다/u,
      ),
    ).toBeVisible();
    expect(within(dialog).queryByRole("button", { name: "시작" })).toBeNull();
    // 시작 버튼이 없으면 Tab 이 닫기 버튼에 머문다.
    await user.tab();
    expect(within(dialog).getByRole("button", { name: "닫기" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    // 닫으면 포커스가 연 버튼으로 돌아온다.
    expect(
      screen.getByRole("button", { name: "튼튼한지 확인하기" }),
    ).toHaveFocus();
  });

  it("실험이 돌린 실행에는 버튼 대신 실험 화면에서 보라고 말한다", async () => {
    server.use(listChecks(() => []));
    mount(false);
    expect(
      await screen.findAllByText(
        "실험이 돌린 실행이라 확인 결과는 그 실험 화면에서 봐요.",
      ),
    ).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "튼튼한지 확인하기" }),
    ).toBeNull();
  });
});
