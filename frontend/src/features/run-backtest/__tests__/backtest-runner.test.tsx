import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";

import type {
  BacktestRunResult,
  BacktestRunSpec,
  StrategySpec,
} from "../../../shared/api";
import { BacktestRunner } from "../ui/backtest-runner";

const strategy: StrategySpec = {
  identity: { strategy_id: "draft", revision: 0, schema_version: "1.0" },
  title: "M5 strategy",
  description: "",
  data: {
    market: "KRX",
    start: "2026-01-02",
    end: "2026-02-20",
    universe_id: "krx.common-stock",
    frequency: "daily",
  },
  eligibility: { rules: [] },
  factors: {
    factors: [
      {
        factor_id: "price.close",
        label: "Close",
        direction: "high",
        weight: 1,
        graph: {
          nodes: [{ kind: "field", node_id: "close", field_id: "price.close" }],
          output_node_id: "close",
        },
      },
    ],
  },
  signal: { method: "weighted_sum", entry_percentile: 0.1 },
  portfolio: {
    side: "long_only",
    selection_count: 2,
    weighting: "equal",
    rebalance: "monthly",
  },
  risk: {
    gross_exposure: 1,
    net_exposure: 1,
    max_name_weight: 0.6,
    max_sector_weight: 1,
  },
  execution: {
    timing: "next_open",
    order_style: "market",
    participation_rate: 0.1,
    fee_bps: 15,
    slippage_bps: 10,
  },
  parameters: [],
};

const definition = (
  metricId: string,
  label: string,
  unit: "percent" | "ratio" | "count" | "currency",
) => ({
  metric_id: metricId,
  label,
  category: "risk_adjusted" as const,
  unit,
  higher_is_better: true,
  nullable: true,
  precision: 4,
  version: 1,
});

const result: BacktestRunResult = {
  manifest: {
    run_id: "run-001",
    created_at: "2026-09-03T00:00:00Z",
    completed_at: "2026-09-03T00:00:01Z",
    engine_core: "rust",
    engine_version: "backtest-engine-v1",
    run_fingerprint: "d".repeat(64),
    run_spec: {
      strategy,
      core: "rust",
      initial_cash: 100_000_000,
      benchmark_security_id: "005930",
      annualization_days: 252,
      metric_windows: [],
    },
    strategy_hash: "a".repeat(64),
    data_snapshot_id: "mock-equity-v0.2-20260903",
    target_tape_hash: "b".repeat(64),
    metric_registry_version: "metric-registry-v1",
    initial_cash: 100_000_000,
    annualization_days: 252,
    fee_bps: 15,
    slippage_bps: 10,
    participation_rate: 0.1,
    strategy_provenance: {
      kind: "inline_draft",
      spec_hash: "a".repeat(64),
      schema_version: "1.0",
      strategy_id: null,
      revision: null,
      source_hash: null,
    },
    schema_version: "backtest-run-v2",
    warnings: [
      {
        code: "mock_equity_data",
        message: "Mock data is active.",
        severity: "info",
      },
    ],
  },
  metric_definitions: [
    definition("total_return", "Total return", "percent"),
    definition("sharpe", "Sharpe ratio", "ratio"),
    definition("max_drawdown", "Maximum drawdown", "percent"),
    definition("calmar", "Calmar ratio", "ratio"),
    definition("turnover", "Turnover", "ratio"),
    definition("trade_count", "Closed trades", "count"),
    definition("total_fees", "Total fees", "currency"),
  ],
  metrics: [
    { metric_id: "total_return", value: 0.12, scope: "full", sample_count: 2 },
    {
      metric_id: "sharpe",
      value: null,
      scope: "full",
      sample_count: 2,
      unavailable_reason: "zero_return_variance",
    },
    { metric_id: "max_drawdown", value: -0.04, scope: "full", sample_count: 2 },
    { metric_id: "calmar", value: 3, scope: "full", sample_count: 2 },
    { metric_id: "turnover", value: 0, scope: "full", sample_count: 2 },
    { metric_id: "trade_count", value: 1, scope: "full", sample_count: 2 },
    { metric_id: "total_fees", value: 0, scope: "full", sample_count: 2 },
  ],
  series: {
    equity: [
      { session: "2026-01-02", equity: 100, benchmark_equity: 100 },
      { session: "2026-01-05", equity: 112, benchmark_equity: 105 },
    ],
    drawdown: [
      { session: "2026-01-02", drawdown: 0 },
      { session: "2026-01-05", drawdown: -0.04 },
    ],
    monthly_returns: [{ year: 2026, month: 1, value: 0.12 }],
    rolling_sharpe: [
      { session: "2026-01-02", value: null },
      { session: "2026-01-05", value: 1.4 },
    ],
  },
  artifacts: {
    snapshots: [
      {
        session: "2026-01-02",
        cash: 100,
        equity: 100,
        gross_exposure: 0,
        net_exposure: 0,
      },
      {
        session: "2026-01-05",
        cash: 10,
        equity: 112,
        gross_exposure: 0.9,
        net_exposure: 0.9,
      },
    ],
    positions: [],
    orders: [],
    fills: [],
    costs: [],
    trades: [
      {
        security_id: "005930",
        opened_on: "2026-01-02",
        closed_on: "2026-01-05",
        side: "long",
        quantity: "10",
        entry_price: 100,
        exit_price: 110,
        pnl: 95,
        fees: 5,
        slippage_cost: 2,
      },
    ],
    schema_version: "backtest-artifacts-v1",
  },
};

