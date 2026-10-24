import type { ResearchCatalog } from "../../shared/api";
import { EditorView } from "@codemirror/view";
import { undo } from "@codemirror/commands";
import { Transaction } from "@codemirror/state";
import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import {
  act,
  cleanup,
  fireEvent,
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
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

// 케이스 timeout(15초)은 `vite.config.ts`, 비동기 대기 예산(10초)은 `shared/config/test-setup.ts`가 정한다 — 이 파일은
// 둘 다 덮어쓰지 않는다. 부하 flake의 원인은 54파일 병렬의 워커 경합(단독 65초, 케이스당 1초)이다(Phase 5 backlog 15·19).


import { backtestHistoryQuery } from "../../entities/backtest";
import { RUN_ENVIRONMENT_STORAGE_PREFIX } from "../../features/run-backtest";
import { strategiesQuery, strategySchemaQuery } from "../../entities/strategy";
import {
  strategyWorkbenchApi,
  type StrategyTraceRequest,
  type StrategyTraceResponse,
} from "../../shared/api";
import type {
  AssistantEventEnvelopeView,
  TurnContextPayload,
} from "../../entities/assistant";
import { readBackendFixture } from "../../shared/testing/backend-fixtures";
import { t } from "../../shared/config";
import { App } from "../app";

const API = "http://localhost:8000";

const spec = (strategyId: string, revision: number, title: string) => ({
  identity: { strategy_id: strategyId, revision, schema_version: "1.2" },
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

const document = (
  strategyId: string,
  revision: number,
  source: string,
  title = "퀄리티 모멘텀",
) => ({
  strategy_id: strategyId,
  revision,
  schema_version: "1.2",
  format: "yaml",
  source,
  source_hash: "b".repeat(64),
  spec: spec(strategyId, revision, title),
  spec_hash: `${revision}`.repeat(64).slice(0, 64),
  origin: "document",
  generated: false,
  requires_upgrade: false,
  created_at: "2026-09-04T09:30:00+00:00",
});

const STORED = 'schema_version: "1.2"\ntitle: 퀄리티 모멘텀\n';
const GRAPH_SOURCE = `schema_version: "1.2"
title: 그래프 전략
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
`;
const graphSpec = (strategyId: string, revision: number) => ({
  ...spec(strategyId, revision, "그래프 전략"),
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
      },
    },
  ],
});
const SIGNAL_SCHEMA_RESPONSE = {
  schema: {
    type: "object",
    properties: {
      signal: {
        type: "object",
        properties: {
          regime_minimum: { type: "number", default: 0.5 },
        },
      },
    },
    additionalProperties: false,
  },
  schema_hash: "h".repeat(64),
  schema_version: "1.2",
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
  },
  description: "Server factor",
  factor_id: "server.momentum",
  label: "Server momentum",
  minimum_history_sessions: 1,
  output_unit: "score",
  preference: "high",
  required_field_ids: ["price.close"],
};
const posted: unknown[] = [];
const started: Record<string, unknown>[] = [];
const compiledSources: string[] = [];
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

type AssistantSessionRow = {
  session_id: string;
  title: string;
  provider_profile_id: string;
  document_ref: unknown;
  created_at: string;
};

let assistantSessions: AssistantSessionRow[] = [];
const assistantTurns: { text: string; context: TurnContextPayload }[] = [];
const cancelledTurns: string[] = [];
let assistantStream: {
  push: (envelope: AssistantEventEnvelopeView) => void;
  close: () => void;
} | null = null;

/**
 * 실행 설정 패널의 마지막 사용값(P3-02). 화면 흐름 테스트는 사용자가 이미 기간·유니버스를 정해 둔
 * 상태에서 시작한다 — 패널 자체의 동작은 `features/run-backtest` 테스트가 본다. 요청에 실리는 모양 그대로라
 * 고정 bp 에서 읽히지 않는 가격 충격 계수는 없다(#352).
 */
const RUN_ENVIRONMENT = {
  market: "KRX",
  frequency: "daily",
  start: "2021-01-01",
  // 추적 mock 응답의 기준일(2026-09-01)·체결일(2026-09-02)이 실행 기간 안에 들도록 잡는다 — 응답 날짜
  // 가드가 실행 기간을 읽는다(P3-02 에서 다시 켜졌다).
  end: "2026-09-30",
  universe_id: "krx.common-stock",
  timing: "next_open",
  participation_rate: 0.1,
  participation_basis: "session_volume",
  fee_bps: 15,
  slippage_bps: 10,
  impact_model: "fixed_bps",
  sell_tax: "krx_statutory",
  missing: "drop",
} as const;
const RUN_OPTIONS_SCHEMA = JSON.parse(readBackendFixture("strategy_documents/run-options-schema.json")) as Record<string, unknown>;
const RUN_ENVIRONMENT_SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/run-environment-schema.json"),
) as Record<string, unknown>;

