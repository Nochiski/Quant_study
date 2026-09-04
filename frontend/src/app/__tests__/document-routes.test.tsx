import { EditorView } from "@codemirror/view";
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
const posted: unknown[] = [];
const started: Record<string, unknown>[] = [];

const server = setupServer(
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
    return HttpResponse.json({
      format: "yaml",
      source_hash: "b".repeat(64),
      schema_version: "1.0",
      spec: spec("s1", 2, title),
      canonical_json: null,
      spec_hash: body.source === STORED ? "2".repeat(64) : "9".repeat(64),
      diagnostics: [],
    });
  }),
  http.post(`${API}/api/v1/backtests`, async ({ request }) => {
    started.push((await request.json()) as Record<string, unknown>);
    return HttpResponse.json(
      {
        run: {
          run_id: "run-7",
          status: "queued",
          progress: 0,
          stage: "queued",
          message: "queued",
          created_at: "2026-09-04T00:00:00Z",
          updated_at: "2026-09-04T00:00:00Z",
        },
      },
      { status: 202 },
    );
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
              message: "latest_revision=3",
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
});
afterAll(() => server.close());

const mount = (initial: string) => {
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
  return history;
};

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
  it("loads the exact stored source of a revision into the editor as the draft base", async () => {
    mount("/research/strategies/s1/revisions/2?view=diff");
    expect(
      await screen.findByRole("heading", { name: "퀄리티 모멘텀" }),
    ).toBeInTheDocument();
    const view = await editor();
    expect(view.state.doc.toString()).toBe(STORED);
    // The stored format is the editable view; DIFF is not implemented, so it falls back with a notice.
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText(/DIFF/)).toBeInTheDocument();
    expect(screen.getByText("저장됨 v2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "리비전 저장" })).toBeDisabled();
    expect(screen.getByText("2026-09-04 09:30")).toBeInTheDocument();
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
    mount("/research/strategies/s1/revisions/2");
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
});

describe("revision conflict (P3-07)", () => {
  it("keeps the text, names both revisions, and offers open / copy / diff", async () => {
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
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/1",
    );
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

  it("keeps text typed while a create is in flight as the new revision's local draft", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API}/api/v1/strategy-documents`, async ({ request }) => {
        const body = (await request.json()) as { source: string };
        await delay(150);
        return HttpResponse.json(document("s9", 1, body.source, "새 전략 A"), {
          status: 201,
        });
      }),
    );
    localStorage.clear();
    const history = mount("/research/strategies/new");
    const view = await editor();
    replaceText(view, 'schema_version: "1.0"\ntitle: 새 전략 A\n');
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "리비전 저장" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "리비전 저장" }));
    replaceText(
      view,
      'schema_version: "1.0"\ntitle: 새 전략 A\ndescription: 나중에 친 글\n',
    );
    await waitFor(() =>
      expect(history.location.pathname).toBe(
        "/research/strategies/s9/revisions/1",
      ),
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    // The revision page offers the in-flight edits back as a recovered draft.
    const banner = await screen.findByRole("region", { name: "복구본" });
    expect(banner).toHaveTextContent("+description: 나중에 친 글");
  });
});
