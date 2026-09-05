import { EditorView } from "@codemirror/view";
import { undo } from "@codemirror/commands";
import { Transaction } from "@codemirror/state";
import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import {
  act,
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

import { strategiesQuery } from "../../entities/strategy";
import { readBackendFixture } from "../../shared/testing/backend-fixtures";
import { t } from "../../shared/config";
import { App } from "../app";

const API = "http://localhost:8000";

const spec = (strategyId: string, revision: number, title: string) => ({
  identity: { strategy_id: strategyId, revision, schema_version: "1.0" },
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

const document = (
  strategyId: string,
  revision: number,
  source: string,
  title = "퀄리티 모멘텀",
) => ({
  strategy_id: strategyId,
  revision,
  schema_version: "1.0",
  format: "yaml",
  source,
  source_hash: "b".repeat(64),
  spec: spec(strategyId, revision, title),
  spec_hash: `${revision}`.repeat(64).slice(0, 64),
  origin: "document",
  generated: false,
  created_at: "2026-09-04T09:30:00+00:00",
});

const STORED = 'schema_version: "1.0"\ntitle: 퀄리티 모멘텀\n';
const GRAPH_SOURCE = `schema_version: "1.0"
title: 그래프 전략
factors:
  factors:
    - factor_id: momentum
      label: 모멘텀
      weight: 1.0
      direction: high
      graph:
        nodes:
          - node_id: close
            kind: field
            field_id: price.close
          - node_id: mom_252
            kind: time_series
            operator: momentum
            input_node_id: close
            window: 252
            lag: 0
        output_node_id: mom_252
        missing_policy: drop
`;
const graphSpec = (strategyId: string, revision: number) => ({
  ...spec(strategyId, revision, "그래프 전략"),
  factors: {
    factors: [
      {
        factor_id: "momentum",
        label: "모멘텀",
        weight: 1,
        direction: "high",
        graph: {
          nodes: [
            { node_id: "close", kind: "field", field_id: "price.close" },
            {
              node_id: "mom_252",
              kind: "time_series",
              operator: "momentum",
              input_node_id: "close",
              window: 252,
              lag: 0,
            },
          ],
          output_node_id: "mom_252",
          missing_policy: "drop",
        },
      },
    ],
  },
});
const SIGNAL_SCHEMA_RESPONSE = {
  schema: {
    type: "object",
    properties: {
      signal: {
        type: "object",
        properties: {
          method: { type: "string", default: "weighted_sum" },
        },
      },
    },
    additionalProperties: false,
  },
  schema_hash: "h".repeat(64),
  schema_version: "1.0",
};
const RUNTIME_SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as Record<string, unknown>;
const FACTOR = {
  availability: "implemented",
  category: "price",
  default_graph: {
    nodes: [{ kind: "field", node_id: "px", field_id: "price.close" }],
    output_node_id: "px",
    missing_policy: "drop",
  },
  description: "Server factor",
  factor_id: "server.momentum",
  label: "Server momentum",
  minimum_history_sessions: 1,
  missing_policy: "drop",
  output_unit: "score",
  preference: "high",
  required_field_ids: ["price.close"],
};
const posted: unknown[] = [];
const started: Record<string, unknown>[] = [];
const explainedGraphs: unknown[] = [];
const tracedStrategies: Record<string, unknown>[] = [];
const acceptedRun = (runId = "run-7") => ({
  run: {
    run_id: runId,
    status: "queued",
    progress: 0,
    stage: "queued",
    message: "queued",
    created_at: "2026-09-04T00:00:00Z",
    updated_at: "2026-09-04T00:00:00Z",
  },
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
  http.get(`${API}/api/v1/strategies/:strategyId/diff`, ({ request }) => {
    const url = new URL(request.url);
    return HttpResponse.json({
      strategy_id: "s1",
      base_revision: Number(url.searchParams.get("base")),
      target_revision: Number(url.searchParams.get("target")),
      base_spec_hash: "1".repeat(64),
      target_spec_hash: "3".repeat(64),
      changes: [
        {
          pointer: "/risk/max_name_weight",
          kind: "changed",
          before: 0.1,
          after: 0.05,
        },
        {
          pointer: "/description",
          kind: "added",
          before: null,
          after: "서버에서 수정",
        },
      ],
    });
  }),
  http.post(`${API}/api/v1/strategy-documents/compile`, async ({ request }) => {
    const body = (await request.json()) as { source: string };
    const title = /title: (.*)/.exec(body.source)?.[1] ?? "";
    const compiledSpec = spec("draft", 0, title);
    const { identity, ...canonicalSpec } = compiledSpec;
    return HttpResponse.json({
      format: "yaml",
      source_hash: "b".repeat(64),
      schema_version: "1.0",
      spec: compiledSpec,
      canonical_json: JSON.stringify({
        ...canonicalSpec,
        schema_version: identity.schema_version,
      }),
      spec_hash: body.source === STORED ? "2".repeat(64) : "9".repeat(64),
      diagnostics: [],
    });
  }),
  http.post(`${API}/api/v1/backtests`, async ({ request }) => {
    started.push((await request.json()) as Record<string, unknown>);
    return HttpResponse.json(acceptedRun(), { status: 202 });
  }),
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
      return HttpResponse.json(document("s1", revision, STORED));
    },
  ),
  http.post(`${API}/api/v1/strategy-documents`, async ({ request }) => {
    const body = (await request.json()) as { format: string; source: string };
    posted.push(body);
    if (body.source.includes("bad")) {
      return HttpResponse.json(
        {
          detail: {
            code: "strategy.document.invalid",
            message: "title must not be empty",
          },
        },
        { status: 422 },
      );
    }
    return HttpResponse.json(document("s9", 1, body.source, "새 전략 A"), {
      status: 201,
    });
  }),
  http.post(
    `${API}/api/v1/strategy-documents/:strategyId/revisions`,
    async ({ params, request }) => {
      const body = (await request.json()) as {
        expected_revision: number;
        source: string;
      };
      posted.push(body);
      if (body.expected_revision !== 2) {
        return HttpResponse.json(
          {
            detail: {
              code: "strategy.revision_conflict",
              message: "another author saved first",
              latest_revision: 3,
            },
          },
          { status: 409 },
        );
      }
      return HttpResponse.json(
        document(String(params.strategyId), 3, body.source),
        { status: 201 },
      );
    },
  ),
  http.get(`${API}/api/v1/strategies/:strategyId/revisions`, () =>
    HttpResponse.json({ items: [], offset: 0, limit: 50, total: 0 }),
  ),
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
    HttpResponse.json(spec("", 0, "새 팩터 전략")),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  posted.length = 0;
  started.length = 0;
  explainedGraphs.length = 0;
  tracedStrategies.length = 0;
});
afterAll(() => server.close());

const mountWithClient = (initial: string) => {
  const history = createMemoryHistory({ initialEntries: [initial] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: 0 } },
  });
  render(
    <App
      history={history}
      queryClient={queryClient}
      operationsEnabled={false}
    />,
  );
  return { history, queryClient };
};

const mount = (initial: string) => mountWithClient(initial).history;

/** Waits for the lazy CodeMirror editor and returns its view for programmatic edits. */
const editor = async () => {
  await screen.findByRole("textbox", { name: "편집기" });
  let view: EditorView | null = null;
  await waitFor(() => {
    const content = globalThis.document.querySelector(".cm-content");
    view = content ? EditorView.findFromDOM(content as HTMLElement) : null;
    expect(view).not.toBeNull();
  });
  return view as unknown as EditorView;
};

const replaceText = (view: EditorView, text: string) =>
  act(() => {
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: text },
    });
  });

const saveButton = () =>
  within(globalThis.document.querySelector(".ide__editor-actions")!).getByRole(
    "button",
    { name: "리비전 저장" },
  );

const legacyLink = () =>
  globalThis.document.querySelector<HTMLAnchorElement>(
    'a[href="/legacy/builder"]',
  )!;

