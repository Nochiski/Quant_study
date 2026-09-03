import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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

const server = setupServer(
  http.get("http://localhost:8000/api/v1/strategies/template", () =>
    HttpResponse.json(template),
  ),
  http.post("http://localhost:8000/api/v1/strategies/validate", () =>
    HttpResponse.json({ valid: true, issues: [] }),
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
  server.resetHandlers();
  lastCreateBody = null;
});
afterAll(() => server.close());

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
