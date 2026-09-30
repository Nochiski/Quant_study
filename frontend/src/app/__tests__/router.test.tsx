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
  identity: { strategy_id: "s1", revision, schema_version: "1.1" },
  title,
  description: "",
  eligibility: { rules: [] },
  factors: [],
  signal: {
    score_threshold: null,
    regime_field_id: null,
    regime_minimum: null,
  },
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
  parameters: [],
});

const document = (revision: number, title: string) => ({
  strategy_id: "s1",
  revision,
  schema_version: "1.1",
  format: "yaml",
  source: `schema_version: "1.1"
title: ${title}
`,
  source_hash: "b".repeat(64),
  spec: spec(revision, title),
  spec_hash: "a".repeat(64),
  origin: "document",
  generated: false,
  created_at: "2026-09-04T00:00:00+00:00",
});

const backtestSummary = ({
  runId,
  status = "completed",
  saved = false,
  kind = "single",
  experimentId = null,
  paused = false,
}: {
  runId: string;
  status?: "queued" | "running" | "completed" | "failed";
  saved?: boolean;
  kind?: "single" | "experiment_trial" | "walk_forward_validation";
  experimentId?: string | null;
  paused?: boolean;
}) => ({
  kind,
  experiment_id: experimentId,
  experiment_paused: paused,
  run: {
    run_id: runId,
    status,
    progress: status === "completed" ? 1 : 0.4,
    stage: status === "completed" ? "completed" : "engine",
    message: status === "completed" ? "Run completed" : "Running engine",
    created_at: saved ? "2026-09-05T00:00:00Z" : "2026-09-05T01:00:00Z",
    updated_at: saved ? "2026-09-05T00:01:00Z" : "2026-09-05T01:01:00Z",
    error: null,
    artifact_sha256: null,
  },
  strategy_provenance: {
    kind: saved ? "saved_revision" : "inline_draft",
    spec_hash: saved ? "1".repeat(64) : "2".repeat(64),
    schema_version: "1.1",
    strategy_id: saved ? "s1" : null,
    revision: saved ? 2 : null,
    source_hash: saved ? "b".repeat(64) : "c".repeat(64),
  },
});

const trialLedger = (trialCount: number) => ({
  lineage_id: "s1",
  merged_lineage_ids: [],
  trial_count: trialCount,
  trials: [
    {
      trial_key: "a".repeat(64),
      runs: [
        {
          run_id: "run-first",
          status: "completed",
          created_at: "2026-09-05T00:00:00Z",
          role: "counted",
        },
        {
          run_id: "run-again",
          status: "completed",
          created_at: "2026-09-05T01:00:00Z",
          role: "recheck",
        },
      ],
      representative_run_id: "run-first",
      representative_sharpe: 0.05,
      metric_registry_version: "metric-registry-v5",
    },
    {
      trial_key: "c".repeat(64),
      runs: [
        {
          run_id: "run-cancelled",
          status: "cancelled",
          created_at: "2026-09-05T02:00:00Z",
          role: "no_result",
        },
      ],
    },
  ],
  blocked: [
    {
      blocked_at: "2026-09-05T03:00:00Z",
      lineage_id: "s1",
      trial_key: "d".repeat(64),
      spec_hash: "e".repeat(64),
      start: "2018-01-02",
    },
  ],
});