const server = setupServer(
  http.post(`${API}/api/v1/strategies/:strategyId/draft-diff`, async ({ request, params }) => {
    const body = await request.json() as { base_revision: number };
    return HttpResponse.json({ strategy_id: params.strategyId, base_revision: body.base_revision, base_spec_hash: "2".repeat(64), target_spec_hash: "2".repeat(64), source_hash: "b".repeat(64), changes: [] });
  }),

  http.get(`${API}/api/v1/experiments`, () => HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 })),
  http.get(`${API}/api/v1/strategy-documents/operators`, () => HttpResponse.json(JSON.parse(readBackendFixture("strategy_documents/operator-catalog.json")))),
  http.get(`${API}/api/v1/run-environments/schema`, () =>
    HttpResponse.json({ schema_hash: "run-env", options_schema: RUN_OPTIONS_SCHEMA, schema: RUN_ENVIRONMENT_SCHEMA }),
  ),
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
    compiledSources.push(body.source);
    const title = /title: (.*)/.exec(body.source)?.[1] ?? "";
    const compiledSpec = spec("draft", 0, title);
    const { identity, ...canonicalSpec } = compiledSpec;
    return HttpResponse.json({
      format: "yaml",
      source_hash: "b".repeat(64),
      schema_version: "1.2",
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
  // 결과 화면은 실행 종류를 서버 판정으로 읽는다(단일 실행에만 실험 만들기 링크).
  http.get(`${API}/api/v1/backtests/:runId/summary`, () =>
    HttpResponse.json({ kind: "single" }),
  ),
  http.get(`${API}/api/v1/backtests/:runId/request`, () =>
    HttpResponse.json(started.at(-1) ?? {}),
  ),
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
  http.get(`${API}/api/v1/backtests/:runId/account-report`, () =>
    HttpResponse.json({ detail: { code: "backtest.result.unreadable" } }, { status: 410 }),
  ),
  http.get(`${API}/api/v1/backtests/:runId/validation-report`, () =>
    HttpResponse.json({}, { status: 503 }),
  ),
  http.get(`${API}/api/v1/backtests/:runId/result`, () => HttpResponse.error()),
  http.get(`${API}/api/v1/strategy-documents/schema`, () =>
    HttpResponse.json({
      schema: { type: "object", properties: {}, additionalProperties: false },
      schema_hash: "h".repeat(64),
      schema_version: "1.2",
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
        schema_version: "1.2",
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
  http.get(`${API}/api/v1/strategies/:strategyId/trials`, ({ params }) =>
    HttpResponse.json({ lineage_id: params.strategyId, merged_lineage_ids: [], trial_count: 3, trials: [], blocked: [] }),
  ),
  http.get(`${API}/api/v1/equity/catalog`, () =>
    HttpResponse.json({
      total: 0,
      page: 1,
      page_size: 1,
      page_count: 0,
      fields: [],
      facets: { dataset_ids: [], frequencies: [], units: [] },
      snapshot: {
        snapshot_id: "snap", schema_version: "1", source: "test",
        built_at: "2026-09-05T00:00:00Z", dataset_revisions: [], point_in_time: true,
      },
    } satisfies ResearchCatalog),
  ),
  http.get(`${API}/api/v1/strategies/template`, () =>
    HttpResponse.json(spec("", 0, "새 팩터 전략")),
  ),
  // AI 어시스턴트 사이드바는 전략 화면 우측 슬롯에 늘 붙어 있다(B-04). 공급자 목록은 첫 렌더에
  // 조회되고, 나머지는 사용자가 질문을 보낼 때만 불린다.
  http.get(`${API}/api/v1/assistant/providers`, () =>
    HttpResponse.json({
      kinds: [
        { kind: "anthropic", installed: true, default_model: "claude-sonnet-5" },
        { kind: "openai", installed: false, default_model: null },
      ],
      profiles: [
        {
          profile_id: "p-1",
          kind: "anthropic",
          label: "작업용 Claude",
          model: "claude-sonnet-5",
          base_url: null,
          secret_tail: "9876",
          active: true,
          created_at: "2026-09-20T00:00:00Z",
        },
      ],
    }),
  ),
  http.get(`${API}/api/v1/assistant/sessions`, () =>
    HttpResponse.json(assistantSessions),
  ),
  http.post(`${API}/api/v1/assistant/sessions`, async ({ request }) => {
    const body = (await request.json()) as { document_ref: unknown };
    const created = {
      session_id: "s-1",
      title: "새 대화",
      provider_profile_id: "p-1",
      document_ref: body.document_ref,
      created_at: "2026-09-20T00:00:00Z",
    };
    assistantSessions = [created];
    return HttpResponse.json(created, { status: 201 });
  }),
  http.get(`${API}/api/v1/assistant/sessions/:sessionId`, ({ params }) => {
    const found = assistantSessions.find(
      (item) => item.session_id === String(params.sessionId),
    );
    return found === undefined
      ? HttpResponse.json(
          { detail: { code: "assistant.session.not_found", message: "" } },
          { status: 404 },
        )
      : HttpResponse.json({
          session: found,
          messages: [],
          turns: [],
          events: [],
        });
  }),
  http.post(
    `${API}/api/v1/assistant/sessions/:sessionId/turns`,
    async ({ params, request }) => {
      assistantTurns.push(
        (await request.json()) as { text: string; context: TurnContextPayload },
      );
      return HttpResponse.json(
        {
          turn_id: "t-1",
          session_id: String(params.sessionId),
          status: "running",
          accepted_sequence: -1,
          started_at: "2026-09-20T00:01:00Z",
        },
        { status: 202 },
      );
    },
  ),
  http.post(
    `${API}/api/v1/assistant/sessions/:sessionId/turns/:turnId/cancel`,
    ({ params }) => {
      cancelledTurns.push(String(params.turnId));
      return HttpResponse.json({
        turn_id: String(params.turnId),
        session_id: String(params.sessionId),
        status: "cancelled",
        accepted_sequence: -1,
        started_at: "2026-09-20T00:01:00Z",
        finished_at: "2026-09-20T00:02:00Z",
      });
    },
  ),
  http.get(`${API}/api/v1/assistant/sessions/:sessionId/events`, () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start: (value) => {
        controller = value;
      },
    });
    assistantStream = {
      push: (envelope) =>
        controller.enqueue(
          new TextEncoder().encode(
            [
              `id: ${envelope.sequence}`,
              "event: assistant",
              `data: ${JSON.stringify(envelope)}`,
              "",
              "",
            ].join("\n"),
          ),
        ),
      close: () => controller.close(),
    };
    return new HttpResponse(stream, {
      headers: { "Content-Type": "text/event-stream" },
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  localStorage.setItem(
    `${RUN_ENVIRONMENT_STORAGE_PREFIX}:last`,
    JSON.stringify(
      Object.fromEntries(
        Object.entries(RUN_ENVIRONMENT).map(([key, value]) => [
          key,
          String(value),
        ]),
      ),
    ),
  );
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
  localStorage.clear();
  sessionStorage.clear();
  delete globalThis.document.documentElement.dataset.theme;
  posted.length = 0;
  started.length = 0;
  compiledSources.length = 0;
  explainedGraphs.length = 0;
  tracedStrategies.length = 0;
  assistantSessions = [];
  assistantTurns.length = 0;
  cancelledTurns.length = 0;
  assistantStream = null;
});
afterAll(() => server.close());

const mountWithClient = (
  initial: string,
  prime: (client: QueryClient) => void = () => undefined,
  sourceView = true,
) => {
  const entry = !sourceView || initial.includes("view=") ? initial : `${initial}${initial.includes("?") ? "&" : "?"}view=yaml`;
  const history = createMemoryHistory({ initialEntries: [entry] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: 0 } },
  });
  prime(queryClient);
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

const openComparison = async () => {
  const button = await screen.findByRole("button", { name: "리비전 변경 비교" });
  if (button.getAttribute("aria-expanded") !== "true") await userEvent.setup().click(button);
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
  screen.getByRole("button", { name: "리비전 저장" });

/** 보조 영역은 기본 접힘이므로 검증할 작업에 들어가는 사용자 행동을 명시한다. */
const openOutline = async () => {
  await userEvent.setup().click(await screen.findByRole("button", { name: "전략 구조" }));
};

const openTrace = async () => {
  await userEvent.setup().click(await screen.findByRole("button", { name: "중간 결과" }));
};

const leaveLink = () =>
  globalThis.document.querySelector<HTMLAnchorElement>(
    'a[href="/research/backtests"]',
  )!;

const WORKFLOW_ROUTES = [
  {
    name: "new strategy",
    route: "/research/strategies/new",
    initialSource: 'schema_version: "1.2"\ntitle: ""\n',
    editedSource:
      'schema_version: "1.2"\ntitle: ""\ndescription: keyboard save\n',
    savedPath: "/research/strategies/s9/revisions/1",
    savedRequest: {
      format: "yaml",
      source: 'schema_version: "1.2"\ntitle: ""\ndescription: keyboard save\n',
    },
    backtestSource: "inline_draft",
  },
  {
    name: "saved revision",
    route: "/research/strategies/s1/revisions/2",
    initialSource: STORED,
    editedSource: `${STORED}description: keyboard save\n`,
    savedPath: "/research/strategies/s1/revisions/3",
    savedRequest: {
      expected_revision: 2,
      format: "yaml",
      source: `${STORED}description: keyboard save\n`,
    },
    backtestSource: "saved_revision",
  },
] as const;

const serveRuntimeGraphDocument = (): void => {
  server.use(
    http.get(`${API}/api/v1/strategy-documents/schema`, () =>
      HttpResponse.json({
        schema: RUNTIME_SCHEMA,
        schema_hash: "h".repeat(64),
        schema_version: "1.2",
      }),
    ),
    http.get(
      `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
      () =>
        HttpResponse.json({
          ...document("s1", 2, GRAPH_SOURCE, "그래프 전략"),
          spec: graphSpec("s1", 2),
        }),
    ),
  );
};

describe("professional keyboard workflow (P6-03)", () => {
  it("그래프에서 문서 경로를 찾아 YAML 원문을 비동기로 드러낸다", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=graph");
    expect(
      await screen.findByRole("tab", { name: "그래프" }),
    ).toBeInTheDocument();

    await user.keyboard("{Control>}k{/Control}");
    const search = screen.getByRole("combobox", {
      name: "명령과 문서 경로 검색",
    });
    await user.type(search, "/title");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(history.location.search).toContain("path=%2Ftitle");
      expect(history.location.search).not.toContain("view=graph");
    });
    const view = await editor();
    // reveal은 route 전환 뒤 비동기로 선택을 옮긴다 — 부하 중에는 한 틱 늦는다(P5-03: 전체 실행 flake).
    await waitFor(() =>
      expect(
        view.state.sliceDoc(
          view.state.selection.main.from,
          view.state.selection.main.to,
        ),
      ).toBe("퀄리티 모멘텀"),
    );
    expect(view.hasFocus).toBe(true);
  });

  it.each(WORKFLOW_ROUTES)(
    "routes Ctrl+Enter through the same Validate gate on $name",
    async ({ route, initialSource }) => {
      mount(route);
      await editor();
      await waitFor(() => expect(compiledSources).toContain(initialSource));
      const validate = screen.getByRole("button", { name: "검증" });
      await waitFor(() => expect(validate).toBeEnabled());
      const before = compiledSources.length;

      fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });

      await waitFor(() => expect(compiledSources.length).toBe(before + 1));
      expect(compiledSources.at(-1)).toBe(initialSource);
    },
  );

  it.each(WORKFLOW_ROUTES)(
    "routes Ctrl+S through the same Save gate on $name",
    async ({ route, editedSource, savedPath, savedRequest }) => {
      const history = mount(route);
      const view = await editor();
      replaceText(view, editedSource);
      // 세 게이트 케이스가 같은 순서: 편집한 원문의 compile이 도착한 뒤 게이트를 본다(#150 재검토 P2-8 —
      // warm 실행에서도 이 케이스가 실패했다).
      await waitFor(() => expect(compiledSources).toContain(editedSource));
      await waitFor(() => expect(saveButton()).toBeEnabled());

      fireEvent.keyDown(window, { key: "s", ctrlKey: true });

      await waitFor(() => expect(history.location.pathname).toBe(savedPath));
      expect(posted).toEqual([savedRequest]);
    },
  );

  it.each(WORKFLOW_ROUTES)(
    "routes Ctrl+Shift+Enter through the same Backtest gate on $name",
    async ({ route, initialSource, backtestSource }) => {
      const history = mount(route);
      await editor();
      // Validate 케이스와 같은 순서: compile 결과가 도착한 뒤 게이트를 본다(cold 실행 1회 flake, #150 리뷰 P1-2).
      await waitFor(() => expect(compiledSources).toContain(initialSource));
      const run = screen.getByRole("button", { name: "백테스트" });
      await waitFor(() => expect(run).toBeEnabled());

      fireEvent.keyDown(window, {
        key: "Enter",
        ctrlKey: true,
        shiftKey: true,
      });

      await waitFor(() => expect(started).toHaveLength(1));
      expect(
        (started[0]?.strategy_source as { kind?: string } | undefined)?.kind,
      ).toBe(backtestSource);
      await waitFor(() =>
        expect(history.location.pathname).toBe("/research/backtests/run-7"),
      );
    },
  );

  it.each(WORKFLOW_ROUTES)(
    "keeps Validate, Save and Backtest blocked while $name is composing or invalid",
    async ({ route }) => {
      const history = mount(route);
      const view = await editor();
      await openOutline();
      const run = screen.getByRole("button", { name: "백테스트" });
      await waitFor(() => expect(run).toBeEnabled());

      act(() =>
        view.contentDOM.dispatchEvent(
          new Event("compositionstart", { bubbles: true }),
        ),
      );
      expect(await screen.findByText(t("document.composing"))).toBeVisible();
      const beforeComposition = compiledSources.length;
      fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
      fireEvent.keyDown(window, { key: "s", ctrlKey: true });
      fireEvent.keyDown(window, {
        key: "Enter",
        ctrlKey: true,
        shiftKey: true,
      });
      expect(compiledSources).toHaveLength(beforeComposition);
      expect(posted).toEqual([]);
      expect(started).toEqual([]);
      act(() =>
        view.contentDOM.dispatchEvent(
          new Event("compositionend", { bubbles: true }),
        ),
      );

      replaceText(view, "title: [broken\n");
      await screen.findByText(t("ide.outline.stale"));
      const beforeInvalid = compiledSources.length;
      fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
      fireEvent.keyDown(window, { key: "s", ctrlKey: true });
      fireEvent.keyDown(window, {
        key: "Enter",
        ctrlKey: true,
        shiftKey: true,
      });
      await new Promise((resolve) => setTimeout(resolve, 350));
      expect(compiledSources).toHaveLength(beforeInvalid);
      expect(posted).toEqual([]);
      expect(started).toEqual([]);
      expect(history.location.pathname).toBe(route.split("?")[0]);
    },
  );

  it("searches a runtime-schema semantic node identity and reveals its pointer", async () => {
    serveRuntimeGraphDocument();
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=graph");
    expect(await screen.findByRole("tab", { name: "그래프" })).toBeVisible();
    await openOutline();
    const outlineFilter = screen.getByRole("searchbox", {
      name: t("ide.outline.filter"),
    });
    await user.type(outlineFilter, "mom_252");
    expect(
      await screen.findByRole("treeitem", { name: /mom_252/ }),
    ).toBeVisible();
    await user.clear(outlineFilter);
    await waitFor(() => expect(compiledSources).toContain(GRAPH_SOURCE));

    await user.keyboard("{Control>}k{/Control}");
    await user.type(
      screen.getByRole("combobox", { name: "명령과 문서 경로 검색" }),
      "node:mom_252",
    );
    const semanticResult = await within(screen.getByRole("dialog")).findByRole("option");
    expect(semanticResult).toHaveTextContent("/factors/0/graph/nodes/1");
    expect(within(screen.getByRole("dialog")).getAllByRole("option")).toHaveLength(1);
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(history.location.search).toContain(
        "path=%2Ffactors%2F0%2Fgraph%2Fnodes%2F1",
      ),
    );
    const view = await editor();
    expect(view.hasFocus).toBe(true);
    expect(
      view.state.sliceDoc(
        view.state.selection.main.from,
        view.state.selection.main.to,
      ),
    ).toContain("node_id: mom_252");
  });

  it("does not expose or execute stale symbols while graph source is updating or invalid", async () => {
    serveRuntimeGraphDocument();
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    await openOutline();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "백테스트" }),
      ).toBeEnabled(),
    );
    view.focus();
    replaceText(view, `${GRAPH_SOURCE}broken: [\n`);
    const selection = view.state.selection.main;

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const search = screen.getByRole("combobox", {
      name: "명령과 문서 경로 검색",
    });
    await user.type(search, "node:mom_252");
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByText(t("command.empty"))).toBeVisible();
    fireEvent.keyDown(search, { key: "Enter" });
    expect(search).toHaveFocus();
    expect(history.location.search).not.toContain("path=");
    expect(view.state.selection.main.from).toBe(selection.from);
    expect(view.state.selection.main.to).toBe(selection.to);

    await screen.findByText(t("ide.outline.stale"));
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    fireEvent.keyDown(search, { key: "Enter" });
    expect(history.location.search).not.toContain("path=");
    expect(view.state.selection.main.from).toBe(selection.from);
    expect(view.state.selection.main.to).toBe(selection.to);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(view.hasFocus).toBe(true));
  });
});

describe("document routes (P2-04)", () => {
  it.each([
    ["/research/strategies/new", 'schema_version: "1.2"\ntitle: ""\n'],
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
    await openOutline();
    act(() => view.dispatch({ selection: { anchor: view.state.doc.length } }));

    await user.click(
      await screen.findByRole("button", {
        name: "signal · 현재 커서에 삽입",
      }),
    );

    expect(view.state.doc.toString()).toBe(
      `${prefix}signal:\n  regime_minimum: 0.5`,
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
      `${prefix}signal:\n  regime_minimum: 0.5\n`,
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

  it("reports an inactive editor from a JSON projection instead of a readiness error", async () => {
    server.use(
      http.get(`${API}/api/v1/strategy-documents/schema`, () =>
        HttpResponse.json(SIGNAL_SCHEMA_RESPONSE),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2?view=graph");
    await openOutline();

    await user.click(
      await screen.findByRole("button", {
        name: "signal · 현재 커서에 삽입",
      }),
    );

    // projection view에서는 편집기 handle이 비활성이라 "비활성" 사유(Phase 3 감사 R1). 준비 안 됨 오류가 아니다.
    expect(screen.getByRole("alert")).toHaveTextContent(
      "소스 편집기가 비활성인 화면에서는 삽입하지 않습니다",
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
    await openOutline();
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
          schema_version: "1.2",
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
    await openOutline();
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
    await openOutline();

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
    await openOutline();

    expect(
      await screen.findByText(/계약·팩터 카탈로그 버전이 일치하지 않아/),
    ).toHaveAttribute("role", "alert");
    expect(
      screen.queryByRole("button", { name: /현재 커서에 삽입/ }),
    ).not.toBeInTheDocument();
  });

  it("IDE 연구는 원장 N을 읽고 dirty 원문을 옛 저장본으로 보내지 않는다", async () => {
    mount("/research/strategies/s1/revisions/2?view=yaml");
    const view = await editor();
    const summary = await screen.findByText("연구", { selector: "summary" });
    await waitFor(() => expect(summary).toHaveTextContent("N 3"));
    await userEvent.setup().click(summary);
    expect(screen.getByText(/현재 계열 시도 수 N=3/u)).toBeVisible();
    expect(screen.getByRole("link", { name: "전략 목록에서 시도 원장 찾기" })).toHaveAttribute("href", "/research/strategies");
    const changed = `${STORED}\n# 미저장 연구\n`;
    replaceText(view, changed);
    expect(await screen.findByText(/현재 원문을 리비전으로 저장한 뒤 보내세요/u)).toBeVisible();
    expect(screen.getByRole("button", { name: "실험으로 보내기" })).toBeDisabled();
    expect(view.state.doc.toString()).toBe(changed);
  });

  it("연구 메뉴 안의 Escape만 소비하고 닫은 뒤 원문을 보존하며 summary에 초점을 돌린다", async () => {
    mount("/research/strategies/s1/revisions/2?view=yaml");
    const view = await editor();
    await openOutline();
    const summary = await screen.findByText("연구", { selector: "summary" });
    const user = userEvent.setup();
    await user.click(summary);
    const link = screen.getByRole("link", { name: "전략 목록에서 시도 원장 찾기" });
    await user.tab();
    expect(link).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(link).not.toBeVisible();
    expect(summary).toHaveFocus();
    expect(screen.getByRole("navigation", { name: "전략 구조" })).toBeVisible();
    expect(view.state.doc.toString()).toBe(STORED);
    await user.click(summary);
    expect(link).toBeVisible();
  });

  it("IDE 계열 조회 실패를 0으로 바꾸지 않고 명시 재조회로 복구한다", async () => {
    let reads = 0;
    server.use(http.get(`${API}/api/v1/strategies/:strategyId/trials`, () =>
      ++reads === 1 ? HttpResponse.json({}, { status: 503 }) : HttpResponse.json({ lineage_id: "s1", merged_lineage_ids: [], trial_count: 7, trials: [], blocked: [] }),
    ));
    mount("/research/strategies/s1/revisions/2?view=yaml");
    const view = await editor();
    const summary = await screen.findByText("연구", { selector: "summary" });
    await userEvent.setup().click(summary);
    await screen.findByRole("button", { name: "계열 다시 조회" });
    expect(summary).toHaveTextContent("N —");
    await userEvent.setup().click(screen.getByRole("button", { name: "계열 다시 조회" }));
    await waitFor(() => expect(summary).toHaveTextContent("N 7"));
    expect(view.state.doc.toString()).toBe(STORED);
    expect(reads).toBe(2);
  });

  it("loads the exact stored source of a revision into the editor as the draft base", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2?view=diff");
    await openComparison();
    expect(
      await screen.findByRole("heading", { name: "퀄리티 모멘텀" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "리비전 변경 비교" })).toHaveAttribute(
      "aria-expanded",
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

  it.each(["/research/strategies/new", "/research/strategies/s1/revisions/2"])(
    "%s의 기본 그래프에서 복구본을 발견하고 원문을 확인한 뒤 명시적으로 복원한다",
    async (route) => {
      const saved = !route.endsWith("/new");
      const original = saved ? STORED : 'schema_version: "1.2"\ntitle: ""\n';
      const recovered = `${original}description: local recovery\n`;
      const key = saved ? "s1@2" : "new";
      localStorage.setItem(
        `strategy-workbench.draft.${key}`,
        JSON.stringify({
          key,
          format: "yaml",
          source: recovered,
          strategyId: saved ? "s1" : null,
          baseRevision: saved ? 2 : null,
          baseSpecHash: saved ? "2".repeat(64) : null,
          schemaVersion: "1.2",
          savedAt: "2026-09-05T01:02:03Z",
        }),
      );
      const user = userEvent.setup();
      mountWithClient(route, undefined, false);

      const banner = await screen.findByRole("region", { name: "복구본" });
      expect(banner).toBeVisible();
      expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute("aria-selected", "true");
      await user.click(screen.getByRole("tab", { name: "YAML" }));
      const view = await editor();
      expect(view.state.doc.toString()).toBe(original);

      await user.click(screen.getByRole("tab", { name: "그래프" }));
      await user.click(within(banner).getByRole("button", { name: "복구본 불러오기" }));
      await waitFor(() => expect(view.state.doc.toString()).toBe(recovered));
      expect(screen.queryByRole("region", { name: "복구본" })).not.toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute("aria-selected", "true");
    },
  );

  it.each(["/research/strategies/new", "/research/strategies/s1/revisions/2"])(
    "%s의 기본 그래프에서 서버 초안 연결 실패를 알리고 원문을 유지한다",
    async (route) => {
      server.use(
        http.get(`${API}/api/v1/strategy-drafts/:draftId`, () =>
          HttpResponse.error(),
        ),
      );
      const user = userEvent.setup();
      mountWithClient(route, undefined, false);

      expect(await screen.findByText(t("draft.server.offline"))).toBeVisible();
      expect(screen.getByRole("button", { name: t("draft.server.retry") })).toBeVisible();
      expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute("aria-selected", "true");
      await user.click(screen.getByRole("tab", { name: "YAML" }));
      expect((await editor()).state.doc.toString()).toBe(
        route.endsWith("/new") ? 'schema_version: "1.2"\ntitle: ""\n' : STORED,
      );
    },
  );

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
          schema_version: "1.2",
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
          schema_version: "1.2",
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
                schema_version: "1.2",
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
    const source = 'schema_version: "1.2"\ntitle: first edit\n';
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
    replaceText(view, 'schema_version: "1.2"\ntitle: 새 전략 A\n');
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
      { format: "yaml", source: 'schema_version: "1.2"\ntitle: 새 전략 A\n' },
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
    const first = 'schema_version: "1.2"\ntitle: A\n';
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
    expect(screen.getByText("저장 요청 거절")).toBeVisible();
    expect(history.location.pathname).toBe("/research/strategies/new");
    expect(view.state.doc.toString()).toBe("title: bad\n");
  });

  it("blocks leaving a dirty draft until the user chooses, then lets them go", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/new");
    const view = await editor();
    replaceText(view, "title: 임시\n");
    await user.click(screen.getByRole("link", { name: "백테스트" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("저장하지 않은 변경이 있습니다");
    await user.click(within(dialog).getByRole("button", { name: "머무르기" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(history.location.pathname).toBe("/research/strategies/new");
    await user.click(screen.getByRole("link", { name: "백테스트" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "나가기",
      }),
    );
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/backtests"),
    );
  });

  it("allows a same-page view switch while dirty but guards a real leave", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}description: dirty\n`);

    await user.click(screen.getByRole("tab", { name: "그래프" }));
    await waitFor(() => expect(history.location.search).toContain("view=graph"));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await user.click(leaveLink());
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
    await waitFor(() =>
      expect(globalThis.document.activeElement).toBe(leaveLink()),
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
          requires_upgrade: false,
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
    await openOutline();
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
    await openOutline();
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
    const history = mount("/research/strategies/s1/revisions/2?view=graph");
    await openOutline();
    expect(
      await screen.findByRole("tab", { name: "그래프" }),
    ).toBeInTheDocument();
    const tree = await screen.findByRole("tree", {
      name: "StrategySpec 문서 구조",
    });
    await user.click(within(tree).getByRole("treeitem", { name: "title" }));
    await waitFor(() => {
      expect(history.location.search).toContain("path=%2Ftitle");
      expect(history.location.search).not.toContain("view=graph");
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

describe("StrategySpec JSON projection and editable Form (P4-06 → P4-04)", () => {
  // 기본 핸들러의 스키마는 비어 있다(properties 없음). Form 섹션은 실제 runtime schema에서 온다.
  beforeEach(() => {
    server.use(
      http.get(`${API}/api/v1/strategy-documents/schema`, () =>
        HttpResponse.json({
          schema: RUNTIME_SCHEMA,
          schema_hash: "h".repeat(64),
          schema_version: "1.2",
        }),
      ),
    );
  });
  const formPanel = () => screen.findByLabelText("전략 파이프라인");
  const formSection = async () => {
    const panel = await formPanel();
    // 패널 껍데기는 첫 parse 전에 보인다. 실제 편집 컨트롤이 준비될 때까지 기다린다.
    await within(panel).findByRole("spinbutton", { name: "종목별 최대 목표 비중 한도" });
    return within(panel);
  };

  it.each(["/research/strategies/new", "/research/strategies/s1/revisions/2"])(
    "그래프 기본 표현과 두 탭만 제공한다: %s", async (route) => {
      mountWithClient(route, undefined, false);
      const graph = await screen.findByRole("tab", { name: "그래프" });
      expect(graph).toHaveAttribute("aria-selected", "true");
      expect(screen.queryByRole("tab", { name: "JSON" })).not.toBeInTheDocument();
      expect(screen.queryByRole("tab", { name: "Form" })).not.toBeInTheDocument();
      expect(await formPanel()).not.toHaveTextContent(/_id|_node|kind:/);
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

    await user.click(screen.getByRole("tab", { name: "그래프" }));
    await waitFor(() =>
      expect(screen.getByLabelText("전략 파이프라인")).toBeVisible(),
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

  it("edits through the Form into the hidden editor, keeps comments, compiles, and undoes in one step", async () => {
    const commented =
      '# 문서 머리말\nschema_version: "1.2"\ntitle: 퀄리티 모멘텀 # 제목 메모\nrisk:\n  # 집중도 상한\n  max_name_weight: 0.05\n';
    server.use(
      http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        () => HttpResponse.json(document("s1", 2, commented)),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    expect(view.state.doc.toString()).toBe(commented);
    const before = compiledSources.length;

    await user.click(screen.getByRole("tab", { name: "그래프" }));
    const risk = await formSection();
    const weight = risk.getByRole("spinbutton", { name: "종목별 최대 목표 비중 한도" });
    expect(weight).toHaveValue(0.05);
    await user.clear(weight);
    await user.type(weight, "0.1{Enter}");

    const expected = commented.replace(
      "max_name_weight: 0.05",
      "max_name_weight: 0.1",
    );
    await waitFor(() => expect(view.state.doc.toString()).toBe(expected));
    expect(within(await formPanel()).getByRole("status")).toHaveTextContent(
      "종목별 최대 목표 비중 한도 반영됨",
    );
    // 편집기 change → reducer → compile 왕복이 같은 텍스트로 일어난다.
    await waitFor(() => {
      expect(compiledSources.length).toBeGreaterThan(before);
      expect(compiledSources[compiledSources.length - 1]).toBe(expected);
    });
    await waitFor(() => expect(saveButton()).toBeEnabled());

    // 미작성 필드의 첫 값은 섹션에 insert-key, 문서 다른 부분은 그대로.
    const portfolio = await formSection();
    await user.selectOptions(
      portfolio.getByRole("combobox", { name: "매매 방향" }),
      "long_short",
    );
    await waitFor(() =>
      expect(view.state.doc.toString()).toBe(
        `${expected}portfolio:\n  side: long_short\n`,
      ),
    );

    await user.click(screen.getByRole("tab", { name: "YAML" }));
    act(() => expect(undo(view)).toBe(true));
    expect(view.state.doc.toString()).toBe(expected);
    act(() => expect(undo(view)).toBe(true));
    expect(view.state.doc.toString()).toBe(commented);
  });

  it("저장된 JSON 바이트를 YAML 원문에서 보존하고 YAML 편집을 허용한다", async () => {
    const jsonSource = '{"schema_version":"1.2","title":"JSON source"}';
    server.use(http.get(`${API}/api/v1/strategies/:strategyId/revisions/:revision/document`, () =>
      HttpResponse.json({ ...document("s1", 2, jsonSource, "JSON source"), format: "json" })));
    mount("/research/strategies/s1/revisions/2?view=json");
    const view = await editor();
    expect(view.state.doc.toString()).toBe(jsonSource);
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute("aria-selected", "true");
    replaceText(view, 'schema_version: "1.2"\ntitle: YAML 수정\n');
    await waitFor(() => expect(compiledSources).toContain('schema_version: "1.2"\ntitle: YAML 수정\n'));
  });

  it("구문 오류 뒤 캔버스는 마지막 파싱 값을 잠그고 실행을 막는다", async () => {
    mount("/research/strategies/s1/revisions/2?view=yaml");
    const view = await editor();
    replaceText(view, 'schema_version: "1.2"\ntitle: valid\nrisk: {max_name_weight: 0.17}\n');
    await waitFor(() => expect(saveButton()).toBeEnabled());
    replaceText(view, 'schema_version: "1.2"\ntitle: [broken\n');
    await userEvent.setup().click(screen.getByRole("tab", { name: "그래프" }));
    const panel = await formPanel();
    expect(await within(panel).findByText("STALE")).toBeInTheDocument();
    const weight = within(panel).getByRole("spinbutton", { name: "종목별 최대 목표 비중 한도" });
    expect(weight).toHaveValue(0.17);
    expect(weight).toBeDisabled();
    expect(saveButton()).toBeDisabled();
  });

  // 그래프 1수준 캔버스(lang2 P4-02, 리드 결정 4): 카드 문장 안의 컨트롤은 Form 행과 한 경로다.
  describe("그래프 1수준 캔버스 (lang2 P4-02)", () => {
    const COMMENTED =
      '# 문서 머리말\nschema_version: "1.2"\ntitle: 퀄리티 모멘텀 # 제목 메모\nrisk:\n  # 집중도 상한\n  max_name_weight: 0.05\n';
    const WEIGHT = "종목별 최대 목표 비중 한도";
    const SIDE = "매매 방향";
    const EDITED = COMMENTED.replace(
      "max_name_weight: 0.05",
      "max_name_weight: 0.1",
    );
    const canvas = () =>
      screen.findByRole("region", { name: "전략 파이프라인" });
    const canvasCard = async (name: string) =>
      within(await within(await canvas()).findByRole("group", { name }));
    const openCommented = async () => {
      server.use(
        http.get(
          `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
          () => HttpResponse.json(document("s1", 2, COMMENTED)),
        ),
      );
      mount("/research/strategies/s1/revisions/2");
      const view = await editor();
      await settled(view, COMMENTED);
      return view;
    };
    /**
     * 편집기 원문이 `text` 이고 그 원문의 parse·compile 이 끝났다(문서 상태 배지 `data-settled` — compile 버전이
     * 원문 버전을 따라잡았다. e2e `waitForSettledDocument` 와 같은 신호). 첫 parse 전에는 캔버스·Form 에 컨트롤이
     * 없고(#413), 편집 직후에는 직전 parse 로 그려져 parse 가 따라오기 전의 구조 연산이 `pending` 으로
     * 보류된다(#392 CI — 빠른 러너에서 parse 전에 입력해 편집이 버려졌다).
     */
    const settled = (view: EditorView, text: string) =>
      waitFor(() => {
        expect(view.state.doc.toString()).toBe(text);
        expect(
          screen.getByRole("status", { name: "문서 상태" }),
        ).toHaveAttribute("data-settled", "true");
      });
    /**
     * `settled` 에 더해 그 원문으로 compile 요청이 나갔다. 뒤이어 다른 원문의 요청이 나갈 수 있어
     * 마지막 요청인지는 보지 않는다.
     */
    const compiled = async (view: EditorView, text: string, before: number) => {
      await settled(view, text);
      expect(compiledSources.slice(before)).toContain(text);
    };

    it("같은 라우트의 문서·뒤로·앞으로 전환은 새 제목 아래 이전 Graph 값을 커밋하지 않는다 (#419)", async () => {
      const sources: Record<string, { text: string; title: string; weight: number }> = {
        s1: { text: COMMENTED, title: "문서 하나", weight: 0.05 },
        s2: { text: COMMENTED.replace("0.05", "0.2"), title: "문서 둘", weight: 0.2 },
        s3v3: { text: COMMENTED.replace("0.05", "0.4"), title: "문서 셋 개정", weight: 0.4 },
        s3: { text: COMMENTED.replace("0.05", "0.3"), title: "문서 셋", weight: 0.3 },
      };
      server.use(http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        ({ params }) => {
          const item = sources[params.revision === "3" ? "s3v3" : String(params.strategyId)]!;
          return HttpResponse.json(document(String(params.strategyId), Number(params.revision), item.text, item.title));
        },
      ));
      const history = mount("/research/strategies/s1/revisions/2?view=graph");
      await waitFor(() => expect(screen.getByRole("spinbutton", { name: WEIGHT })).toHaveValue(0.05));
      const commits: { title: string; weight: string | null }[] = [];
      // passive effect 뒤 최종 DOM만 보면 한 커밋짜리 잘못된 투영이 사라져 결함을 놓친다.
      const observer = new MutationObserver(() => {
        const title = screen.queryByRole("heading", { level: 1 })?.textContent;
        if (title) commits.push({ title, weight: (screen.queryByRole("spinbutton", { name: WEIGHT }) as HTMLInputElement | null)?.value ?? null });
      });
      observer.observe(globalThis.document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      const arrived = async (id: string) => {
        const item = sources[id]!;
        await waitFor(() => {
          expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(item.title);
          expect(screen.getByRole("spinbutton", { name: WEIGHT })).toHaveValue(item.weight);
        });
      };
      try {
        history.push("/research/strategies/s2/revisions/2?view=graph");
        await arrived("s2");
        history.back();
        await arrived("s1");
        history.forward();
        await arrived("s2");
        // 같은 틱에서 두 요청이 와도 마지막 문서가 원문 owner다.
        history.push("/research/strategies/s1/revisions/2?view=graph");
        history.push("/research/strategies/s3/revisions/2?view=graph");
        await arrived("s3");
        history.push("/research/strategies/s3/revisions/3?view=graph");
        await arrived("s3v3");
        history.back();
        await arrived("s3");
        await userEvent.setup().click(screen.getByRole("tab", { name: "YAML" }));
        expect((await editor()).state.doc.toString()).toBe(sources.s3!.text);
        expect(posted).toHaveLength(0);
        for (const item of Object.values(sources)) {
          const visible = commits.filter((commit) => commit.title === item.title && commit.weight !== null);
          expect(visible.length).toBeGreaterThan(0);
          expect(visible.every((commit) => Number(commit.weight) === item.weight)).toBe(true);
        }
      } finally {
        observer.disconnect();
      }
    });

    it("YAML 문서 전환도 새 제목 아래 이전 원문을 커밋하지 않는다 (#419)", async () => {
      const second = COMMENTED.replace("0.05", "0.2");
      server.use(http.get(`${API}/api/v1/strategies/:strategyId/revisions/:revision/document`, ({ params }) =>
        HttpResponse.json(document(String(params.strategyId), 2, params.strategyId === "s1" ? COMMENTED : second,
          params.strategyId === "s1" ? "문서 하나" : "문서 둘"))));
      const history = mount("/research/strategies/s1/revisions/2?view=yaml");
      await settled(await editor(), COMMENTED);
      const visibleSources: string[] = [];
      const observer = new MutationObserver(() => {
        if (screen.queryByRole("heading", { name: "문서 둘" }) === null) return;
        const content = screen.queryByRole("textbox", { name: t("ide.editor") });
        if (content) visibleSources.push(EditorView.findFromDOM(content)!.state.doc.toString());
      });
      observer.observe(globalThis.document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      try {
        history.push("/research/strategies/s2/revisions/2?view=yaml");
        await screen.findByRole("heading", { name: "문서 둘" });
        await settled(await editor(), second);
        expect(visibleSources.length).toBeGreaterThan(0);
        expect(visibleSources.every((source) => source === second)).toBe(true);
      } finally { observer.disconnect(); }
    });

    it("첫 구문 오류에는 Graph 기본값을 만들지 않고 원문을 보존한다 (#420)", async () => {
      const broken = COMMENTED.replace("title: 퀄리티 모멘텀", "title: [broken");
      server.use(http.get(
        `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
        () => HttpResponse.json(document("s1", 2, broken)),
      ));
      mount("/research/strategies/s1/revisions/2?view=graph");
      await waitFor(() => expect(screen.getByRole("status", { name: "문서 상태" }))
        .toHaveAttribute("data-settled", "true"));
      const pipeline = await canvas();
      expect(within(pipeline).queryByRole("spinbutton", { name: WEIGHT })).toBeNull();
      expect(pipeline).toHaveTextContent(t("form.panel.noValidParse"));
      expect(saveButton()).toBeDisabled();
      await userEvent.setup().click(screen.getByRole("tab", { name: "YAML" }));
      const view = await editor();
      expect(view.state.doc.toString()).toBe(broken);
      replaceText(view, COMMENTED);
      await settled(view, COMMENTED);
      await userEvent.setup().click(screen.getByRole("tab", { name: "그래프" }));
      expect(within(await canvas()).getByRole("spinbutton", { name: WEIGHT })).toHaveValue(0.05);
    });

    it("첫 parse 전에만 Form·캔버스·그래프 편집기가 컨트롤 없이 기다리고, 그 뒤 편집 직후에는 직전 parse 로 그린다 (#413)", async () => {
      // 스키마를 query cache 에 먼저 넣는다: 문서가 오는 순간 Form·캔버스를 막는 것은 첫 parse 하나뿐이라
      // "첫 parse 전"(150ms)이 스키마 대기(로딩)와 섞이지 않는다.
      server.use(
        http.get(
          `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
          () => HttpResponse.json(document("s1", 2, COMMENTED)),
        ),
      );
      mountWithClient("/research/strategies/s1/revisions/2?view=form", (client) =>
        client.setQueryData(strategySchemaQuery().queryKey, {
          schema: RUNTIME_SCHEMA,
          schema_hash: "h".repeat(64),
          schema_version: "1.2",
        }),
      );

      const graphEditor = () =>
        screen.queryByRole("region", { name: "그래프 편집", hidden: true });
      // 로딩 문구와 컨트롤 부재를 같은 DOM 시점에서 관찰한다. await 사이에 실제
      // 디바운스가 끝나면 서로 다른 parse 상태를 비교하게 된다.
      await waitFor(() => {
        const pipeline = screen.getByRole("region", { name: "전략 파이프라인" });
        expect(pipeline).toHaveTextContent("문서를 읽는 중입니다.");
        expect(
          within(pipeline).queryAllByRole("spinbutton", { hidden: true }),
        ).toHaveLength(0);
        expect(graphEditor()).toBeNull();
      });
      const form = screen.getByRole("region", { name: "전략 파이프라인" });
      const canvas = form;

      // parse 뒤: 적힌 값(0.05)이 두 화면에 보인다.
      await waitFor(() =>
        expect(
          screen.getByRole("status", { name: "문서 상태" }),
        ).toHaveAttribute("data-settled", "true"),
      );
      expect(
        within(form).getByRole("spinbutton", { name: "종목별 최대 목표 비중 한도" }),
      ).toHaveValue(0.05);
      expect(
        within(canvas).getByRole("spinbutton", { name: WEIGHT, hidden: true }),
      ).toHaveValue(0.05);
      // 노드 편집은 명시적으로 여는 별도 주 작업이므로 파이프라인과 동시에 만들지 않는다.
      expect(graphEditor()).toBeNull();
      // 팩터가 없는 문서에 기본 팩터나 편집 입구를 만들어 내지 않는다.
      expect(screen.queryAllByRole("button", { name: /· 노드 편집$/ })).toHaveLength(0);

      // 첫 parse 뒤 편집 직후(디바운스)는 로딩으로 바꾸지 않고 직전 parse 로 그린다(DEFECT-P404-001,
      // #416 리뷰 P2-1). 숨은 편집기는 role 로 찾지 못해 DOM 에서 view 를 얻는다.
      const hidden = EditorView.findFromDOM(
        globalThis.document.querySelector<HTMLElement>(".cm-content")!,
      )!;
      replaceText(hidden, EDITED);
      expect(within(form).queryByText("문서를 읽는 중입니다.")).toBeNull();
      expect(
        within(form).getByRole("spinbutton", { name: "종목별 최대 목표 비중 한도" }),
      ).toHaveValue(0.05);
    });

    it("카드 문장 안의 컨트롤로 값을 바꾸면 YAML 그 줄만 바뀌고 compile 된다", async () => {
      const user = userEvent.setup();
      const view = await openCommented();
      const before = compiledSources.length;

      await user.click(screen.getByRole("tab", { name: "그래프" }));
      const weight = (await canvasCard(WEIGHT)).getByRole("spinbutton", {
        name: WEIGHT,
      });
      expect(weight).toHaveValue(0.05);
      await user.clear(weight);
      await user.type(weight, "0.1{Enter}");

      // 주석·다른 줄은 그대로, 그 값 한 줄만 바뀐다.
      await waitFor(() => expect(view.state.doc.toString()).toBe(EDITED));
      expect(within(await canvas()).getByRole("status")).toHaveTextContent(
        `${WEIGHT} 반영됨`,
      );
      await compiled(view, EDITED, before);
    });

    it("카드 편집을 되돌린 뒤 같은 값을 다시 입력해도 한 번 확정된다", async () => {
      // 적힌 값 바꾸기(replace)와 없는 섹션에 키 넣기(insert-key)를 카드로 한 결과가 Form 행으로 한 결과와
      // 바이트 단위로 같고, 편집마다 실행 취소 한 번이다.
      const user = userEvent.setup();
      const view = await openCommented();

      await user.click(screen.getByRole("tab", { name: "그래프" }));
      const weight = (await canvasCard(WEIGHT)).getByRole("spinbutton", {
        name: WEIGHT,
      });
      let before = compiledSources.length;
      await user.clear(weight);
      await user.type(weight, "0.1{Enter}");
      await compiled(view, EDITED, before);
      await user.selectOptions(
        (await canvasCard(SIDE)).getByRole("combobox", { name: SIDE }),
        "long_short",
      );
      await waitFor(() =>
        expect(view.state.doc.toString()).toBe(
          `${EDITED}portfolio:\n  side: long_short\n`,
        ),
      );
      const byCard = view.state.doc.toString();

      await user.click(screen.getByRole("tab", { name: "YAML" }));
      before = compiledSources.length;
      act(() => expect(undo(view)).toBe(true));
      act(() => expect(undo(view)).toBe(true));
      expect(view.state.doc.toString()).toBe(COMMENTED);
      await compiled(view, COMMENTED, before);

      await user.click(screen.getByRole("tab", { name: "그래프" }));
      const risk = await formSection();
      const formWeight = risk.getByRole("spinbutton", {
        name: "종목별 최대 목표 비중 한도",
      });
      before = compiledSources.length;
      await user.clear(formWeight);
      await user.type(formWeight, "0.1{Enter}");
      await compiled(view, EDITED, before);
      await user.selectOptions(
        (await formSection()).getByRole("combobox", {
          name: "매매 방향",
        }),
        "long_short",
      );
      await waitFor(() => expect(view.state.doc.toString()).toBe(byCard));
    });
  });
});

/** 같은 그래프 문서를 compile하되 진단만 바꿔 답한다. */
const graphCompileWith = (diagnostics: readonly unknown[]) =>
  http.post(`${API}/api/v1/strategy-documents/compile`, () => {
    const compiledSpec = graphSpec("draft", 0);
    const { identity, ...canonicalSpec } = compiledSpec;
    return HttpResponse.json({
      format: "yaml",
      source_hash: "b".repeat(64),
      schema_version: "1.2",
      spec: compiledSpec,
      canonical_json: JSON.stringify({
        ...canonicalSpec,
        schema_version: identity.schema_version,
      }),
      spec_hash: "7".repeat(64),
      diagnostics,
    });
  });

const graphHandlers = () => [
  graphCompileWith([]),
  http.get(`${API}/api/v1/strategy-documents/schema`, () =>
    HttpResponse.json({
      schema: RUNTIME_SCHEMA,
      schema_hash: "h".repeat(64),
      schema_version: "1.2",
    }),
  ),
  http.get(`${API}/api/v1/equity/catalog`, () =>
    HttpResponse.json({
      snapshot: {
        snapshot_id: "snap",
        schema_version: "1.2",
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
      synthesized_nodes: [],
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
        minimum_history_sessions: 252,
        missing_policy: "drop",
        as_of_policy: "available_date_lte_as_of",
      },
    });
  }),
];

const revisionDocumentHandler = (source: string, strategyId = "s1") =>
  http.get(
    `${API}/api/v1/strategies/:strategyId/revisions/:revision/document`,
    () =>
      HttpResponse.json({
        ...document(strategyId, 2, source, "그래프 전략"),
        spec: graphSpec(strategyId, 2),
      }),
  );

describe("FactorGraph read-only projection (P4-07)", () => {
  const completedTrace = (
    request: StrategyTraceRequest,
  ): StrategyTraceResponse => ({
  factor_preview: { valid_count: 0, missing_count: 0, missing: "drop", top: [] },
    spec_hash: "7".repeat(64),
    snapshot_id: "snap",
    registry_version: "v1",
    plan_hash: "p".repeat(64),
    factor_id: request.factor_id,
    as_of: request.as_of ?? "2026-08-31",
    provenance: {
      kind: "saved_revision",
      schema_version: "1.2",
      spec_hash: "7".repeat(64),
      source_hash: "b".repeat(64),
      strategy_id: "s1",
      revision: 2,
    },
    raw: [],
    raw_truncated: false,
    warnings: [],
    trace: {
      rows: (request.node_ids ?? []).flatMap((nodeId) =>
        request.security_ids.map((securityId) => ({
          node_id: nodeId,
          operation: nodeId === "close" ? "field" : "time_series.momentum",
          as_of: request.as_of ?? "2026-08-31",
          security_id: securityId,
          value: nodeId === "close" ? 10 : 0.2,
          status: "ok",
          inputs: nodeId === "close" ? [] : [{ node_id: "close", value: 10 }],
        })),
      ),
      offset: request.offset ?? 0,
      limit: request.limit ?? 200,
      returned: (request.node_ids ?? []).length * request.security_ids.length,
      has_more: false,
    },
    target: null,
  });

  it.each(["/research/strategies/new", "/research/strategies/s1/revisions/2"])(
    "renders the same backend-owned DAG on %s",
    async (route) => {
      server.use(
        ...graphHandlers(),
        revisionDocumentHandler(GRAPH_SOURCE),
        http.get(`${API}/api/v1/strategies/template`, () => HttpResponse.json(graphSpec("", 0))),
      );
      const user = userEvent.setup();
      mount(`${route}?view=graph`);
      if (route.endsWith("/new")) {
        await user.click(await screen.findByRole("tab", { name: "YAML" }));
        replaceText(await editor(), GRAPH_SOURCE);
        await user.click(screen.getByRole("tab", { name: "그래프" }));
      }
      await user.click(await screen.findByRole("button", { name: /· 노드 편집$/ }));
      expect(screen.queryByRole("region", { name: "전략 파이프라인" })).toBeNull();
      await screen.findByRole("region", { name: "노드 캔버스" });
      await waitFor(() => expect(explainedGraphs).toHaveLength(1));
      // 계획 ready 전환은 캔버스를 다시 마운트할 수 있으므로 현재 DOM을 매번 조회한다.
      await waitFor(() =>
        expect(within(screen.getByRole("region", { name: "노드 캔버스" })).getAllByText("종목별 수치").length).toBeGreaterThan(0),
      );
      const graph = screen.getByRole("region", { name: "노드 캔버스" });
      const node = within(graph).getByRole("button", { name: /^노드 편집: 2\./ });
      expect(node).toHaveTextContent("기간 수익률");
      expect(within(graph).getAllByText("ratio").length).toBeGreaterThan(0);
      expect(within(graph).getAllByText("H 252 세션").length).toBeGreaterThan(0);
      await user.click(node);
      await user.click(screen.getByRole("button", { name: "전략 구성으로 돌아가기" }));
      expect(await screen.findByRole("region", { name: "전략 파이프라인" })).toBeVisible();
      expect(screen.queryByRole("region", { name: "노드 캔버스" })).toBeNull();
    },
    15_000,
  );

  it.each([
    ["/research/strategies/new", "snap", true],
    ["/research/strategies/new", "old-snapshot", false],
    ["/research/strategies/new", "pending", false],
    ["/research/strategies/new", "failure", false],
    ["/research/strategies/s1/revisions/2", "snap", true],
    ["/research/strategies/s1/revisions/2", "old-snapshot", false],
    ["/research/strategies/s1/revisions/2", "pending", false],
    ["/research/strategies/s1/revisions/2", "failure", false],
  ] as const)("%s의 필드 선택기는 계약 판본 %s 정합성을 따른다", async (route, snapshotId, available) => {
    let recovered = false;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    server.use(
      http.get(`${API}/api/v1/equity/catalog`, async () => {
        if (snapshotId === "pending" && !recovered) await gate;
        if (snapshotId === "failure" && !recovered) return HttpResponse.json({ detail: "fixture failure" }, { status: 503 });
        return HttpResponse.json({
        snapshot: { snapshot_id: recovered ? "snap" : snapshotId, schema_version: "1", source: "test", built_at: "2026-10-13T00:00:00Z", point_in_time: true, dataset_revisions: [] },
        page: 1, page_size: 100, page_count: 1, total: 1,
        facets: { dataset_ids: ["prices"], frequencies: ["daily"], units: ["KRW"] },
        fields: [{ field_id: "price.close", label: "판본별 종가", description: "test", dataset_id: "prices", unit: "KRW", frequency: "daily", value_type: "price", available_date_basis: "session", disclosure_basis: "session", evidence: "fixture", recommended_lag_sessions: 0, coverage: { starts_on: "2020-01-01", ends_on: "2026-10-12", estimated_coverage_pct: 100, point_in_time: true, supported_cell_kinds: [], venues: ["KRX"] } }],
      } satisfies ResearchCatalog);
      }),
      ...graphHandlers(), revisionDocumentHandler(GRAPH_SOURCE),
    );
    const user = userEvent.setup();
    const { queryClient } = mountWithClient(`${route}?view=graph&path=%2Ffactors%2F0%2Fgraph%2Fnodes%2F0`);
    if (route.endsWith("/new")) {
      await user.click(await screen.findByRole("tab", { name: "YAML" }));
      replaceText(await editor(), GRAPH_SOURCE);
      await user.click(screen.getByRole("tab", { name: "그래프" }));
    }
    const graph = await screen.findByRole("region", { name: "노드 캔버스" });
    await user.click(within(graph).getByRole("button", { name: /^노드 편집: 1\./ }));
    if (available) expect((await screen.findAllByRole("option", { name: /판본별 종가/ })).length).toBeGreaterThan(0);
    else {
      await waitFor(() => expect(screen.queryAllByRole("option", { name: /판본별 종가/ })).toHaveLength(0));
      const field = await waitFor(() => {
        const input = screen.getAllByRole("textbox").find((node) => (node as HTMLInputElement).value === "price.close");
        expect(input).toBeDefined();
        // revision 화면은 그래프로 바로 열려 숨은 편집기 chunk가 늦게 붙는다. 붙기 전 칸은 잠겨 있으니(`editor`)
        // 보이는 것만이 아니라 편집 가능해질 때까지 기다린다.
        expect(input).toBeEnabled();
        return input!;
      });
      await user.clear(field);
      await user.type(field, "price.manual");
      await user.tab();
      await user.click(screen.getByRole("tab", { name: "YAML" }));
      const currentEditor = await editor();
      await waitFor(() => expect(currentEditor.state.doc.toString()).toContain("price.manual"));
      await user.click(screen.getByRole("tab", { name: "그래프" }));
      recovered = true;
      release();
      await act(async () => { await queryClient.invalidateQueries(); });
      expect((await screen.findAllByRole("option", { name: /판본별 종가/ })).length).toBeGreaterThan(0);
      expect(currentEditor.state.doc.toString()).toContain("price.manual");
    }
    release();
  }, 15_000);

  it.each([
    ["/research/strategies/new", "pending"],
    ["/research/strategies/new", "mismatch"],
    ["/research/strategies/s1/revisions/2", "pending"],
    ["/research/strategies/s1/revisions/2", "mismatch"],
  ] as const)("%s 레시피가 카탈로그 %s 안내를 보존한다", async (route, state) => {
    const source = GRAPH_SOURCE.slice(0, GRAPH_SOURCE.indexOf("      nodes:")) + '      nodes: []\n      output_node_id: ""\n';
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    server.use(
      http.get(`${API}/api/v1/equity/catalog`, async () => {
        if (state === "pending") await gate;
        return HttpResponse.json({
          snapshot: { snapshot_id: "old-snapshot", schema_version: "1", source: "test", built_at: "2026-10-13T00:00:00Z", point_in_time: true, dataset_revisions: [] },
          page: 1, page_size: 100, page_count: 1, total: 0,
          facets: { dataset_ids: [], frequencies: [], units: [] }, fields: [],
        } satisfies ResearchCatalog);
      }),
      ...graphHandlers(), revisionDocumentHandler(source),
    );
    const user = userEvent.setup();
    try {
      mount(`${route}?view=graph&recipe=true&path=%2Ffactors%2F0%2Fgraph`);
      if (route.endsWith("/new")) {
        await user.click(await screen.findByRole("tab", { name: "YAML" }));
        replaceText(await editor(), source);
        await user.click(screen.getByRole("tab", { name: "그래프" }));
      }
      await waitFor(() => expect(screen.getByRole("status", { name: "문서 상태" })).toHaveAttribute("data-settled", "true"));
      await user.click(await screen.findByRole("button", { name: "데이터 필드 노드 추가" }));
      const message = state === "pending"
        ? "카탈로그를 불러오는 중입니다."
        : "계약과 카탈로그 버전이 달라 상세 정보를 연결하지 않았습니다.";
      expect(await screen.findByText(message)).toBeVisible();
      expect(screen.getByRole("combobox", { name: "데이터 필드 1" })).toHaveValue("");
    } finally { release(); }
  }, 15_000);

  // #357 C-P3-16: 유니버스가 비어 실행 설정 전체가 무효여도 실행 계획 설명은 고른 결측 정책을 싣는다.
  it.each(["/research/strategies/new", "/research/strategies/s1/revisions/2"])(
    "sends the chosen missing policy to the plan explanation on %s before the settings are complete",
    async (route) => {
      localStorage.setItem(
        `${RUN_ENVIRONMENT_STORAGE_PREFIX}:last`,
        JSON.stringify({
          ...Object.fromEntries(
            Object.entries(RUN_ENVIRONMENT).map(([key, value]) => [
              key,
              String(value),
            ]),
          ),
          universe_id: "",
          missing: "zero",
        }),
      );
      server.use(...graphHandlers());
      mount(`${route}?view=graph`);

      await waitFor(() => expect(explainedGraphs).toHaveLength(1));
      expect(explainedGraphs[0]).toMatchObject({ missing: "zero" });
    },
    15_000,
  );

  it("keeps graph selection in the URL, then opens the exact YAML node", async () => {
    server.use(
      ...graphHandlers(),
      revisionDocumentHandler(GRAPH_SOURCE),
    );
    const user = userEvent.setup();
    const nodePath = "%2Ffactors%2F0%2Fgraph%2Fnodes%2F1";
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
    await user.click(screen.getByRole("tab", { name: "그래프" }));
    await screen.findByRole("region", { name: "노드 캔버스" });
    const node = await screen.findByRole("button", {
      name: /^노드 편집: 2\./,
    });
    await user.click(node);
    await waitFor(() => {
      expect(history.location.search).toContain("view=graph");
      expect(history.location.search).toContain(
        "path=%2Ffactors%2F0%2Fgraph%2Fnodes%2F1",
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
            schema_version: "1.2",
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
    await openTrace();
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
    expect(screen.getByText("2026-09-01", { selector: ".strategy-debugger__source > summary > code" })).toBeVisible();
  }, 15_000);

  // #351: 추적의 실행 설정 거절은 백테스트 시작과 같은 코드라 같은 문장·날짜로 보이고, 서버 원문은 접힌 사유로
  // 간다. 전에는 봉인 구간 같은 영구 조건이 "잠시 뒤 다시 추적하세요"로 떨어지고 사유도 비었다.
  it("says a research window trace rejection with its dates and folds the server reason", async () => {
    server.use(
      ...graphHandlers(),
      http.post(`${API}/api/v1/strategies/debug/trace`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "backtest.run.research_window_violation",
              message:
                "측정 시작일이 연구 구간 밖이라 실행할 수 없다 — requested_by=strategy.trace('그래프 전략')",
              sealed_start: "2016-01-01",
              sealed_end: "2019-12-31",
              research_start: "2020-01-02",
            },
          },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/new");
    await openTrace();
    const security = await screen.findByRole("textbox", { name: "종목 ID" });
    await waitFor(() => expect(security).toBeEnabled());
    await user.type(security, "sec-a");
    await user.click(screen.getByRole("button", { name: "추적 실행" }));

    const alert = await screen.findByRole("alert", { name: "추적 실패" });
    expect(alert).toHaveTextContent(
      "추적 실패: 시작일이 연구 구간 밖입니다. 2016-01-01~2019-12-31은 홀드아웃으로 봉인돼 있고 그 앞도 측정하지 않습니다. 전략 편집기의 실행 설정에서 시작일을 2020-01-02 이후로 옮긴 뒤",
    );
    expect(alert).not.toHaveTextContent("잠시 뒤 다시");
    expect(
      within(alert).getByText("서버 사유").closest("details"),
    ).toHaveTextContent("requested_by=strategy.trace");
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
            schema_version: "1.2",
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
    const nodePath = "%2Ffactors%2F0%2Fgraph%2Fnodes%2F1";
    const initial = `/research/strategies/s1/revisions/2?asOf=2026-08-31&security=sec-r&path=${nodePath}`;
    const history = mount(initial);
    const user = userEvent.setup();
    await openTrace();

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
        `/research/strategies/s1/revisions/2?asOf=2026-08-30&security=sec-next&path=%2Ffactors%2F0%2Fgraph%2Fnodes%2F0`,
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

  it.each(["resolve", "reject", "abort"] as const)(
    "keeps a newer Graph URL generation after a deferred trace %s",
    async (outcome) => {
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
      );
      let request: StrategyTraceRequest | undefined;
      let requestSignal: AbortSignal | undefined;
      let resolveTrace: ((response: StrategyTraceResponse) => void) | undefined;
      let rejectTrace: ((reason?: unknown) => void) | undefined;
      vi.spyOn(strategyWorkbenchApi, "traceStrategy").mockImplementation(
        (nextRequest, signal) => {
          request = nextRequest;
          requestSignal = signal;
          return new Promise((resolve, reject) => {
            resolveTrace = resolve;
            rejectTrace = reject;
            signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          });
        },
      );
      const nodePath = "/factors/0/graph/nodes/1";
      const history = mount(
        `/research/strategies/s1/revisions/2?asOf=2026-08-31&security=sec-r&path=${encodeURIComponent(nodePath)}`,
      );
      const user = userEvent.setup();
      await openTrace();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "추적 실행" })).toBeEnabled(),
      );
      await user.click(screen.getByRole("button", { name: "추적 실행" }));
      await waitFor(() => expect(request).toBeDefined());

      await user.click(screen.getByRole("button", { name: "전략 편집" }));
      await user.click(screen.getByRole("tab", { name: "그래프" }));
      await openComparison();
      await screen.findByLabelText("StrategySpec Diff");
      await waitFor(() =>
        expect(new URLSearchParams(history.location.search).get("view")).toBe(
          "graph",
        ),
      );

      if (outcome === "resolve") {
        await act(async () => resolveTrace?.(completedTrace(request!)));
      } else if (outcome === "reject") {
        await act(async () =>
          rejectTrace?.(new Error("deferred trace failed")),
        );
      } else {
        await openTrace();
        await user.click(screen.getByRole("button", { name: "취소" }));
        await waitFor(() => expect(requestSignal?.aborted).toBe(true));
        await user.click(screen.getByRole("button", { name: "전략 편집" }));
      }

      await waitFor(() => {
        const params = new URLSearchParams(history.location.search);
        expect(params.get("view")).toBe("graph");
        expect(params.get("path")).toBe(nodePath);
        expect(params.get("asOf")).toBe("2026-08-31");
        expect(params.get("security")).toBe("sec-r");
      });
      expect(screen.getByRole("button", { name: "리비전 변경 비교" })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
      await openTrace();
      expect(screen.getByLabelText("기준일")).toHaveValue("2026-08-31");
      expect(screen.getByRole("textbox", { name: "종목 ID" })).toHaveValue(
        "sec-r",
      );
    },
    15_000,
  );
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
            schema_version: "1.2",
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

    await openComparison();
    const panel = await screen.findByLabelText("StrategySpec Diff");
    expect(within(panel).getByText("+1 / −0 변경 항목")).toBeInTheDocument();
    expect(within(panel).getByText("# research note")).toBeInTheDocument();
    expect(
      await within(panel).findByText("의미 변경이 없습니다 (같은 spec hash)."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "YAML" }));
    const invalidView = await editor();
    replaceText(invalidView, 'schema_version: "1.2"\ntitle: "broken\n');
    await waitFor(() => expect(saveButton()).toBeDisabled());
    await openComparison();
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
        ? 'schema_version: "1.2"\ntitle: 이전 전략\n'
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
    await openComparison();
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

  it("저장 중 추가 편집은 새 저장 revision을 backend 의미 diff 기준으로 사용한다", async () => {
    const savedSource = `${STORED}description: saved\n`;
    const currentSource = `${STORED}description: after\n`;
    const preSaveHash = "a".repeat(64);
    const savedHash = "c".repeat(64);
    const currentHash = "d".repeat(64);
    const compileSources: string[] = [];
    let saveCompleted = false;
    server.use(
      http.post(`${API}/api/v1/strategies/:strategyId/draft-diff`, async ({ request }) => {
        const body = await request.json() as { base_revision: number; source: string };
        expect(body).toMatchObject({ base_revision: 3, source: currentSource });
        return HttpResponse.json({ strategy_id: "s1", base_revision: 3, base_spec_hash: savedHash, target_spec_hash: currentHash, source_hash: "b".repeat(64), changes: [{ pointer: "/description", kind: "changed", before: "saved", after: "after" }] });
      }),

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
            schema_version: "1.2",
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

    await openComparison();
    const panel = await screen.findByLabelText("StrategySpec Diff");
    expect(await within(panel).findByText("/description")).toBeInTheDocument();
    expect(within(panel).getByText('"saved"')).toBeInTheDocument();
    expect(within(panel).getByText('"after"')).toBeInTheDocument();
    expect(within(panel).queryByText("/deployment")).not.toBeInTheDocument();
    expect(
      compileSources.filter((source) => source === savedSource),
    ).toHaveLength(1);
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
              `schema_version: "1.2"\ntitle: revision ${revision}\n`,
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
    await openComparison();
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
  it.each(["loading", "mismatch", "error"])("선택 설정 %s에서는 단축키와 추적 모두 POST하지 않고 명시 해제 뒤만 기본값을 쓴다", async (mode) => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    server.use(
      ...graphHandlers(), revisionDocumentHandler(GRAPH_SOURCE),
      http.get(`${API}/api/v1/backtests/selected/request`, async () => {
        if (mode === "loading") await pending;
        if (mode === "error") return HttpResponse.json({}, { status: 503 });
        return HttpResponse.json({ strategy_source: { kind: "saved_revision", strategy_id: "s1", revision: mode === "mismatch" ? 1 : 2, expected_spec_hash: "2".repeat(64) }, parameter_values: { scale: 0 } });
      }),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2?run=selected&asOf=2026-08-31&security=sec-a&path=%2Ffactors%2F0%2Fgraph%2Fnodes%2F1");
    await waitFor(() => expect(explainedGraphs).toHaveLength(1));
    if (mode !== "loading") await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "백테스트" })).toBeDisabled();
    await openTrace();
    expect(screen.getByRole("button", { name: "추적 실행" })).toBeDisabled();
    await user.keyboard("{Control>}{Shift>}{Enter}{/Shift}{/Control}");
    await user.keyboard("{Meta>}{Shift>}{Enter}{/Shift}{/Meta}");
    expect(started).toHaveLength(0);
    expect(tracedStrategies).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "문서 기본값으로 돌아가기" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "추적 실행" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "백테스트" })).toBeEnabled();
    release();
    expect(started).toHaveLength(0);
    expect(tracedStrategies).toHaveLength(0);
    // 같은 실행 단축키는 선택을 명시 해제한 뒤에는 실제 요청을 만든다.
    await user.keyboard("{Control>}{Shift>}{Enter}{/Shift}{/Control}");
    await waitFor(() => expect(started).toHaveLength(1));
  });

  it("선택 요청의 값을 실행 gateway에 그대로 넘기고 실행 환경은 현재 입력을 유지한다", async () => {
    server.use(http.get(`${API}/api/v1/backtests/selected/request`, () => HttpResponse.json({
      strategy_source: { kind: "saved_revision", strategy_id: "s1", revision: 2, expected_spec_hash: "2".repeat(64) },
      parameter_values: { scale: 0, enabled: false }, environment: { start: "2024-01-01", end: "2024-12-31", universe_id: "past" },
    })));
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2?run=selected");
    const run = await screen.findByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);
    await waitFor(() => expect(started).toHaveLength(1));
    expect(started[0]).toMatchObject({ parameter_values: { scale: 0, enabled: false }, environment: RUN_ENVIRONMENT });
  });

  it("blocks lossy and non-positive integers locally before any request", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests`, async ({ request }) => {
        started.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ detail: "unexpected" }, { status: 500 });
      }),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    await editor();
    const run = screen.getByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(screen.getByLabelText("실행 설정 열기"));
    await user.click(screen.getByLabelText("고급 실행", { selector: "summary" }));
    const annualization = screen.getByRole("spinbutton", {
      name: "연환산 거래일",
    });

    fireEvent.change(annualization, {
      target: { value: "9007199254740993" },
    });
    await waitFor(() => expect(run).toBeDisabled());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "정확히 전송 가능한 정수",
    );
    // 0 이하도 backend 가 거절하므로 패널이 먼저 막는다(이슈 #260).
    fireEvent.change(annualization, { target: { value: "-1" } });
    await waitFor(() => expect(run).toBeDisabled());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "연환산 거래일: 1 이상이고 정확히 전송 가능한 정수를 입력하세요.",
    );
    expect(started).toHaveLength(0);
  });

  // 이슈 #260 DEFECT-1: 시작 거절은 개발자 진단(`API request failed: … status=422 code=…`)이 아니라 코드의
  // 번역 문장으로 보이고, 서버 사유는 접힌 상세로 내려간다.
  it("shows a coded start rejection as a translated sentence with the server reason folded", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests`, async ({ request }) => {
        started.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          {
            detail: {
              code: "backtest.run.field_invalid",
              field: "initial_cash",
              message:
                "Value error, initial_cash must be positive — initial_cash=0.0 — field=initial_cash",
            },
          },
          { status: 422 },
        );
      }),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    await editor();
    const run = screen.getByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);

    await waitFor(() => expect(started).toHaveLength(1));
    const alert = await screen.findByRole("alert");
    // 거절이 가리킨 칸(`field`)을 실행 설정의 칸 이름으로 말한다.
    expect(alert).toHaveTextContent(
      "백테스트 시작 실패: 서버가 실행 설정의 초기 자본 칸 값을 받지 않았습니다. 전략 편집기의 실행 설정에서 그 칸을 고친 뒤 다시 실행하세요.",
    );
    expect(alert).not.toHaveTextContent("API request failed");
    expect(alert).not.toHaveTextContent("status=422");
    const reason = within(alert).getByText("서버 사유");
    expect(reason.closest("details")).not.toHaveAttribute("open");
    expect(reason.closest("details")).toHaveTextContent(
      "initial_cash must be positive",
    );
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/2",
    );
  });

  it("names the rejected run environment field on the new strategy screen too", async () => {
    server.use(
      ...graphHandlers(),
      http.post(`${API}/api/v1/backtests`, async ({ request }) => {
        started.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          {
            detail: {
              code: "backtest.run.field_invalid",
              field: "environment.fee_bps",
              message:
                "Value error, run environment value is out of range — field=environment.fee_bps",
            },
          },
          { status: 422 },
        );
      }),
    );
    const user = userEvent.setup();
    mount("/research/strategies/new");
    const run = await screen.findByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);

    await waitFor(() => expect(started).toHaveLength(1));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "백테스트 시작 실패: 서버가 실행 설정의 수수료 칸 값을 받지 않았습니다.",
    );
    // 교정 버튼은 연구 구간 거절에만 있다(V5-05).
    expect(within(alert).queryByRole("button")).toBeNull();
  }, 15_000);

  // 검증 랩 V1-01·V5-05: 툴바도 거절 문장의 날짜 자리표시자를 detail 값으로 채우고, 교정 버튼이 시작일을
  // detail 의 연구 하한으로 옮긴다(날짜 owner 는 backend).
  it("fills the research window dates into the toolbar rejection and moves the start date on request", async () => {
    server.use(
      ...graphHandlers(),
      http.post(`${API}/api/v1/backtests`, async ({ request }) => {
        started.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          {
            detail: {
              code: "backtest.run.research_window_violation",
              message: "측정 시작일이 연구 구간 밖이라 실행할 수 없다",
              sealed_start: "2016-01-01",
              sealed_end: "2019-12-31",
              research_start: "2020-01-02",
            },
          },
          { status: 422 },
        );
      }),
    );
    const user = userEvent.setup();
    mount("/research/strategies/new");
    const run = await screen.findByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "백테스트 시작 실패: 시작일이 연구 구간 밖입니다. 2016-01-01~2019-12-31은 홀드아웃으로 봉인돼 있고 그 앞도 측정하지 않습니다. 전략 편집기의 실행 설정에서 시작일을 2020-01-02 이후로 옮긴 뒤 다시 실행하세요.",
    );
    await user.click(
      within(alert).getByRole("button", { name: "시작일을 2020-01-02로" }),
    );

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);
    await waitFor(() => expect(started).toHaveLength(2));
    expect(started[1]?.environment).toEqual({
      ...RUN_ENVIRONMENT,
      start: "2020-01-02",
    });
  }, 15_000);

  it("falls back to a general sentence when the rejection has no translation", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests`, () =>
        HttpResponse.json(
          { detail: [{ type: "missing", loc: ["body"], msg: "Field required" }] },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2");
    await editor();
    const run = screen.getByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "백테스트 시작 실패: 서버가 실행 요청을 받지 않았습니다.",
    );
    expect(alert).not.toHaveTextContent("API request failed");
    expect(within(alert).getByText("서버 사유").closest("details")).toHaveTextContent(
      "body: Field required",
    );
  });

  it("runs a clean saved revision by reference, retires history, and moves to the run page", async () => {
    const user = userEvent.setup();
    const { history, queryClient } = mountWithClient(
      "/research/strategies/s1/revisions/2",
    );
    const list = backtestHistoryQuery({ offset: 0, limit: 25 });
    const stalePage = { items: [], total: 0, offset: 0, limit: 25 };
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
    await editor();
    const run = screen.getByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(screen.getByLabelText("문서 정보", { selector: "summary" }));
    expect(within(globalThis.document.querySelector(".ide__meta")!).getByText("2".repeat(64))).toBeVisible(); // backend 원본 spec hash
    await user.click(run);
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/backtests/run-7"),
    );
    expect(started).toEqual([
      {
        core: "rust",
        initial_cash: 100_000_000,
        benchmark_security_id: null,
        annualization_days: 252,
        metric_windows: [],
        environment: RUN_ENVIRONMENT,
        strategy_source: {
          kind: "saved_revision",
          strategy_id: "s1",
          revision: 2,
          expected_spec_hash: "2".repeat(64),
        },
      },
    ]);
    expect(queryClient.getQueryData(list.queryKey)).toBeUndefined();
    releaseLatePage?.();
    await oldFetch;
    expect(queryClient.getQueryData(list.queryKey)).toBeUndefined();
  });

  it("runs an edited document as an inline draft with provenance, and never while invalid", async () => {
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    const view = await editor();
    replaceText(view, `${STORED}description: 개정\n`);
    const run = screen.getByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);
    await waitFor(() => expect(started).toHaveLength(1));
    expect(started[0]).toEqual({
      core: "rust",
      initial_cash: 100_000_000,
      benchmark_security_id: null,
      annualization_days: 252,
      metric_windows: [],
      environment: RUN_ENVIRONMENT,
      strategy_source: {
        kind: "inline_draft",
        spec: expect.objectContaining({ title: "퀄리티 모멘텀" }),
        source_hash: "b".repeat(64),
      },
      lineage_strategy_id: "s1",
    });
    const firstPrompt = await screen.findByRole("alertdialog");
    await user.click(
      within(firstPrompt).getByRole("button", { name: "편집 계속" }),
    );
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/2",
    );
    expect(screen.getByText("백테스트 run-7 접수됨")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "백테스트 보기" }));
    const secondPrompt = await screen.findByRole("alertdialog");
    expect(started).toHaveLength(1);
    await user.click(
      within(secondPrompt).getByRole("button", { name: "결과 보기" }),
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
        screen.getByRole("button", { name: "백테스트" }),
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
    const run = screen.getByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);
    await waitFor(() => expect(started).toHaveLength(1));

    history.push("/research/strategies/s1/revisions/1?view=yaml");
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

  it("explains a saved retired-schema revision the backend refuses to run and disables the run control", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests`, async ({ request }) => {
        started.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          {
            detail: {
              code: "backtest.strategy.requires_upgrade",
              message: "strategy s1 revision 2 is a frozen schema 1.0 row",
            },
          },
          { status: 422 },
        );
      }),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2");
    await editor();
    const run = screen.getByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);

    await waitFor(() => expect(started).toHaveLength(1));
    expect(started[0]).toMatchObject({
      strategy_source: {
        kind: "saved_revision",
        strategy_id: "s1",
        revision: 2,
      },
    });
    const banner = await screen.findByRole("region", {
      name: "이전 schema 문서",
    });
    expect(banner).toHaveTextContent(
      "저장된 이전 schema revision으로는 백테스트를 실행할 수 없습니다",
    );
    expect(run).toBeDisabled();
    expect(history.location.pathname).toBe(
      "/research/strategies/s1/revisions/2",
    );
  });
});

