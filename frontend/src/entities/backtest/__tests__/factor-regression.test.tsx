import {
  focusManager,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterAll,
  beforeAll,
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { type RunFactorRegression } from "../../../shared/api";
import { BacktestFactorRegression } from "../ui/backtest-factor-regression";

const report = (runId = "run-a"): RunFactorRegression => ({
  run_id: runId,
  run_fingerprint: `fingerprint-${runId}`,
  data_snapshot_id: "synthetic-snapshot",
  factor_registry_version: "factors-v1",
  factor_evaluation_version: "evaluator-v1",
  momentum_factor_id: "price.momentum_12_1",
  value_factor_id: "financial.book_to_market",
  trial_count: 24,
  alpha_t_threshold: 3,
  report: {
    annualization_days: 260,
    observation_count: 100,
    strategy_period_count: 105,
    excluded_periods: [{ reason: "no_market_weight", count: 5 }],
    unavailable_reason: null,
    fit: {
      alpha_annualized: 0.12,
      alpha_per_session: 0.0004615,
      alpha_standard_error: 0.0001,
      alpha_t_value: 4.615,
      hac_lag: 4,
      r_squared: 0.7,
      exposures: [
        { factor: "market", beta: 0.8, standard_error: 0.1, t_value: 8 },
      ],
    },
  },
});
const mount = (runId = "run-a") => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const element = (id: string) => (
    <QueryClientProvider client={client}>
      <BacktestFactorRegression runId={id} fingerprint={`fingerprint-${id}`} />
    </QueryClientProvider>
  );
  return { ...render(element(runId)), element, client };
};
const URL = "http://localhost:8000/api/v1/backtests/:runId/factor-regression";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  server.resetHandlers();
  vi.useRealTimers();
  focusManager.setFocused(undefined);
});
const serve = (body: RunFactorRegression) => {
  const read = vi.fn();
  server.use(
    http.get(URL, ({ params }) => {
      read(params.runId);
      return HttpResponse.json(body);
    }),
  );
  return read;
};

describe("팩터 회귀 조회", () => {
  it("명시적 조회만 시작하고 서버 숫자·계열 기준·판본·빠진 구간을 읽는다", async () => {
    const read = serve(report());
    mount();
    expect(read).not.toHaveBeenCalled();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "팩터 회귀 조회" }));
    expect(await screen.findByText("12.00%")).toBeVisible();
    expect(screen.getByText(/알파 t 기준/u)).toHaveTextContent("3.0000 · N 24");
    const table = screen.getByRole("table");
    expect(within(table).getByText("0.8000")).toBeVisible();
    await userEvent.setup().click(screen.getByText("회귀 근거와 한계"));
    expect(screen.getByText("synthetic-snapshot")).toBeVisible();
    expect(screen.getByText("시장 가중치 없음: 5")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(1);
  });
  it.each([
    "insufficient_observations",
    "data_snapshot_changed",
    "computation_failed",
  ] as const)("%s는 숫자를 채우거나 자동 반복하지 않는다", async (reason) => {
    const body = report();
    body.report.fit = null;
    body.report.unavailable_reason = reason;
    const read = serve(body);
    mount();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "팩터 회귀 조회" }));
    await screen.findByRole("status");
    expect(screen.queryByRole("table")).toBeNull();
    expect(read).toHaveBeenCalledTimes(1);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "회귀 다시 조회" }));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  });
  it.each([404, 409, 410, 503])(
    "HTTP %s는 자동 재시도하지 않으며 410은 명시 재조회도 차단한다",
    async (status) => {
      const read = vi.fn();
      server.use(
        http.get(URL, () => {
          read();
          return HttpResponse.json(
            {
              detail: {
                code:
                  status === 410
                    ? "backtest.result.unreadable"
                    : status === 409
                      ? "backtest.result.not_ready"
                      : "backtest.run.not_found",
              },
            },
            { status },
          );
        }),
      );
      mount();
      await userEvent
        .setup()
        .click(screen.getByRole("button", { name: "팩터 회귀 조회" }));
      await screen.findByRole("alert");
      expect(read).toHaveBeenCalledTimes(1);
      if (status === 410) expect(screen.queryByRole("button")).toBeNull();
      else
        expect(
          screen.getByRole("button", { name: "회귀 다시 조회" }),
        ).toBeEnabled();
    },
  );
  it("실행 전환 뒤 늦은 응답과 잘못된 실행 신원을 표시하지 않는다", async () => {
    let release!: (value: RunFactorRegression) => void;
    const pending = new Promise<RunFactorRegression>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(URL, async ({ params }) =>
        HttpResponse.json(
          params.runId === "run-a" ? await pending : report("wrong-run"),
        ),
      ),
    );
    const view = mount();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "팩터 회귀 조회" }));
    view.rerender(view.element("run-b"));
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "팩터 회귀 조회" }));
    await screen.findByRole("alert");
    await act(async () => release(report()));
    expect(screen.queryByText("12.00%")).toBeNull();
  });
  it("성공A뒤410은 prefix무효화·캐시된B왕복·재마운트·포커스로 다시 읽지 않는다", async () => {
    const read = vi.fn();
    server.use(
      http.get(URL, () => {
        read();
        return read.mock.calls.length === 1
          ? HttpResponse.json(report())
          : HttpResponse.json({}, { status: 410 });
      }),
    );
    const mounted = mount();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "팩터 회귀 조회" }));
    await screen.findByText("12.00%");
    await user.click(screen.getByRole("button", { name: "회귀 다시 조회" }));
    await screen.findByRole("alert");
    expect(screen.queryByText("12.00%")).toBeNull();
    await act(async () => {
      await mounted.client.invalidateQueries({ queryKey: ["backtest"] });
    });
    mounted.client.setQueryData(
      ["backtest", "run-b", "factor-regression", "fingerprint-run-b"],
      report("run-b"),
    );
    mounted.rerender(mounted.element("run-b"));
    await screen.findByText("12.00%");
    mounted.rerender(mounted.element("run-a"));
    await screen.findByRole("alert");
    mounted.rerender(
      <QueryClientProvider client={mounted.client}>
        <p>다른 화면</p>
      </QueryClientProvider>,
    );
    mounted.rerender(mounted.element("run-a"));
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
    render(
      <QueryClientProvider client={mounted.client}>
        <Control />
      </QueryClientProvider>,
    );
    await screen.findByText("포커스 대조");
    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(focused).toBeGreaterThan(1));
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("12.00%")).toBeNull();
  });
  it("computing 응답은 최대36회만 읽고 명시 재조회로 새 예산을 시작한다", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const body = report();
    body.report.fit = null;
    body.report.unavailable_reason = "computing";
    const read = serve(body);
    mount();
    await act(async () => {
      screen.getByRole("button", { name: "팩터 회귀 조회" }).click();
    });
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    for (let index = 0; index < 45; index++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "회귀 다시 조회" }),
        ).toBeEnabled(),
      );
    }
    expect(read).toHaveBeenCalledTimes(36);
    expect(screen.getByRole("status")).toHaveTextContent(
      "자동 확인을 멈췄어요",
    );
    await act(async () => {
      screen.getByRole("button", { name: "회귀 다시 조회" }).click();
    });
    await waitFor(() => expect(read).toHaveBeenCalledTimes(37));
    await waitFor(() =>
      expect(screen.getByRole("status")).not.toHaveTextContent(
        "자동 확인을 멈췄어요",
      ),
    );
  });
});
