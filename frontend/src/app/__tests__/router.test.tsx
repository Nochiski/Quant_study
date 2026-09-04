import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { App } from "../app";

const API = "http://localhost:8000";

const spec = (revision: number, title: string) => ({
  identity: { strategy_id: "s1", revision, schema_version: "1.0" },
  title,
  description: "",
  data: {
    market: "KRX",
    start: "2021-09-03",
    end: "2026-09-03",
    universe_id: "krx.common-stock",
    frequency: "daily",
  },
  eligibility: { rules: [] },
  factors: { factors: [] },
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
    fee_bps: 15,
    slippage_bps: 10,
  },
  parameters: [],
});

const document = (revision: number, title: string) => ({
  strategy_id: "s1",
  revision,
  schema_version: "1.0",
  format: "yaml",
  source: `schema_version: "1.0"
title: ${title}
`,
  source_hash: "b".repeat(64),
  spec: spec(revision, title),
  spec_hash: "a".repeat(64),
  origin: "document",
  generated: false,
  created_at: "2026-09-04T00:00:00+00:00",
});

const server = setupServer(
  http.get(`${API}/api/v1/strategy-documents/schema`, () =>
    HttpResponse.json({
      schema: { type: "object", properties: {}, additionalProperties: false },
      schema_hash: "h".repeat(64),
      schema_version: "1.0",
    }),
  ),
  http.get(`${API}/api/v1/strategy-documents/contract`, () =>
    HttpResponse.json({
      contract: {
        contract_hash: "c".repeat(64),
        dataset_snapshot_id: "snap",
        factor_registry_version: "v1",
        fields: [],
        schema_hash: "h".repeat(64),
        schema_version: "1.0",
      },
      equity_catalog_url: "/api/v1/equity/catalog",
      factor_catalog_url: "/api/v1/factors/catalog",
    }),
  ),
  http.get(`${API}/api/v1/factors/catalog`, () =>
    HttpResponse.json({
      facets: {},
      factors: [],
      page: 1,
      page_count: 0,
      page_size: 100,
      registry_version: "v1",
      total: 0,
    }),
  ),
  http.get(
    `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
    ({ params }) => {
      const revision = Number(params.revision);
      if (params.strategyId !== "s1" || revision > 2) {
        return HttpResponse.json(
          { detail: { code: "strategy.not_found", message: "missing" } },
          { status: 404 },
        );
      }
      return HttpResponse.json(
        document(revision, `퀄리티 모멘텀 v${revision}`),
      );
    },
  ),
  http.get(`${API}/api/v1/backtests/:runId`, ({ params }) =>
    HttpResponse.json({
      run_id: params.runId,
      status: "completed",
      progress: 1,
      stage: "done",
      message: "Run completed",
      created_at: "2026-09-04T00:00:00Z",
      updated_at: "2026-09-04T00:00:01Z",
    }),
  ),
  http.get(`${API}/api/v1/backtests/:runId/result`, () => HttpResponse.error()),
  http.get(`${API}/api/v1/equity/catalog`, () =>
    HttpResponse.json({
      total: 0,
      page: 1,
      page_size: 1,
      page_count: 0,
      fields: [],
      facets: {},
    }),
  ),
  http.get(`${API}/api/v1/strategies/template`, () =>
    HttpResponse.json(spec(0, "새 팩터 전략")),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

const mount = (initial: string, operationsEnabled = false) => {
  const history = createMemoryHistory({ initialEntries: [initial] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: 0 } },
  });
  render(
    <App
      history={history}
      queryClient={queryClient}
      operationsEnabled={operationsEnabled}
    />,
  );
  return history;
};

describe("App Shell routes", () => {
  it("opens a saved revision directly and restores the typed view from the URL", async () => {
    const history = mount(
      "/research/strategies/s1/revisions/2?view=diff&path=%2Frisk",
    );
    expect(
      await screen.findByRole("heading", { name: "퀄리티 모멘텀 v2" }),
    ).toBeInTheDocument();
    // The diff view is not implemented yet: the stored YAML is shown, the URL keeps the request,
    // a notice says so.
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText(/DIFF/)).toBeInTheDocument();
    expect(screen.getAllByRole("tabpanel").length).toBeGreaterThan(0);
    expect(history.location.search).toContain("view=diff");
    expect(history.location.search).toContain("path=%2Frisk");
  });

  it("drops an invalid view from the URL instead of failing", async () => {
    const history = mount("/research/strategies/s1/revisions/1?view=bogus");
    await screen.findByRole("heading", { name: "퀄리티 모멘텀 v1" });
    await waitFor(() => expect(history.location.search).not.toContain("view"));
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("shows the not-found state for an unknown revision and for an unknown path", async () => {
    mount("/research/strategies/s1/revisions/9");
    expect(
      await screen.findByText("페이지를 찾을 수 없습니다"),
    ).toBeInTheDocument();
    cleanup();
    mount("/nowhere/at/all");
    expect(
      await screen.findByText("페이지를 찾을 수 없습니다"),
    ).toBeInTheDocument();
  });

  it("redirects the bare root to the new strategy route", async () => {
    const history = mount("/");
    await screen.findByRole("heading", { name: "새 전략" });
    expect(history.location.pathname).toBe("/research/strategies/new");
    expect(screen.getByRole("tab", { name: "JSON" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("keeps legacy bookmarks on the legacy builder with their query and run id", async () => {
    const history = mount("/?step=portfolio&run=bt-42");
    await waitFor(() =>
      expect(history.location.pathname).toBe("/legacy/builder"),
    );
    expect(history.location.search).toContain("step=portfolio");
    expect(history.location.search).toContain("run=bt-42");
    expect(screen.getAllByRole("main")).toHaveLength(1);
  });

  it("shows loading inside the shell and a localised error with a way back on 500", async () => {
    server.use(
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        async ({ params }) => {
          await delay(300);
          if (params.revision === "2") {
            return HttpResponse.json({ detail: "boom" }, { status: 500 });
          }
          return HttpResponse.json(document(1, "느린 전략"));
        },
      ),
    );
    mount("/research/strategies/s1/revisions/1");
    expect(await screen.findByRole("status")).toHaveTextContent("불러오는 중");
    expect(
      screen.getByRole("navigation", { name: "주 메뉴" }),
    ).toBeInTheDocument();
    await screen.findByRole("heading", { name: "느린 전략" });
    cleanup();
    mount("/research/strategies/s1/revisions/2");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("페이지를 표시할 수 없습니다");
    expect(alert).not.toHaveTextContent("API request failed");
    expect(
      screen.getByRole("navigation", { name: "주 메뉴" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "새 전략으로 이동" }),
    ).toBeInTheDocument();
  });

  it("hides operations behind the flag and never renders them as live controls", async () => {
    mount("/operations/orders");
    expect(
      await screen.findByText("페이지를 찾을 수 없습니다"),
    ).toBeInTheDocument();
    const orders = screen.getByText("주문").closest('[aria-disabled="true"]');
    expect(orders).not.toBeNull();
    expect(orders).toHaveTextContent("향후 제공, 사용 불가");
    cleanup();
    mount("/operations/orders", true);
    expect(
      await screen.findByRole("heading", { name: "주문" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "아직 제공되지 않습니다",
    );
  });

  it("supports back and forward between routes", async () => {
    const user = userEvent.setup();
    const history = mount("/research/backtests/run-1");
    expect(
      await screen.findByRole("heading", { name: "백테스트 실행" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "전략" }));
    await screen.findByRole("heading", { name: "새 전략" });
    history.back();
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/backtests/run-1"),
    );
    expect(
      await screen.findByRole("heading", { name: "백테스트 실행" }),
    ).toBeInTheDocument();
    history.forward();
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/strategies/new"),
    );
  });
});