describe("revision conflict (P3-07)", () => {
  it("keeps the conflict recovery notice visible from the Diff view", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/1");
    const view = await editor();
    replaceText(view, `${STORED}description: 충돌\n`);
    await waitFor(() => expect(saveButton()).toBeEnabled());
    await openComparison();
    expect(screen.getByRole("button", { name: "리비전 변경 비교" })).toHaveAttribute(
      "aria-expanded",
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
    expect(screen.getByRole("button", { name: "리비전 변경 비교" })).toHaveAttribute(
      "aria-expanded",
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
    await user.click(screen.getByRole("tab", { name: "YAML" }));
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

    history.push("/research/strategies/s1/revisions/2?view=yaml");
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
    await user.click(screen.getByRole("tab", { name: "그래프" }));
    await waitFor(() => expect(history.location.search).toContain("view=graph"));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(screen.getByRole("link", { name: "백테스트" }));
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

describe("problems and the document status badge follow no tab (P1-01)", () => {
  const problemRow = (message: string | RegExp) =>
    screen.findByRole("button", { name: message });

  /** 키 범위를 쓰는 진단(backend 가 `anchor: key` 로 알린다) — outline의 값 범위 reveal과 구별된다. */
  const UNKNOWN_KEY = {
    code: "structure.unknown_key",
    anchor: "key",
    kind: "structural",
    severity: "error",
    pointer: "/extra_key",
    message: "extra_key is not a known field",
  };

  // jsdom에는 `scrollIntoView`가 없다. 선택된 카드를 화면으로 끌어오는지 보려고 심는다.
  const scrollIntoView = vi.fn();
  let previousScrollIntoView: PropertyDescriptor | undefined;
  beforeEach(() => {
    previousScrollIntoView = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      "scrollIntoView",
    );
    scrollIntoView.mockClear();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
  });
  afterEach(() => {
    if (previousScrollIntoView)
      Object.defineProperty(
        HTMLElement.prototype,
        "scrollIntoView",
        previousScrollIntoView,
      );
    else
      delete (HTMLElement.prototype as { scrollIntoView?: unknown })
        .scrollIntoView;
  });
  /**
   * 보이는 탭이 마지막으로 끌어온 요소. 탭 패널은 모두 마운트돼 숨은 탭(`hidden`)의 reveal 도 mock 에
   * 기록되지만, 브라우저는 박스 없는 요소의 스크롤을 무시한다(`[hidden]` 은 display:none) — 화면에서
   * 일어나는 스크롤만 본다(#392 리뷰 P3-2).
   */
  const lastVisibleScroll = () =>
    scrollIntoView.mock.contexts
      .filter((element) => (element as Element).closest("[hidden]") === null)
      .at(-1);

  it("편집만 한 문서의 검증 오류는 저장 전 수정 안내를 보이고 저장 POST를 만들지 않는다", async () => {
    server.use(graphCompileWith([{
      code: "factor.graph.time_series_window", kind: "semantic", severity: "error",
      pointer: "/factors/0/graph/nodes/1", node_id: "mom_252",
      message: "window는 1 이상이어야 합니다: window=0",
    }]), ...graphHandlers());
    mount("/research/strategies/new");
    const view = await editor();
    replaceText(view, GRAPH_SOURCE.replace("window: 252", "window: 0"));
    expect(await screen.findByText("저장 전 검증 오류 수정")).toBeVisible();
    expect(saveButton()).toBeDisabled();
    expect(screen.queryByText("저장 오류")).not.toBeInTheDocument();
    expect(screen.queryByText("저장 실패")).not.toBeInTheDocument();
    expect(posted).toHaveLength(0);
  });

  it("shows the badge and the problem list while the Graph tab is selected", async () => {
    server.use(
      graphCompileWith([
        {
          // backend는 그래프 노드 진단을 노드 **객체** pointer로 낸다(`_validation.py`의
          // `nodes.{index}` → `<factor>.graph.nodes.N`). 필드 pointer로 mocking하면 화면이
          // 실제로 못 하는 일을 통과시킨다(리뷰 차단 2).
          code: "factor.graph.time_series_window",
          kind: "semantic",
          severity: "error",
          pointer: "/factors/0/graph/nodes/1",
          node_id: "mom_252",
          message: "window는 1 이상이고 lag는 0 이상이어야 합니다: window=0 lag=0",
        },
      ]),
      ...graphHandlers(),
      revisionDocumentHandler(GRAPH_SOURCE),
    );
    mount("/research/strategies/s1/revisions/2?view=graph");

    const status = await screen.findByRole("status", { name: "문서 상태" });
    await waitFor(() => expect(status).toHaveTextContent("검증 오류"));
    const problems = await screen.findByRole("region", { name: "문제" });
    expect(problems).toHaveTextContent("오류 1 · 경고 0");
    expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  }, 15_000);

  it("keeps the Graph tab and selects the pointer the graph editor draws", async () => {
    server.use(
      graphCompileWith([
        {
          // backend는 그래프 노드 진단을 노드 **객체** pointer로 낸다(`_validation.py`의
          // `nodes.{index}` → `<factor>.graph.nodes.N`). 필드 pointer로 mocking하면 화면이
          // 실제로 못 하는 일을 통과시킨다(리뷰 차단 2).
          code: "factor.graph.time_series_window",
          kind: "semantic",
          severity: "error",
          pointer: "/factors/0/graph/nodes/1",
          node_id: "mom_252",
          message: "window는 1 이상이고 lag는 0 이상이어야 합니다: window=0 lag=0",
        },
      ]),
      ...graphHandlers(),
      revisionDocumentHandler(GRAPH_SOURCE),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=graph");

    // 진단 선택이 전략 구성에서 해당 노드 편집으로 이동한다. compile 오류여도 원문 tree를 편집한다.
    await screen.findByRole("region", { name: "전략 파이프라인" });
    await user.click(await problemRow(/window는 1 이상이고 lag는 0 이상이어야 합니다/));
    await screen.findByRole("region", { name: "그래프 편집" });

    await waitFor(() => {
      expect(history.location.search).toContain("view=graph");
      expect(history.location.search).toContain(
        "path=%2Ffactors%2F0%2Fgraph%2Fnodes%2F1",
      );
    });
    // URL은 wire일 뿐이다 — 그래프 노드가 실제로 선택 표시를 받고 화면으로 끌려오는지도 본다.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^노드 편집: 2\./ }).closest(".node-canvas__node"),
      ).toHaveAttribute("aria-current", "true"),
    );
    // 노드 pointer 진단이므로 가장 구체적인 선택은 노드 행이고 스크롤도 거기로 간다.
    const editorRow = screen
      .getByRole("button", { name: /^노드 편집: 2\./ })
      .closest(".node-canvas__node");
    expect(lastVisibleScroll()).toBe(editorRow);
    // 캔버스 badge가 해당 노드를 가리키며, 인스펙터가 진단 본문을 한 번 소유한다.
    expect(editorRow).toHaveTextContent("문제 1개");
    expect(
      screen.getByRole("group", { name: /선택한 노드/ }),
    ).toHaveTextContent("window는 1 이상이고 lag는 0 이상이어야 합니다");
    expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  }, 15_000);

  it("keeps the Graph tab and reveals the pipeline card a stage problem points at (lang2 P4-02)", async () => {
    server.use(
      graphCompileWith([
        {
          code: "strategy.risk.max_name_weight",
          kind: "semantic",
          severity: "error",
          pointer: "/risk/max_name_weight",
          message: "종목 한도는 0보다 크고 1 이하여야 합니다.",
        },
      ]),
      ...graphHandlers(),
      revisionDocumentHandler(GRAPH_SOURCE),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=graph");

    const canvas = await screen.findByRole("region", {
      name: "전략 파이프라인",
    });
    await user.click(await problemRow(/종목 한도는 0보다 크고 1 이하여야/));

    // 단계 카드 행은 Graph 탭 맨 위 캔버스가 그린다 — 원문 탭으로 가지 않고 그 카드로 간다.
    await waitFor(() => {
      expect(history.location.search).toContain("view=graph");
      expect(history.location.search).toContain(
        "path=%2Frisk%2Fmax_name_weight",
      );
    });
    const card = within(canvas).getByRole("group", {
      name: "종목별 최대 목표 비중 한도",
    });
    await waitFor(() => expect(card).toHaveAttribute("aria-current", "true"));
    expect(card).toHaveTextContent("종목 한도는 0보다 크고 1 이하여야 합니다.");
    await waitFor(() => expect(lastVisibleScroll()).toBe(card.querySelector('.pipeline__field[aria-current="true"]')));
    expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  }, 15_000);

  it("keeps the Graph tab and marks the canvas list a list-level problem points at (lang2 P4-02)", async () => {
    // 빈 문서의 첫 문제(`strategy.factor.required`)는 목록 자체(`/factors`)를 가리킨다. 캔버스에서 이
    // pointer 를 표시하는 것은 목록 group 의 `aria-current` 하나다(#395 리뷰 P3-1).
    server.use(
      graphCompileWith([
        {
          code: "strategy.factor.required",
          kind: "semantic",
          severity: "error",
          pointer: "/factors",
          message: "팩터를 하나 이상 추가하세요.",
        },
      ]),
      ...graphHandlers(),
      revisionDocumentHandler('schema_version: "1.2"\ntitle: 빈 전략\n'),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=graph");

    const canvas = await screen.findByRole("region", {
      name: "전략 파이프라인",
    });
    await user.click(await problemRow(/팩터를 하나 이상 추가하세요/));

    await waitFor(() => {
      expect(history.location.search).toContain("view=graph");
      expect(history.location.search).toContain("path=%2Ffactors");
    });
    const list = within(canvas).getByRole("group", { name: "알파 팩터" });
    await waitFor(() => expect(list).toHaveAttribute("aria-current", "true"));
    expect(list).toHaveTextContent("팩터를 하나 이상 추가하세요.");
    expect(lastVisibleScroll()).toBe(list);
  }, 15_000);

  it("제목 오류를 누르면 그래프를 유지하고 문서 속성의 실제 입력을 드러낸다", async () => {
    server.use(
      graphCompileWith([{ code: "strategy.title.required", kind: "semantic", severity: "error",
        pointer: "/title", message: "전략 이름을 입력하세요." }]),
      ...graphHandlers(),
      revisionDocumentHandler('schema_version: "1.2"\ntitle: ""\n'),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=graph");
    const canvas = await screen.findByRole("region", { name: "전략 파이프라인" });
    await user.click(await problemRow(/전략 이름을 입력하세요/));
    await waitFor(() => {
      expect(history.location.search).toContain("view=graph");
      expect(history.location.search).toContain("path=%2Ftitle");
    });
    const title = within(canvas).getByRole("textbox", { name: "전략 이름" });
    await waitFor(() => expect(lastVisibleScroll()).toBe(title.closest('[aria-current="true"]')));
    expect(title).toBeVisible();
  }, 15_000);

  // plan이 ready면 읽기 전용 DAG 노드와 그 아래 편집기 행에 같은 pointer로 `aria-current`가
  // 둘 붙는다. 중첩된 두 reveal 훅이 같은 요소로 수렴해야 한다(2차 리뷰 R2-1).
  it("reveals the editable canvas node again after removing the old DAG strip", async () => {
    server.use(
      graphCompileWith([
        {
          code: "factor.graph.time_series_window",
          kind: "semantic",
          severity: "warning",
          pointer: "/factors/0/graph/nodes/1",
          node_id: "mom_252",
          message: "window가 깁니다",
        },
      ]),
      ...graphHandlers(),
      revisionDocumentHandler(GRAPH_SOURCE),
    );
    const user = userEvent.setup();
    mount("/research/strategies/s1/revisions/2?view=graph");

    await screen.findByRole("region", { name: "전략 파이프라인" });
    await user.click(await problemRow(/window가 깁니다/));
    await screen.findByRole("region", { name: "노드 캔버스" });
    await screen.findByRole("region", { name: "그래프 편집" });

    const editorRow = screen
      .getByRole("button", { name: /^노드 편집: 2\./ })
      .closest(".node-canvas__node");
    await waitFor(() =>
      expect(editorRow).toHaveAttribute("aria-current", "true"),
    );
    expect(globalThis.document.querySelector(".factor-graph__canvas")).toBeNull();
    const before = scrollIntoView.mock.calls.length;
    await user.click(await problemRow(/window가 깁니다/));
    await waitFor(() => expect(scrollIntoView.mock.calls.length).toBeGreaterThan(before));
    expect(lastVisibleScroll()).toBe(editorRow);
    // 경고도 같은 자리에 본문으로 붙는다(alert이 아니라 본문이다 — 리뷰 P3).
    expect(editorRow).toHaveTextContent("문제 1개");
    expect(screen.getByRole("group", { name: /선택한 노드/ })).toHaveTextContent("window가 깁니다");
  }, 15_000);

  it("falls back to the source tab and the line when the Graph tab cannot draw the pointer", async () => {
    server.use(
      graphCompileWith([UNKNOWN_KEY]),
      ...graphHandlers(),
      revisionDocumentHandler(`${GRAPH_SOURCE}\nextra_key: example\n`),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=graph");

    await user.click(await problemRow(/extra_key is not a known field/));

    await waitFor(() =>
      expect(history.location.search).not.toContain("view=graph"),
    );
    // `structure.unknown_key`는 키 범위(`keyRanges`), outline reveal은 값 범위(`valueRanges`)라
    // 둘이 다르다. 정확히 비교해야 진단 reveal이 마지막에 썼음을 고정한다(리뷰 P3-1).
    const view = await editor();
    await waitFor(() =>
      expect(
        view.state.sliceDoc(
          view.state.selection.main.from,
          view.state.selection.main.to,
        ),
      ).toBe("extra_key"),
    );
  }, 15_000);

  it("falls back to the source tab from the read-only Diff projection too", async () => {
    server.use(
      graphCompileWith([UNKNOWN_KEY]),
      ...graphHandlers(),
      revisionDocumentHandler(`${GRAPH_SOURCE}\nextra_key: example\n`),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=diff");
    await openComparison();

    await user.click(await problemRow(/extra_key is not a known field/));

    await waitFor(() =>
      expect(history.location.search).not.toContain("view=diff"),
    );
    const view = await editor();
    await waitFor(() =>
      expect(
        view.state.sliceDoc(
          view.state.selection.main.from,
          view.state.selection.main.to,
        ),
      ).toBe("extra_key"),
    );
  }, 15_000);

  it("reveals the Form card the problem points at without leaving the Form tab", async () => {
    server.use(
      graphCompileWith([
        {
          code: "strategy.factor.weight",
          kind: "semantic",
          severity: "error",
          pointer: "/factors/0/weight",
          message: "weight must be positive",
        },
      ]),
      ...graphHandlers(),
      revisionDocumentHandler(GRAPH_SOURCE),
    );
    const user = userEvent.setup();
    const history = mount("/research/strategies/s1/revisions/2?view=form");

    await screen.findByLabelText("전략 파이프라인");
    await user.click(await problemRow(/weight must be positive/));

    await waitFor(() =>
      expect(history.location.search).toContain("view=graph"),
    );
    const card = within(screen.getByRole("region", { name: "전략 파이프라인" })).getByRole("group", { name: "모멘텀" });
    await waitFor(() => expect(card).toHaveAttribute("aria-current", "true"));
    // 카드 안에서 문제가 가리킨 필드 행까지 표시되고, 스크롤은 그 행으로 간다(P1-04).
    const row = card.querySelector('.pipeline__field[aria-current="true"]');
    expect(row).not.toBeNull();
    expect(card).toHaveTextContent("weight must be positive");
    await waitFor(() => expect(lastVisibleScroll()).toBe(row));

    // 같은 행을 다시 눌렀을 때도 끌어온다 — URL은 그대로라 reveal 신호가 대신 올라간다(2차 리뷰 R2-2).
    const before = scrollIntoView.mock.calls.length;
    await user.click(await problemRow(/weight must be positive/));
    await waitFor(() =>
      expect(scrollIntoView.mock.calls.length).toBeGreaterThan(before),
    );
    expect(lastVisibleScroll()).toBe(row);
  }, 15_000);
});

describe("AI 어시스턴트 제안 적용 (B-04)", () => {
  /** 사이드바를 열고 질문 하나를 보낸 뒤 그 턴의 SSE 연결을 돌려준다. */
  const askAssistant = async (user: ReturnType<typeof userEvent.setup>) => {
    // AI 열은 처음부터 열려 있다(RD-04-A). 접혀 있을 때만 단축키로 연다.
    if (screen.queryByRole("complementary", { name: "AI 어시스턴트" }) === null)
      fireEvent.keyDown(window, { key: "a", altKey: true });
    const input = await screen.findByRole("textbox", {
      name: "어시스턴트에게 보낼 메시지",
    });
    await user.type(input, "지금 시장에 맞는 전략을 제안해 줘");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(assistantTurns).toHaveLength(1));
    await waitFor(() => expect(assistantStream).not.toBeNull());
    return assistantStream!;
  };

  const proposal = (source: string) => ({
    title: "저변동 모멘텀",
    summary: "변동성이 낮은 모멘텀 상위 종목을 매수합니다.",
    rationale: "최근 국내 시장은 저변동 구간입니다.",
    sources: [],
    source_format: "yaml" as const,
    source_text: source,
    compile: { ok: true, spec_hash: "hash-1", diagnostics: [] },
  });

  it("사이드바 제안을 편집기에 적용하고 실행 취소 한 번으로 되돌린다", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/new");
    const view = await editor();
    const before = view.state.doc.toString();
    // 실행 옵션 칸이 틀려도 턴은 실행 설정을 싣는다(#355 위험성의 두 번째 경우).
    await user.click(screen.getByLabelText("실행 설정 열기"));
    await user.click(screen.getByLabelText("고급 실행", { selector: "summary" }));
    fireEvent.change(
      screen.getByRole("spinbutton", { name: "초기 자본 (KRW)" }),
      { target: { value: "0" } },
    );
    const stream = await askAssistant(user);

    // 턴에는 지금 편집기 텍스트가 실린다(서버가 문서를 따로 들지 않는다).
    expect(assistantTurns[0].context.source_text).toBe(before);
    expect(assistantTurns[0].context.source_format).toBe("yaml");
    // 실행 설정은 패널이 검증한 값만 싣는다. 실행 옵션 전체가 아니다(#355).
    expect(assistantTurns[0].context.environment).toEqual(RUN_ENVIRONMENT);

    const proposed = 'schema_version: "1.2"\ntitle: "저변동 모멘텀"\n';
    act(() =>
      stream.push({
        sequence: 1,
        turn_id: "t-1",
        event: { type: "proposal", proposal: proposal(proposed) },
      }),
    );

    await user.click(
      await screen.findByRole("button", { name: "문서에 적용" }),
    );
    expect(view.state.doc.toString()).toBe(proposed);
    expect(
      screen.getByText("제안을 문서에 적용했습니다."),
    ).toBeInTheDocument();

    act(() => expect(undo(view)).toBe(true));
    expect(view.state.doc.toString()).toBe(before);
  });

  it("세션 생성 왕복 뒤에 시작되는 턴도 최신 편집기 텍스트를 싣는다", async () => {
    let release: (() => void) | null = null;
    const created = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post(`${API}/api/v1/assistant/sessions`, async ({ request }) => {
        const body = (await request.json()) as { document_ref: unknown };
        // 세션이 만들어지는 동안 사용자가 계속 타자를 친다.
        await created;
        const row = {
          session_id: "s-1",
          title: "새 대화",
          provider_profile_id: "p-1",
          document_ref: body.document_ref,
          created_at: "2026-09-20T00:00:00Z",
        };
        assistantSessions = [row];
        return HttpResponse.json(row, { status: 201 });
      }),
    );

    const user = userEvent.setup();
    mount("/research/strategies/new");
    const view = await editor();
    // AI 열은 처음부터 열려 있다(RD-04-A).
    const input = await screen.findByRole("textbox", {
      name: "어시스턴트에게 보낼 메시지",
    });
    await user.type(input, "전략을 제안해 줘");
    await user.keyboard("{Enter}");

    const typedAfterSend =
      'schema_version: "1.2"\ntitle: "보낸 뒤에 친 제목"\n';
    replaceText(view, typedAfterSend);
    act(() => release?.());

    await waitFor(() => expect(assistantTurns).toHaveLength(1));
    expect(assistantTurns[0].context.source_text).toBe(typedAfterSend);
  });

  it("턴이 도는 중 사이드바를 닫으면 취소를 확인하고, 계속 두면 닫히지 않는다", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/new");
    await editor();
    await askAssistant(user);

    await user.click(screen.getByRole("button", { name: "사이드바 닫기" }));
    const confirm = await screen.findByRole("alertdialog", {
      name: "사이드바 닫기",
    });
    expect(cancelledTurns).toHaveLength(0);

    await user.click(
      within(confirm).getByRole("button", { name: "계속 두기" }),
    );
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();
    expect(cancelledTurns).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "사이드바 닫기" }));
    await user.click(
      await screen.findByRole("button", { name: "취소하고 닫기" }),
    );
    await waitFor(() => expect(cancelledTurns).toHaveLength(1));
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
  });

  it("기다리는 동안 문서를 고쳤으면 확인을 거쳐 덮어쓴다", async () => {
    const user = userEvent.setup();
    mount("/research/strategies/new");
    const view = await editor();
    const stream = await askAssistant(user);

    const typed = 'schema_version: "1.2"\ntitle: "직접 쓴 제목"\n';
    replaceText(view, typed);
    const proposed = 'schema_version: "1.2"\ntitle: "저변동 모멘텀"\n';
    act(() =>
      stream.push({
        sequence: 1,
        turn_id: "t-1",
        event: { type: "proposal", proposal: proposal(proposed) },
      }),
    );

    await user.click(
      await screen.findByRole("button", { name: "문서에 적용" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "문서가 바뀌었습니다",
    });
    expect(view.state.doc.toString()).toBe(typed);

    await user.click(
      within(dialog).getByRole("button", { name: "그래도 덮어쓰기" }),
    );
    expect(view.state.doc.toString()).toBe(proposed);
    act(() => expect(undo(view)).toBe(true));
    expect(view.state.doc.toString()).toBe(typed);
  });
});

describe("새 전략 화면의 은퇴 버전 업그레이드 (#257)", () => {
  /** backend 업그레이더가 돌려준다고 가정한 현재 버전 원문. 변환 규칙은 backend 소유라 모양만 흉내 낸다. */
  const UPGRADED = 'schema_version: "1.2"\ntitle: 퀄리티 모멘텀\n';
  /** 옛 문서가 들고 있던 실행 설정(업그레이드 응답 `environment`). 마지막 사용값과 기간이 다르다. */
  const OLD_ENVIRONMENT = {
    ...RUN_ENVIRONMENT,
    start: "2019-01-02",
    end: "2024-12-30",
  };
  const WARNING = {
    code: "strategy_document.upgrade_weighting_rule_changed",
    pointer: "/portfolio/weighting",
    message:
      "점수 비례 비중은 이제 기준점 위의 몫으로 나눕니다: weighting='factor_score'",
  } as const;

  /** 은퇴 버전 원문이면 backend 처럼 구조 오류 하나로 답하고, 아니면 기본 handler 와 같은 모양으로 답한다. */
  const compiledResponse = (source: string, retired: string) => {
    if (source === retired) {
      return {
        format: "yaml",
        source_hash: "r".repeat(64),
        schema_version: null,
        spec: null,
        canonical_json: null,
        spec_hash: null,
        diagnostics: [
          {
            code: "structure.unsupported_schema_version",
            kind: "structural",
            severity: "error",
            pointer: "/schema_version",
            message:
              "지원하지 않는 schema_version입니다. 업그레이드하면 지금 버전으로 바꿔 줍니다",
          },
        ],
      };
    }
    const compiledSpec = spec("draft", 0, "퀄리티 모멘텀");
    const { identity, ...canonicalSpec } = compiledSpec;
    return {
      format: "yaml",
      source_hash: "b".repeat(64),
      schema_version: "1.2",
      spec: compiledSpec,
      canonical_json: JSON.stringify({
        ...canonicalSpec,
        schema_version: identity.schema_version,
      }),
      spec_hash: "9".repeat(64),
      diagnostics: [],
    };
  };

  const serveRetiredCompile = (retired: string, upgradeRequests: string[]) =>
    server.use(
      http.post(
        `${API}/api/v1/strategy-documents/compile`,
        async ({ request }) => {
          const body = (await request.json()) as { source: string };
          compiledSources.push(body.source);
          return HttpResponse.json(compiledResponse(body.source, retired));
        },
      ),
      http.post(
        `${API}/api/v1/strategy-documents/upgrade`,
        async ({ request }) => {
          const body = (await request.json()) as { source: string };
          upgradeRequests.push(body.source);
          return HttpResponse.json({
            format: "yaml",
            source: UPGRADED,
            source_hash: "u".repeat(64),
            compiled: compiledResponse(UPGRADED, retired),
            environment: OLD_ENVIRONMENT,
            warnings: [WARNING],
          });
        },
      ),
    );

  it.each([
    ["1.1", "strategy_documents/quality_momentum.v1_1.yaml"],
    ["1.0", "strategy_documents/quality_momentum.v1_0.yaml"],
  ])(
    "schema %s 문서를 붙여 넣으면 배너로 업그레이드하고 실행 설정을 채워 저장한다",
    async (_version, fixture) => {
      const user = userEvent.setup();
      const retired = readBackendFixture(fixture);
      const upgradeRequests: string[] = [];
      serveRetiredCompile(retired, upgradeRequests);
      const history = mount("/research/strategies/new");
      const view = await editor();
      replaceText(view, retired);

      const banner = await screen.findByRole("region", {
        name: "이전 schema 문서",
      });
      expect(banner).toHaveTextContent(
        "이 문서는 지원이 끝난 schema 버전입니다",
      );
      const upgrade = within(banner).getByRole("button", {
        name: "현재 버전으로 업그레이드",
      });
      await waitFor(() => expect(upgrade).toBeEnabled());
      expect(saveButton()).toBeDisabled();
      expect(
        screen.getByRole("button", { name: "백테스트" }),
      ).toBeDisabled();

      await user.click(upgrade);
      await waitFor(() => expect(view.state.doc.toString()).toBe(UPGRADED));
      // 업그레이드 요청은 편집기 원문 그대로다. 옛 문서의 실행 설정·알아 둘 점이 배너에 남는다.
      expect(upgradeRequests).toEqual([retired]);
      expect(banner).toHaveTextContent("현재 버전으로 다시 썼습니다");
      expect(banner).toHaveTextContent(WARNING.message);
      await user.click(
        within(banner).getByRole("button", { name: "실행 설정에 채우기" }),
      );
      expect(banner).toHaveTextContent("옛 문서의 실행 설정을 채웠습니다.");

      // 업그레이드는 되돌리기 한 단계다(전체 범위 교체 경로).
      act(() => expect(undo(view)).toBe(true));
      expect(view.state.doc.toString()).toBe(retired);
      await user.click(
        await within(
          await screen.findByRole("region", { name: "이전 schema 문서" }),
        ).findByRole("button", { name: "현재 버전으로 업그레이드" }),
      );
      await waitFor(() => expect(view.state.doc.toString()).toBe(UPGRADED));

      // 채운 실행 설정이 실행 요청에 그대로 실린다(초안 실행).
      const run = screen.getByRole("button", { name: "백테스트" });
      await waitFor(() => expect(run).toBeEnabled());
      await user.click(run);
      await waitFor(() => expect(started).toHaveLength(1));
      expect(started[0]).toMatchObject({
        environment: OLD_ENVIRONMENT,
        strategy_source: { kind: "inline_draft" },
      });
      // 저장된 적 없는 새 전략의 초안은 계열이 없다(검증 랩 spec D2).
      expect(started[0]).not.toHaveProperty("lineage_strategy_id");
      await user.click(
        within(await screen.findByRole("alertdialog")).getByRole("button", {
          name: "편집 계속",
        }),
      );

      await waitFor(() => expect(saveButton()).toBeEnabled());
      await user.click(saveButton());
      await waitFor(() =>
        expect(history.location.pathname).toBe(
          "/research/strategies/s9/revisions/1",
        ),
      );
      expect(posted).toEqual([{ format: "yaml", source: UPGRADED }]);
    },
  );

  // #352 C-P2-4: 실행 설정 스키마를 읽기 전에 "실행 설정에 채우기"를 눌러도 옛 실행 설정과 마지막 사용값이
  // 지워지지 않는다. 스키마가 늦게 오면 채운 값으로 칸이 서고 그 값으로 실행한다.
  it("keeps the old run settings filled before the run environment schema arrives", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(`${API}/api/v1/run-environments/schema`, async () => {
        await gate;
        return HttpResponse.json({
          schema_hash: "run-env",
          options_schema: RUN_OPTIONS_SCHEMA, schema: RUN_ENVIRONMENT_SCHEMA,
        });
      }),
    );
    const user = userEvent.setup();
    const retired = readBackendFixture(
      "strategy_documents/quality_momentum.v1_1.yaml",
    );
    serveRetiredCompile(retired, []);
    mount("/research/strategies/new");
    const view = await editor();
    replaceText(view, retired);
    const banner = await screen.findByRole("region", {
      name: "이전 schema 문서",
    });
    const upgrade = within(banner).getByRole("button", {
      name: "현재 버전으로 업그레이드",
    });
    await waitFor(() => expect(upgrade).toBeEnabled());
    await user.click(upgrade);
    await user.click(
      await within(banner).findByRole("button", { name: "실행 설정에 채우기" }),
    );
    expect(banner).toHaveTextContent("옛 문서의 실행 설정을 채웠습니다.");
    expect(
      JSON.parse(
        localStorage.getItem(`${RUN_ENVIRONMENT_STORAGE_PREFIX}:last`) ?? "{}",
      ),
    ).toMatchObject({ start: OLD_ENVIRONMENT.start, end: OLD_ENVIRONMENT.end });

    release();
    const run = screen.getByRole("button", { name: "백테스트" });
    await waitFor(() => expect(run).toBeEnabled());
    await user.click(run);
    await waitFor(() => expect(started).toHaveLength(1));
    expect(started[0]?.environment).toEqual(OLD_ENVIRONMENT);
  });
});

