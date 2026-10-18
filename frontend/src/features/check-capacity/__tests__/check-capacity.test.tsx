import {
  focusManager,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
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
import {
  afterAll,
  beforeAll,
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  type BacktestRunSpec,
  type CapacityReport as Report,
  type ExperimentPreview,
} from "../../../shared/api";
import { useCapacityPreview } from "../../../entities/experiment";
import { CapacityReport } from "../ui/capacity-report";
import { CheckCapacity } from "../ui/check-capacity";

const REQUEST: BacktestRunSpec = {
  strategy_source: {
    kind: "saved_revision",
    strategy_id: "s1",
    revision: 1,
    expected_spec_hash: "pinned",
  },
  parameter_values: { scale: 0 },
};
const PREVIEW: ExperimentPreview = {
  trial_count: 12,
  trial_count_after: 12,
  new_trial_count: 0,
  combination_count: 3,
  run_count: 3,
  lineage_id: "s1",
  time_estimate: null,
  design: {
    kind: "capacity_sweep",
    measured: false,
    initial_cash: [1e6, 1e7, 1e8],
    search: { axes: [] },
    parameter_values: { scale: 0 },
    windows: [],
  },
};
const REPORT: Report = {
  limit: { amount: 1e7, best_amount: 1e6, gap: null, threshold_sharpe: 0.1 },
  points: [
    {
      trial_index: 0,
      initial_cash: 1e6,
      status: "completed",
      sharpe: 0.2,
      participation_rate: 0.01,
      impact_cost_bps: 2.3,
      rounding_error: 0.003,
      session_unfilled_ratio: 0.04,
    },
    {
      trial_index: 1,
      initial_cash: 1e7,
      status: "completed",
      sharpe: 0.15,
      participation_rate: null,
      impact_cost_bps: 4.5,
      rounding_error: null,
      session_unfilled_ratio: 0.08,
    },
    {
      trial_index: 2,
      initial_cash: 1e8,
      status: "failed",
      sharpe: null,
      participation_rate: null,
      impact_cost_bps: null,
      rounding_error: null,
      session_unfilled_ratio: null,
    },
  ],
};
const mount = (element: React.ReactNode) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {element}
    </QueryClientProvider>,
  );
