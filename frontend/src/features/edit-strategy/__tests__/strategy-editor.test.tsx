import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";

import type { StrategySpec } from "../../../entities/strategy";
import { StrategyDraftProvider } from "../model/strategy-draft-provider";
import { StrategyEditorWorkspace } from "../ui/strategy-editor-workspace";

const template: StrategySpec = {
  identity: { strategy_id: "draft", revision: 0, schema_version: "1.0" },
  title: "새 팩터 전략",
  description: "",
  data: {
    market: "KRX",
    start: "2021-09-03",
    end: "2026-09-03",
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

let lastCreateBody: StrategySpec | null = null;
let lastPreviewBody: { expected_data_snapshot_id?: string | null } | null =
  null;

const server = setupServer(
  http.get("http://localhost:8000/api/v1/strategies/template", () =>
    HttpResponse.json(template),
  ),
  http.post("http://localhost:8000/api/v1/strategies/validate", () =>
    HttpResponse.json({ valid: true, issues: [] }),
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
      factors: [
        {
          factor_id: "financial.book_to_market",
          label: "Book to market",
          description: "Point-in-time value factor",
          category: "financial",
          preference: "high",
          output_unit: "ratio",
          required_field_ids: ["financial.book_equity", "price.market_cap"],
          minimum_history_sessions: 1,
          missing_policy: "drop",
          availability: "implemented",
          default_graph: {
            nodes: [
              {
                kind: "field",
                node_id: "book",
                field_id: "financial.book_equity",
              },
              { kind: "field", node_id: "cap", field_id: "price.market_cap" },
              {
                kind: "binary",
                node_id: "ratio",
                operator: "divide",
                left_node_id: "book",
                right_node_id: "cap",
              },
              {
                kind: "cross_sectional",
                node_id: "rank",
                operator: "rank",
                input_node_id: "ratio",
              },
            ],
            output_node_id: "rank",
            missing_policy: "drop",
          },
          tags: ["financial", "high"],
        },
      ],
      total: 1,
      page: 1,
      page_size: 12,
      page_count: 1,
      facets: {
        categories: ["financial"],
        availability: ["implemented"],
        output_units: ["ratio"],
      },
    }),
  ),
  http.post(
    "http://localhost:8000/api/v1/factors/validate",
    async ({ request }) => {
      const body = (await request.json()) as {
        graph: StrategySpec["factors"]["factors"][number]["graph"];
      };
      return HttpResponse.json({
        valid: true,
        issues: [],
        node_contracts: body.graph.nodes.map((node) => ({
          node_id: node.node_id,
          value_type: "numeric_series",
          unit: "ratio",
          minimum_history_sessions: 1,
        })),
        minimum_history_sessions: 1,
        required_field_ids: ["financial.book_equity", "price.market_cap"],
      });
    },
  ),
  http.post(
    "http://localhost:8000/api/v1/factors/preview",
    async ({ request }) => {
      lastPreviewBody = (await request.json()) as {
        expected_data_snapshot_id?: string | null;
      };
      return HttpResponse.json({
        plan: {
          graph_hash: "a".repeat(64),
          plan_hash: "b".repeat(64),
          registry_version: "factor-registry-v1",
          output_node_id: "rank_5",
          steps: [],
          required_field_ids: [],
          referenced_factor_ids: [],
          referenced_subgraph_ids: [],
          minimum_history_sessions: 1,
          missing_policy: "drop",
          as_of_policy: "available_date_lte_as_of",
        },
        data_snapshot_id: "mock-equity-v0.2-20260903",
        cache_key: {
          fingerprint: "c".repeat(64),
          data_snapshot_id: "mock-equity-v0.2-20260903",
          plan_hash: "b".repeat(64),
          registry_version: "factor-registry-v1",
          parameters: [],
          as_of_start: "2021-09-03",
          as_of_end: "2026-09-03",
        },
        evaluation: { output_node_id: "rank_5", values: [] },
        analytics: {
          information_coefficient: 0.12,
          rank_information_coefficient: 0.11,
          quantile_spread: 0.03,
          coverage: 0.98,
          turnover: 0.22,
          decay: 0.73,
          observation_count: 100,
          valid_count: 98,
        },
      });
    },
  ),
  http.post("http://localhost:8000/api/v1/strategies", async ({ request }) => {
    lastCreateBody = (await request.json()) as StrategySpec;
    return HttpResponse.json(
      {
        spec: {
          ...lastCreateBody,
          identity: {
            strategy_id: "strategy-1",
            revision: 1,
            schema_version: "1.0",
          },
        },
        spec_hash: "a".repeat(64),
      },
      { status: 201 },
    );
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  lastCreateBody = null;
  lastPreviewBody = null;
});
afterAll(() => server.close());

test("팩터를 찾아 변환한 뒤 모드 전환과 저장에도 같은 StrategySpec을 유지한다", async () => {
  const user = userEvent.setup();
  renderEditor();

  expect(await screen.findByText("Book to market")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "전략에 추가" }));
  const rankButtons = screen.getAllByRole("button", { name: "+ Rank" });
  await user.click(rankButtons.at(-1)!);
  expect(screen.getByText("Rank → Rank")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: "Advanced Graph" }));
  expect(await screen.findAllByText("유효한 typed DAG")).not.toHaveLength(0);
  expect(screen.getAllByText("rank_5")).toHaveLength(2);

  await user.click(screen.getByRole("tab", { name: "Quick Builder" }));
  const diagnostics = screen.getAllByRole("button", { name: "팩터 진단 실행" });
  await user.click(diagnostics.at(-1)!);
  expect(await screen.findByText("0.1200")).toBeInTheDocument();
  // provenance: the catalog snapshot travels as expected_data_snapshot_id (P1.5-01)
  expect(lastPreviewBody?.expected_data_snapshot_id).toBe(
    "mock-equity-v0.2-20260903",
  );

  await user.click(screen.getByRole("button", { name: /저장/ }));
  await waitFor(() => {
    const savedFactor = lastCreateBody?.factors.factors.find(
      (factor) => factor.factor_id === "financial.book_to_market",
    );
    expect(savedFactor?.graph.output_node_id).toBe("rank_5");
    expect(savedFactor?.graph.nodes).toHaveLength(5);
  });
});

const renderEditor = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <StrategyDraftProvider>
        <StrategyEditorWorkspace />
      </StrategyDraftProvider>
    </QueryClientProvider>,
  );
};

test("Quick과 Advanced가 같은 draft를 편집하고 저장 후 revision 상태를 표시한다", async () => {
  const user = userEvent.setup();
  renderEditor();

  const title = await screen.findByRole("textbox", { name: "전략 이름" });
  await user.clear(title);
  await user.type(title, "퀄리티 모멘텀");
  expect(screen.getByText("저장하지 않은 변경")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: "Advanced Graph" }));
  expect(screen.getAllByText("close")).toHaveLength(2);
  await user.click(screen.getByRole("tab", { name: "Quick Builder" }));
  expect(screen.getByRole("textbox", { name: "전략 이름" })).toHaveValue(
    "퀄리티 모멘텀",
  );

  await user.click(screen.getByRole("button", { name: "검증" }));
  expect(await screen.findByText("실행 가능한 전략")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "전략 저장" }));
  expect(
    await screen.findByText("리비전이 저장되었습니다."),
  ).toBeInTheDocument();
  await waitFor(() => expect(lastCreateBody?.title).toBe("퀄리티 모멘텀"));
  expect(screen.getByText("저장된 상태")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "새 리비전 저장" })).toBeDisabled();
});