describe("새 전략 단계별 안내(RD-04-D)", () => {
  const SOON = {
    ...FACTOR,
    availability: "catalog_only",
    category: "financial",
    default_graph: null,
    factor_id: "server.quality",
    label: "Server quality",
  };
  const useGuideCatalog = () =>
    server.use(
      http.get(`${API}/api/v1/strategy-documents/schema`, () =>
        HttpResponse.json({
          schema: RUNTIME_SCHEMA,
          schema_hash: "h".repeat(64),
          schema_version: "1.2",
        }),
      ),
      http.get(`${API}/api/v1/factors/catalog`, () =>
        HttpResponse.json({
          facets: {},
          factors: [FACTOR, SOON],
          page: 1,
          page_count: 1,
          page_size: 100,
          registry_version: "v1",
          total: 2,
        }),
      ),
    );
  /** 안내 동안 편집기는 숨겨져 있다. 트랜잭션이 쓴 원문은 숨은 편집기에서 읽는다. */
  const hiddenSource = async () => {
    let view: EditorView | null = null;
    await waitFor(() => {
      const content = globalThis.document.querySelector(".cm-content");
      view = content ? EditorView.findFromDOM(content as HTMLElement) : null;
      expect(view).not.toBeNull();
    });
    return () => (view as unknown as EditorView).state.doc.toString();
  };
  const steps = () => screen.getByRole("navigation", { name: "만드는 단계" });

  it("일곱 단계를 보이고 첫 단계에서 시작하며 편집기로 바로 갈 수 있다", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    const history = mountWithClient("/research/strategies/new?guide=true", undefined, false).history;
    await screen.findByRole("heading", { name: "새 전략 만들기" });
    // 단계 상태(완료·오류 N)는 이름 뒤에 붙는다. 처음에는 아무 단계도 채우지 않았다.
    await waitFor(() =>
      expect(within(steps()).getAllByRole("button").map((button) => button.textContent)).toEqual([
        "1이름·의도",
        "2유니버스",
        "3알파 팩터",
        "4결합",
        "5포트폴리오 구성",
        "6리스크 제약",
        "7확인",
      ]),
    );
    expect(within(steps()).getByRole("button", { name: "이름·의도" })).toHaveAttribute("aria-current", "step");

    await user.click(screen.getByRole("button", { name: "편집기에서 직접 쓰기" }));
    await waitFor(() => expect(history.location.search).not.toContain("guide"));
    // 같은 초안을 편집기 그래프 보기(파이프라인)로 연다.
    expect(history.location.search).toContain("view=graph");
    expect(
      await screen.findByRole("region", { name: t("graph.pipeline.label") }),
    ).toBeVisible();
  });

  it("팩터는 분류별로 보이고 바로 쓸 수 있는 것만 담겨 문서와 미리보기에 들어간다", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?guide=true&step=signal.factors", undefined, false);
    const source = await hiddenSource();
    const chips = await screen.findByRole("group", { name: "팩터 분류" });
    expect(within(chips).getByRole("button", { name: "가격 1" })).toHaveAttribute("aria-pressed", "true");

    await user.click(within(chips).getByRole("button", { name: "재무 1" }));
    const soon = screen.getByRole("button", { name: "Server quality · 담기" });
    expect(soon).toBeDisabled();
    expect(soon).toHaveAccessibleDescription("계산식이 아직 없어 담을 수 없어요.");

    await user.click(within(chips).getByRole("button", { name: "가격 1" }));
    await user.click(screen.getByRole("button", { name: "Server momentum · 담기" }));
    await waitFor(() => expect(source()).toContain("factor_id: server.momentum"));
    // 공급자가 연결돼 있어 오른쪽 칸은 AI 도우미 탭으로 열려 있다. 미리보기 탭에 바뀐 줄 수가 붙는다.
    // 바뀐 줄 수는 탭 이름이 아니라 설명으로 읽힌다 — 편집마다 탭 이름이 바뀌지 않는다(가이드 재리뷰 8).
    const previewTab = screen.getByRole("tab", { name: "전략 문서 미리보기" });
    expect(previewTab).toHaveAccessibleDescription(/^\d+줄 바뀜$/u);
    await user.click(previewTab);
    const preview = screen.getByRole("complementary", { name: "전략 문서 미리보기" });
    await waitFor(() =>
      expect(within(preview).getAllByText("방금 바뀐 줄:").length).toBeGreaterThan(0),
    );
    expect(preview).toHaveTextContent("factor_id: server.momentum");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Server momentum · 담기" })).toHaveAccessibleDescription("이미 담았어요."),
    );
  });

  it("고르지 않고 넘어가면 문서에 아무것도 쓰지 않고, 핵심 칸 밖은 더 설정하기에 접는다", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?guide=true", undefined, false);
    const source = await hiddenSource();
    const before = source();
    await screen.findByRole("button", { name: "유니버스" });
    for (let index = 0; index < 6; index += 1)
      await user.click(screen.getByRole("button", { name: "다음" }));
    expect(within(steps()).getByRole("button", { name: "확인" })).toHaveAttribute("aria-current", "step");
    expect(source()).toBe(before);

    // 지나온 단계는 완료 표시가 붙지만 단계 이름은 그대로다(상태는 설명으로 읽힌다).
    await user.click(within(steps()).getByRole("button", { name: "결합" }));
    const combine = screen.getByRole("region", { name: "결합" });
    expect(within(combine).getByText(/^더 설정하기 \(\d+\)$/u)).toBeInTheDocument();
  });

  it("공급자가 연결돼 있으면 AI 도우미 탭이 먼저 열리고, 추천 질문은 입력칸만 채우며 탭을 바꿔도 남는다", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    const { history } = mountWithClient("/research/strategies/new?guide=true", undefined, false);
    const assistantTab = await screen.findByRole("tab", { name: "AI 도우미" });
    await waitFor(() => expect(assistantTab).toHaveAttribute("aria-selected", "true"));

    const panel = screen.getByRole("tabpanel", { name: "AI 도우미" });
    const suggestions = await within(panel).findByRole("group", { name: "이 단계에서 자주 묻는 것" });
    await user.click(
      within(suggestions).getByRole("button", { name: "어떤 전략을 만들지 같이 정해 주세요" }),
    );
    const input = within(panel).getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    expect(input).toHaveValue("어떤 전략을 만들지 같이 정해 주세요");
    expect(input).toHaveFocus();
    // 채우기만 하고 보내지 않는다.
    expect(assistantTurns).toHaveLength(0);

    // 미리보기로 바꾸면 URL 이 탭을 들고, 돌아와도 친 문장이 그대로다(AI 패널은 마운트를 유지한다).
    await user.click(screen.getByRole("tab", { name: "전략 문서 미리보기" }));
    await waitFor(() => expect(history.location.search).toContain("assist=preview"));
    expect(screen.getByRole("complementary", { name: "전략 문서 미리보기" })).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "AI 도우미" }));
    await waitFor(() => expect(history.location.search).toContain("assist=assistant"));
    expect(input).toHaveValue("어떤 전략을 만들지 같이 정해 주세요");

    // 추천 질문은 지금 단계를 따른다.
    await user.click(await within(steps()).findByRole("button", { name: "유니버스" }));
    expect(
      await within(panel).findByRole("button", { name: "거르기 규칙에 쓸 수 있는 필드를 설명해 주세요" }),
    ).toBeInTheDocument();
  });

  it("공급자 목록이 오기 전에는 탭을 정하지 않고, 정한 뒤 다시 읽혀도 탭이 저절로 바뀌지 않는다(가이드 리뷰 2)", async () => {
    useGuideCatalog();
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let active = true;
    server.use(
      http.get(`${API}/api/v1/assistant/providers`, async () => {
        await held;
        return HttpResponse.json({
          kinds: [],
          profiles: [
            {
              profile_id: "p-1",
              kind: "anthropic",
              label: "작업용 Claude",
              model: "claude-sonnet-5",
              base_url: null,
              secret_tail: "9876",
              active,
              created_at: "2026-09-20T00:00:00Z",
            },
          ],
        });
      }),
    );
    const { queryClient } = mountWithClient("/research/strategies/new?guide=true", undefined, false);
    await screen.findByRole("heading", { name: "새 전략 만들기" });
    expect(screen.queryByRole("tab", { name: "AI 도우미" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "전략 문서 미리보기" })).toBeNull();

    release();
    const assistantTab = await screen.findByRole("tab", { name: "AI 도우미" });
    expect(assistantTab).toHaveAttribute("aria-selected", "true");

    active = false;
    await queryClient.invalidateQueries();
    await waitFor(() =>
      expect(screen.getByRole("tabpanel", { name: "AI 도우미" })).toHaveTextContent(
        "연결된 AI 공급자가 없습니다",
      ),
    );
    expect(screen.getByRole("tab", { name: "AI 도우미" })).toHaveAttribute("aria-selected", "true");
  });

  it("공급자 조회가 실패하면 기본 재시도를 기다리지 않고 미리보기로 정한다(가이드 재리뷰 1)", async () => {
    useGuideCatalog();
    server.use(
      http.get(`${API}/api/v1/assistant/providers`, () =>
        HttpResponse.json({ detail: "down" }, { status: 500 }),
      ),
    );
    // 앱 기본값처럼 재시도(3회 백오프)를 켠다 — 그동안 "불러오는 중"에 머물지 않아야 한다.
    mountWithClient("/research/strategies/new?guide=true", (client) =>
      client.setDefaultOptions({ queries: { retry: 3 } }),
    false);
    await waitFor(
      () =>
        expect(screen.getByRole("tab", { name: "전략 문서 미리보기" })).toHaveAttribute(
          "aria-selected",
          "true",
        ),
      { timeout: 900 },
    );
  });

  it("숨은 AI 탭은 처음 열기 전까지 대화 목록을 읽지 않는다(가이드 재리뷰 7)", async () => {
    useGuideCatalog();
    let sessionReads = 0;
    server.use(
      http.get(`${API}/api/v1/assistant/sessions`, () => {
        sessionReads += 1;
        return HttpResponse.json(assistantSessions);
      }),
    );
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?guide=true&assist=preview", undefined, false);
    await within(await screen.findByRole("navigation", { name: "만드는 단계" })).findByRole(
      "button",
      { name: "유니버스" },
    );
    expect(sessionReads).toBe(0);
    expect(screen.queryByRole("textbox", { name: "어시스턴트에게 보낼 메시지" })).toBeNull();

    await user.click(screen.getByRole("tab", { name: "AI 도우미" }));
    await screen.findByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    await waitFor(() => expect(sessionReads).toBeGreaterThan(0));
  });

  it("주소의 단계가 바뀌면(뒤로 가기 포함) 떠난 단계를 지나간 것으로 본다(가이드 재리뷰 6)", async () => {
    useGuideCatalog();
    const { history } = mountWithClient(
      "/research/strategies/new?guide=true&step=eligibility&assist=preview",
      undefined,
      false,
    );
    await screen.findByRole("navigation", { name: "만드는 단계" });
    const universe = await within(steps()).findByRole("button", { name: "유니버스" });
    expect(universe).not.toHaveTextContent("(완료)");
    act(() => history.push("/research/strategies/new?guide=true&step=portfolio&assist=preview"));
    await waitFor(() => expect(universe).toHaveTextContent("(완료)"));
    act(() => history.back());
    const portfolio = within(steps()).getByRole("button", { name: "포트폴리오 구성" });
    await waitFor(() => expect(portfolio).toHaveTextContent("(완료)"));
  });

  it("안내를 닫고 편집기로 가도 AI 입력칸 문장과 진행 중 답변이 이어진다(가이드 재리뷰 4)", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?guide=true&assist=assistant", undefined, false);
    const panel = await screen.findByRole("tabpanel", { name: "AI 도우미" });
    const input = await within(panel).findByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    await user.type(input, "모멘텀 전략을 만들어 줘");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(assistantStream).not.toBeNull());
    await user.type(input, "그리고 거래대금도");

    await user.click(screen.getByRole("button", { name: "편집기에서 직접 쓰기" }));
    const rail = await screen.findByRole("complementary", { name: "AI 어시스턴트" });
    expect(
      within(rail).getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" }),
    ).toHaveValue("그리고 거래대금도");
    act(() =>
      assistantStream!.push({
        sequence: 0,
        turn_id: "t-1",
        event: { type: "text_delta", text: "편집기에서도 이어지는 답" },
      }),
    );
    expect(await within(rail).findByText("편집기에서도 이어지는 답")).toBeInTheDocument();
  });

  /** 첫 조회는 실패하고 재시도는 활성 공급자를 돌려준다. */
  const failOnceThenConnected = () => {
    let calls = 0;
    server.use(
      http.get(`${API}/api/v1/assistant/providers`, () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "down" }, { status: 500 })
          : HttpResponse.json({
              kinds: [],
              profiles: [
                {
                  profile_id: "p-1",
                  kind: "anthropic",
                  label: "작업용 Claude",
                  model: "claude-sonnet-5",
                  base_url: null,
                  secret_tail: "9876",
                  active: true,
                  created_at: "2026-09-20T00:00:00Z",
                },
              ],
            });
      }),
    );
  };
  const retryOnce = (client: QueryClient) =>
    client.setDefaultOptions({ queries: { retry: 3, retryDelay: 300 } });

  it("첫 실패 동안은 잠정으로 미리보기를 보이고, 재시도가 성공하면 고르지 않은 탭은 AI 도우미로 정한다(5차 리뷰 4)", async () => {
    useGuideCatalog();
    failOnceThenConnected();
    mountWithClient("/research/strategies/new?guide=true", retryOnce, false);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "전략 문서 미리보기" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "AI 도우미" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
  });

  it("잠정 미리보기 동안 사용자가 탭을 고르면 재시도가 성공해도 그 탭이 남는다(5차 리뷰 4)", async () => {
    useGuideCatalog();
    failOnceThenConnected();
    const user = userEvent.setup();
    const { history } = mountWithClient("/research/strategies/new?guide=true", retryOnce, false);
    const previewTab = await screen.findByRole("tab", { name: "전략 문서 미리보기" });
    await user.click(previewTab);
    await waitFor(() => expect(history.location.search).toContain("assist=preview"));
    // 재시도가 성공할 시간을 준다.
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(screen.getByRole("tab", { name: "전략 문서 미리보기" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("편집기로 떠날 때 지금 단계를 지나간 것으로 남기고, 고른 오른쪽 탭도 돌아오면 그대로다(5차 리뷰 2·5)", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    const { history } = mountWithClient(
      "/research/strategies/new?guide=true&step=eligibility&assist=preview",
      undefined,
      false,
    );
    await screen.findByRole("navigation", { name: "만드는 단계" });
    const universe = await within(steps()).findByRole("button", { name: "유니버스" });
    expect(universe).not.toHaveAccessibleDescription("(완료)");

    await user.click(screen.getByRole("button", { name: "편집기에서 직접 쓰기" }));
    await waitFor(() => expect(history.location.search).not.toContain("guide"));
    expect(history.location.search).toContain("assist=preview");
    act(() => history.back());
    expect(
      await within(
        await screen.findByRole("navigation", { name: "만드는 단계" }),
      ).findByRole("button", { name: "유니버스" }),
    ).toHaveAccessibleDescription("(완료)");
    expect(screen.getByRole("tab", { name: "전략 문서 미리보기" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("방문 기록은 commit 뒤 탭 보관함에 쓰이고, 초안이 저장되면 지워진다(5차 리뷰 7·SoT 5차)", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    const { history } = mountWithClient(
      "/research/strategies/new?guide=true&step=eligibility&assist=preview",
      undefined,
      false,
    );
    await within(
      await screen.findByRole("navigation", { name: "만드는 단계" }),
    ).findByRole("button", { name: "유니버스" });
    await user.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(history.location.search).toContain("draft="));
    const draft = new URLSearchParams(history.location.search).get("draft")!;
    const key = `strategy-guide.visited.${draft}`;
    await waitFor(() => expect(sessionStorage.getItem(key)).toContain("eligibility"));

    // 이름을 적고 확인 단계에서 저장한다.
    await user.click(within(steps()).getByRole("button", { name: "이름·의도" }));
    const title = await screen.findByRole("textbox", { name: /^전략 이름/u });
    await user.type(title, "저장할 전략");
    await user.tab();
    await user.click(within(steps()).getByRole("button", { name: "확인" }));
    const save = await screen.findByRole("button", { name: "저장하고 백테스트 준비" });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);
    await waitFor(() =>
      expect(history.location.pathname).toBe("/research/strategies/s9/revisions/1"),
    );
    expect(sessionStorage.getItem(key)).toBeNull();
  });

  it("지나간 단계는 편집기에 다녀오거나 새로고침해도 완료로 남는다(최종 리뷰 2)", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    const { history } = mountWithClient(
      "/research/strategies/new?guide=true&step=eligibility&assist=preview",
      undefined,
      false,
    );
    await screen.findByRole("navigation", { name: "만드는 단계" });
    await within(steps()).findByRole("button", { name: "유니버스" });
    await user.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() =>
      expect(within(steps()).getByRole("button", { name: "유니버스" })).toHaveAccessibleDescription(
        "(완료)",
      ),
    );
    await waitFor(() => expect(history.location.search).toContain("draft="));

    // 편집기에 다녀온다 — 안내가 다시 마운트된다.
    await user.click(screen.getByRole("button", { name: "편집기에서 직접 쓰기" }));
    await waitFor(() => expect(history.location.search).not.toContain("guide"));
    act(() => history.back());
    expect(
      await within(
        await screen.findByRole("navigation", { name: "만드는 단계" }),
      ).findByRole("button", { name: "유니버스" }),
    ).toHaveAccessibleDescription("(완료)");

    // 같은 초안 주소로 새로 연다(새로고침).
    const reload = `${history.location.pathname}${history.location.search}`;
    cleanup();
    useGuideCatalog();
    mountWithClient(reload, undefined, false);
    expect(
      await within(
        await screen.findByRole("navigation", { name: "만드는 단계" }),
      ).findByRole("button", { name: "유니버스" }),
    ).toHaveAccessibleDescription("(완료)");
  });

  it("추천 질문의 맥락은 안내가 해석한 단계라, 모르는 주소 단계에서 같은 단계를 다시 눌러도 지워지지 않는다(최종 리뷰 4)", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    mountWithClient(
      "/research/strategies/new?guide=true&step=unknown-step&assist=assistant",
      undefined,
      false,
    );
    const panel = await screen.findByRole("tabpanel", { name: "AI 도우미" });
    const input = await within(panel).findByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    await user.click(
      await within(panel).findByRole("button", { name: "어떤 전략을 만들지 같이 정해 주세요" }),
    );
    await user.click(await within(steps()).findByRole("button", { name: "이름·의도" }));
    expect(input).toHaveValue("어떤 전략을 만들지 같이 정해 주세요");
  });

  it("URL 에 적힌 탭은 공급자 연결 여부보다 먼저다(가이드 리뷰 2)", async () => {
    useGuideCatalog();
    mountWithClient("/research/strategies/new?guide=true&assist=preview", undefined, false);
    const previewTab = await screen.findByRole("tab", { name: "전략 문서 미리보기" });
    expect(previewTab).toHaveAttribute("aria-selected", "true");
  });

  it("단계를 옮기면 고치지 않은 앞 단계 추천 질문은 지우고, 고친 문장은 남긴다(가이드 리뷰 6)", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?guide=true&assist=assistant", undefined, false);
    const panel = await screen.findByRole("tabpanel", { name: "AI 도우미" });
    const input = await within(panel).findByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    await user.click(
      within(panel).getByRole("button", { name: "어떤 전략을 만들지 같이 정해 주세요" }),
    );
    await user.click(await within(steps()).findByRole("button", { name: "유니버스" }));
    await waitFor(() => expect(input).toHaveValue(""));

    await user.click(
      within(panel).getByRole("button", { name: "거르기 규칙에 쓸 수 있는 필드를 설명해 주세요" }),
    );
    await user.type(input, " 특히 거래대금");
    await user.click(within(steps()).getByRole("button", { name: "알파 팩터" }));
    expect(input).toHaveValue("거르기 규칙에 쓸 수 있는 필드를 설명해 주세요 특히 거래대금");
  });

  it("필수 칸이 없는 단계는 한 번 지나가면 완료로 보이고, 거르기 규칙 안내는 스키마 설명을 쓴다(가이드 리뷰 5·SoT F2)", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?guide=true&step=eligibility&assist=preview", undefined, false);
    expect(await screen.findByText("모두 만족하는 종목만 남습니다.")).toBeInTheDocument();
    const universe = await within(steps()).findByRole("button", { name: "유니버스" });
    expect(universe).not.toHaveTextContent("(완료)");
    await user.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(universe).toHaveTextContent("(완료)"));
  });

  it("공급자가 없으면 문서 미리보기 탭으로 열고, AI 탭은 설정으로 가는 길을 보인다", async () => {
    useGuideCatalog();
    server.use(
      http.get(`${API}/api/v1/assistant/providers`, () =>
        HttpResponse.json({ kinds: [], profiles: [] }),
      ),
    );
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?guide=true", undefined, false);
    const previewTab = await screen.findByRole("tab", { name: "전략 문서 미리보기" });
    await waitFor(() => expect(previewTab).toHaveAttribute("aria-selected", "true"));
    await user.click(screen.getByRole("tab", { name: "AI 도우미" }));
    const panel = screen.getByRole("tabpanel", { name: "AI 도우미" });
    expect(within(panel).getByRole("link", { name: "설정 열기" })).toBeVisible();
    expect(within(panel).queryByRole("group", { name: "이 단계에서 자주 묻는 것" })).toBeNull();
  });

  it("안내에서 AI 제안을 적용하면 같은 문서가 바뀌어 단계 값이 채워지고, 적용 후 백테스트는 없다", async () => {
    useGuideCatalog();
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?guide=true", undefined, false);
    const source = await hiddenSource();
    const panel = await screen.findByRole("tabpanel", { name: "AI 도우미" });
    const input = await within(panel).findByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    await user.type(input, "싼 종목 중 오르는 종목을 사고 싶어요");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(assistantTurns).toHaveLength(1));
    await waitFor(() => expect(assistantStream).not.toBeNull());
    expect(assistantTurns[0]!.context.source_text).toBe(source());

    const proposed = 'schema_version: "1.2"\ntitle: "저변동 모멘텀"\n';
    act(() =>
      assistantStream!.push({
        sequence: 1,
        turn_id: "t-1",
        event: {
          type: "proposal",
          proposal: {
            title: "저변동 모멘텀",
            summary: "변동성이 낮은 모멘텀 상위 종목을 매수합니다.",
            rationale: "최근 국내 시장은 저변동 구간입니다.",
            sources: [],
            source_format: "yaml",
            source_text: proposed,
            compile: { ok: true, spec_hash: "hash-1", diagnostics: [] },
          },
        },
      }),
    );
    await within(panel).findByRole("button", { name: "문서에 적용" });
    expect(within(panel).queryByRole("button", { name: "적용 후 백테스트" })).toBeNull();
    await user.click(within(panel).getByRole("button", { name: "문서에 적용" }));

    expect(source()).toBe(proposed);
    expect(screen.getByText("제안을 문서에 적용했습니다.")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /^전략 이름/u })).toHaveValue("저변동 모멘텀"),
    );
    await waitFor(() =>
      expect(within(steps()).getByRole("button", { name: "이름·의도" })).toHaveAccessibleDescription(
        "(완료)",
      ),
    );
  });
});