const form = () => {
  const root = createRootRoute({ component: Outlet });
  const home = createRoute({
    getParentRoute: () => root,
    path: "/",
    component: () => <CheckCapacity runId="run-a" request={REQUEST} single />,
  });
  const result = createRoute({
    getParentRoute: () => root,
    path: "/research/experiments/$experimentId",
    component: () => <p>영속 용량 실험</p>,
  });
  const router = createRouter({
    routeTree: root.addChildren([home, result]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  mount(<RouterProvider router={router} />);
};
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  vi.useRealTimers();
  focusManager.setFocused(undefined);
});
afterAll(() => server.close());
const API = "http://localhost:8000/api/v1/experiments";
const servePreview = () =>
  server.use(
    http.post(`${API}/capacity/preview`, () => HttpResponse.json(PREVIEW)),
  );
const serveReport = (body: Report) => {
  const read = vi.fn();
  server.use(
    http.get(`${API}/:id/capacity`, () => {
      read();
      return HttpResponse.json(body);
    }),
  );
  return read;
};

describe("용량 확인", () => {
  it("현재 요청을 그대로 미리 계산하고 서버 N불변 확인 뒤 같은 금액으로 접수한다", async () => {
    const preview = vi.fn();
    const create = vi.fn();
    server.use(
      http.post(
        "http://localhost:8000/api/v1/experiments/capacity/preview",
        async ({ request }) => {
          preview(await request.json());
          return HttpResponse.json(PREVIEW);
        },
      ),
      http.post(
        "http://localhost:8000/api/v1/experiments/capacity",
        async ({ request }) => {
          create(await request.json());
          return HttpResponse.json({ record: { experiment_id: "capacity-1" } });
        },
      ),
    );
    form();
    const user = userEvent.setup();
    expect(preview).not.toHaveBeenCalled();
    await user.click(
      await screen.findByText("용량 확인", { selector: "summary" }),
    );
    await user.click(screen.getByRole("button", { name: "시도 수 영향 확인" }));
    expect(
      await screen.findByText("계열 시도 수 12 → 12 · 새 시도 0 · 실행 3개"),
    ).toBeVisible();
    expect(preview).toHaveBeenCalledWith({
      run: REQUEST,
      initial_cash: [1e6, 1e7, 1e8],
    });
    await user.click(screen.getByRole("button", { name: "용량 스윕 시작" }));
    await screen.findByText("영속 용량 실험");
    expect(create).toHaveBeenCalledWith({
      run: REQUEST,
      initial_cash: [1e6, 1e7, 1e8],
    });
  });
  it("금액을 바꾸면 앞 미리 계산으로 실행하지 않고 새 확인을 요구한다", async () => {
    servePreview();
    form();
    const user = userEvent.setup();
    await user.click(
      await screen.findByText("용량 확인", { selector: "summary" }),
    );
    await user.click(screen.getByRole("button", { name: "시도 수 영향 확인" }));
    await screen.findByRole("button", { name: "용량 스윕 시작" });
    await user.type(screen.getByRole("textbox"), ", 1000000000");
    expect(screen.queryByRole("button", { name: "용량 스윕 시작" })).toBeNull();
  });
  it("시작 요청이 실패한 뒤 금액을 바꾸면 이전 요청 오류를 지운다", async () => {
    servePreview();
    server.use(
      http.post(`${API}/capacity`, () =>
        HttpResponse.json({}, { status: 503 }),
      ),
    );
    form();
    const user = userEvent.setup();
    await user.click(
      await screen.findByText("용량 확인", { selector: "summary" }),
    );
    await user.click(screen.getByRole("button", { name: "시도 수 영향 확인" }));
    await user.click(
      await screen.findByRole("button", { name: "용량 스윕 시작" }),
    );
    await screen.findByRole("alert");
    await user.type(screen.getByRole("textbox"), ", 1000000000");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "용량 스윕 시작" })).toBeNull();
  });
  it("기반 시도가 없다는 서버 거절은 다음 행동으로 설명하고 접수하지 않는다", async () => {
    const create = vi.fn();
    server.use(
      http.post(`${API}/capacity/preview`, () =>
        HttpResponse.json(
          { detail: { code: "experiment.capacity.base_not_run" } },
          { status: 422 },
        ),
      ),
      http.post(`${API}/capacity`, () => {
        create();
        return HttpResponse.json({});
      }),
    );
    form();
    const user = userEvent.setup();
    await user.click(
      await screen.findByText("용량 확인", { selector: "summary" }),
    );
    await user.click(screen.getByRole("button", { name: "시도 수 영향 확인" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("먼저");
    expect(create).not.toHaveBeenCalled();
  });
  it("미리 계산503 뒤 같은 입력으로 명시 재조회하여 복구한다", async () => {
    const read = vi.fn();
    server.use(
      http.post(`${API}/capacity/preview`, () => {
        read();
        return read.mock.calls.length === 1
          ? HttpResponse.json({}, { status: 503 })
          : HttpResponse.json(PREVIEW);
      }),
    );
    form();
    const user = userEvent.setup();
    await user.click(
      await screen.findByText("용량 확인", { selector: "summary" }),
    );
    await user.click(screen.getByRole("button", { name: "시도 수 영향 확인" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "시도 수 영향 확인" }));
    await screen.findByRole("button", { name: "용량 스윕 시작" });
    expect(read).toHaveBeenCalledTimes(2);
  });
  it("금액별 숫자·상태·legacy부재를 보이고 실패를0으로 채우지 않는다", async () => {
    serveReport(REPORT);
    mount(<CapacityReport experimentId="capacity-1" settled />);
    const table = await screen.findByRole("table");
    const order = screen.getByRole("list", { name: "곡선 왼쪽부터 시험 순번" });
    expect(
      within(order)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(
      REPORT.points.map(
        (point) => `${point.initial_cash.toLocaleString()} KRW`,
      ),
    );
    expect(
      within(table).getByRole("columnheader", {
        name: "순번",
      }),
    ).toBeVisible();
    expect(
      within(table).getByRole("row", { name: /1,000,000 /u }).firstElementChild,
    ).toHaveTextContent("1");
    expect(within(table).getByText("0.2000")).toBeVisible();
    expect(within(table).getByText("2.3000")).toBeVisible();
    const failed = within(table).getByRole("row", { name: /100,000,000/u });
    expect(failed).toHaveTextContent("실패");
    expect(failed).not.toHaveTextContent("0.0000");
    expect(screen.getByText(/서버가 판정한 한계 금액/u)).toHaveTextContent(
      "10,000,000",
    );
    expect(screen.getByText(/최종 잔량 비율이 아니며/u)).toBeVisible();
  });
  it.each([
    "cancelled",
    "pending",
    "no_positive_sharpe",
    "beyond_tested",
  ] as const)("한계 %s의 서버 사유를 읽는다", async (gap) => {
    const read = serveReport({
      ...REPORT,
      limit: { ...REPORT.limit, amount: null, gap },
    });
    mount(<CapacityReport experimentId="capacity-1" settled />);
    await screen.findByRole("table");
    expect(screen.getByText(/서버가 판정한 한계 금액/u)).toHaveTextContent("—");
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  });
  it("부모가 먼저 완료되어도 부분 보고서의 마지막 진행 금액을 자동으로 다시 읽는다", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const read = vi.fn();
    const partial: Report = {
      ...REPORT,
      points: REPORT.points.map((point, index) =>
        index === 0 ? { ...point, status: "running", sharpe: null } : point,
      ),
    };
    server.use(
      http.get(`${API}/:id/capacity`, () => {
        read();
        return HttpResponse.json(
          read.mock.calls.length === 1 ? partial : REPORT,
        );
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = (settled: boolean) => (
      <QueryClientProvider client={client}>
        <CapacityReport experimentId="capacity-1" settled={settled} />
      </QueryClientProvider>
    );
    const mounted = render(view(false));
    await screen.findByRole("table");
    expect(screen.queryByText("0.2000")).toBeNull();
    expect(read).toHaveBeenCalledTimes(1);
    mounted.rerender(view(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(await screen.findByText("0.2000")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(read).toHaveBeenCalledTimes(2);
  });
  it("성공 보고서 뒤410은 prefix무효화·재마운트·포커스에도 자동 재전송하지 않는다", async () => {
    const read = vi.fn();
    server.use(
      http.get(`${API}/:id/capacity`, () => {
        read();
        return read.mock.calls.length === 1
          ? HttpResponse.json(REPORT)
          : HttpResponse.json({}, { status: 410 });
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    let focused = 0;
    const Control = () => {
      const query = useQuery({
        queryKey: ["focus-control"],
        queryFn: async () => {
          focused++;
          return "포커스 대조";
        },
        staleTime: 0,
      });
      return <p>{query.data}</p>;
    };
    const view = (visible: boolean) => (
      <QueryClientProvider client={client}>
        <Control />
        {visible && <CapacityReport experimentId="capacity-1" settled />}
      </QueryClientProvider>
    );
    const mounted = render(view(true));
    await screen.findByText("0.2000");
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "용량 다시 조회" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("table")).toBeNull();
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["experiments"] });
    });
    mounted.rerender(view(false));
    mounted.rerender(view(true));
    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(focused).toBeGreaterThan(1));
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("성공한 미리 계산 뒤410도 같은 요청 무효화·재마운트로 다시 읽지 않는다", async () => {
    const read = vi.fn();
    server.use(
      http.post(`${API}/capacity/preview`, () => {
        read();
        return read.mock.calls.length === 1
          ? HttpResponse.json(PREVIEW)
          : HttpResponse.json({}, { status: 410 });
      }),
    );
    const request = { run: REQUEST, initial_cash: [1e6, 1e7, 1e8] };
    const Probe = () => {
      const query = useCapacityPreview(request);
      return (
        <>
          {query.isError ? (
            <p role="alert">미리 계산 오류</p>
          ) : query.data ? (
            <p>미리 계산 성공</p>
          ) : null}
          <button onClick={() => void query.refetch()}>
            미리 계산 다시 읽기
          </button>
        </>
      );
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = (visible: boolean) => (
      <QueryClientProvider client={client}>
        {visible && <Probe />}
      </QueryClientProvider>
    );
    const mounted = render(view(true));
    await screen.findByText("미리 계산 성공");
    await userEvent.setup().click(screen.getByRole("button"));
    await screen.findByRole("alert");
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["experiments"] });
    });
    mounted.rerender(view(false));
    mounted.rerender(view(true));
    await screen.findByRole("alert");
    expect(read).toHaveBeenCalledTimes(2);
  });
  it("결과410은 자동 조회와 명시 재조회를 막는다", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const read = vi.fn();
    server.use(
      http.get(`${API}/:id/capacity`, () => {
        read();
        return HttpResponse.json({}, { status: 410 });
      }),
    );
    mount(<CapacityReport experimentId="capacity-1" settled={false} />);
    await screen.findByRole("alert");
    expect(screen.queryByRole("button")).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(read).toHaveBeenCalledTimes(1);
  });
});
