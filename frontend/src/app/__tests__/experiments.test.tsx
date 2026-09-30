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
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { App } from "../app";

// 실험 화면(검증 랩 V5-01, US-SM-14·15). 상태·진행 수·슬롯·우선순위 상한·조합 수·시도 수 변화는 backend
// 응답이고, 화면은 그 값을 보이고 요청을 싣기만 한다.
const API = "http://localhost:8000";

const SAVED = {
  kind: "saved_revision",
  strategy_id: "s1",
  revision: 2,
  expected_spec_hash: "a".repeat(64),
} as const;
const ENVIRONMENT = {
  start: "2021-01-04",
  end: "2025-12-30",
  universe_id: "krx.common-stock",
};
const BASE = {
  strategy_source: SAVED,
  environment: ENVIRONMENT,
  metric_windows: [
    {
      scope: "out_of_sample",
      start: "2025-01-02",
      end: "2025-12-30",
      label: "OOS",
    },
  ],
};
const SPLIT = {
  mode: "anchored",
  train_years: 2,
  test_years: 1,
  embargo_sessions: 3,
  selection_rule: "train_sharpe_max",
};
const WINDOWS = [
  {
    train_start: "2021-01-04",
    train_end: "2022-12-26",
    test_start: "2023-01-02",
    test_end: "2023-12-28",
  },
  {
    train_start: "2021-01-04",
    train_end: "2023-12-21",
    test_start: "2024-01-02",
    test_end: "2024-12-30",
  },
];

const experiment = (
  id: string,
  status: string,
  controls = { paused: false, priority: 1 },
) => ({
  record: {
    experiment_id: id,
    created_at: "2026-09-30T00:00:00Z",
    run: { ...BASE, metric_windows: [] },
    split: SPLIT,
    design: {
      search: { axes: [{ parameter_id: "top_n", values: [10, 20] }] },
      parameter_values: {},
      windows: WINDOWS,
      measured: true,
    },
    controls,
  },
  status,
  trial_counts: { completed: 3, running: 1, failed: 1, queued: 3 },
  selections: [],
  finished: status === "completed" || status === "cancelled",
});

const document = {
  strategy_id: "s1",
  revision: 2,
  schema_version: "1.2",
  format: "yaml",
  source: 'schema_version: "1.2"\ntitle: 탐색\n',
  source_hash: "b".repeat(64),
  spec: {
    identity: { strategy_id: "s1", revision: 2, schema_version: "1.2" },
    title: "탐색",
    parameters: [
      {
        parameter_id: "top_n",
        kind: "integer",
        default: 20,
        minimum: 10,
        maximum: 30,
        step: 10,
      },
    ],
  },
  spec_hash: "a".repeat(64),
  origin: "document",
  generated: false,
  created_at: "2026-09-04T00:00:00Z",
};