describe("편집기 AI 열의 대화 조회(SoT 3차 경미 1·2)", () => {
  /** 대화 목록 조회 수를 센다. */
  const countSessionReads = () => {
    const counter = { reads: 0 };
    server.use(
      http.get(`${API}/api/v1/assistant/sessions`, () => {
        counter.reads += 1;
        return HttpResponse.json(assistantSessions);
      }),
    );
    return counter;
  };
  const rail = () => screen.queryByRole("complementary", { name: "AI 어시스턴트" });

  /** AI 열을 접으면 다시 읽어도 대화 목록을 조회하지 않고, 펼치면 치던 문장이 남아 있다. */
  const expectCollapsePausesQueries = async (
    queryClient: QueryClient,
    counter: { reads: number },
  ) => {
    const user = userEvent.setup();
    const input = await within(
      await screen.findByRole("complementary", { name: "AI 어시스턴트" }),
    ).findByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    await waitFor(() => expect(counter.reads).toBeGreaterThan(0));
    await user.type(input, "접어도 남을 문장");

    fireEvent.keyDown(window, { key: "a", altKey: true });
    await waitFor(() => expect(rail()).toBeNull());
    const before = counter.reads;
    await act(() => queryClient.invalidateQueries());
    expect(counter.reads).toBe(before);

    fireEvent.keyDown(window, { key: "a", altKey: true });
    const reopened = await within(
      await screen.findByRole("complementary", { name: "AI 어시스턴트" }),
    ).findByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    expect(reopened).toHaveValue("접어도 남을 문장");
  };

  it("새 전략 편집기: AI 열을 접으면 조회를 멈추고 입력 초안은 남긴다", async () => {
    const counter = countSessionReads();
    const { queryClient } = mountWithClient("/research/strategies/new");
    await editor();
    await expectCollapsePausesQueries(queryClient, counter);
  });

  it("리비전 편집기도 같은 방식으로 화면이 대화 상태를 들고 접으면 조회를 멈춘다", async () => {
    server.use(...graphHandlers(), revisionDocumentHandler(GRAPH_SOURCE));
    const counter = countSessionReads();
    const { queryClient } = mountWithClient("/research/strategies/s1/revisions/2");
    await editor();
    await expectCollapsePausesQueries(queryClient, counter);
  });
});