const openTrialLedger = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(
    await screen.findByRole("button", {
      name: "Revision 펼치기: Alpha strategy (s1)",
    }),
  );
  await user.click(await screen.findByRole("tab", { name: "시도 원장 1" }));
  return screen.getByRole("tabpanel");
};

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
        requires_upgrade: false,
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
  http.get(`${API}/api/v1/backtests`, ({ request }) => {
    const url = new URL(request.url);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 25);
    const strategyId = url.searchParams.get("strategy_id");
    const kind = url.searchParams.get("kind");
    const all = [
      backtestSummary({ runId: "run-inline" }),
      backtestSummary({
        runId: "run-saved",
        saved: true,
        kind: "experiment_trial",
        experimentId: "exp-7",
        paused: true,
      }),
    ];
    const filtered = all.filter(
      (item) =>
        (strategyId === null ||
          item.strategy_provenance.strategy_id === strategyId) &&
        (kind === null || item.kind === kind),
    );
    return HttpResponse.json({
      items: filtered.slice(offset, offset + limit),
      total: filtered.length,
      offset,
      limit,
    });
  }),
  http.post(`${API}/api/v1/strategy-documents/compile`, async ({ request }) => {
    const body = (await request.json()) as { source: string };
    return HttpResponse.json({
      format: "yaml",
      source_hash: "b".repeat(64),
      schema_version: "1.1",
      spec: spec(1, "퀄리티 모멘텀 v1"),
      canonical_json: '{"schema_version":"1.1","title":"퀄리티 모멘텀 v1"}',
      spec_hash: "a".repeat(64),
      diagnostics: [],
      echo: body.source,
    });
  }),
  http.get(`${API}/api/v1/strategy-documents/schema`, () =>
    HttpResponse.json({
      schema: { type: "object", properties: {}, additionalProperties: false },
      schema_hash: "h".repeat(64),
      schema_version: "1.1",
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
        schema_version: "1.1",
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
  http.get(`${API}/api/v1/backtests/:runId/request`, () =>
    HttpResponse.json({
      strategy: spec(0, "Rerun fixture"),
      core: "rust",
      initial_cash: 100_000_000,
      benchmark_security_id: "005930",
      annualization_days: 252,
      metric_windows: [],
      strategy_source: null,
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
  http.get(`${API}/api/v1/assistant/providers`, () =>
    HttpResponse.json({
      kinds: [
        {
          kind: "anthropic",
          installed: true,
          default_model: "claude-sonnet-5",
        },
        { kind: "openai", installed: false, default_model: null },
      ],
      profiles: [],
    }),
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

  it("moves legacy bookmarks to a clean YAML draft route", async () => {
    const history = mount("/?step=portfolio&run=bt-42");
    await screen.findByRole("heading", { name: "새 전략" });
    expect(history.location.pathname).toBe("/research/strategies/new");
    expect(history.location.search).toMatch(/^\?draft=draft-[a-f0-9]{32}$/u);
    expect(history.location.search).not.toContain("step=");
    expect(history.location.search).not.toContain("run=");
    cleanup();

    const legacyHistory = mount("/legacy/builder?step=risk&run=bt-99");
    await screen.findByRole("heading", { name: "새 전략" });
    expect(legacyHistory.location.pathname).toBe("/research/strategies/new");
    expect(legacyHistory.location.search).toMatch(
      /^\?draft=draft-[a-f0-9]{32}$/u,
    );
    expect(legacyHistory.location.search).not.toContain("step=");
    expect(legacyHistory.location.search).not.toContain("run=");
    expect(screen.queryByText("Quick Builder")).not.toBeInTheDocument();
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
        name: "Revision 펼치기: Alpha strategy (s1)",
      }),
    );
    const revisions = await screen.findByRole("region", {
      name: "전략 이력: Alpha strategy (s1)",
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

  it("marks frozen schema 1.0 strategies and revisions in the history lists", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${API}/api/v1/strategies`, () =>
        HttpResponse.json({
          items: [
            {
              strategy_id: "frozen-legacy",
              latest_revision: 1,
              requires_upgrade: true,
              title: "Frozen strategy",
              spec_hash: "f".repeat(64),
              updated_at: "2026-09-04T00:00:00Z",
            },
          ],
          total: 1,
          offset: 0,
          limit: 20,
        }),
      ),
      http.get(`${API}/api/v1/strategies/:strategyId/revisions`, () =>
        HttpResponse.json({
          items: [1, 2].map((revision) => ({
            strategy_id: "frozen-legacy",
            revision,
            requires_upgrade: revision === 1,
            spec_hash: `${revision}`.repeat(64).slice(0, 64),
            source_hash: null,
            source_format: null,
            origin: "legacy_json",
            change_note: null,
            created_at: `2026-09-0${revision}T00:00:00Z`,
          })),
          total: 2,
          offset: 0,
          limit: 20,
        }),
      ),
    );
    mount("/research/strategies");
    const row = (await screen.findByText("Frozen strategy")).closest("tr")!;
    expect(within(row).getByText("이전 버전 동결")).toBeInTheDocument();
    await user.click(
      within(row).getByRole("button", {
        name: "Revision 펼치기: Frozen strategy (frozen-legacy)",
      }),
    );
    const revisions = await screen.findByRole("region", {
      name: "전략 이력: Frozen strategy (frozen-legacy)",
    });
    const rows = within(revisions).getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByText("이전 버전 동결")).toBeInTheDocument();
    expect(within(rows[1]!).queryByText("이전 버전 동결")).toBeNull();
  });

  it("paginates strategy and revision pages with distinct disclosure ownership", async () => {
    const strategyOffsets: number[] = [];
    const revisionOffsets: number[] = [];
    const strategies = Array.from({ length: 21 }, (_, index) => {
      const number = index + 1;
      return {
        strategy_id: `s${String(number).padStart(2, "0")}`,
        latest_revision: 21,
        requires_upgrade: false,
        title:
          number <= 2
            ? "Duplicate title"
            : `Strategy ${String(number).padStart(2, "0")}`,
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
      name: "Revision 펼치기: Duplicate title (s01)",
    });
    const secondToggle = screen.getByRole("button", {
      name: "Revision 펼치기: Duplicate title (s02)",
    });
    expect(firstToggle).toHaveAttribute("aria-controls");
    expect(firstToggle.getAttribute("aria-controls")).not.toBe(
      secondToggle.getAttribute("aria-controls"),
    );
    await user.click(firstToggle);
    await user.click(secondToggle);
    const firstHistory = await screen.findByRole("region", {
      name: "전략 이력: Duplicate title (s01)",
    });
    expect(
      screen.getByRole("region", {
        name: "전략 이력: Duplicate title (s02)",
      }),
    ).toBeInTheDocument();
    expect(firstHistory.id).toBe(firstToggle.getAttribute("aria-controls"));
    expect(
      within(firstHistory).getByRole("navigation", {
        name: "Revision 목록 페이지: Duplicate title (s01)",
      }),
    ).toBeInTheDocument();

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

  it("shows the lineage trial ledger with rechecks and uncounted runs and only a merge action", async () => {
    // 검증 랩 V5-03(US-SM-12): 역할·N 은 backend 원장 응답 그대로다. 지우기·나누기 버튼은 없다.
    server.use(
      http.get(`${API}/api/v1/strategies/:strategyId/trials`, () =>
        HttpResponse.json(trialLedger(1)),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies");
    const ledger = await openTrialLedger(user);

    expect(ledger).toHaveTextContent("계열 시도 수 1회");
    const row = (runId: string) =>
      within(ledger).getByRole("link", { name: runId.slice(0, 12) })
        .parentElement!;
    expect(row("run-first")).toHaveTextContent("시도로 셈");
    expect(row("run-again")).toHaveTextContent("재확인");
    expect(row("run-cancelled")).toHaveTextContent("시도 수 제외");
    expect(
      within(ledger).getByText(
        /봉인 구간과 겹쳐 거절된 요청 · 시작일 2018-01-02/u,
      ),
    ).toBeInTheDocument();
    expect(
      within(ledger)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["다른 계열과 합치기"]);
  });

  it("merges another lineage after confirmation and translates a refusal", async () => {
    let merged: { strategyId: unknown; body: unknown } | null = null;
    let refuse = false;
    server.use(
      http.get(`${API}/api/v1/strategies`, () =>
        HttpResponse.json({
          items: [
            {
              strategy_id: "s1",
              latest_revision: 2,
              requires_upgrade: false,
              title: "Alpha strategy",
              spec_hash: "a".repeat(64),
              updated_at: "2026-09-05T00:00:00Z",
            },
            {
              strategy_id: "s2",
              latest_revision: 1,
              requires_upgrade: false,
              title: "Beta strategy",
              spec_hash: "b".repeat(64),
              updated_at: "2026-09-04T00:00:00Z",
            },
          ],
          total: 2,
          offset: 0,
          limit: 20,
        }),
      ),
      http.get(`${API}/api/v1/strategies/:strategyId/trials`, () =>
        HttpResponse.json(trialLedger(merged === null ? 1 : 3)),
      ),
      http.post(
        `${API}/api/v1/strategies/:strategyId/trials/merge`,
        async ({ params, request }) => {
          if (refuse)
            return HttpResponse.json(
              {
                detail: {
                  code: "backtest.lineage.already_merged",
                  message: "lineages are already one — source_id=s2",
                },
              },
              { status: 409 },
            );
          merged = {
            strategyId: params.strategyId,
            body: await request.json(),
          };
          return HttpResponse.json(trialLedger(3));
        },
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies");
    const ledger = await openTrialLedger(user);

    await user.click(
      within(ledger).getByRole("button", { name: "다른 계열과 합치기" }),
    );
    const dialog = within(ledger).getByRole("dialog", {
      name: "다른 계열과 합치기",
    });
    expect(dialog).toHaveTextContent("합치기는 되돌릴 수 없습니다.");
    const confirm = within(dialog).getByRole("button", { name: "합치기" });
    expect(confirm).toBeDisabled();
    await user.selectOptions(
      await within(dialog).findByRole("combobox", { name: "합칠 전략" }),
      "s2",
    );
    await user.click(confirm);

    await waitFor(() =>
      expect(merged).toEqual({
        strategyId: "s1",
        body: { source_strategy_id: "s2" },
      }),
    );
    expect(
      await within(ledger).findByText("계열 시도 수 3회"),
    ).toBeInTheDocument();
    expect(within(ledger).queryByRole("dialog")).not.toBeInTheDocument();

    refuse = true;
    await user.click(
      within(ledger).getByRole("button", { name: "다른 계열과 합치기" }),
    );
    const again = within(ledger).getByRole("dialog");
    await user.selectOptions(
      await within(again).findByRole("combobox", { name: "합칠 전략" }),
      "s2",
    );
    await user.click(within(again).getByRole("button", { name: "합치기" }));
    expect(await within(again).findByRole("alert")).toHaveTextContent(
      "두 전략은 이미 같은 시도 계열입니다.",
    );
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

  it("browses saved and inline backtest provenance and filters by strategy", async () => {
    const user = userEvent.setup();
    const history = mount("/research/backtests");
    expect(
      await screen.findByRole("heading", { name: "백테스트 이력" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "백테스트" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(await screen.findByText("run-inline")).toBeInTheDocument();
    expect(screen.getByText("run-saved")).toBeInTheDocument();
    expect(screen.getByText("Inline draft")).toBeInTheDocument();
    expect(screen.getByText("저장 revision")).toBeInTheDocument();
    expect(screen.getByText("bbbbbbbbbbbb")).toBeInTheDocument();
    expect(screen.getByText("cccccccccccc")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "s1 · v2" })).toHaveAttribute(
      "href",
      "/research/strategies/s1/revisions/2",
    );
    expect(
      within(screen.getByText("run-inline").closest("tr")!).getByRole("link", {
        name: "실행 열기",
      }),
    ).toHaveAttribute("href", "/research/backtests/run-inline");

    const filter = screen.getByRole("textbox", { name: "Strategy ID" });
    await user.type(filter, " s1 ");
    await user.click(screen.getByRole("button", { name: "필터 적용" }));
    await waitFor(() =>
      expect(history.location.search).toContain("strategy=s1"),
    );
    expect(await screen.findByText("run-saved")).toBeInTheDocument();
    expect(screen.queryByText("run-inline")).not.toBeInTheDocument();
  });

  it("labels each run's kind and filters the history by kind", async () => {
    // 검증 랩 V5-03: 종류는 backend 가 정하고 화면은 이름을 붙여 고른 값을 목록 질의에 싣는다.
    const user = userEvent.setup();
    const history = mount("/research/backtests");
    const kindOf = async (runId: string) =>
      (await screen.findByText(runId)).closest("tr")!;
    expect(await kindOf("run-inline")).toHaveTextContent("단일 실행");
    // 실험 run 은 쓰는 실험과 일시정지 여부를 함께 보인다(#382). 실험 화면이 없어 링크는 아직 없다.
    const experimentRun = await kindOf("run-saved");
    expect(experimentRun).toHaveTextContent("실험 시도");
    expect(experimentRun).toHaveTextContent("실험 exp-7");
    expect(experimentRun).toHaveTextContent("실험 일시정지");
    expect(await kindOf("run-inline")).not.toHaveTextContent("실험 일시정지");

    const kinds = screen.getByRole("group", { name: "종류" });
    await user.click(within(kinds).getByRole("button", { name: "실험 시도" }));
    await waitFor(() =>
      expect(history.location.search).toContain("kind=experiment_trial"),
    );
    await waitFor(() =>
      expect(screen.queryByText("run-inline")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("run-saved")).toBeInTheDocument();
    expect(
      within(kinds).getByRole("button", { name: "실험 시도" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("paginates and canonicalizes out-of-range backtest history URLs", async () => {
    const offsets: number[] = [];
    const runs = Array.from({ length: 26 }, (_, index) =>
      backtestSummary({ runId: `run-${String(index + 1).padStart(2, "0")}` }),
    );
    server.use(
      http.get(`${API}/api/v1/backtests`, ({ request }) => {
        const url = new URL(request.url);
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const limit = Number(url.searchParams.get("limit") ?? 25);
        offsets.push(offset);
        return HttpResponse.json({
          items: runs.slice(offset, offset + limit),
          total: runs.length,
          offset,
          limit,
        });
      }),
    );
    const user = userEvent.setup();
    const history = mount("/research/backtests");
    await screen.findByText("run-01");
    const pager = screen.getByRole("navigation", {
      name: "백테스트 이력 페이지",
    });
    await user.click(within(pager).getByRole("button", { name: "다음" }));
    expect(await screen.findByText("run-26")).toBeInTheDocument();
    await waitFor(() => expect(history.location.search).toContain("offset=25"));
    expect(offsets).toContain(25);

    history.push("/research/backtests?offset=50");
    await waitFor(() => expect(offsets).toContain(50));
    await waitFor(() => expect(history.location.search).toContain("offset=25"));
    expect(await screen.findByText("run-26")).toBeInTheDocument();
  });

  it("polls nonterminal backtest history and stops once every row is terminal", async () => {
    let requests = 0;
    server.use(
      http.get(`${API}/api/v1/backtests`, () => {
        requests += 1;
        const status = requests === 1 ? "running" : "completed";
        return HttpResponse.json({
          items: [backtestSummary({ runId: "run-live", status })],
          total: 1,
          offset: 0,
          limit: 25,
        });
      }),
    );
    mount("/research/backtests");
    expect(await screen.findByText("running")).toBeInTheDocument();
    await waitFor(() => expect(requests).toBeGreaterThanOrEqual(2), {
      timeout: 2_500,
    });
    expect((await screen.findAllByText("completed")).length).toBeGreaterThan(0);
    const terminalRequestCount = requests;
    await new Promise((resolve) => setTimeout(resolve, 1_200));
    expect(requests).toBe(terminalRequestCount);
  }, 7_500);

  it("shows loading, filtered-empty, and retryable error states for backtest history", async () => {
    server.use(
      http.get(`${API}/api/v1/backtests`, async ({ request }) => {
        await delay(250);
        const url = new URL(request.url);
        return HttpResponse.json({
          items: [],
          total: 0,
          offset: Number(url.searchParams.get("offset") ?? 0),
          limit: Number(url.searchParams.get("limit") ?? 25),
        });
      }),
    );
    mount("/research/backtests?strategy=missing");
    expect(await screen.findByRole("status")).toHaveTextContent("불러오는 중");
    expect(
      await screen.findByText("이 전략으로 실행한 백테스트가 없습니다."),
    ).toBeInTheDocument();

    cleanup();
    server.use(http.get(`${API}/api/v1/backtests`, () => HttpResponse.error()));
    mount("/research/backtests");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "백테스트 이력을 불러올 수 없습니다",
    );
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeEnabled();
  });

  it("shows the server-owned failure reason of a failed backtest run", async () => {
    // 이슈 #154: 어댑터가 거절한 실행은 "failed" 배지만 보이고 원인이 화면에 없었다.
    server.use(
      http.get(`${API}/api/v1/backtests/:runId`, ({ params }) =>
        HttpResponse.json({
          run_id: params.runId,
          status: "failed",
          progress: 0.05,
          stage: "data",
          message: "Run failed",
          error:
            "ValueError: malformed security_id — expected <ticker>:<span_seq> got='005930'",
          created_at: "2026-09-04T00:00:00Z",
          updated_at: "2026-09-04T00:00:01Z",
        }),
      ),
    );
    mount("/research/backtests/run-failed");
    expect(
      await screen.findByRole("alert", { name: "실행 오류" }),
    ).toHaveTextContent("malformed security_id");
    expect(screen.getByRole("status", { name: "실행 상태" })).toHaveTextContent(
      "failed",
    );
  });

  it("translates a coded tape-stage failure and keeps the server reason", async () => {
    // 이슈 #158: 데이터 의존 실패는 시작 422 대신 run `failed` + `error_code` 로 온다.
    server.use(
      http.get(`${API}/api/v1/backtests/:runId`, ({ params }) =>
        HttpResponse.json({
          run_id: params.runId,
          status: "failed",
          progress: 0.02,
          stage: "tape",
          message: "Run failed",
          error:
            "RawObservationUnavailableError: raw observations unavailable — status=no_data detail=no members in universe — universe_id=krx.common-stok start=2026-01-02 end=2026-02-20",
          error_code: "portfolio.data.unavailable",
          created_at: "2026-09-04T00:00:00Z",
          updated_at: "2026-09-04T00:00:01Z",
        }),
      ),
    );
    mount("/research/backtests/run-failed-tape");
    const alert = await screen.findByRole("alert", { name: "실행 오류" });
    expect(alert).toHaveTextContent("유니버스 ID 와 데이터 기간을 확인하세요");
    // 서버 원문은 본문이 아니라 접힌 진단 상세("서버 사유") 안에만 있다.
    const detail = within(alert).getByRole("group");
    expect(detail).toHaveTextContent("universe_id=krx.common-stok");
    expect(alert.textContent?.indexOf("유니버스 ID")).toBeLessThan(
      alert.textContent?.indexOf("universe_id=") ?? -1,
    );
    expect(screen.getByRole("status", { name: "실행 진행" })).toHaveTextContent(
      "tape · 2%",
    );
  });

  it("labels a failure that raced a cancellation as an error before cancellation", async () => {
    // 이슈 #158: 실패와 취소가 겹친 run 은 `cancelled` 배지에 사유가 같이 온다 — "실행 오류" 로 부르지 않는다.
    server.use(
      http.get(`${API}/api/v1/backtests/:runId`, ({ params }) =>
        HttpResponse.json({
          run_id: params.runId,
          status: "cancelled",
          progress: 0.02,
          stage: "cancelled",
          message: "Run cancelled",
          error:
            "RawObservationUnavailableError: raw observations unavailable — status=no_data",
          error_code: "portfolio.data.unavailable",
          created_at: "2026-09-04T00:00:00Z",
          updated_at: "2026-09-04T00:00:01Z",
        }),
      ),
    );
    mount("/research/backtests/run-cancelled-with-error");
    const alert = await screen.findByRole("alert", {
      name: "취소 전 발생한 오류",
    });
    expect(alert).toHaveTextContent("유니버스 ID 와 데이터 기간을 확인하세요");
    expect(screen.queryByRole("alert", { name: "실행 오류" })).toBeNull();
    expect(screen.getByRole("status", { name: "실행 상태" })).toHaveTextContent(
      "cancelled",
    );
  });

  it("shows a failed run in the backtest history with the result screen's sentence", async () => {
    // #304: 목록은 서버 원문(`error`)을 본문에 그대로 보였다. 결과 화면과 같은 규칙으로 번역을 본문에 두고
    // 원문은 접힌 "서버 사유"에 둔다. 지난 실행이라 줄마다 경고로 읽히지 않는다.
    const summary = backtestSummary({
      runId: "run-wiped-out",
      status: "failed",
    });
    server.use(
      http.get(`${API}/api/v1/backtests`, () =>
        HttpResponse.json({
          items: [
            {
              ...summary,
              run: {
                ...summary.run,
                error:
                  "EquityWipedOutError: session-end equity fell to or below zero — session=2021-03-02 equity=-1204.5",
                error_code: "backtest.run.equity_wiped_out",
              },
            },
          ],
          total: 1,
          offset: 0,
          limit: 25,
        }),
      ),
    );
    mount("/research/backtests");
    const row = (await screen.findByText("run-wiped-out")).closest("tr")!;
    expect(row).toHaveTextContent(
      "실행 오류: 세션 종료 자산이 0 이하가 되어 실행이 멈췄습니다(자본 잠식).",
    );
    const reason = within(row).getByRole("group");
    expect(reason).toHaveTextContent("서버 사유");
    expect(reason).not.toHaveAttribute("open");
    expect(
      row.textContent?.replace(reason.textContent ?? "", ""),
    ).not.toContain("EquityWipedOutError");
    expect(within(row).queryByRole("alert")).toBeNull();
  });

  it("tells to rerun when a completed run's result file cannot be read", async () => {
    // #330: 결과 파일이 없거나 손상된 완료 run은 410 `backtest.result.unreadable`로 온다. 일반 문구 대신 다시
    // 불러와도 소용없고 같은 설정으로 다시 실행하라고 말하며, 서버 사유는 접힌 상세에 둔다.
    server.use(
      http.get(`${API}/api/v1/backtests/:runId`, ({ params }) =>
        HttpResponse.json({
          run_id: params.runId,
          status: "completed",
          progress: 1,
          stage: "completed",
          message: "Run completed",
          created_at: "2026-09-04T00:00:00Z",
          updated_at: "2026-09-04T00:00:01Z",
        }),
      ),
      http.get(`${API}/api/v1/backtests/:runId/result`, ({ params }) =>
        HttpResponse.json(
          {
            detail: {
              code: "backtest.result.unreadable",
              message: `run result file is missing — run_id=${String(params.runId)}`,
            },
          },
          { status: 410 },
        ),
      ),
    );
    mount("/research/backtests/run-unreadable");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("이 실행의 결과 파일을 읽을 수 없습니다.");
    expect(alert).toHaveTextContent(
      "다시 불러와도 같으니 같은 설정으로 다시 실행하세요.",
    );
    expect(within(alert).getByRole("group")).toHaveTextContent(
      "run result file is missing — run_id=run-unreadable",
    );
    expect(alert).not.toHaveTextContent("백테스트 결과를 불러올 수 없습니다");
  });

  it("opens the settings route with the AI provider section from the shell", async () => {
    const user = userEvent.setup();
    const history = mount("/research/backtests");
    await user.click(await screen.findByRole("link", { name: "설정" }));

    await waitFor(() => expect(history.location.pathname).toBe("/settings"));
    expect(
      await screen.findByRole("heading", { level: 1, name: "설정" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "AI 어시스턴트 공급자" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("연결된 공급자가 없습니다"),
    ).toBeInTheDocument();
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