const server = setupServer(
  http.get(`${API}/api/v1/strategy-drafts/:draftId`, () =>
    HttpResponse.json({ detail: { code: "x" } }, { status: 404 }),
  ),
  http.get(`${API}/api/v1/backtests`, () =>
    HttpResponse.json({ items: [], total: 0, offset: 0, limit: 25 }),
  ),
  http.get(`${API}/api/v1/experiments`, () =>
    HttpResponse.json({
      items: [
        experiment("e-run", "running"),
        experiment("e-top", "queued", { paused: false, priority: 5 }),
        experiment("e-done", "completed"),
      ],
      next_after: null,
      slots: { total: 3, running: 1 },
      max_priority: 5,
    }),
  ),
  http.get(`${API}/api/v1/experiments/:experimentId`, ({ params }) =>
    HttpResponse.json(experiment(String(params.experimentId), "completed")),
  ),
  http.get(`${API}/api/v1/backtests/:runId/request`, ({ params }) =>
    HttpResponse.json(
      params.runId === "run-inline"
        ? {
            ...BASE,
            strategy_source: {
              kind: "inline_draft",
              strategy: document.spec,
            },
          }
        : BASE,
    ),
  ),
  http.get(
    `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
    () => HttpResponse.json(document),
  ),
);

const previews: unknown[] = [];

const trialState = (
  index: number,
  status: string,
  attempt: Record<string, unknown> | null,
  retryable = false,
) => ({
  trial: {
    index,
    grid_index: [index],
    parameter_values: { top_n: [10, 20][index % 2] },
    window: WINDOWS[Math.floor(index / 2)],
  },
  status,
  attempts:
    attempt === null
      ? []
      : [
          {
            experiment_id: "e-run",
            trial_index: index,
            attempt: 1,
            created_at: "2026-09-30T00:00:00Z",
            ...attempt,
          },
        ],
  awaiting_recovery: false,
  retryable,
  selectable: status === "completed",
});

/** 진행 스트림: 프레임 하나를 보내고 닫는다(실험이 끝났다). */
const progressStream = () =>
  new HttpResponse(
    new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'id: 0\nevent: progress\ndata: {"status":"completed","trial_counts":{"completed":4}}\n\n',
          ),
        );
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  previews.length = 0;
});
afterAll(() => server.close());

const mount = (initial: string) => {
  const history = createMemoryHistory({ initialEntries: [initial] });
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

const usePreview = (rejected = false) =>
  server.use(
    http.post(`${API}/api/v1/experiments/preview`, async ({ request }) => {
      const body = (await request.json()) as {
        search: Record<string, unknown>;
      };
      previews.push(body);
      if (rejected)
        return HttpResponse.json(
          {
            detail: {
              code: "experiment.base.unsaved",
              message: "base is an inline draft",
            },
          },
          { status: 422 },
        );
      const explored = "top_n" in body.search;
      return HttpResponse.json({
        design: {
          search: {
            axes: explored
              ? [{ parameter_id: "top_n", values: [10, 20, 30] }]
              : [],
          },
          parameter_values: {},
          windows: WINDOWS,
          measured: true,
        },
        combination_count: explored ? 3 : 1,
        run_count: explored ? 6 : 2,
        lineage_id: "s1",
        trial_count: 4,
        new_trial_count: explored ? 3 : 1,
        trial_count_after: explored ? 7 : 5,
      });
    }),
  );

describe("experiments", () => {
  it("lists the queue with backend status, counts and slots and sends the controls", async () => {
    const controls: unknown[] = [];
    const cancelled: string[] = [];
    server.use(
      http.patch(
        `${API}/api/v1/experiments/:experimentId/controls`,
        async ({ params, request }) => {
          controls.push([params.experimentId, await request.json()]);
          return HttpResponse.json(experiment("e-run", "paused"));
        },
      ),
      http.post(
        `${API}/api/v1/experiments/:experimentId/cancel`,
        ({ params }) => {
          cancelled.push(String(params.experimentId));
          return HttpResponse.json(experiment("e-run", "cancelled"));
        },
      ),
    );
    const user = userEvent.setup();
    const history = mount("/research/backtests");
    await user.click(await screen.findByRole("link", { name: "실험" }));
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/experiments"),
    );

    expect(
      await screen.findByText("동시 실행 슬롯 1 / 3 사용 중"),
    ).toBeInTheDocument();
    const row = (id: string) => screen.getByText(id).closest("tr")!;
    expect(row("e-run")).toHaveTextContent("실행 중");
    expect(row("e-run")).toHaveTextContent("완료 3 / 8 · 실행 중 1 · 실패 1");
    expect(row("e-run")).toHaveTextContent("s1 · v2");
    // 끝난 실험은 조작 없이 같은 설정으로 다시 만들기만 있다.
    expect(within(row("e-done")).queryByRole("button")).not.toBeInTheDocument();
    expect(
      within(row("e-done")).getByRole("link", {
        name: "같은 설정으로 새 실험",
      }),
    ).toHaveAttribute("href", "/research/experiments/new?from=e-done");
    // 우선순위 상한은 목록 응답이 준다.
    expect(
      within(row("e-top")).getByRole("button", { name: "우선순위 올리기" }),
    ).toBeDisabled();

    await user.click(
      within(row("e-run")).getByRole("button", { name: "일시정지" }),
    );
    await user.click(
      within(row("e-run")).getByRole("button", { name: "우선순위 올리기" }),
    );
    await waitFor(() =>
      expect(controls).toEqual([
        ["e-run", { paused: true }],
        ["e-run", { priority: 2 }],
      ]),
    );
    // 취소는 되돌릴 수 없어 한 번 더 확인한다.
    await user.click(
      within(row("e-run")).getByRole("button", { name: "취소" }),
    );
    expect(cancelled).toEqual([]);
    await user.click(
      within(row("e-run")).getByRole("button", { name: "실험 취소 확인" }),
    );
    await waitFor(() => expect(cancelled).toEqual(["e-run"]));
  });

  // #402 리뷰 P2-1·P3-2: 뒤쪽 실험도 커서로 열어 조작하고, 조작 거절은 코드 번역으로 말한다.
  // #402 리뷰 P2-1: 21번째 뒤로 밀린 일시정지 실험도 "더 보기"로 읽어 재개한다.
  it("loads more experiments with the response cursor and resumes a paused one past the first page", async () => {
    const afters: (string | null)[] = [];
    const controls: unknown[] = [];
    server.use(
      http.get(`${API}/api/v1/experiments`, ({ request }) => {
        const after = new URL(request.url).searchParams.get("after");
        afters.push(after);
        return HttpResponse.json({
          items:
            after === null
              ? Array.from({ length: 20 }, (_, index) =>
                  experiment(`e-${index + 1}`, "running"),
                )
              : [
                  experiment("e-old", "paused", {
                    paused: true,
                    priority: 1,
                  }),
                ],
          next_after: after === null ? "e-20" : null,
          slots: { total: 3, running: 2 },
          max_priority: 5,
        });
      }),
      http.patch(
        `${API}/api/v1/experiments/:experimentId/controls`,
        async ({ params, request }) => {
          controls.push([params.experimentId, await request.json()]);
          return HttpResponse.json(experiment("e-old", "queued"));
        },
      ),
    );
    const user = userEvent.setup();
    mount("/research/experiments");

    expect(await screen.findByText("e-20")).toBeInTheDocument();
    expect(screen.queryByText("e-old")).toBeNull();
    await user.click(screen.getByRole("button", { name: "실험 더 보기" }));
    const row = (await screen.findByText("e-old")).closest("tr")!;
    expect(afters.slice(0, 2)).toEqual([null, "e-20"]);
    expect(screen.queryByRole("button", { name: "실험 더 보기" })).toBeNull();
    await user.click(within(row).getByRole("button", { name: "재개" }));
    await waitFor(() =>
      expect(controls).toEqual([["e-old", { paused: false }]]),
    );
  });

  // #402 리뷰 P3-2·P3-3: 조작 거절도 코드로 번역한다 — 이미 완료한 실험의 취소는 backend 가 거절한다.
  it("translates a refused control with its code", async () => {
    server.use(
      http.post(`${API}/api/v1/experiments/:experimentId/cancel`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "experiment.cancel.completed",
              message: "experiment_id=e-run",
            },
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    mount("/research/experiments");

    const row = (await screen.findByText("e-run")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "취소" }));
    await user.click(
      within(row).getByRole("button", { name: "실험 취소 확인" }),
    );
    expect(
      await screen.findByText(
        "이미 완료한 실험이라 취소하지 않았습니다. 결과는 그대로 남아 있습니다.",
      ),
    ).toBeInTheDocument();
  });

  it("points to the backtest result when there is no experiment yet", async () => {
    server.use(
      http.get(`${API}/api/v1/experiments`, () =>
        HttpResponse.json({
          items: [],
          next_after: null,
          slots: { total: 3, running: 0 },
          max_priority: 5,
        }),
      ),
    );
    mount("/research/experiments");

    expect(await screen.findByText("아직 실험이 없습니다")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "백테스트 이력으로" }),
    ).toHaveAttribute("href", "/research/backtests");
  });

  it("previews a new experiment from a run and queues it", async () => {
    usePreview();
    let created: unknown = null;
    server.use(
      http.post(`${API}/api/v1/experiments`, async ({ request }) => {
        created = await request.json();
        return HttpResponse.json(experiment("e-new", "queued"), {
          status: 202,
        });
      }),
    );
    const user = userEvent.setup();
    const history = mount("/research/experiments/new?run=run-1");

    const summary = await screen.findByRole("status", { name: "시작 전 확인" });
    expect(summary).toHaveTextContent(
      "조합 1개 · 창 2개 · 백테스트 실행 2회 · 계열 시도 수 4회 → 5회",
    );
    // 기반이 무엇인지 한 줄로 보인다(#402 리뷰 P2-2).
    expect(
      screen.getByText("기반 s1 · v2 · 연구 기간 2021-01-04 ~ 2025-12-30"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "탐색: top_n" }));
    await waitFor(() =>
      expect(summary).toHaveTextContent(
        "조합 3개 · 창 2개 · 백테스트 실행 6회 · 계열 시도 수 4회 → 7회",
      ),
    );
    expect(screen.getByText("10, 20, 30 (3)")).toBeInTheDocument();
    expect(screen.getByText("2024-01-02 ~ 2024-12-30")).toBeInTheDocument();
    // 기반 요청의 지표 창은 싣지 않고, 탐색 값을 정하지 않으면 null 로 정의의 격자를 편다.
    expect(previews.at(-1)).toEqual({
      run: { ...BASE, metric_windows: [] },
      search: { top_n: null },
      split: {
        mode: "rolling",
        train_years: 3,
        test_years: 1,
        embargo_sessions: 5,
        selection_rule: "neighbor_mean_sharpe_max",
      },
    });

    await user.click(screen.getByRole("button", { name: "대기열에 넣기" }));
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/experiments"),
    );
    expect(created).toEqual(previews.at(-1));
  });

  it("refuses an unsaved base with the translated reason", async () => {
    usePreview(true);
    mount("/research/experiments/new?run=run-inline");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "실험은 저장한 전략 리비전으로만 만들 수 있습니다.",
    );
    expect(
      screen.getByRole("button", { name: "대기열에 넣기" }),
    ).toBeDisabled();
  });

  it("monitors trials, retries a failed one and reads again when the progress stream moves", async () => {
    let reads = 0;
    let retried: string | null = null;
    server.use(
      http.get(`${API}/api/v1/experiments/:experimentId`, () => {
        reads += 1;
        return HttpResponse.json(
          experiment("e-run", reads === 1 ? "running" : "completed"),
        );
      }),
      http.get(`${API}/api/v1/experiments/:experimentId/trials`, () =>
        HttpResponse.json([
          trialState(0, "completed", { run_id: "run-trial-0000" }),
          trialState(1, "failed", { run_id: "run-trial-0001" }, true),
          trialState(
            2,
            "failed",
            {
              run_id: null,
              error_code: "backtest.strategy.stale",
              error: "revision changed",
            },
            true,
          ),
          trialState(3, "queued", null),
        ]),
      ),
      http.get(`${API}/api/v1/experiments/:experimentId/walk-forward`, () =>
        HttpResponse.json({
          windows: [
            {
              pick: {
                experiment_id: "e-run",
                window_index: 0,
                attempt: 1,
                trial_index: 0,
                train_sharpe: 0.0512,
                created_at: "2026-09-30T00:00:00Z",
                run_id: "run-test-00000",
              },
              run_status: "completed",
              run_error_code: null,
              gap: null,
            },
            {
              pick: {
                experiment_id: "e-run",
                window_index: 1,
                attempt: 1,
                trial_index: null,
                train_sharpe: null,
                created_at: "2026-09-30T00:00:00Z",
              },
              run_status: null,
              run_error_code: null,
              gap: "no_cell",
            },
          ],
          curve: [],
          out_of_sample_sharpe: null,
          retention: null,
          gap: "no_cell",
        }),
      ),
      http.get(
        `${API}/api/v1/experiments/:experimentId/events`,
        progressStream,
      ),
      http.post(
        `${API}/api/v1/experiments/:experimentId/trials/:trialIndex/retry`,
        ({ params }) => {
          retried = String(params.trialIndex);
          return HttpResponse.json(trialState(1, "queued", null));
        },
      ),
    );
    const user = userEvent.setup();
    mount("/research/experiments/e-run");

    const trial = async (label: string) =>
      (await screen.findByText(label)).closest("tr")!;
    expect(await trial("#2")).toHaveTextContent("실패");
    expect(await trial("#1")).toHaveTextContent("top_n 10");
    expect(
      within(await trial("#1")).getByRole("link", { name: "run-trial-00" }),
    ).toHaveAttribute("href", "/research/backtests/run-trial-0000");
    expect(await trial("#3")).toHaveTextContent(
      "저장된 revision이 그사이 바뀌었습니다.",
    );
    // 다시 실행은 backend 가 `retryable` 로 준 trial 에만 있다.
    expect(within(await trial("#1")).queryByRole("button")).toBeNull();
    expect(within(await trial("#4")).queryByRole("button")).toBeNull();
    await user.click(
      within(await trial("#2")).getByRole("button", { name: "다시 실행" }),
    );
    await waitFor(() => expect(retried).toBe("1"));
    // 워크포워드: 창마다 고른 칸·학습 점수·검증 실행, 요약이 비는 이유는 backend `gap` 을 번역한다.
    const gap = "학습 결과가 있는 칸이 없는 창이 있어 요약을 비웠습니다.";
    expect(screen.getByText(gap).tagName).toBe("P");
    expect(
      screen.getByRole("cell", { name: new RegExp(gap, "u") }),
    ).toBeInTheDocument();
    expect(screen.getByText("0.0512")).toBeInTheDocument();
    // 진행 스트림의 프레임과 닫힘이 상세를 다시 읽게 한다.
    await waitFor(() => expect(reads).toBeGreaterThanOrEqual(2));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "실험 e-run" }).nextElementSibling,
      ).toHaveTextContent("완료"),
    );
  });

  it("announces an experiment that finishes while the app is open, but not one done before", async () => {
    let listed = 0;
    server.use(
      http.get(`${API}/api/v1/experiments`, () => {
        listed += 1;
        return HttpResponse.json({
          items: [
            experiment("e-late", listed === 1 ? "running" : "completed"),
            experiment("e-old", "completed"),
          ],
          next_after: null,
          slots: { total: 3, running: 0 },
          max_priority: 5,
        });
      }),
    );
    const user = userEvent.setup();
    mount("/research/backtests");

    const done = await screen.findByText("실험 e-late 완료", undefined, {
      timeout: 5_000,
    });
    expect(screen.queryByText("실험 e-old 완료")).not.toBeInTheDocument();
    const item = done.closest("[role='status']")!;
    expect(
      within(item as HTMLElement).getByRole("link", { name: "후보 보기" }),
    ).toHaveAttribute("href", "/research/experiments/e-late");
    await user.click(
      within(item as HTMLElement).getByRole("button", { name: "닫기" }),
    );
    expect(screen.queryByText("실험 e-late 완료")).not.toBeInTheDocument();
  }, 10_000);

  it("fills a new experiment with the search space and split of a finished one", async () => {
    usePreview();
    mount("/research/experiments/new?from=e-done");

    await waitFor(() =>
      expect(previews.at(-1)).toEqual({
        run: { ...BASE, metric_windows: [] },
        search: { top_n: [10, 20] },
        split: SPLIT,
      }),
    );
    expect(screen.getByRole("checkbox", { name: "탐색: top_n" })).toBeChecked();
    expect(screen.getByLabelText("학습(년)")).toHaveValue(2);
    expect(screen.getByRole("combobox", { name: "창 방식" })).toHaveValue(
      "anchored",
    );
  });
});