describe("안내에 없는 칸의 오류(SoT 3차 경미 3)", () => {
  it("이름 단계가 그 진단 문장과 확인 단계·편집기로 가는 길을 보인다", async () => {
    server.use(
      http.get(`${API}/api/v1/strategy-documents/schema`, () =>
        HttpResponse.json({
          schema: RUNTIME_SCHEMA,
          schema_hash: "h".repeat(64),
          schema_version: "1.2",
        }),
      ),
      graphCompileWith([
        {
          code: "strategy.parameter.bounds",
          kind: "semantic",
          severity: "error",
          pointer: "/parameters",
          message: "파라미터 범위가 맞지 않습니다.",
        },
      ]),
    );
    const user = userEvent.setup();
    const { history } = mountWithClient(
      "/research/strategies/new?guide=true&assist=preview",
      undefined,
      false,
    );
    const outside = await screen.findByRole("region", { name: "안내에 없는 칸의 문제" });
    expect(outside).toHaveTextContent("파라미터 범위가 맞지 않습니다.");
    expect(
      within(screen.getByRole("navigation", { name: "만드는 단계" })).getByRole("button", {
        name: /^이름·의도/u,
      }),
    ).toHaveTextContent("오류 1");

    await user.click(within(outside).getByRole("button", { name: "편집기에서 고치기" }));
    await waitFor(() => expect(history.location.search).not.toContain("guide"));
    expect(history.location.search).toContain("view=yaml");
    expect(decodeURIComponent(history.location.search)).toContain("path=/parameters");
  });
});