describe("document routes (P2-04)", () => {
  it.each([
    ["/research/strategies/new", 'schema_version: "1.0"\ntitle: ""\n'],
    ["/research/strategies/s1/revisions/2", STORED],
  ])("wires a runtime-schema snippet through %s", async (route, prefix) => {
    server.use(
      http.get(`${API}/api/v1/strategy-documents/schema`, () =>
        HttpResponse.json(SIGNAL_SCHEMA_RESPONSE),
      ),
    );
    const user = userEvent.setup();
    mount(route);
    const view = await editor();
    act(() => view.dispatch({ selection: { anchor: view.state.doc.length } }));

    await user.click(
      await screen.findByRole("button", {
        name: "signal · 현재 커서에 삽입",
      }),
    );

    expect(view.state.doc.toString()).toBe(
      `${prefix}signal:\n  method: weighted_sum`,
    );
    expect(
      screen.getByText(/YAML 문법 검사를 통과했습니다/),
    ).toBeInTheDocument();
    expect(view.hasFocus).toBe(true);
    expect(screen.getByText("저장되지 않은 변경")).toBeInTheDocument();
    await waitFor(() => expect(saveButton()).toBeEnabled());

    act(() =>
      view.dispatch({
        changes: { from: view.state.doc.length, insert: "\n" },
        selection: { anchor: view.state.doc.length + 1 },
        annotations: Transaction.addToHistory.of(false),
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "signal · 현재 커서에 삽입" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "같은 항목이 이미 존재합니다",
    );
    expect(view.state.doc.toString()).toBe(
      `${prefix}signal:\n  method: weighted_sum\n`,
    );

    act(() =>
      view.dispatch({
        changes: {
          from: view.state.doc.length - 1,
          to: view.state.doc.length,
        },
        annotations: Transaction.addToHistory.of(false),
      }),
    );
    act(() => expect(undo(view)).toBe(true));
    expect(view.state.doc.toString()).toBe(prefix);
  });

  it("reports YAML-only from a JSON projection instead of an editor lifecycle error", async () => {
    server.use(
      http.get(`${API}/api/v1/strategy-documents/schema`, () =>
        HttpResponse.json(SIGNAL_SCHEMA_RESPONSE),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2?view=json");

    await user.click(
      await screen.findByRole("button", {
        name: "signal · 현재 커서에 삽입",
      }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "YAML 편집 화면에서만 사용할 수 있습니다",
    );
    expect(
      screen.queryByText(/아직 준비되지 않았습니다/),
    ).not.toBeInTheDocument();
  });

  it("keeps invalid route source untouched when insertion preflight fails", async () => {
    server.use(
      http.get(`${API}/api/v1/strategy-documents/schema`, () =>
        HttpResponse.json(SIGNAL_SCHEMA_RESPONSE),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/new");
    const view = await editor();
    const invalid = "title: [broken\n\n";
    replaceText(view, invalid);
    act(() => view.dispatch({ selection: { anchor: view.state.doc.length } }));

    await user.click(
      await screen.findByRole("button", {
        name: "signal · 현재 커서에 삽입",
      }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "YAML 1.2 문법을 통과하지 않아 변경하지 않았습니다",
    );
    expect(view.state.doc.toString()).toBe(invalid);
  });

  it("inserts and duplicate-checks a backend-owned factor graph on the real route", async () => {
    server.use(
      http.get(`${API}/api/v1/strategy-documents/schema`, () =>
        HttpResponse.json({
          schema: RUNTIME_SCHEMA,
          schema_hash: "h".repeat(64),
          schema_version: "1.0",
        }),
      ),
      http.get(`${API}/api/v1/factors/catalog`, () =>
        HttpResponse.json({
          facets: {},
          factors: [FACTOR],
          page: 1,
          page_count: 1,
          page_size: 100,
          registry_version: "v1",
          total: 1,
        }),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/new");
    const view = await editor();
    act(() => view.dispatch({ selection: { anchor: view.state.doc.length } }));

    const insert = await screen.findByRole("button", {
      name: "Server momentum · 현재 커서에 삽입",
    });
    await user.click(insert);
    expect(view.state.doc.toString()).toContain("factor_id: server.momentum");
    expect(view.state.doc.toString()).toContain("field_id: price.close");

    await user.click(insert);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "같은 항목이 이미 존재합니다",
    );
  });

  it("fails closed when a required snippet metadata query fails", async () => {
    server.use(
      http.get(`${API}/api/v1/strategy-documents/contract`, () =>
        HttpResponse.json({ detail: "unavailable" }, { status: 503 }),
      ),
    );
    mount("/research/strategies/new");

    expect(
      await screen.findByText(/메타데이터를 불러올 수 없어 스니펫을 차단/),
    ).toHaveAttribute("role", "alert");
    expect(
      screen.queryByRole("button", { name: /현재 커서에 삽입/ }),
    ).not.toBeInTheDocument();
  });

  it("fails closed when the factor catalog generation mismatches the contract", async () => {
    server.use(
      http.get(`${API}/api/v1/factors/catalog`, () =>
        HttpResponse.json({
          facets: {},
          factors: [],
          page: 1,
          page_count: 0,
          page_size: 100,
          registry_version: "v2",
          total: 0,
        }),
      ),
    );
    mount("/research/strategies/new");

    expect(
      await screen.findByText(/계약·팩터 카탈로그 버전이 일치하지 않아/),
    ).toHaveAttribute("role", "alert");
    expect(
      screen.queryByRole("button", { name: /현재 커서에 삽입/ }),
    ).not.toBeInTheDocument();
  });

  it("loads the exact stored source of a revision into the editor as the draft base", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2?view=diff");
    expect(
      await screen.findByRole("heading", { name: "퀄리티 모멘텀" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByLabelText("StrategySpec Diff")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    const view = await editor();
    expect(view.state.doc.toString()).toBe(STORED);
    expect(screen.getByText("저장됨 v2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "리비전 저장" })).toBeDisabled();
    expect(screen.getByText("2026-09-04 09:30")).toBeInTheDocument();
  });

  it("recovers an exact server draft on a direct revision route without silent overwrite", async () => {
    const recovered = `${STORED}description: server recovery\n`;
    server.use(
      http.get(`${API}/api/v1/strategy-drafts/:draftId`, ({ params }) =>
        HttpResponse.json({
          draft_id: params.draftId,
          version: 7,
          source: recovered,
          format: "yaml",
          source_hash: "d".repeat(64),
          schema_version: "1.0",
          updated_at: "2026-09-05T01:02:03Z",
          strategy_id: "s1",
          base_revision: 2,
          base_spec_hash: "2".repeat(64),
        }),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    expect(view.state.doc.toString()).toBe(STORED);
    const banner = await screen.findByRole("region", {
      name: "복구할 서버 초안",
    });
    expect(view.state.doc.toString()).toBe(STORED);
    await user.click(
      within(banner).getByRole("button", { name: "서버 초안 적용" }),
    );
    await waitFor(() => expect(view.state.doc.toString()).toBe(recovered));
  });

  it("rejects a server draft whose payload identity differs from the requested route", async () => {
    let deleteCalls = 0;
    server.use(
      http.get(`${API}/api/v1/strategy-drafts/:draftId`, () =>
        HttpResponse.json({
          draft_id: "revision:another-strategy:99:wrong",
          version: 7,
          source: `${STORED}description: wrong identity\n`,
          format: "yaml",
          source_hash: "d".repeat(64),
          schema_version: "1.0",
          updated_at: "2026-09-05T01:02:03Z",
          strategy_id: "s1",
          base_revision: 2,
          base_spec_hash: "2".repeat(64),
        }),
      ),
      http.delete(`${API}/api/v1/strategy-drafts/:draftId`, () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent(t("draft.server.rejected"));
    expect(alert).toHaveTextContent(
      "Draft response did not match the requested draft identity or contract.",
    );
    expect(view.state.doc.toString()).toBe(STORED);
    expect(
      screen.queryByRole("button", { name: t("draft.server.applyRemote") }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: t("draft.server.keepLocal") }),
    ).not.toBeInTheDocument();
    expect(deleteCalls).toBe(0);
  });

  it("rejects a malformed draft conflict instead of exposing another draft's actions", async () => {
    let putCalls = 0;
    let deleteCalls = 0;
    server.use(
      http.put(`${API}/api/v1/strategy-drafts/:draftId`, async () => {
        putCalls += 1;
        return HttpResponse.json(
          {
            detail: {
              code: "strategy.draft.conflict",
              message: "newer writer",
              current: {
                draft_id: "revision:another-strategy:99:wrong",
                version: 2,
                source: "title: another draft\n",
                format: "yaml",
                source_hash: "e".repeat(64),
                schema_version: "1.0",
                updated_at: "2026-09-05T01:02:04Z",
                strategy_id: "s1",
                base_revision: 2,
                base_spec_hash: "2".repeat(64),
              },
            },
          },
          { status: 409 },
        );
      }),
      http.delete(`${API}/api/v1/strategy-drafts/:draftId`, () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}description: local edit\n`);
    const alert = await screen.findByRole("alert", undefined, {
      timeout: 3_000,
    });

    expect(putCalls).toBe(1);
    expect(alert).toHaveTextContent(t("draft.server.rejected"));
    expect(alert).toHaveTextContent(
      "Draft conflict response did not match the requested draft identity.",
    );
    expect(
      screen.queryByRole("button", { name: t("draft.server.applyRemote") }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: t("draft.server.keepLocal") }),
    ).not.toBeInTheDocument();
    expect(deleteCalls).toBe(0);
  });

  it("assigns a new-draft recovery identity before persisting the first edit", async () => {
    const writes: Array<{ draftId: string; source: string }> = [];
    server.use(
      http.put(
        `${API}/api/v1/strategy-drafts/:draftId`,
        async ({ params, request }) => {
          const body = (await request.json()) as Record<string, unknown>;
          const draftId = String(params.draftId);
          writes.push({ draftId, source: String(body.source) });
          return HttpResponse.json({
            draft_id: draftId,
            version: Number(body.expected_version) + 1,
            source: body.source,
            format: body.format,
            source_hash: "d".repeat(64),
            schema_version: body.schema_version,
            updated_at: "2026-09-05T00:00:00Z",
            strategy_id: null,
            base_revision: null,
            base_spec_hash: null,
          });
        },
      ),
    );

    const history = mount("/research/strategies/new");
    const view = await editor();
    const source = 'schema_version: "1.0"\ntitle: first edit\n';
    replaceText(view, source);

    await waitFor(() => expect(writes).toHaveLength(1), { timeout: 3_000 });
    const routeDraft = new URLSearchParams(history.location.search).get(
      "draft",
    );
    expect(routeDraft).not.toBeNull();
    expect(writes).toEqual([{ draftId: routeDraft, source }]);
  });

  it("creates a strategy from the new draft and moves the URL to revision 1", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/new");
    const view = await editor();
    expect(screen.getAllByText("초안").length).toBeGreaterThan(0);
    replaceText(view, 'schema_version: "1.0"\ntitle: 새 전략 A\n');
    expect(screen.getByText("저장되지 않은 변경")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "리비전 저장" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "리비전 저장" }));
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s9/revisions/1",
      ),
    );
    expect(posted).toEqual([
      { format: "yaml", source: 'schema_version: "1.0"\ntitle: 새 전략 A\n' },
    ]);
    // The revision page opens from the cache the save filled: no extra document fetch, no prompt.
    expect(
      await screen.findByRole("heading", { name: "새 전략 A" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("keeps edits typed during create and saves them before following the new revision", async () => {
    const user = userEvent.setup();
    const revisionRequests: Array<{
      expected_revision: number;
      format: string;
      source: string;
    }> = [];
    server.use(
      http.post(`${API}/api/v1/strategy-documents`, async ({ request }) => {
        const body = (await request.json()) as { source: string };
        await delay(100);
        return HttpResponse.json(document("s9", 1, body.source, "A"), {
          status: 201,
        });
      }),
      http.post(
        `${API}/api/v1/strategy-documents/:strategyId/revisions`,
        async ({ request }) => {
          const body = (await request.json()) as {
            expected_revision: number;
            format: string;
            source: string;
          };
          revisionRequests.push(body);
          expect(body.expected_revision).toBe(1);
          return HttpResponse.json(document("s9", 2, body.source, "B"), {
            status: 201,
          });
        },
      ),
    );
    const history = mount("/research/strategies/new");
    const view = await editor();
    const first = 'schema_version: "1.0"\ntitle: A\n';
    const second = `${first}description: typed while saving\n`;
    replaceText(view, first);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(saveButton());
    replaceText(view, second);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    expect(history.location.pathname).toBe("/research/strategies/new");
    expect(view.state.doc.toString()).toBe(second);

    await user.click(saveButton());
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s9/revisions/2",
      ),
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(revisionRequests).toEqual([
      { expected_revision: 1, format: "yaml", source: second },
    ]);
  });

  it("keeps the draft and reports a validation failure without leaving the page", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/new");
    const view = await editor();
    replaceText(view, "title: bad\n");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "리비전 저장" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "리비전 저장" }));
    expect(
      await screen.findByText(
        "저장 실패: 문서 검증 오류: title must not be empty",
      ),
    ).toBeInTheDocument();
    expect(history.location.pathname).toBe("/research/strategies/new");
    expect(view.state.doc.toString()).toBe("title: bad\n");
  });

  it("blocks leaving a dirty draft until the user chooses, then lets them go", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/new");
    const view = await editor();
    replaceText(view, "title: 임시\n");
    await user.click(screen.getByRole("link", { name: "기존 편집기" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("저장하지 않은 변경이 있습니다");
    await user.click(within(dialog).getByRole("button", { name: "머무르기" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(history.location.pathname).toBe("/research/strategies/new");
    await user.click(screen.getByRole("link", { name: "기존 편집기" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "나가기",
      }),
    );
    await waitFor(() =>
      expect(history.location.pathname).toBe("/legacy/builder"),
    );
  });

  it("allows a same-page view switch while dirty but guards a real leave", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}description: dirty\n`);

    await user.click(screen.getByRole("tab", { name: "JSON" }));
    await waitFor(() => expect(history.location.search).toContain("view=json"));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await user.click(legacyLink());
    const dialog = await screen.findByRole("alertdialog");
    expect(globalThis.document.activeElement).toBe(
      within(dialog).getAllByRole("button")[0],
    );
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(globalThis.document.activeElement).toBe(
      within(dialog).getAllByRole("button")[1],
    );
    await user.keyboard("{Tab}");
    expect(globalThis.document.activeElement).toBe(
      within(dialog).getAllByRole("button")[0],
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/2",
    );
  });

  it("appends the next revision from a saved base and surfaces a stale-base conflict", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}description: 개정\n`);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "리비전 저장" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "리비전 저장" }));
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s1/revisions/3",
      ),
    );
    expect(posted).toEqual([
      {
        format: "yaml",
        source: `${STORED}description: 개정\n`,
        expected_revision: 2,
      },
    ]);
    expect(await screen.findByText("방금 저장됨")).toBeInTheDocument();

    // Someone else saved revision 4 meanwhile: revising from base 3 is refused, the draft stays.
    cleanup();
    mount("/research/strategies/s1/revisions/1");
    const second = await editor();
    replaceText(second, `${STORED}description: 충돌\n`);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "리비전 저장" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "리비전 저장" }));
    expect(await screen.findByText(/^충돌:/)).toBeInTheDocument();
    expect(second.state.doc.toString()).toBe(`${STORED}description: 충돌\n`);
  });

  it("removes stale strategy-list pages and ignores an older in-flight page after save", async () => {
    const user = userEvent.setup();
    const { history, queryClient } = mountWithClient(
      "/research/strategies/s1/revisions/2",
    );
    const list = strategiesQuery({ offset: 0, limit: 20 });
    const stalePage = {
      items: [
        {
          strategy_id: "s1",
          title: "Old title",
          latest_revision: 2,
          spec_hash: "2".repeat(64),
          updated_at: "2026-09-04T00:00:00Z",
        },
      ],
      total: 1,
      offset: 0,
      limit: 20,
    };
    queryClient.setQueryData(list.queryKey, stalePage);
    let releaseLatePage: (() => void) | undefined;
    const latePage = new Promise<void>((resolve) => {
      releaseLatePage = resolve;
    });
    const oldFetch = queryClient
      .fetchQuery({
        ...list,
        queryFn: async () => {
          await latePage;
          return stalePage;
        },
      })
      .catch(() => undefined);
    await waitFor(() =>
      expect(queryClient.getQueryState(list.queryKey)?.fetchStatus).toBe(
        "fetching",
      ),
    );

    const view = await editor();
    replaceText(view, `${STORED}description: latest\n`);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(saveButton());
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s1/revisions/3",
      ),
    );
    expect(await screen.findByText("방금 저장됨")).toBeInTheDocument();
    expect(queryClient.getQueryData(list.queryKey)).toBeUndefined();

    releaseLatePage?.();
    await oldFetch;
    expect(queryClient.getQueryData(list.queryKey)).toBeUndefined();
  });
});

describe("Strategy Outline route integration (P4-01)", () => {
  it("maps an edit-owned cursor only after the new source parse is current", async () => {
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    await screen.findByRole("tree", { name: "StrategySpec 문서 구조" });
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(history.location.search).not.toContain("path=");

    const addition = "description: 새 설명\n";
    const from = view.state.doc.length;
    act(() => {
      view.dispatch({
        changes: { from, insert: addition },
        selection: { anchor: from + addition.indexOf("새 설명") + 1 },
      });
    });

    await waitFor(() =>
      expect(history.location.search).toContain("path=%2Fdescription"),
    );
    expect(
      screen.getByRole("treeitem", {
        name: "description",
        selected: true,
      }),
    ).toBeInTheDocument();
  });

  it("keeps URL path, tree selection and source selection in sync through parse errors", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    const tree = await screen.findByRole("tree", {
      name: "StrategySpec 문서 구조",
    });
    const title = within(tree).getByRole("treeitem", { name: "title" });

    await user.click(title);
    await waitFor(() =>
      expect(history.location.search).toContain("path=%2Ftitle"),
    );
    const selectedText = view.state.sliceDoc(
      view.state.selection.main.from,
      view.state.selection.main.to,
    );
    expect(selectedText).toBe("퀄리티 모멘텀");

    act(() => {
      const offset = view.state.doc.toString().indexOf("schema_version") + 2;
      view.dispatch({ selection: { anchor: offset } });
    });
    await waitFor(() =>
      expect(history.location.search).toContain("path=%2Fschema_version"),
    );
    expect(
      within(tree).getByRole("treeitem", {
        name: "schema_version",
        selected: true,
      }),
    ).toBeInTheDocument();

    replaceText(view, `${STORED}broken: [\n`);
    expect(
      await screen.findByText("문법 오류 전 마지막 정상 구조를 표시합니다."),
    ).toBeInTheDocument();
    expect(within(tree).getByRole("treeitem", { name: "title" })).toBeVisible();
  });

  it("switches a read-only projection to source and reveals the selected path atomically", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=json");
    expect(
      await screen.findByLabelText("StrategySpec JSON"),
    ).toBeInTheDocument();
    const tree = await screen.findByRole("tree", {
      name: "StrategySpec 문서 구조",
    });
    await user.click(within(tree).getByRole("treeitem", { name: "title" }));
    await waitFor(() => {
      expect(history.location.search).toContain("path=%2Ftitle");
      expect(history.location.search).not.toContain("view=json");
    });
    const view = await editor();
    await waitFor(() =>
      expect(
        view.state.sliceDoc(
          view.state.selection.main.from,
          view.state.selection.main.to,
        ),
      ).toBe("퀄리티 모멘텀"),
    );
  });
});

describe("StrategySpec JSON and Form projections (P4-06)", () => {
  it.each(["/research/strategies/new", "/research/strategies/s1/revisions/2"])(
    "shows the same backend-owned projections on %s",
    async (route) => {
      const user = userEvent.setup();
      mount(route);
      await editor();

      await user.click(screen.getByRole("tab", { name: "JSON" }));
      const json = await screen.findByLabelText("StrategySpec JSON");
      await waitFor(() => expect(json).toBeVisible());
      expect(json.textContent).toContain('"market":"KRX"');
      expect(json.textContent).toContain('"schema_version":"1.0"');
      expect(json.textContent).not.toContain("identity");
      expect(within(json).getByText("현재 문서")).toBeInTheDocument();

      await user.click(screen.getByRole("tab", { name: "Form" }));
      const form = await screen.findByLabelText("StrategySpec 요약 Form");
      await waitFor(() => expect(form).toBeVisible());
      expect(within(form).getByText('"KRX"')).toBeInTheDocument();
      expect(within(form).getByText("15")).toBeInTheDocument();
      expect(within(form).queryByText("strategy_id")).not.toBeInTheDocument();
      expect(within(form).queryByText("revision")).not.toBeInTheDocument();
      expect(within(form).queryByRole("textbox")).not.toBeInTheDocument();
    },
  );

  it("keeps the exact source, selection, editor identity and undo history across views", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    const edited = `${STORED}description: tab-preserved\n`;
    replaceText(view, edited);
    act(() =>
      view.dispatch({
        selection: { anchor: edited.indexOf("tab-preserved") + 3 },
        annotations: Transaction.addToHistory.of(false),
      }),
    );
    const selection = view.state.selection.main.anchor;
    await waitFor(() => expect(saveButton()).toBeEnabled());

    await user.click(screen.getByRole("tab", { name: "Form" }));
    await waitFor(() =>
      expect(screen.getByLabelText("StrategySpec 요약 Form")).toBeVisible(),
    );
    const hiddenContent = globalThis.document.querySelector(".cm-content");
    expect(hiddenContent).not.toBeNull();
    expect(EditorView.findFromDOM(hiddenContent as HTMLElement)).toBe(view);
    expect(view.state.doc.toString()).toBe(edited);
    expect(view.state.selection.main.anchor).toBe(selection);

    await user.click(screen.getByRole("tab", { name: "YAML" }));
    await waitFor(() =>
      expect(globalThis.document.querySelector(".cm-content")).toBeVisible(),
    );
    expect(
      EditorView.findFromDOM(globalThis.document.querySelector(".cm-content")!),
    ).toBe(view);
    expect(view.state.doc.toString()).toBe(edited);
    expect(view.state.selection.main.anchor).toBe(selection);
    act(() => expect(undo(view)).toBe(true));
    expect(view.state.doc.toString()).toBe(STORED);
  });

  it("keeps a stored JSON document as the editable source while Form stays read-only", async () => {
    const jsonSource = '{"schema_version":"1.0","title":"JSON source"}';
    server.use(
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        () =>
          HttpResponse.json({
            ...document("s1", 2, jsonSource, "JSON source"),
            format: "json",
          }),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    expect(view.state.doc.toString()).toBe(jsonSource);
    expect(screen.getByRole("tab", { name: "JSON" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    await user.click(screen.getByRole("tab", { name: "Form" }));
    const form = await screen.findByLabelText("StrategySpec 요약 Form");
    expect(form).toBeVisible();
    expect(within(form).queryByRole("textbox")).not.toBeInTheDocument();
    expect(view.state.doc.toString()).toBe(jsonSource);

    await user.click(screen.getByRole("tab", { name: "JSON" }));
    expect(view.state.doc.toString()).toBe(jsonSource);
  });

  it("labels the same-document last valid projection stale and never enables execution", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, 'schema_version: "1.0"\ntitle: last-valid\n');
    await waitFor(() => expect(saveButton()).toBeEnabled());

    replaceText(view, 'schema_version: "1.0"\ntitle: [broken\n');
    await user.click(screen.getByRole("tab", { name: "Form" }));
    const form = await screen.findByLabelText("StrategySpec 요약 Form");
    expect(within(form).getByText("STALE")).toBeInTheDocument();
    expect(within(form).getByText('"last-valid"')).toBeInTheDocument();
    expect(within(form).getByRole("status")).toHaveTextContent(
      "저장·실행에는 사용되지 않습니다",
    );
    expect(saveButton()).toBeDisabled();
    for (const run of screen.getAllByRole("button", { name: /백테스트 실행/ }))
      expect(run).toBeDisabled();
  });
});

describe("FactorGraph read-only projection (P4-07)", () => {
  const graphHandlers = () => [
    http.post(`${API}/api/v1/strategy-documents/compile`, () => {
      const compiledSpec = graphSpec("draft", 0);
      const { identity, ...canonicalSpec } = compiledSpec;
      return HttpResponse.json({
        format: "yaml",
        source_hash: "b".repeat(64),
        schema_version: "1.0",
        spec: compiledSpec,
        canonical_json: JSON.stringify({
          ...canonicalSpec,
          schema_version: identity.schema_version,
        }),
        spec_hash: "7".repeat(64),
        diagnostics: [],
      });
    }),
    http.get(`${API}/api/v1/strategy-documents/schema`, () =>
      HttpResponse.json({
        schema: RUNTIME_SCHEMA,
        schema_hash: "h".repeat(64),
        schema_version: "1.0",
      }),
    ),
    http.get(`${API}/api/v1/equity/catalog`, () =>
      HttpResponse.json({
        snapshot: {
          snapshot_id: "snap",
          schema_version: "1.0",
          built_at: "2026-09-05T00:00:00Z",
          source: "route-test",
          point_in_time: true,
          dataset_revisions: [],
        },
        total: 0,
        page: 1,
        page_size: 100,
        page_count: 0,
        fields: [],
        facets: { dataset_ids: [], units: [], frequencies: [] },
      }),
    ),
    http.post(`${API}/api/v1/factors/explain`, async ({ request }) => {
      explainedGraphs.push(await request.json());
      return HttpResponse.json({
        registry_version: "v1",
        data_snapshot_id: "snap",
        narrative: [],
        validation: {
          valid: true,
          issues: [],
          node_contracts: [
            {
              node_id: "close",
              value_type: "numeric_series",
              unit: "KRW",
              minimum_history_sessions: 1,
            },
            {
              node_id: "mom_252",
              value_type: "numeric_series",
              unit: "ratio",
              minimum_history_sessions: 252,
            },
          ],
          minimum_history_sessions: 252,
          required_field_ids: ["price.close"],
        },
        plan: {
          graph_hash: "g".repeat(64),
          plan_hash: "p".repeat(64),
          registry_version: "v1",
          output_node_id: "mom_252",
          steps: [
            {
              sequence: 1,
              node_id: "close",
              operation: "field",
              input_node_ids: [],
              output_type: "numeric_series",
              output_unit: "KRW",
              minimum_history_sessions: 1,
            },
            {
              sequence: 2,
              node_id: "mom_252",
              operation: "time_series.momentum",
              input_node_ids: ["close"],
              output_type: "numeric_series",
              output_unit: "ratio",
              minimum_history_sessions: 252,
            },
          ],
          required_field_ids: ["price.close"],
          referenced_factor_ids: [],
          referenced_subgraph_ids: [],
          minimum_history_sessions: 252,
          missing_policy: "drop",
          as_of_policy: "available_date_lte_as_of",
        },
      });
    }),
  ];

  it.each(["/research/strategies/new", "/research/strategies/s1/revisions/2"])(
    "renders the same backend-owned DAG on %s",
    async (route) => {
      server.use(...graphHandlers());
      const user = userEvent.setup();
      mount(`${route}?view=graph`);
      await screen.findByLabelText("FactorGraph DAG");
      await waitFor(() => expect(explainedGraphs).toHaveLength(1));
      const node = await screen.findByRole("button", {
        name: "그래프 노드 선택: mom_252",
      });
      const graph = screen.getByLabelText("FactorGraph DAG");
      expect(within(graph).getByText("time_series.momentum")).toBeVisible();
      expect(
        within(graph).getAllByText("numeric_series").length,
      ).toBeGreaterThan(0);
      expect(within(graph).getAllByText("ratio").length).toBeGreaterThan(0);
      expect(within(graph).getAllByText("252 세션").length).toBeGreaterThan(0);
      await user.click(node);
    },
    15_000,
  );

  it("keeps graph selection in the URL, then opens the exact YAML node", async () => {
    server.use(
      ...graphHandlers(),
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        () =>
          HttpResponse.json({
            ...document("s1", 2, GRAPH_SOURCE, "그래프 전략"),
            spec: graphSpec("s1", 2),
          }),
      ),
    );
    const user = userEvent.setup();
    const nodePath = "%2Ffactors%2Ffactors%2F0%2Fgraph%2Fnodes%2F1";
    const history = mount(
      `/research/strategies/s1/revisions/2?path=${nodePath}`,
    );
    const sourceView = await editor();
    await waitFor(() =>
      expect(
        sourceView.state.sliceDoc(
          sourceView.state.selection.main.from,
          sourceView.state.selection.main.to,
        ),
      ).toContain("node_id: mom_252"),
    );
    await user.click(screen.getByRole("tab", { name: "Graph" }));
    await screen.findByLabelText("FactorGraph DAG");
    const node = await screen.findByRole("button", {
      name: "그래프 노드 선택: mom_252",
    });
    await user.click(node);
    await waitFor(() => {
      expect(history.location.search).toContain("view=graph");
      expect(history.location.search).toContain(
        "path=%2Ffactors%2Ffactors%2F0%2Fgraph%2Fnodes%2F1",
      );
    });

    const nodeCard = globalThis.document.querySelector(
      '[data-node-id="mom_252"]',
    );
    await user.click(
      within(nodeCard as HTMLElement).getByText("소스에서 열기"),
    );
    await waitFor(() =>
      expect(history.location.search).not.toContain("view=graph"),
    );
    const view = await editor();
    await waitFor(() =>
      expect(
        view.state.sliceDoc(
          view.state.selection.main.from,
          view.state.selection.main.to,
        ),
      ).toContain("node_id: mom_252"),
    );
  }, 15_000);

  it("connects URL-owned trace scope to the generated API on the new-strategy route", async () => {
    server.use(
      ...graphHandlers(),
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown> & {
          as_of?: string;
          node_ids: string[];
          security_ids: string[];
        };
        tracedStrategies.push(body);
        const resolvedAsOf = body.as_of ?? "2026-09-01";
        return HttpResponse.json({
          spec_hash: "7".repeat(64),
          snapshot_id: "snap",
          registry_version: "v1",
          plan_hash: "p".repeat(64),
          factor_id: "momentum",
          as_of: resolvedAsOf,
          provenance: {
            kind: "inline_draft",
            schema_version: "1.0",
            spec_hash: "7".repeat(64),
            source_hash: "b".repeat(64),
            strategy_id: null,
            revision: null,
          },
          raw: [],
          raw_truncated: false,
          warnings: [],
          trace: {
            rows: body.node_ids.flatMap((nodeId) =>
              body.security_ids.map((securityId) => ({
                node_id: nodeId,
                operation:
                  nodeId === "close" ? "field" : "time_series.momentum",
                as_of: resolvedAsOf,
                security_id: securityId,
                value: nodeId === "close" ? 10 : 0.2,
                status: "ok",
                inputs:
                  nodeId === "close" ? [] : [{ node_id: "close", value: 10 }],
              })),
            ),
            offset: 0,
            limit: body.security_ids.length * body.node_ids.length,
            returned: body.security_ids.length * body.node_ids.length,
            has_more: false,
          },
          target: {
            signal_as_of: resolvedAsOf,
            execution_on: "2026-09-02",
            candidates: body.security_ids.map((securityId, index) => ({
              as_of: resolvedAsOf,
              security_id: securityId,
              sector_id: null,
              eligible: true,
              composite_score: 0.2,
              rank: index + 1,
              selected: true,
              side: "long",
              exclusion_reasons: [],
              target_weight: 0.05,
            })),
            targets: body.security_ids.map((securityId, index) => ({
              security_id: securityId,
              side: "long",
              weight: 0.05,
              composite_score: 0.2,
              rank: index + 1,
            })),
            construction: body.security_ids.map((securityId, index) => ({
              as_of: resolvedAsOf,
              security_id: securityId,
              factor_contributions: [
                {
                  factor_id: "momentum",
                  value: 0.2,
                  configured_weight: 1,
                  direction: "high",
                  weighted_value: 0.2,
                  normalized_contribution: 0.2,
                  status: "ok",
                },
              ],
              composite_score: 0.2,
              rank: index + 1,
              eligible: true,
              selected: true,
              side: "long",
              unconstrained_target_weight: 0.05,
              constrained_target_weight: 0.05,
              previous_weight: null,
              estimated_order_delta: null,
              constraint_effect: "unchanged",
              exclusion_reasons: [],
            })),
          },
        });
      }),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/new");
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "종목 ID" })).toBeEnabled(),
    );
    const security = screen.getByRole("textbox", { name: "종목 ID" });
    expect(screen.getByRole("button", { name: "추적 실행" })).toBeDisabled();

    await user.type(security, "sec-a, sec-b");
    expect(security).toHaveValue("sec-a, sec-b");
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await waitFor(() =>
      expect(history.location.search).toContain("security=sec-a%2C+sec-b"),
    );

    expect(await screen.findAllByText("5.00%")).toHaveLength(4);
    expect(tracedStrategies).toHaveLength(1);
    expect(tracedStrategies[0]).toMatchObject({
      security_ids: ["sec-a", "sec-b"],
      factor_id: "momentum",
      node_ids: ["close", "mom_252"],
      strategy_source: {
        kind: "inline_draft",
        source_hash: "b".repeat(64),
      },
    });
    expect(tracedStrategies[0]).not.toHaveProperty("as_of");
    expect(screen.getByText("2026-09-01")).toBeInTheDocument();
  }, 15_000);

  it("restores revision trace scope from history and sends the saved revision source", async () => {
    const specHash = "7".repeat(64);
    server.use(
      ...graphHandlers(),
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        () =>
          HttpResponse.json({
            ...document("s1", 2, GRAPH_SOURCE, "그래프 전략"),
            spec: graphSpec("s1", 2),
            spec_hash: specHash,
          }),
      ),
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown> & {
          as_of: string;
          node_ids: string[];
          security_ids: string[];
        };
        tracedStrategies.push(body);
        return HttpResponse.json({
          spec_hash: specHash,
          snapshot_id: "snap",
          registry_version: "v1",
          plan_hash: "p".repeat(64),
          factor_id: "momentum",
          as_of: body.as_of,
          provenance: {
            kind: "saved_revision",
            schema_version: "1.0",
            spec_hash: specHash,
            source_hash: "b".repeat(64),
            strategy_id: "s1",
            revision: 2,
          },
          raw: [],
          raw_truncated: false,
          warnings: [],
          trace: {
            rows: body.node_ids.flatMap((nodeId) =>
              body.security_ids.map((securityId) => ({
                node_id: nodeId,
                operation:
                  nodeId === "close" ? "field" : "time_series.momentum",
                as_of: body.as_of,
                security_id: securityId,
                value: nodeId === "close" ? 10 : 0.2,
                status: "ok",
                inputs:
                  nodeId === "close" ? [] : [{ node_id: "close", value: 10 }],
              })),
            ),
            offset: 0,
            limit: body.security_ids.length * body.node_ids.length,
            returned: body.security_ids.length * body.node_ids.length,
            has_more: false,
          },
          target: {
            signal_as_of: body.as_of,
            execution_on: "2026-09-01",
            candidates: body.security_ids.map((securityId) => ({
              as_of: body.as_of,
              security_id: securityId,
              sector_id: null,
              eligible: true,
              composite_score: 0.2,
              rank: 1,
              selected: true,
              side: "long",
              exclusion_reasons: [],
              target_weight: 0.05,
            })),
            targets: body.security_ids.map((securityId) => ({
              security_id: securityId,
              side: "long",
              weight: 0.05,
              composite_score: 0.2,
              rank: 1,
            })),
            construction: body.security_ids.map((securityId) => ({
              as_of: body.as_of,
              security_id: securityId,
              factor_contributions: [
                {
                  factor_id: "momentum",
                  value: 0.2,
                  configured_weight: 1,
                  direction: "high",
                  weighted_value: 0.2,
                  normalized_contribution: 0.2,
                  status: "ok",
                },
              ],
              composite_score: 0.2,
              rank: 1,
              eligible: true,
              selected: true,
              side: "long",
              unconstrained_target_weight: 0.05,
              constrained_target_weight: 0.05,
              previous_weight: null,
              estimated_order_delta: null,
              constraint_effect: "unchanged",
              exclusion_reasons: [],
            })),
          },
        });
      }),
    );
    const nodePath = "%2Ffactors%2Ffactors%2F0%2Fgraph%2Fnodes%2F1";
    const initial = `/research/strategies/s1/revisions/2?asOf=2026-08-31&security=sec-r&path=${nodePath}`;
    const history = mount(initial);
    const user = userEvent.setup();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "추적 실행" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("기준일")).toHaveValue("2026-08-31");
    expect(screen.getByRole("textbox", { name: "종목 ID" })).toHaveValue(
      "sec-r",
    );
    expect(screen.getByRole("combobox", { name: "노드" })).toHaveValue(
      "mom_252",
    );
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(await screen.findAllByText("5.00%")).toHaveLength(2);
    expect(tracedStrategies[0]).toMatchObject({
      as_of: "2026-08-31",
      security_ids: ["sec-r"],
      node_ids: ["close", "mom_252"],
      strategy_source: {
        kind: "saved_revision",
        strategy_id: "s1",
        revision: 2,
        expected_spec_hash: specHash,
      },
    });

    act(() => {
      history.push(
        `/research/strategies/s1/revisions/2?asOf=2026-08-30&security=sec-next&path=%2Ffactors%2Ffactors%2F0%2Fgraph%2Fnodes%2F0`,
      );
    });
    await waitFor(() => {
      expect(screen.getByLabelText("기준일")).toHaveValue("2026-08-30");
      expect(screen.getByRole("textbox", { name: "종목 ID" })).toHaveValue(
        "sec-next",
      );
      expect(screen.getByRole("combobox", { name: "노드" })).toHaveValue(
        "close",
      );
    });

    act(() => history.back());
    await waitFor(() => {
      expect(screen.getByLabelText("기준일")).toHaveValue("2026-08-31");
      expect(screen.getByRole("textbox", { name: "종목 ID" })).toHaveValue(
        "sec-r",
      );
      expect(screen.getByRole("combobox", { name: "노드" })).toHaveValue(
        "mom_252",
      );
    });
    act(() => history.forward());
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "종목 ID" })).toHaveValue(
        "sec-next",
      ),
    );
  }, 15_000);
});

describe("StrategySpec Diff projection (P4-08)", () => {
  it("separates comment-only source changes from backend-proven semantic changes and stays text-only when invalid", async () => {
    server.use(
      http.post(
        `${API}/api/v1/strategy-documents/compile`,
        async ({ request }) => {
          const body = (await request.json()) as { source: string };
          const title = /title: ([^\n]*)/.exec(body.source)?.[1] ?? "";
          const compiledSpec = spec("draft", 0, title);
          const { identity, ...canonicalSpec } = compiledSpec;
          return HttpResponse.json({
            format: "yaml",
            source_hash: "b".repeat(64),
            schema_version: "1.0",
            spec: compiledSpec,
            canonical_json: JSON.stringify({
              ...canonicalSpec,
              schema_version: identity.schema_version,
            }),
            spec_hash:
              title === "퀄리티 모멘텀" ? "2".repeat(64) : "9".repeat(64),
            diagnostics: [],
          });
        },
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}# research note\n`);
    await waitFor(() => expect(saveButton()).toBeEnabled());

    await user.click(screen.getByRole("tab", { name: "Diff" }));
    const panel = await screen.findByLabelText("StrategySpec Diff");
    expect(within(panel).getByText("+1 / −0 변경 항목")).toBeInTheDocument();
    expect(within(panel).getByText("# research note")).toBeInTheDocument();
    expect(
      within(panel).getByText("의미 변경이 없습니다 (같은 spec hash)."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "YAML" }));
    const invalidView = await editor();
    replaceText(invalidView, 'schema_version: "1.0"\ntitle: "broken\n');
    await waitFor(() => expect(saveButton()).toBeDisabled());
    await user.click(screen.getByRole("tab", { name: "Diff" }));
    const invalidPanel = await screen.findByLabelText("StrategySpec Diff");
    expect(
      await within(invalidPanel).findByText(
        "현재 원문이 유효하게 compile되지 않아 원문 Diff만 제공합니다.",
      ),
    ).toBeInTheDocument();
    expect(
      within(invalidPanel).getByText("+1 / −1 변경 항목"),
    ).toBeInTheDocument();
  });

  it("compares exact stored sources and backend semantic revision diff", async () => {
    const revisionSource = (revision: number) =>
      revision === 1
        ? 'schema_version: "1.0"\ntitle: 이전 전략\n'
        : revision === 3
          ? `${STORED}description: 서버 최신\n`
          : STORED;
    server.use(
      http.get(`${API}/api/v1/strategies/:strategyId/revisions`, () =>
        HttpResponse.json({
          items: [1, 2, 3].map((revision) => ({
            strategy_id: "s1",
            revision,
            spec_hash: String(revision).repeat(64).slice(0, 64),
            source_hash: "b".repeat(64),
            source_format: "yaml",
            origin: "document",
            change_note: null,
            created_at: `2026-09-0${revision}T09:30:00+00:00`,
          })),
          offset: 0,
          limit: 50,
          total: 3,
        }),
      ),
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        ({ params }) => {
          const revision = Number(params.revision);
          return HttpResponse.json(
            document("s1", revision, revisionSource(revision)),
          );
        },
      ),
    );

    mount("/research/strategies/s1/revisions/2?view=diff");
    const panel = await screen.findByLabelText("StrategySpec Diff");
    const base = await within(panel).findByRole("combobox", {
      name: "기준 revision",
    });
    const target = within(panel).getByRole("combobox", {
      name: "대상 revision",
    });
    expect(base).toHaveValue("1");
    expect(target).toHaveValue("2");
    expect(
      await within(panel).findByText("title: 이전 전략"),
    ).toBeInTheDocument();
    expect(within(panel).getByText("title: 퀄리티 모멘텀")).toBeInTheDocument();
    expect(
      await within(panel).findByText("/risk/max_name_weight"),
    ).toBeInTheDocument();
    expect(within(panel).getByText('"서버에서 수정"')).toBeInTheDocument();
  });

  it("recompiles a saved source when Save returns a different spec hash", async () => {
    const savedSource = `${STORED}description: saved\n`;
    const currentSource = `${STORED}description: after\n`;
    const preSaveHash = "a".repeat(64);
    const savedHash = "c".repeat(64);
    const currentHash = "d".repeat(64);
    const compileSources: string[] = [];
    let saveCompleted = false;
    server.use(
      http.post(
        `${API}/api/v1/strategy-documents/compile`,
        async ({ request }) => {
          const body = (await request.json()) as { source: string };
          compileSources.push(body.source);
          const title = /title: ([^\n]*)/.exec(body.source)?.[1] ?? "";
          const compiledSpec = spec("draft", 0, title);
          const semantic =
            body.source === currentSource
              ? { deployment: "B", description: "after" }
              : body.source === savedSource
                ? {
                    deployment: saveCompleted ? "B" : "A",
                    description: "saved",
                  }
                : { deployment: "base", description: "stored" };
          const specHash =
            body.source === currentSource
              ? currentHash
              : body.source === savedSource
                ? saveCompleted
                  ? savedHash
                  : preSaveHash
                : "2".repeat(64);
          return HttpResponse.json({
            format: "yaml",
            source_hash: "b".repeat(64),
            schema_version: "1.0",
            spec: compiledSpec,
            canonical_json: JSON.stringify(semantic),
            spec_hash: specHash,
            diagnostics: [],
          });
        },
      ),
      http.post(
        `${API}/api/v1/strategy-documents/:strategyId/revisions`,
        async ({ params, request }) => {
          const body = (await request.json()) as { source: string };
          await delay(450);
          saveCompleted = true;
          return HttpResponse.json(
            {
              ...document(String(params.strategyId), 3, body.source),
              spec_hash: savedHash,
            },
            { status: 201 },
          );
        },
      ),
    );

    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, savedSource);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(saveButton());

    // Keep typing while Save is in flight. The route must stay on the dirty draft while the
    // exact source returned by Save is recompiled as its new immutable semantic baseline.
    replaceText(view, currentSource);
    await waitFor(() => expect(saveButton()).toBeEnabled(), { timeout: 4_000 });
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/2",
    );

    await user.click(screen.getByRole("tab", { name: "Diff" }));
    const panel = await screen.findByLabelText("StrategySpec Diff");
    expect(await within(panel).findByText("/description")).toBeInTheDocument();
    expect(within(panel).getByText('"saved"')).toBeInTheDocument();
    expect(within(panel).getByText('"after"')).toBeInTheDocument();
    expect(within(panel).queryByText("/deployment")).not.toBeInTheDocument();
    expect(
      compileSources.filter((source) => source === savedSource),
    ).toHaveLength(2);
  }, 10_000);

  it("retries a transient saved-baseline failure on the next edit", async () => {
    let allowBaselineSuccess = false;
    let baselineAttempts = 0;
    server.use(
      http.post(
        `${API}/api/v1/strategy-documents/compile`,
        async ({ request }) => {
          const body = (await request.json()) as { source: string };
          if (body.source === STORED) {
            baselineAttempts += 1;
            if (!allowBaselineSuccess) {
              return HttpResponse.json(
                { detail: "temporary baseline outage" },
                { status: 503 },
              );
            }
          }
          const title = /title: ([^\n]*)/.exec(body.source)?.[1] ?? "";
          const compiledSpec = spec("draft", 0, title);
          return HttpResponse.json({
            format: "yaml",
            source_hash: "b".repeat(64),
            schema_version: "1.0",
            spec: compiledSpec,
            canonical_json: '{"schema_version":"1.0","title":"same"}',
            spec_hash: "2".repeat(64),
            diagnostics: [],
          });
        },
      ),
    );

    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}# first edit\n`);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(screen.getByRole("tab", { name: "Diff" }));
    const panel = await screen.findByLabelText("StrategySpec Diff");
    expect(
      await within(panel).findByText("저장본 canonical 기준을 검증 중입니다."),
    ).toBeInTheDocument();
    const failedAttempts = baselineAttempts;
    expect(failedAttempts).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "검증" }));
    await waitFor(() =>
      expect(baselineAttempts).toBeGreaterThan(failedAttempts),
    );
    const explicitRetryAttempts = baselineAttempts;

    allowBaselineSuccess = true;
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    const currentView = await editor();
    replaceText(currentView, `${STORED}# second edit\n`);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(screen.getByRole("tab", { name: "Diff" }));
    const retriedPanel = await screen.findByLabelText("StrategySpec Diff");
    expect(
      await within(retriedPanel).findByText(
        "의미 변경이 없습니다 (같은 spec hash).",
      ),
    ).toBeInTheDocument();
    expect(baselineAttempts).toBeGreaterThan(explicitRetryAttempts);
  }, 10_000);

  it("loads the bounded history page that contains a route beyond revision 50", async () => {
    let historyOffset: string | null = null;
    let historyLimit: string | null = null;
    let requestedPair: [string | null, string | null] | null = null;
    server.use(
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions`,
        ({ request }) => {
          const url = new URL(request.url);
          historyOffset = url.searchParams.get("offset");
          historyLimit = url.searchParams.get("limit");
          const offset = Number(historyOffset ?? 0);
          return HttpResponse.json({
            items: Array.from({ length: 50 }, (_, index) => {
              const revision = offset + index + 1;
              return {
                strategy_id: "s1",
                revision,
                spec_hash: String(revision).repeat(64).slice(0, 64),
                source_hash: "b".repeat(64),
                source_format: "yaml",
                origin: "document",
                change_note: null,
                created_at: "2026-09-05T09:30:00+00:00",
              };
            }),
            offset,
            limit: 50,
            total: 51,
          });
        },
      ),
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        ({ params }) => {
          const revision = Number(params.revision);
          return HttpResponse.json(
            document(
              String(params.strategyId),
              revision,
              `schema_version: "1.0"\ntitle: revision ${revision}\n`,
              `revision ${revision}`,
            ),
          );
        },
      ),
      http.get(`${API}/api/v1/strategies/:strategyId/diff`, ({ request }) => {
        const url = new URL(request.url);
        requestedPair = [
          url.searchParams.get("base"),
          url.searchParams.get("target"),
        ];
        return HttpResponse.json({
          strategy_id: "s1",
          base_revision: Number(requestedPair[0]),
          target_revision: Number(requestedPair[1]),
          base_spec_hash: "5".repeat(64),
          target_spec_hash: "6".repeat(64),
          changes: [],
        });
      }),
    );

    mount("/research/strategies/s1/revisions/51?view=diff");
    const panel = await screen.findByLabelText("StrategySpec Diff");
    const base = await within(panel).findByRole("combobox", {
      name: "기준 revision",
    });
    const target = within(panel).getByRole("combobox", {
      name: "대상 revision",
    });
    expect(historyOffset).toBe("1");
    expect(historyLimit).toBe("50");
    expect(base).toHaveValue("50");
    expect(target).toHaveValue("51");
    await waitFor(() => expect(requestedPair).toEqual(["50", "51"]));
  });
});

describe("backtest from the editor (P3-05)", () => {
  it("runs a clean saved revision by reference and moves to the run page", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    await editor();
    const run = screen.getByRole("button", { name: /백테스트 실행/ });
    await waitFor(() => expect(run).toBeEnabled());
    expect(screen.getAllByText("2".repeat(12) + "…").length).toBeGreaterThan(0); // backend spec hash
    await user.click(run);
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/backtests/run-7"),
    );
    expect(started).toEqual([
      {
        strategy_source: {
          kind: "saved_revision",
          strategy_id: "s1",
          revision: 2,
          expected_spec_hash: "2".repeat(64),
        },
      },
    ]);
  });

  it("runs an edited document as an inline draft with provenance, and never while invalid", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}description: 개정\n`);
    const run = screen.getByRole("button", { name: /백테스트 실행/ });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);
    await waitFor(() => expect(started).toHaveLength(1));
    expect(started[0]).toEqual({
      strategy_source: {
        kind: "inline_draft",
        spec: expect.objectContaining({ title: "퀄리티 모멘텀" }),
        source_hash: "b".repeat(64),
      },
    });
    const firstPrompt = await screen.findByRole("alertdialog");
    await user.click(
      within(firstPrompt).getByRole("button", { name: "머무르기" }),
    );
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/2",
    );
    expect(screen.getByText("백테스트 run-7 접수됨")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "백테스트 보기" }));
    const secondPrompt = await screen.findByRole("alertdialog");
    expect(started).toHaveLength(1);
    await user.click(
      within(secondPrompt).getByRole("button", { name: "나가기" }),
    );
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/backtests/run-7"),
    );
    cleanup();
    mount("/research/strategies/new");
    const fresh = await editor();
    replaceText(fresh, "title: [broken\n");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /백테스트 실행/ }),
      ).toBeDisabled(),
    );
    expect(screen.getByRole("button", { name: "리비전 저장" })).toBeDisabled();
  });

  it("does not let a run response from an older document replace the current route", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests`, async ({ request }) => {
        started.push((await request.json()) as Record<string, unknown>);
        await delay(200);
        return HttpResponse.json(acceptedRun("run-stale"), { status: 202 });
      }),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    await editor();
    const run = screen.getByRole("button", { name: /백테스트 실행/ });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);
    await waitFor(() => expect(started).toHaveLength(1));

    history.push("/research/strategies/s1/revisions/1");
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s1/revisions/1",
      ),
    );
    await editor();
    await new Promise((resolve) => setTimeout(resolve, 300));

    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/1",
    );
    expect(screen.queryByText(/run-stale.*접수됨/)).not.toBeInTheDocument();
  });
});

describe("revision conflict (P3-07)", () => {
  it("keeps the conflict recovery notice visible from the Diff view", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/1");
    const view = await editor();
    replaceText(view, `${STORED}description: 충돌\n`);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(screen.getByRole("tab", { name: "Diff" }));
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(saveButton());

    const banner = await screen.findByRole("region", { name: "리비전 충돌" });
    expect(
      within(banner).getByText("서버 최신 v3 · 현재 기준 v1"),
    ).toBeVisible();
    expect(
      within(banner).getByRole("button", { name: "현재 문서 복사" }),
    ).toBeVisible();
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("keeps the text, names both revisions, and offers open / copy / diff", async () => {
    const serverSource = `${STORED}description: 서버 최신\n`;
    server.use(
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        ({ params }) =>
          HttpResponse.json(
            document(
              String(params.strategyId),
              Number(params.revision),
              Number(params.revision) === 3 ? serverSource : STORED,
            ),
          ),
      ),
    );
    const user = userEvent.setup();
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const history = mount("/research/strategies/s1/revisions/1");
    const view = await editor();
    replaceText(view, `${STORED}description: 충돌\n`);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "리비전 저장" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "리비전 저장" }));
    const banner = await screen.findByRole("region", { name: "리비전 충돌" });
    expect(banner).toHaveTextContent("서버 최신 v3 · 현재 기준 v1");
    expect(view.state.doc.toString()).toBe(`${STORED}description: 충돌\n`);
    expect(
      within(banner).getByRole("link", { name: "서버본 열기 (v3)" }),
    ).toHaveAttribute("href", "/research/strategies/s1/revisions/3");
    await user.click(
      within(banner).getByRole("button", { name: "현재 문서 복사" }),
    );
    expect(writeText).toHaveBeenCalledWith(`${STORED}description: 충돌\n`);
    expect(await within(banner).findByRole("status")).toHaveTextContent(
      "복사했습니다",
    );
    await user.click(within(banner).getByRole("button", { name: "Diff 열기" }));
    expect(
      await within(banner).findByText("v1 → v3 의미 변경"),
    ).toBeInTheDocument();
    expect(
      within(banner).getByText("/risk/max_name_weight"),
    ).toBeInTheDocument();
    expect(within(banner).getByText('"서버에서 수정"')).toBeInTheDocument();
    expect(within(banner).getByText("수정")).toBeInTheDocument();
    expect(within(banner).getByText("추가")).toBeInTheDocument();
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/1",
    );

    await user.click(
      within(banner).getByRole("link", { name: "서버본 열기 (v3)" }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "나가기",
      }),
    );
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s1/revisions/3",
      ),
    );
    const serverView = await editor();
    expect(serverView.state.doc.toString()).toBe(serverSource);
    expect(
      screen.queryByRole("region", { name: "리비전 충돌" }),
    ).not.toBeInTheDocument();
  });

  it("drops a delayed 409 after the editor loads another revision", async () => {
    server.use(
      http.post(
        `${API}/api/v1/strategy-documents/:strategyId/revisions`,
        async () => {
          await delay(200);
          return HttpResponse.json(
            {
              detail: {
                code: "strategy.revision_conflict",
                message: "message wording is not an identity contract",
                latest_revision: 3,
              },
            },
            { status: 409 },
          );
        },
      ),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/1");
    const first = await editor();
    replaceText(first, `${STORED}description: 이전 문서\n`);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(saveButton());

    history.push("/research/strategies/s1/revisions/2");
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "나가기",
      }),
    );
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s1/revisions/2",
      ),
    );
    const second = await editor();
    expect(second.state.doc.toString()).toBe(STORED);
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(
      screen.queryByRole("region", { name: "리비전 충돌" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/^충돌:/)).not.toBeInTheDocument();
  });

  it("explicitly publishes the preserved whole document after the latest immutable revision", async () => {
    const expectedRevisions: number[] = [];
    server.use(
      http.post(
        `${API}/api/v1/strategy-documents/:strategyId/revisions`,
        async ({ params, request }) => {
          const body = (await request.json()) as {
            expected_revision: number;
            source: string;
          };
          expectedRevisions.push(body.expected_revision);
          if (body.expected_revision === 1) {
            return HttpResponse.json(
              {
                detail: {
                  code: "strategy.revision_conflict",
                  message: "another author saved first",
                  latest_revision: 3,
                },
              },
              { status: 409 },
            );
          }
          return HttpResponse.json(
            document(String(params.strategyId), 4, body.source),
            { status: 201 },
          );
        },
      ),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/1");
    const view = await editor();
    const preserved = `${STORED}description: 내 전체 문서\n`;
    replaceText(view, preserved);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(saveButton());

    const banner = await screen.findByRole("region", { name: "리비전 충돌" });
    expect(banner).toHaveTextContent("자동 병합은 제공하지 않습니다");
    await user.click(
      within(banner).getByRole("button", {
        name: "현재 전체 문서로 v4 생성",
      }),
    );
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s1/revisions/4",
      ),
    );
    expect(expectedRevisions).toEqual([1, 3]);
    expect(view.state.doc.toString()).toBe(preserved);
  });

  it("keeps recovery actions usable when clipboard and diff requests fail", async () => {
    server.use(
      http.get(`${API}/api/v1/strategies/:strategyId/diff`, () =>
        HttpResponse.error(),
      ),
    );
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn(() => Promise.reject(new Error("denied"))) },
      configurable: true,
    });
    mount("/research/strategies/s1/revisions/1");
    const view = await editor();
    replaceText(view, `${STORED}description: 충돌\n`);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await user.click(saveButton());
    const banner = await screen.findByRole("region", { name: "리비전 충돌" });

    await user.click(
      within(banner).getByRole("button", { name: "현재 문서 복사" }),
    );
    expect(await within(banner).findByRole("status")).toHaveTextContent(
      "복사할 수 없습니다",
    );
    await user.click(within(banner).getByRole("button", { name: "Diff 열기" }));
    expect(
      await within(banner).findByText("Diff를 불러올 수 없습니다."),
    ).toBeInTheDocument();
    expect(view.state.doc.toString()).toBe(`${STORED}description: 충돌\n`);
  });
});

describe("dirty guard follow-ups (P2-04 review)", () => {
  it("lets a same-route view switch through while dirty, and only prompts on a real leave", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}description: 편집 중\n`);
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    await waitFor(() => expect(history.location.search).toContain("view=json"));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "JSON" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(screen.getByRole("link", { name: "기존 편집기" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(globalThis.document.activeElement).toBe(
      within(dialog).getByRole("button", { name: "머무르기" }),
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/2",
    );
  });
});
