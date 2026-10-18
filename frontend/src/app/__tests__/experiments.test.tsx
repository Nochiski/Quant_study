import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import {
  act,
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
import type { BacktestRunSpec } from "../../shared/api";

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
      scope: "out_of_sample" as const,
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
  settled: status === "completed" || status === "cancelled",
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
  http.get(`${API}/api/v1/experiments/:id/parameter-map`, () => HttpResponse.json({ cells: [], no_recommendation: "no_scored_cell" })),
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
  http.get(`${API}/api/v1/experiments/:experimentId/continuous-account`, () =>
    HttpResponse.json({
      readiness: { ready: false, code: "continuous.unfinished", detail: "" },
      evaluation: null,
      status: "not_started",
      data_mode: "mock",
      interpretation: "학습 종료일 종가에 선택 가능하다고 가정한 평가입니다.",
    }),
  ),
  http.get(`${API}/api/v1/experiments/:experimentId`, ({ params }) =>
    HttpResponse.json(experiment(String(params.experimentId), "completed")),
  ),
  // 감사 기록(로드맵 M6): backend 가 시각 순으로 편 사건.
  http.get(`${API}/api/v1/experiments/:experimentId/audit`, () =>
    HttpResponse.json([
      { occurred_at: "2026-09-30T00:00:00Z", kind: "created" },
      {
        occurred_at: "2026-09-30T00:00:05Z",
        kind: "controls_changed",
        paused: true,
        priority: 3,
      },
      {
        occurred_at: "2026-09-30T00:01:00Z",
        kind: "run_finished",
        trial_index: 1,
        run_id: "run-trial-0001",
        run_status: "failed",
        code: "backtest.run.internal",
      },
    ]),
  ),
  // 남은 시간 어림의 근거가 없는 계열(로드맵 M6 ETA). 모니터 테스트가 값을 덮어쓴다.
  http.get(`${API}/api/v1/experiments/:experimentId/remaining-time`, () =>
    HttpResponse.json(null),
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

/** 실험 상세 머리의 상태 배지. 옆의 진행 수 문장에도 "완료"가 있어 배지만 본다. */
const statusBadge = (id: string) =>
  screen.getByRole("heading", { name: `실험 ${id}` }).nextElementSibling
    ?.firstElementChild;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  previews.length = 0;
});
afterAll(() => server.close());

const mount = (initial: string, seed?: BacktestRunSpec, client = new QueryClient({ defaultOptions: { queries: { retry: 0 } } })) => {
  const history = createMemoryHistory({ initialEntries: [initial] });
  if (seed !== undefined) history.replace(initial, { experimentSeed: seed });
  render(
    <App
      history={history}
      queryClient={client}
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
        // 탐색하지 않으면 계열에 잰 실행이 없는 경우를 흉내 낸다.
        time_estimate: explored
          ? { total_seconds: 250.2, sample_count: 5 }
          : null,
      });
    }),
  );