let received: BacktestRunSpec | null = null;

const server = setupServer(
  http.post("http://localhost:8000/api/v1/backtests", async ({ request }) => {
    received = (await request.json()) as BacktestRunSpec;
    return HttpResponse.json(
      {
        run: {
          run_id: "run-001",
          status: "queued",
          progress: 0,
          stage: "queued",
          message: "Run accepted",
          created_at: "2026-09-03T00:00:00Z",
          updated_at: "2026-09-03T00:00:00Z",
        },
      },
      { status: 202 },
    );
  }),
  http.get("http://localhost:8000/api/v1/backtests/run-001", () =>
    HttpResponse.json({
      run_id: "run-001",
      status: "completed",
      progress: 1,
      stage: "completed",
      message: "Run completed",
      created_at: "2026-09-03T00:00:00Z",
      updated_at: "2026-09-03T00:00:01Z",
      artifact_sha256: "c".repeat(64),
    }),
  ),
  http.get("http://localhost:8000/api/v1/backtests/run-001/result", () =>
    HttpResponse.json(result),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  received = null;
});
afterAll(() => server.close());

test("Rust 실행부터 전문 차트·raw metric·manifest 경고까지 한 흐름으로 표시한다", async () => {
  const user = userEvent.setup();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <BacktestRunner strategy={strategy} />
    </QueryClientProvider>,
  );

  await user.type(screen.getByLabelText("OOS 시작일 (선택)"), "2026-02-02");
  await user.click(screen.getByRole("button", { name: "백테스트 실행" }));

  expect(
    await screen.findByRole("heading", { name: "백테스트 결과" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Equity curve 차트")).toBeInTheDocument();
  expect(screen.getByLabelText("Drawdown 차트")).toBeInTheDocument();
  expect(screen.getByLabelText("Rolling Sharpe 차트")).toBeInTheDocument();
  expect(screen.getByLabelText("Exposure 차트")).toBeInTheDocument();
  expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
  expect(screen.getAllByText("0.0000").length).toBeGreaterThan(0);
  expect(screen.getAllByText("zero return variance").length).toBeGreaterThan(0);
  expect(screen.getByText("005930")).toBeInTheDocument();

  await user.click(screen.getByText("Manifest · 데이터 경고 · 재현성 정보"));
  expect(screen.getByText("mock_equity_data")).toBeInTheDocument();
  await waitFor(() => {
    expect(received?.core).toBe("rust");
    expect(received?.metric_windows?.[0]?.scope).toBe("out_of_sample");
    expect(received?.metric_windows?.[0]?.start).toBe("2026-02-02");
  });
});
