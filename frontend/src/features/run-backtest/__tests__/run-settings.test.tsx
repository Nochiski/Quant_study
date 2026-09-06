import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import type { ReactElement, ReactNode } from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type { BacktestRunSpec } from "../../../shared/api";
import { buildBacktestRunOptions } from "../model/run-settings";
import { useBacktestRunSettings } from "../model/use-backtest-run-settings";
import { BacktestRunActions } from "../ui/backtest-run-actions";
import { BacktestRunSettings } from "../ui/backtest-run-settings";

const API = "http://localhost:8000";
const acceptedRequest: BacktestRunSpec = {
  strategy_source: {
    kind: "saved_revision",
    strategy_id: "strategy-1",
    revision: 4,
    expected_spec_hash: "a".repeat(64),
  },
  core: "python",
  initial_cash: 123_456_789,
  benchmark_security_id: "sec-benchmark",
  annualization_days: 260,
  metric_windows: [
    {
      scope: "out_of_sample",
      start: "2025-01-02",
      end: "2026-08-31",
      label: "OOS 2025-01-02",
    },
  ],
};

let cancelledRun: string | null = null;
let replayedRequest: BacktestRunSpec | null = null;
const server = setupServer(
  http.post(`${API}/api/v1/backtests/:runId/cancel`, ({ params }) => {
    cancelledRun = String(params.runId);
    return HttpResponse.json({
      run_id: params.runId,
      status: "cancel_requested",
      progress: 0.4,
      stage: "cancellation",
      message: "Cancellation requested",
      created_at: "2026-09-06T00:00:00Z",
      updated_at: "2026-09-06T00:00:01Z",
    });
  }),
  http.post(`${API}/api/v1/backtests`, async ({ request }) => {
    replayedRequest = (await request.json()) as BacktestRunSpec;
    return HttpResponse.json(
      {
        run: {
          run_id: "run-replayed",
          status: "queued",
          progress: 0,
          stage: "queued",
          message: "Run accepted",
          created_at: "2026-09-06T00:00:02Z",
          updated_at: "2026-09-06T00:00:02Z",
        },
      },
      { status: 202 },
    );
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  cancelledRun = null;
  replayedRequest = null;
});
afterAll(() => server.close());

const renderWithQuery = (ui: ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
};

describe("backtest run settings", () => {
  it("builds explicit non-default generated RunSpec fields without a strategy source", () => {
    const result = buildBacktestRunOptions(
      {
        core: "python",
        initialCashKrw: "123456789",
        benchmarkSecurityId: " sec-benchmark ",
        annualizationDays: "260",
        oosStart: "2025-01-02",
      },
      { start: "2021-01-01", end: "2026-08-31" },
    );

    expect(result).toEqual({
      valid: true,
      errors: [],
      options: {
        core: "python",
        initial_cash: 123_456_789,
        benchmark_security_id: "sec-benchmark",
        annualization_days: 260,
        metric_windows: [
          {
            scope: "out_of_sample",
            start: "2025-01-02",
            end: "2026-08-31",
            label: "OOS 2025-01-02",
          },
        ],
      },
    });
    if (result.valid) {
      expect(result.options).not.toHaveProperty("strategy");
      expect(result.options).not.toHaveProperty("strategy_source");
    }
  });

  it("blocks malformed numeric assumptions and an OOS date outside the compiled range", () => {
    expect(
      buildBacktestRunOptions(
        {
          core: "rust",
          initialCashKrw: "0",
          benchmarkSecurityId: "",
          annualizationDays: "252.5",
          oosStart: "2020-12-31",
        },
        { start: "2021-01-01", end: "2026-08-31" },
      ),
    ).toEqual({
      valid: false,
      options: null,
      errors: ["initial_cash", "annualization_days", "oos_range"],
    });
  });

  it("exposes every assumption through accessible controls and reports invalid input", async () => {
    const Harness = () => {
      const controller = useBacktestRunSettings({
        start: "2021-01-01",
        end: "2026-08-31",
      });
      return <BacktestRunSettings controller={controller} />;
    };
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("실행 설정 열기"));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "실행 core" }),
      "python",
    );
    const cash = screen.getByRole("spinbutton", { name: "초기 자본 (KRW)" });
    await user.clear(cash);
    await user.type(cash, "0");

    expect(screen.getByRole("alert")).toHaveTextContent(
      "초기 자본은 0보다 큰 숫자여야 합니다.",
    );
    expect(screen.getByText("입력 확인")).toBeInTheDocument();
  });
});

describe("backtest run actions", () => {
  it("cancels a nonterminal run and reruns the exact server-owned request", async () => {
    const onReplayed = vi.fn();
    const view = renderWithQuery(
      <BacktestRunActions
        runId="run-active"
        status="running"
        request={acceptedRequest}
        onReplayed={onReplayed}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "실행 취소" }));
    await waitFor(() => expect(cancelledRun).toBe("run-active"));

    view.rerender(
      <BacktestRunActions
        runId="run-active"
        status="cancelled"
        request={acceptedRequest}
        onReplayed={onReplayed}
      />,
    );
    await user.click(screen.getByRole("button", { name: "동일 설정 재실행" }));

    await waitFor(() =>
      expect(onReplayed).toHaveBeenCalledWith("run-replayed"),
    );
    expect(replayedRequest).toEqual(acceptedRequest);
  });
});