describe("experiments", () => {
  it("감사 기록 조회 실패를 알리고 재시도 후 원본 사건 순서와 키보드 탐색을 복구한다", async () => {
    let available = false;
    server.use(
      http.get(`${API}/api/v1/experiments/:experimentId/trials`, () => HttpResponse.json([])),
      http.get(`${API}/api/v1/experiments/:experimentId/walk-forward`, () => HttpResponse.json({
        windows: [], curve: [], out_of_sample_sharpe: null, retention: null, gap: "no_cell",
      })),
      http.get(`${API}/api/v1/experiments/:experimentId/audit`, () =>
        available
          ? HttpResponse.json([
              { occurred_at: "2026-09-30T00:00:00Z", kind: "created" },
              { occurred_at: "2026-09-30T00:01:00Z", kind: "run_finished", trial_index: 1,
                run_id: "run-audit-recovered", run_status: "failed", code: "backtest.run.internal" },
            ])
          : new HttpResponse(null, { status: 503 }),
      ),
    );
    mount("/research/experiments/e-run");
    expect(await screen.findByRole("alert", { name: "감사 기록" })).toHaveTextContent("감사 기록을 불러오지 못했습니다");
    expect(screen.queryByRole("table", { name: "감사 기록" })).not.toBeInTheDocument();
    available = true;
    await userEvent.setup().click(screen.getByRole("button", { name: "감사 기록 다시 불러오기" }));
    const region = await screen.findByRole("region", { name: "감사 기록" });
    region.focus();
    expect(region).toHaveFocus();
    const rows = within(region).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("실험 생성");
    expect(rows[1]).toHaveTextContent("실행 끝trial #2실패 · run-audit-recovered · backtest.run.internal");
    expect(screen.queryByRole("alert", { name: "감사 기록" })).not.toBeInTheDocument();
  });

  it("워크포워드 조회 실패를 보이고 다시 불러오면 서버 해석과 결과를 복구한다", async () => {
    let available = false;
    server.use(
      http.get(`${API}/api/v1/experiments/:experimentId`, () =>
        HttpResponse.json(experiment("e-run", "completed")),
      ),
      http.get(`${API}/api/v1/experiments/:experimentId/trials`, () =>
        HttpResponse.json([]),
      ),
      http.get(`${API}/api/v1/experiments/:experimentId/walk-forward`, () =>
        available
          ? HttpResponse.json({
              windows: [], curve: [], out_of_sample_sharpe: null,
              retention: null, gap: null, interpretation: "서버가 제공한 평가 한계",
              evaluation_basis: "window_restart_returns",
              retention_gap: "out_of_sample_unavailable", train_sharpe_mean: 0.02,
            })
          : new HttpResponse(null, { status: 503 }),
      ),
    );
    mount("/research/experiments/e-run");
    expect(await screen.findByRole("alert")).toHaveTextContent("워크포워드 결과를 불러오지 못했습니다");
    expect(screen.queryByText(/표본 밖 샤프/u)).not.toBeInTheDocument();
    available = true;
    await userEvent.setup().click(screen.getByRole("button", { name: "다시 불러오기" }));
    expect(await screen.findByText("서버가 제공한 평가 한계")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/표본 밖 샤프.*—.*유지율 —/u)).toBeVisible();
    // "—"만 두지 않고 유지율이 빈 이유를 서버 코드대로 말한다.
    expect(
      screen.getByText(/유지율 없음: 이어 붙인 검증 곡선의 샤프를 잴 수 없어요/u),
    ).toBeVisible();
    const windows = screen.getByRole("region", { name: "워크포워드 검증 창" });
    windows.focus();
    expect(windows).toHaveFocus();
    expect(within(windows).getByRole("table", { name: "워크포워드 결과" })).toBeVisible();
  });

  it.each([
    [
      "창마다 재시작하며 현금·보유를 이월하지 않는 backend 안내",
      "창마다 재시작하며 현금·보유를 이월하지 않는 backend 안내",
    ],
    [
      undefined,
      "이 서버가 결과의 계좌 연결 방식을 제공하지 않아 연속 계좌 손익인지 확인할 수 없습니다. 서버를 갱신해 평가 방식을 확인하세요.",
    ],
  ])(
    "shows the supplied interpretation or marks an older response as unknown: %s",
    async (interpretation, expected) => {
      server.use(
        http.get(`${API}/api/v1/experiments/:experimentId`, () =>
          HttpResponse.json(experiment("e-run", "completed")),
        ),
        http.get(`${API}/api/v1/experiments/:experimentId/trials`, () =>
          HttpResponse.json([]),
        ),
        http.get(`${API}/api/v1/experiments/:experimentId/walk-forward`, () =>
          HttpResponse.json({
            windows: [],
            curve: [],
            out_of_sample_sharpe: 0.1234,
            retention: 0.8,
            gap: null,
            interpretation,
            evaluation_basis: "window_restart_returns",
          }),
        ),
      );
      mount("/research/experiments/e-run");
      expect(await screen.findByText(expected)).toBeVisible();
      expect(screen.getByText(/표본 밖 샤프.*0.1234.*80.0%/u)).toBeVisible();
    },
  );

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

  it("IDE에서 보낸 현재 환경·선택값은 기존 실험 Form에서 명시 접수 전까지 보존한다", async () => {
    usePreview();
    let created = 0;
    server.use(http.post(`${API}/api/v1/experiments`, () => {
      created++;
      return HttpResponse.json(experiment("e-new", "queued"), { status: 202 });
    }));
    const seed = { ...BASE, parameter_values: { top_n: 20 }, environment: { ...ENVIRONMENT, start: "2022-01-03" } };
    mount("/research/experiments/new", seed);
    await screen.findByRole("status", { name: "시작 전 확인" });
    expect(screen.getByText(/편집기에서 보낸 저장 리비전/u)).toBeVisible();
    expect(previews.at(-1)).toMatchObject({ run: { ...seed, metric_windows: [] } });
    expect(created).toBe(0);
    await userEvent.setup().click(screen.getByRole("button", { name: "대기열에 넣기" }));
    await waitFor(() => expect(created).toBe(1));
  });

  it("서로 다른 IDE history entry는 축·제약·분할을 새로 열고 같은 entry의 조회 갱신은 편집을 유지한다", async () => {
    usePreview();
    server.use(http.get(`${API}/api/v1/strategies/:strategyId/revisions/:revision/document`, ({ params }) =>
      HttpResponse.json(params.strategyId === "s2" ? { ...document, strategy_id: "s2", spec: { ...document.spec, parameters: [{ ...document.spec.parameters[0], parameter_id: "lookback" }] } } : document),
    ));
    const client = new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
    const history = mount("/research/experiments/new", { ...BASE, parameter_values: { top_n: 20 } }, client);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "탐색: top_n" }));
    await user.type(screen.getByLabelText("값 직접 입력: top_n"), "10,20");
    await user.click(screen.getByRole("button", { name: "제약 추가" }));
    await user.type(screen.getByLabelText("조합 제약 1: 값"), "15");
    await user.selectOptions(screen.getByLabelText("창 방식"), "anchored");
    const years = screen.getByLabelText("학습(년)");
    await user.clear(years);
    await user.type(years, "5");
    await act(async () => { await client.invalidateQueries(); });
    expect(screen.getByLabelText("값 직접 입력: top_n")).toHaveValue("10,20");
    expect(screen.getByLabelText("조합 제약 1: 값")).toHaveValue("15");
    expect(screen.getByLabelText("학습(년)")).toHaveValue(5);
    const seedB: BacktestRunSpec = { ...BASE, strategy_source: { ...SAVED, strategy_id: "s2" }, parameter_values: { lookback: 10 } };
    await act(async () => { history.push("/research/experiments/new", { experimentSeed: seedB }); });
    expect(await screen.findByRole("checkbox", { name: "탐색: lookback" })).not.toBeChecked();
    expect(screen.queryByLabelText("조합 제약 1: 값")).toBeNull();
    expect(screen.getByLabelText("창 방식")).toHaveValue("rolling");
    expect(screen.getByLabelText("학습(년)")).toHaveValue(3);
    await act(async () => { history.back(); });
    expect(await screen.findByRole("checkbox", { name: "탐색: top_n" })).not.toBeChecked();
    expect(screen.getByLabelText("학습(년)")).toHaveValue(3);
    expect(screen.queryByLabelText("조합 제약 1: 값")).toBeNull();
    await act(async () => { history.forward(); });
    expect(await screen.findByRole("checkbox", { name: "탐색: lookback" })).not.toBeChecked();
  });

  it.each(["run", "from"] as const)("캐시된 %s 기반을 왕복해도 다른 기반의 축·제약·분할을 물려받지 않는다", async (kind) => {
    usePreview();
    const baseB: BacktestRunSpec = { ...BASE, strategy_source: { ...SAVED, strategy_id: "s2" } };
    server.use(
      http.get(`${API}/api/v1/backtests/:runId/request`, ({ params }) => HttpResponse.json(params.runId === "b" ? baseB : BASE)),
      http.get(`${API}/api/v1/strategies/:strategyId/revisions/:revision/document`, ({ params }) => HttpResponse.json(params.strategyId === "s2" ? { ...document, strategy_id: "s2", spec: { ...document.spec, parameters: [{ ...document.spec.parameters[0], parameter_id: "lookback" }] } } : document)),
      http.get(`${API}/api/v1/experiments/:experimentId`, ({ params }) => {
        const original = experiment(String(params.experimentId), "completed");
        return HttpResponse.json(params.experimentId === "b" ? { ...original, record: { ...original.record, run: baseB, design: { ...original.record.design, search: { axes: [{ parameter_id: "lookback", values: [10, 20] }] } } } } : original);
      }),
    );
    const history = mount(`/research/experiments/new?${kind}=a`);
    await screen.findByRole("checkbox", { name: "탐색: top_n" });
    await act(async () => { history.push(`/research/experiments/new?${kind}=b`); });
    const box = await screen.findByRole("checkbox", { name: "탐색: lookback" });
    const user = userEvent.setup();
    if (!(box as HTMLInputElement).checked) await user.click(box);
    await user.click(screen.getByRole("button", { name: "제약 추가" }));
    await user.type(screen.getByLabelText("조합 제약 1: 값"), "15");
    await user.clear(screen.getByLabelText("학습(년)"));
    await user.type(screen.getByLabelText("학습(년)"), "9");
    await act(async () => { history.back(); });
    const a = await screen.findByRole("checkbox", { name: "탐색: top_n" });
    expect(a).toHaveProperty("checked", kind === "from");
    expect(screen.queryByLabelText("조합 제약 1: 값")).toBeNull();
    expect(screen.getByLabelText("학습(년)")).toHaveValue(kind === "from" ? 2 : 3);
    await act(async () => { history.forward(); });
    await screen.findByRole("checkbox", { name: "탐색: lookback" });
    expect(screen.queryByLabelText("조합 제약 1: 값")).toBeNull();
    expect(screen.getByLabelText("학습(년)")).toHaveValue(kind === "from" ? 2 : 3);
  });

  it("명시 run URL은 남아 있는 IDE seed보다 우선한다", async () => {
    usePreview();
    mount("/research/experiments/new?run=run-1", { ...BASE, parameter_values: { top_n: 20 } });
    await screen.findByRole("status", { name: "시작 전 확인" });
    expect(previews.at(-1)).toMatchObject({ run: { ...BASE, metric_windows: [] } });
    expect(screen.queryByText(/편집기에서 보낸 저장 리비전/u)).not.toBeInTheDocument();
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
    expect(
      screen.getByText(
        "예상 소요는 이 전략 계열에 시간을 잰 완료 실행이 생기면 보입니다.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "탐색: top_n" }));
    await waitFor(() =>
      expect(summary).toHaveTextContent(
        "조합 3개 · 창 2개 · 백테스트 실행 6회 · 계열 시도 수 4회 → 7회",
      ),
    );
    // backend 어림을 근거와 함께 보이고, 초는 위로 올린다.
    expect(
      screen.getByText(
        /^예상 소요 최대 약 4분 11초 · 이 계열의 최근 완료 실행 5개를/u,
      ),
    ).toBeInTheDocument();
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

  it("sends explicit search values and a combination constraint", async () => {
    usePreview();
    const user = userEvent.setup();
    mount("/research/experiments/new?run=run-1");

    await screen.findByRole("status", { name: "시작 전 확인" });
    await user.click(screen.getByRole("checkbox", { name: "탐색: top_n" }));
    await user.type(screen.getByLabelText("값 직접 입력: top_n"), "10, 20, 30");
    await user.click(screen.getByRole("button", { name: "제약 추가" }));
    // 탐색 파라미터가 하나뿐이라 오른쪽은 상수 값으로 시작한다.
    await user.selectOptions(
      screen.getByRole("combobox", { name: "조합 제약 1: 비교" }),
      "ge",
    );
    await user.type(screen.getByLabelText("조합 제약 1: 값"), "20");

    // 허용 판정은 backend 미리 계산이 하고, 화면은 입력을 그대로 싣는다.
    await waitFor(() =>
      expect(previews.at(-1)).toMatchObject({
        search: { top_n: [10, 20, 30] },
        constraints: [
          {
            left: "top_n",
            operator: "ge",
            right_parameter: null,
            right_value: 20,
          },
        ],
      }),
    );

    // 탐색을 끄면 그 파라미터를 가리키는 제약도 함께 빠진다.
    await user.click(screen.getByRole("checkbox", { name: "탐색: top_n" }));
    await waitFor(() => expect(previews.at(-1)).toMatchObject({ search: {} }));
    expect(previews.at(-1)).not.toHaveProperty("constraints");
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

  // 초·분은 위로 올린다(어림이라 짧게 보이지 않게). 근거가 없으면 null 이고 안내를 보인다.
  it.each([
    [{ total_seconds: 3661, sample_count: 5 }, /^남은 시간 최대 약 1시간 2분/u],
    [null, /^남은 시간은 이 전략 계열에 시간을 잰 완료 실행이 생기면/u],
  ])(
    "shows the backend remaining-time estimate %j while running",
    async (estimate, text) => {
      server.use(
        http.get(`${API}/api/v1/experiments/:experimentId`, () =>
          HttpResponse.json(experiment("e-run", "running")),
        ),
        http.get(`${API}/api/v1/experiments/:experimentId/trials`, () =>
          HttpResponse.json([trialState(0, "running", null)]),
        ),
        http.get(`${API}/api/v1/experiments/:experimentId/walk-forward`, () =>
          HttpResponse.json({
            windows: [],
            curve: [],
            out_of_sample_sharpe: null,
            retention: null,
            gap: "pending",
          }),
        ),
        http.get(
          `${API}/api/v1/experiments/:experimentId/events`,
          progressStream,
        ),
        http.get(`${API}/api/v1/experiments/:experimentId/remaining-time`, () =>
          HttpResponse.json(estimate),
        ),
      );
      mount("/research/experiments/e-run");

      expect(await screen.findByText(text)).toBeInTheDocument();
    },
  );

  it("hides the remaining time of a cancelled experiment that is still winding down", async () => {
    let asked = false;
    server.use(
      http.get(`${API}/api/v1/experiments/:experimentId`, () =>
        HttpResponse.json({
          ...experiment("e-run", "cancelled"),
          settled: false,
        }),
      ),
      http.get(`${API}/api/v1/experiments/:experimentId/trials`, () =>
        HttpResponse.json([trialState(0, "running", null)]),
      ),
      http.get(`${API}/api/v1/experiments/:experimentId/walk-forward`, () =>
        HttpResponse.json({
          windows: [],
          curve: [],
          out_of_sample_sharpe: null,
          retention: null,
          gap: "pending",
        }),
      ),
      http.get(
        `${API}/api/v1/experiments/:experimentId/events`,
        progressStream,
      ),
      http.get(`${API}/api/v1/experiments/:experimentId/remaining-time`, () => {
        asked = true;
        return HttpResponse.json({ total_seconds: 0, sample_count: 5 });
      }),
    );
    mount("/research/experiments/e-run");

    expect(await screen.findByText("#1")).toBeInTheDocument();
    expect(screen.queryByText(/^남은 시간/u)).toBeNull();
    expect(asked).toBe(false);
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
    // 감사 기록은 backend 가 편 순서대로 사건·대상·내용을 보인다.
    const audit = await screen.findByRole("table", { name: "감사 기록" });
    const rows = within(audit).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("실험 생성"),
      expect.stringContaining("조작 변경—일시정지 · 우선순위 3"),
      expect.stringContaining(
        "실행 끝trial #2실패 · run-trial-0001 · backtest.run.internal",
      ),
    ]);
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
      expect(statusBadge("e-run")).toHaveTextContent(/완료$/u),
    );
  });

  // #407 리뷰 P2-2: 실험이 끝나기 전에 스트림이 프레임 없이 끊겨도(서버 재시작 등) 보조 폴링이 화면을 맞춘다.
  it.each(["running", "cancelled"] as const)(
    "keeps reading an unsettled %s experiment after its progress stream drops",
    async (initialStatus) => {
      let reads = 0;
      let trialReads = 0;
      server.use(
        http.get(`${API}/api/v1/experiments/:experimentId`, () => {
          reads += 1;
          return HttpResponse.json({
            ...experiment(
              "e-run",
              initialStatus === "cancelled"
                ? "cancelled"
                : reads < 3
                  ? "running"
                  : "completed",
            ),
            settled: reads >= 3,
          });
        }),
        http.get(`${API}/api/v1/experiments/:experimentId/trials`, () => {
          trialReads += 1;
          return HttpResponse.json([trialState(0, "running", null)]);
        }),
        http.get(`${API}/api/v1/experiments/:experimentId/walk-forward`, () =>
          HttpResponse.json({
            windows: [],
            curve: [],
            out_of_sample_sharpe: null,
            retention: null,
            gap: "pending",
          }),
        ),
        // 프레임 없이 바로 닫힌다 — 실험은 아직 돈다.
        http.get(
          `${API}/api/v1/experiments/:experimentId/events`,
          () =>
            new HttpResponse(
              new ReadableStream({
                start(controller) {
                  controller.close();
                },
              }),
              { headers: { "Content-Type": "text/event-stream" } },
            ),
        ),
      );
      mount("/research/experiments/e-run");

      await waitFor(
        () => {
          expect(reads).toBeGreaterThanOrEqual(3);
          expect(statusBadge("e-run")).toHaveTextContent(
            initialStatus === "cancelled" ? /취소됨$/u : /완료$/u,
          );
        },
        { timeout: 8_000 },
      );
      expect(reads).toBeGreaterThanOrEqual(3);
      expect(trialReads).toBeGreaterThanOrEqual(2);
    },
    12_000,
  );

  it.each(["e-late", "4a8d0176-85c8-4292-a233-5e0d158c409d"])("완료 알림은 식별자 %s를 요약해도 원본 상세로 이동하고 닫을 수 있다", async (id) => {
    const summary = id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
    let listed = 0;
    server.use(
      http.get(`${API}/api/v1/experiments`, () => {
        listed += 1;
        return HttpResponse.json({
          items: [
            experiment(id, listed === 1 ? "running" : "completed"),
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

    const done = await screen.findByText(`실험 ${summary} 완료`, undefined, {
      timeout: 5_000,
    });
    expect(screen.queryByText("실험 e-old 완료")).not.toBeInTheDocument();
    expect(done).toHaveAttribute("title", id);
    const item = done.closest("[role='status']")!;
    expect(
      within(item as HTMLElement).getByRole("link", { name: "후보 보기" }),
    ).toHaveAttribute("href", `/research/experiments/${id}`);
    await user.click(
      within(item as HTMLElement).getByRole("button", { name: "닫기" }),
    );
    expect(screen.queryByText(`실험 ${summary} 완료`)).not.toBeInTheDocument();
  }, 10_000);

  it("fills a new experiment with the constraints and exact choice values of a finished one", async () => {
    usePreview();
    const constraint = {
      left: "top_n",
      operator: "ge",
      right_parameter: null,
      right_value: 20,
    };
    const done = experiment("e-done", "completed");
    server.use(
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        () =>
          HttpResponse.json({
            ...document,
            spec: {
              ...document.spec,
              parameters: [
                ...document.spec.parameters,
                {
                  parameter_id: "horizon",
                  kind: "choice",
                  default: "20d",
                  choices: ["1d, 5d", "20d"],
                },
              ],
            },
          }),
      ),
      http.get(`${API}/api/v1/experiments/:experimentId`, () =>
        HttpResponse.json({
          ...done,
          record: {
            ...done.record,
            design: {
              ...done.record.design,
              search: {
                axes: [
                  { parameter_id: "top_n", values: [10, 20] },
                  { parameter_id: "horizon", values: ["1d, 5d"] },
                ],
                constraints: [constraint],
              },
            },
          },
        }),
      ),
    );
    mount("/research/experiments/new?from=e-done");

    // 쉼표가 든 선택지도 글자로 쪼개지지 않고 받은 그대로 다시 실린다.
    await waitFor(() =>
      expect(previews.at(-1)).toMatchObject({
        search: { top_n: [10, 20], horizon: ["1d, 5d"] },
        constraints: [constraint],
      }),
    );
    expect(screen.getByLabelText("조합 제약 1: 값")).toHaveValue("20");
    expect(
      screen.getByRole("listbox", { name: "값 직접 입력: horizon" }),
    ).toHaveValue(["0"]);
  });

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
