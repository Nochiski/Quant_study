import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";

import type {
  PortfolioPreviewRequest,
  StrategySpec,
} from "../../../shared/api";
import { StrategyEditor } from "../ui/strategy-editor";

const template: StrategySpec = {
  identity: { strategy_id: "draft", revision: 0, schema_version: "1.0" },
  title: "멀티팩터 전략",
  description: "",
  data: {
    market: "KRX",
    start: "2026-01-02",
    end: "2026-01-16",
    universe_id: "krx.common-stock",
    frequency: "daily",
  },
  eligibility: { rules: [] },
  factors: {
    factors: [
      {
        factor_id: "price.close",
        label: "종가",
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
    selection_count: 20,
    weighting: "equal",
    rebalance: "monthly",
  },
  risk: {
    gross_exposure: 1,
    net_exposure: 1,
    max_name_weight: 0.1,
    max_sector_weight: 0.3,
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

let lastPreview: PortfolioPreviewRequest | null = null;

const server = setupServer(
  http.get("http://localhost:8000/api/v1/strategies/template", () =>
    HttpResponse.json(template),
  ),
  http.get("http://localhost:8000/api/v1/equity/catalog", () =>
    HttpResponse.json({
      snapshot: {
        snapshot_id: "mock-equity-v0.2-20260903",
        schema_version: "equity-v0.2-mock",
        built_at: "2026-09-03T00:00:00Z",
        source: "test",
        point_in_time: true,
        dataset_revisions: [],
      },
      fields: [],
      total: 0,
      page: 1,
      page_size: 1,
      page_count: 0,
      facets: { dataset_ids: [], units: [], frequencies: [] },
    }),
  ),
  http.get("http://localhost:8000/api/v1/factors/catalog", () =>
    HttpResponse.json({
      registry_version: "factor-registry-v1",
      factors: [],
      total: 0,
      page: 1,
      page_size: 12,
      page_count: 0,
      facets: { categories: [], availability: [], output_units: [] },
    }),
  ),
  http.post(
    "http://localhost:8000/api/v1/portfolio/preview",
    async ({ request }) => {
      lastPreview = (await request.json()) as PortfolioPreviewRequest;
      return HttpResponse.json({
        tape: {
          data_snapshot_id: "mock-equity-v0.2-20260903",
          strategy_hash: "a".repeat(64),
          tape_hash: "b".repeat(64),
          execution_timing: "next_open",
          frames: [
            {
              signal_as_of: "2026-01-02",
              execution_on: "2026-01-05",
              targets: [
                {
                  security_id: "sec-005930-1",
                  weight: 0.2,
                  composite_score: 1.25,
                  rank: 1,
                  side: "long",
                },
              ],
              candidates: [
                {
                  as_of: "2026-01-02",
                  security_id: "sec-005930-1",
                  eligible: true,
                  selected: true,
                  composite_score: 1.25,
                  rank: 1,
                  side: "long",
                  target_weight: 0.2,
                  sector_id: "technology",
                  exclusion_reasons: [],
                },
                {
                  as_of: "2026-01-02",
                  security_id: "sec-000660-1",
                  eligible: true,
                  selected: false,
                  composite_score: 0.3,
                  rank: 2,
                  side: null,
                  target_weight: 0,
                  sector_id: "technology",
                  exclusion_reasons: ["outside_selection"],
                },
              ],
            },
          ],
        },
        engine: {
          compatible: true,
          requirements: {
            schedule: "EverySession",
            actions: ["set_portfolio_target"],
            events: ["corporate_action", "market"],
            features: ["short_selling"],
          },
          issues: [],
        },
      });
    },
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  lastPreview = null;
});
afterAll(() => server.close());

test("포트폴리오·리스크·실행 설정과 T+1 타깃 근거를 한 draft에서 확인한다", async () => {
  const user = userEvent.setup();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <StrategyEditor />
    </QueryClientProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "3 포트폴리오" }));
  await user.selectOptions(
    screen.getByLabelText("포트폴리오 방향"),
    "long_short",
  );
  await user.selectOptions(screen.getByLabelText("선택 방식"), "percentile");
  await user.clear(screen.getByLabelText("상·하위 비율"));
  await user.type(screen.getByLabelText("상·하위 비율"), "0.2");
  await user.click(screen.getByRole("button", { name: "조건 추가" }));
  expect(screen.getByLabelText("편입 필드 1")).toHaveValue("price.market_cap");

  await user.click(screen.getByRole("button", { name: "4 리스크" }));
  await user.clear(screen.getByLabelText("Gross exposure"));
  await user.type(screen.getByLabelText("Gross exposure"), "1.2");
  await user.clear(screen.getByLabelText("Net exposure"));
  await user.type(screen.getByLabelText("Net exposure"), "0");

  await user.click(screen.getByRole("button", { name: "5 실행" }));
  expect(screen.getByText("종가로 신호 확정")).toBeInTheDocument();
  expect(screen.getByText("다음 세션 시가부터 체결")).toBeInTheDocument();
  await user.clear(screen.getByLabelText("수수료 (bps)"));
  await user.type(screen.getByLabelText("수수료 (bps)"), "8");

  await user.click(screen.getByRole("button", { name: "3 포트폴리오" }));
  await user.click(
    screen.getByRole("button", { name: "타깃 테이프 미리보기" }),
  );

  expect(await screen.findByText("엔진 호환")).toBeInTheDocument();
  expect(screen.getByText("2026-01-02")).toBeInTheDocument();
  expect(screen.getByText("2026-01-05")).toBeInTheDocument();
  expect(screen.getByText("outside selection")).toBeInTheDocument();
  await waitFor(() => {
    expect(lastPreview?.spec.portfolio.side).toBe("long_short");
    expect(lastPreview?.spec.portfolio.selection_percentile).toBe(0.2);
    expect(lastPreview?.spec.risk.gross_exposure).toBe(1.2);
    expect(lastPreview?.spec.risk.net_exposure).toBe(0);
    expect(lastPreview?.spec.execution.fee_bps).toBe(8);
  });
});