describe("내 팩터 라이브러리(US-CS-08)", () => {
  const LIBRARY_GRAPH = {
    nodes: [
      { node_id: "close", kind: "field", field_id: "price.close" },
      { node_id: "win", kind: "parameter", parameter_id: "band_window" },
      {
        node_id: "mean",
        kind: "time_series",
        operator: "mean",
        input_node_id: "close",
        window: 20,
      },
    ],
    output_node_id: "mean",
  };
  const PARAMETER = {
    kind: "integer",
    parameter_id: "band_window",
    default: 20,
    minimum: 5,
    maximum: 60,
  };
  const version = (number: number, graph: unknown) => ({
    version: number,
    identity: String(number).repeat(64),
    factor: {
      factor_id: "bollinger_b",
      label: "볼린저 %B",
      direction: "high",
      graph,
    },
    parameters: [PARAMETER],
    created_at: "2026-10-19T00:00:00Z",
    note: null,
  });
  const ENTRY = {
    library_id: "lib-1",
    key: "bollinger_b",
    name: "볼린저 %B",
    description: "",
    hidden: false,
    created_at: "2026-10-19T00:00:00Z",
    latest_version: 2,
    versions: [
      version(1, LIBRARY_GRAPH),
      version(2, { ...LIBRARY_GRAPH, output_node_id: "close" }),
    ],
    availability: { available: true, missing_fields: [], reasons: [] },
  };
  const libraryHandlers = (matches: unknown[] = []) => [
    http.get(`${API}/api/v1/strategy-documents/schema`, () =>
      HttpResponse.json({
        schema: RUNTIME_SCHEMA,
        schema_hash: "h".repeat(64),
        schema_version: "1.2",
      }),
    ),
    http.get(`${API}/api/v1/factor-library`, () =>
      HttpResponse.json({ items: [ENTRY] }),
    ),
    http.post(`${API}/api/v1/factor-library/matches`, () =>
      HttpResponse.json({ compiled: true, matches }),
    ),
  ];
  const hiddenView = async () => {
    let view: EditorView | null = null;
    await waitFor(() => {
      const content = globalThis.document.querySelector(".cm-content");
      view = content ? EditorView.findFromDOM(content as HTMLElement) : null;
      expect(view).not.toBeNull();
    });
    return view as unknown as EditorView;
  };

  it("안내의 내 팩터 탭에서 꺼내 쓰면 팩터와 파라미터가 복사되고 되돌리기 한 번에 취소된다", async () => {
    server.use(...libraryHandlers());
    const user = userEvent.setup();
    mountWithClient(
      "/research/strategies/new?guide=true&step=signal.factors",
      undefined,
      false,
    );
    const view = await hiddenView();
    const before = view.state.doc.toString();
    await user.click(await screen.findByRole("button", { name: "내 팩터" }));
    await user.click(await screen.findByRole("button", { name: "볼린저 %B · 담기" }));

    await waitFor(() => expect(view.state.doc.toString()).toContain("factor_id: bollinger_b"));
    expect(view.state.doc.toString()).toContain("parameter_id: band_window");
    // 최신(v2) 그래프를 복사한다.
    expect(view.state.doc.toString()).toMatch(/output_node_id: close/u);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "볼린저 %B · 담기" }),
      ).toHaveAccessibleDescription("이미 담았어요."),
    );

    await user.click(screen.getByRole("button", { name: "되돌리기" }));
    await waitFor(() => expect(view.state.doc.toString()).toBe(before));
  });

  it("같은 키가 이미 저장돼 있으면 그 팩터의 새 버전으로 저장한다", async () => {
    const saved: unknown[] = [];
    const versions: unknown[] = [];
    server.use(
      ...libraryHandlers(),
      http.post(`${API}/api/v1/factor-library`, async ({ request }) => {
        saved.push(await request.json());
        return HttpResponse.json(
          {
            detail: {
              code: "library.factor.key_exists",
              message: "exists",
              existing_library_id: "lib-1",
            },
          },
          { status: 409 },
        );
      }),
      http.post(`${API}/api/v1/factor-library/lib-1/versions`, async ({ request }) => {
        versions.push(await request.json());
        return HttpResponse.json({ ...ENTRY, latest_version: 3 }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?view=graph", undefined, false);
    const view = await hiddenView();
    replaceText(view, GRAPH_SOURCE);

    await user.click(await screen.findByRole("button", { name: "모멘텀 · 내 팩터로 저장" }));
    await user.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "같은 팩터 id로 저장한 내 팩터가 있어요.",
    );
    expect(saved).toMatchObject([{ name: "모멘텀", factor: { factor_id: "momentum" } }]);

    await user.click(
      await screen.findByRole("button", { name: "\"볼린저 %B\"의 새 버전(v3)으로 저장" }),
    );
    await waitFor(() =>
      expect(versions).toMatchObject([
        { expected_latest: 2, factor: { factor_id: "momentum" } },
      ]),
    );
    expect(await screen.findByText("내 팩터 \"볼린저 %B\"에 저장했어요 (v3).")).toBeVisible();
  });

  it("예전 버전을 복사한 팩터는 새 버전을 알리고 바꾸기로 그 팩터의 그래프만 바꾼다", async () => {
    server.use(
      ...libraryHandlers([
        {
          factor_index: 0,
          factor_id: "momentum",
          library_id: "lib-1",
          status: "outdated",
          matched_version: 1,
          latest_version: 2,
        },
      ]),
    );
    const user = userEvent.setup();
    mountWithClient("/research/strategies/new?view=graph", undefined, false);
    const view = await hiddenView();
    replaceText(view, GRAPH_SOURCE);

    const notice = await screen.findByRole("region", { name: "내 팩터 새 버전" });
    expect(notice).toHaveTextContent(
      "\"모멘텀\"은(는) 내 팩터 \"볼린저 %B\" v1를 복사한 것이고 v2이 있어요.",
    );
    await user.click(within(notice).getByRole("button", { name: "v2로 바꾸기" }));

    await waitFor(() => expect(view.state.doc.toString()).toContain("parameter_id: band_window"));
    const source = view.state.doc.toString();
    expect(source).toContain("factor_id: momentum");
    expect(source).toContain("label: 모멘텀");
    expect(source).not.toContain("mom_252");
  });
});
