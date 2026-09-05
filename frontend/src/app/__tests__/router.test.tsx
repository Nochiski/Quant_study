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
import { HttpResponse, delay, http } from "msw";
import { setupServer } from "msw/node";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";

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
  http.get(`${API}/api/v1/strategy-drafts/:draftId`, () =>
    HttpResponse.json(
      { detail: { code: "strategy.draft.not_found" } },
      { status: 404 },
    ),
  ),
  http.put(
    `${API}/api/v1/strategy-drafts/:draftId`,
    async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({
        draft_id: params.draftId,
        version: Number(body.expected_version) + 1,
        source: body.source,
        format: body.format,
        source_hash: "d".repeat(64),
        schema_version: body.schema_version,
        updated_at: "2026-09-05T00:00:00Z",
        strategy_id: body.strategy_id ?? null,
        base_revision: body.base_revision ?? null,
        base_spec_hash: body.base_spec_hash ?? null,
      });
    },
  ),
  http.delete(
    `${API}/api/v1/strategy-drafts/:draftId`,
    () => new HttpResponse(null, { status: 204 }),
  ),
  http.get(`${API}/api/v1/strategies`, ({ request }) => {
    const url = new URL(request.url);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 20);
    const items = [
      {
        strategy_id: "s1",
        latest_revision: 2,
        title: "Alpha strategy",
        spec_hash: "a".repeat(64),
        updated_at: "2026-09-05T00:00:00Z",
      },
    ];
    return HttpResponse.json({
      items: offset === 0 ? items : [],
      total: items.length,
      offset,
      limit,
    });
  }),
  http.get(`${API}/api/v1/strategies/:strategyId/revisions`, ({ params }) =>
    HttpResponse.json({
      items: [1, 2].map((revision) => ({
        strategy_id: params.strategyId,
        revision,
        spec_hash: `${revision}`.repeat(64).slice(0, 64),
        source_hash: revision === 1 ? null : "b".repeat(64),
        source_format: revision === 1 ? null : "yaml",
        origin: revision === 1 ? "legacy_json" : "document",
        change_note: null,
        created_at: `2026-09-0${revision}T00:00:00Z`,
      })),
      total: 2,
      offset: 0,
      limit: 20,
    }),
  ),
  http.post(`${API}/api/v1/strategy-documents/compile`, async ({ request }) => {
    const body = (await request.json()) as { source: string };
    return HttpResponse.json({
      format: "yaml",
      source_hash: "b".repeat(64),
      schema_version: "1.0",
      spec: spec(1, "퀄리티 모멘텀 v1"),
      canonical_json: '{"schema_version":"1.0","title":"퀄리티 모멘텀 v1"}',
      spec_hash: "a".repeat(64),
      diagnostics: [],
      echo: body.source,
    });
  }),
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
  vi.unstubAllGlobals();
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
  it("starts compact on a narrow viewport and lets the control visibly expand it", async () => {
    const list = {
      matches: true,
      media: "(max-width: 1279px)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => list),
    );
    const user = userEvent.setup();
    mount("/research/strategies/new");
    await waitFor(() =>
      expect(globalThis.document.querySelector(".app-shell")).not.toBeNull(),
    );
    const shell = globalThis.document.querySelector(".app-shell")!;
    const button = globalThis.document.querySelector<HTMLButtonElement>(
      ".app-shell__collapse button",
    )!;
    expect(shell).toHaveClass("app-shell--collapsed");
    expect(button).toHaveAttribute("aria-expanded", "false");
    await user.click(button);
    expect(shell).not.toHaveClass("app-shell--collapsed");
    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("opens a saved revision directly and restores the typed view from the URL", async () => {
    const history = mount(
      "/research/strategies/s1/revisions/2?view=diff&path=%2Frisk",
    );
    expect(
      await screen.findByRole("heading", { name: "퀄리티 모멘텀 v2" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByLabelText("StrategySpec Diff")).toBeVisible();
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

  it("normalizes malformed pointers and preserves escaped RFC 6901 paths", async () => {
    const malformed = mount(
      "/research/strategies/s1/revisions/1?path=%2Ffoo~2bar",
    );
    await screen.findByRole("heading", { name: "퀄리티 모멘텀 v1" });
    await waitFor(() =>
      expect(malformed.location.search).not.toContain("path="),
    );

    cleanup();
    const history = mount("/research/strategies/s1/revisions/1");
    await screen.findByRole("heading", { name: "퀄리티 모멘텀 v1" });
    history.push("/research/strategies/s1/revisions/1?path=%2Ffoo~1bar~0baz");
    await waitFor(() =>
      expect(history.location.search).toContain("path=%2Ffoo~1bar~0baz"),
    );
    history.push("/research/strategies/s1/revisions/1?path=%2Frisk");
    await waitFor(() => expect(history.location.search).toContain("%2Frisk"));

    history.back();
    await waitFor(() =>
      expect(history.location.search).toContain("path=%2Ffoo~1bar~0baz"),
    );
    history.forward();
    await waitFor(() => expect(history.location.search).toContain("%2Frisk"));
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
    expect(screen.getByRole("tab", { name: "JSON" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "Form" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "Graph" })).toBeEnabled();
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

  it("browses saved strategies and opens immutable revision history", async () => {
    const user = userEvent.setup();
    mount("/research/strategies");
    expect(
      await screen.findByRole("heading", { name: "전략 이력" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Alpha strategy")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "최신본 열기" })).toHaveAttribute(
      "href",
      "/research/strategies/s1/revisions/2",
    );
    await user.click(
      screen.getByRole("button", {
        name: "Revision 펼치기: Alpha strategy",
      }),
    );
    const revisions = await screen.findByRole("region", {
      name: "저장 revision 목록: Alpha strategy",
    });
    expect(within(revisions).getByText("bbbbbbbbbbbb")).toBeInTheDocument();
    expect(within(revisions).getByText("원문 hash 없음")).toBeInTheDocument();
    expect(within(revisions).getByText("111111111111")).toBeInTheDocument();
    expect(
      within(revisions).getAllByRole("link", { name: "편집" }),
    ).toHaveLength(2);
    expect(
      within(revisions).getAllByRole("link", { name: "Diff" })[0],
    ).toHaveAttribute("href", expect.stringContaining("view=diff"));
  });

  it("paginates strategy and revision pages with distinct disclosure ownership", async () => {
    const strategyOffsets: number[] = [];
    const revisionOffsets: number[] = [];
    const strategies = Array.from({ length: 21 }, (_, index) => {
      const number = index + 1;
      return {
        strategy_id: `s${String(number).padStart(2, "0")}`,
        latest_revision: 21,
        title: `Strategy ${String(number).padStart(2, "0")}`,
        spec_hash: "a".repeat(64),
        updated_at: "2026-09-05T00:00:00Z",
      };
    });
    server.use(
      http.get(`${API}/api/v1/strategies`, ({ request }) => {
        const url = new URL(request.url);
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const limit = Number(url.searchParams.get("limit") ?? 20);
        strategyOffsets.push(offset);
        return HttpResponse.json({
          items: strategies.slice(offset, offset + limit),
          total: strategies.length,
          offset,
          limit,
        });
      }),
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions`,
        ({ params, request }) => {
          const url = new URL(request.url);
          const offset = Number(url.searchParams.get("offset") ?? 0);
          const limit = Number(url.searchParams.get("limit") ?? 20);
          revisionOffsets.push(offset);
          const items = Array.from({ length: 21 }, (_, index) => ({
            strategy_id: params.strategyId,
            revision: index + 1,
            spec_hash: `${index + 1}`.repeat(64).slice(0, 64),
            source_hash: "b".repeat(64),
            source_format: "yaml",
            origin: "document",
            change_note: null,
            created_at: "2026-09-05T00:00:00Z",
          }));
          return HttpResponse.json({
            items: items.slice(offset, offset + limit),
            total: items.length,
            offset,
            limit,
          });
        },
      ),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies");
    const firstToggle = await screen.findByRole("button", {
      name: "Revision 펼치기: Strategy 01",
    });
    const secondToggle = screen.getByRole("button", {
      name: "Revision 펼치기: Strategy 02",
    });
    expect(firstToggle).toHaveAttribute("aria-controls");
    expect(firstToggle.getAttribute("aria-controls")).not.toBe(
      secondToggle.getAttribute("aria-controls"),
    );
    await user.click(firstToggle);
    await user.click(secondToggle);
    const firstHistory = await screen.findByRole("region", {
      name: "저장 revision 목록: Strategy 01",
    });
    expect(
      screen.getByRole("region", {
        name: "저장 revision 목록: Strategy 02",
      }),
    ).toBeInTheDocument();
    expect(firstHistory.id).toBe(firstToggle.getAttribute("aria-controls"));

    await user.click(
      within(firstHistory).getByRole("button", { name: "다음" }),
    );
    expect(await within(firstHistory).findByText("v21")).toBeInTheDocument();
    expect(revisionOffsets).toContain(20);

    const listPager = screen.getByRole("navigation", {
      name: "전략 목록 페이지",
    });
    await user.click(within(listPager).getByRole("button", { name: "다음" }));
    expect(await screen.findByText("Strategy 21")).toBeInTheDocument();
    await waitFor(() => expect(history.location.search).toContain("offset=20"));
    expect(strategyOffsets).toContain(20);
  });

  it("canonicalizes malformed and out-of-range strategy list offsets", async () => {
    const beyond = mount("/research/strategies?offset=20");
    expect(await screen.findByText("Alpha strategy")).toBeInTheDocument();
    await waitFor(() => expect(beyond.location.search).toBe(""));

    cleanup();
    const malformed = mount("/research/strategies?offset=1e2");
    expect(await screen.findByText("Alpha strategy")).toBeInTheDocument();
    await waitFor(() => expect(malformed.location.search).toBe(""));
  });

  it("shows explicit loading, empty, and error states for strategy history", async () => {
    server.use(
      http.get(`${API}/api/v1/strategies`, async () => {
        await delay(250);
        return HttpResponse.json({ items: [], total: 0, offset: 0, limit: 20 });
      }),
    );
    mount("/research/strategies");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "불러오는 중입니다",
    );
    expect(
      await screen.findByText("저장된 전략이 없습니다"),
    ).toBeInTheDocument();

    cleanup();
    server.use(
      http.get(`${API}/api/v1/strategies`, () => HttpResponse.error()),
    );
    mount("/research/strategies");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "전략 목록을 불러올 수 없습니다",
    );
  });

  it("supports back and forward between routes", async () => {
    const user = userEvent.setup();
    const history = mount("/research/backtests/run-1");
    expect(
      await screen.findByRole("heading", { name: "백테스트 실행" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "전략" }));
    await screen.findByRole("heading", { name: "전략 이력" });
    history.back();
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/backtests/run-1"),
    );
    expect(
      await screen.findByRole("heading", { name: "백테스트 실행" }),
    ).toBeInTheDocument();
    history.forward();
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/strategies"),
    );
  });
});
