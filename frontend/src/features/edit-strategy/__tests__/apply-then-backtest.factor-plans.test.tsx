/**
 * "적용 후 백테스트"가 팩터 계획 조회를 기다리는지 실제 훅 조합으로 본다(C-02 리뷰 P1-1).
 *
 * 팩터 그래프를 바꾸는 제안은 compile 직후 새 explain 쿼리가 아직 돌고 있어 실행 결정이
 * `blocked/factor-plan`이다. 이것은 "실행할 수 없음"이 아니라 "아직 검증 중"이라 체인이 기다려야 한다.
 * 계획 조회가 실패하면 그때는 끝난 판정이라 요청을 버린다.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import type { PropsWithChildren } from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type {
  DatasetFieldProfile,
  FactorExplanation,
  FactorGraphRequest,
  FactorCatalog,
  ResearchCatalog,
  StrategySpec,
} from "../../../shared/api";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import {
  decideBacktestSource,
  gateBacktestSourceWithFactorPlans,
  isBacktestSettling,
} from "../model/backtest-source";
import type { ContractInspectorSource } from "../model/contract-inspector";
import {
  documentReducer,
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import { useApplyAssistantProposal } from "../model/use-apply-assistant-proposal";
import { useApplyProposalThenBacktest } from "../model/use-apply-then-backtest";
import { useExecutionPlans } from "../model/use-execution-plans";

const API = "http://localhost:8000";
const FIXTURE_SPEC = JSON.parse(
  readBackendFixture("strategy_documents/quality_momentum.legacy.json"),
) as StrategySpec;
const MOMENTUM_FACTOR = FIXTURE_SPEC.factors![0]!;
const SPEC: StrategySpec = {
  ...FIXTURE_SPEC,
  title: "멀티 팩터",
  factors: [
    MOMENTUM_FACTOR,
    {
      ...MOMENTUM_FACTOR,
      factor_id: "quality",
      label: "퀄리티",
      weight: 0.4,
      graph: {
        nodes: [
          {
            node_id: "book",
            field_id: "financial.book_equity",
            kind: "field",
          },
        ],
        output_node_id: "book",
      },
    },
  ],
};

const catalogField = (
  fieldId: string,
  datasetId: string,
  label: string,
  frequency: string,
): DatasetFieldProfile => ({
  field_id: fieldId,
  dataset_id: datasetId,
  label,
  unit: "KRW",
  value_type: "price",
  frequency,
  available_date_basis: "session close",
  recommended_lag_sessions: 0,
  description: label,
  disclosure_basis: "test",
  evidence: "test",
  coverage: {
    starts_on: "2000-01-01",
    ends_on: "2026-08-31",
    venues: ["KRX"],
    estimated_coverage_pct: 100,
    supported_cell_kinds: ["observed"],
    point_in_time: true,
    requires_confirmation: false,
  },
});

const EQUITY_CATALOG = {
  snapshot: {
    snapshot_id: "dataset-v1",
    schema_version: "1",
    built_at: "2026-09-04T00:00:00Z",
    source: "test",
    point_in_time: true,
    dataset_revisions: [
      { dataset_id: "prices", as_of: "2026-08-31", revision: "r1" },
      { dataset_id: "financials", as_of: "2026-08-31", revision: "r2" },
    ],
  },
  fields: [
    catalogField("price.close", "prices", "Close", "daily"),
    catalogField(
      "financial.book_equity",
      "financials",
      "Book equity",
      "quarterly",
    ),
  ],
  total: 2,
  page: 1,
  page_size: 100,
  page_count: 1,
  facets: {
    dataset_ids: ["prices", "financials"],
    units: ["KRW"],
    frequencies: ["daily", "quarterly"],
  },
} satisfies ResearchCatalog;

const FACTOR_CATALOG = {
  registry_version: "registry-v1",
  factors: [],
  total: 0,
  page: 1,
  page_size: 100,
  page_count: 0,
  facets: { categories: [], availability: [], output_units: [] },
} satisfies FactorCatalog;

const METADATA: ContractInspectorSource = {
  schema: {
    schema: { type: "object" },
    schema_hash: "schema-hash",
    schema_version: "1.2",
  },
  contract: {
    contract: {
      contract_hash: "contract-hash",
      dataset_snapshot_id: "dataset-v1",
      factor_registry_version: "registry-v1",
      fields: [],
      schema_hash: "schema-hash",
      schema_version: "1.2",
    },
    equity_catalog_url: "/api/v1/equity/catalog",
    factor_catalog_url: "/api/v1/factors/catalog",
  },
  equityCatalog: EQUITY_CATALOG,
  factorCatalog: FACTOR_CATALOG,
  state: {
    schema: "ready",
    contract: "ready",
    equityCatalog: "ready",
    factorCatalog: "ready",
  },
};

const explanation = (graph: FactorGraphRequest["graph"]): FactorExplanation => {
  const contracts = graph.nodes.map((node, index) => ({
    node_id: node.node_id,
    value_type: "numeric_series" as const,
    unit: "KRW",
    minimum_history_sessions: index === 0 ? 1 : 252,
  }));
  return {
    registry_version: "registry-v1",
    data_snapshot_id: "dataset-v1",
    validation: {
      valid: true,
      issues: [],
      node_contracts: contracts,
      minimum_history_sessions: contracts.at(-1)?.minimum_history_sessions ?? 0,
      required_field_ids: graph.nodes.flatMap((node) =>
        node.kind === "field" ? [node.field_id] : [],
      ),
    },
    plan: {
      graph_hash: "a".repeat(64),
      plan_hash: "b".repeat(64),
      registry_version: "registry-v1",
      output_node_id: graph.output_node_id,
      steps: graph.nodes.map((node, index) => ({
        sequence: index + 1,
        node_id: node.node_id,
        operation:
          node.kind === "time_series"
            ? `time_series.${node.operator}`
            : node.kind,
        input_node_ids: node.kind === "time_series" ? [node.input_node_id] : [],
        output_type: "numeric_series",
        output_unit: "KRW",
        minimum_history_sessions: index === 0 ? 1 : 252,
      })),
      required_field_ids: graph.nodes.flatMap((node) =>
        node.kind === "field" ? [node.field_id] : [],
      ),
      referenced_factor_ids: [],
      referenced_subgraph_ids: [],
      minimum_history_sessions: contracts.at(-1)?.minimum_history_sessions ?? 0,
      // schema 1.2 그래프에는 결측 정책이 없다. 계획의 결측 정책은 실행 설정 기본값이다(P2-03).
      missing_policy: "drop",
      as_of_policy: "available_date_lte_as_of",
    },
    narrative: [],
  };
};

let explainFails = false;
const requests: FactorGraphRequest[] = [];
const server = setupServer(
  http.post(`${API}/api/v1/factors/explain`, async ({ request }) => {
    const body = (await request.json()) as FactorGraphRequest;
    requests.push(body);
    if (explainFails) {
      return HttpResponse.json(
        { detail: { code: "factor.graph.invalid", message: "explain 실패" } },
        { status: 500 },
      );
    }
    return HttpResponse.json(explanation(body.graph));
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  requests.length = 0;
  server.resetHandlers();
});
afterAll(() => server.close());

const wrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};
const BASE = 'schema_version: "1.2"\ntitle: "old"\n';
const PROPOSED = 'schema_version: "1.2"\ntitle: "new"\n';

const editorOf = (initial: string) => {
  let text = initial;
  const listeners: ((next: string) => void)[] = [];
  const handle: CodeEditorHandle = {
    getText: () => text,
    setText: vi.fn(),
    replaceRange: vi.fn((from: number, to: number, insert: string) => {
      text = `${text.slice(0, from)}${insert}${text.slice(to)}`;
      listeners.forEach((listener) => listener(text));
    }),
    getSelection: () => ({ from: 0, to: 0 }),
    setSelection: vi.fn(),
    offsetToPosition: vi.fn(() => ({ line: 0, column: 0 })),
    positionToOffset: vi.fn(() => 0),
    scrollTo: vi.fn(),
    focus: vi.fn(),
    getHistoryState: vi.fn(() => null),
    restoreHistoryState: vi.fn(),
  };
  return {
    handle,
    subscribe: (listener: (next: string) => void) => {
      listeners.push(listener);
    },
  };
};

/** 페이지와 같은 조합: 적용 훅 + 실행 계획 + 계획 게이트 + 체인. */
const mountChain = () => {
  const run = vi.fn();
  const editor = editorOf(BASE);
  const hook = renderHook(
    ({ state }: { state: DocumentState }) => {
      const apply = useApplyAssistantProposal(state);
      const plans = useExecutionPlans(state, METADATA);
      const decision = gateBacktestSourceWithFactorPlans(
        decideBacktestSource(state),
        plans,
      );
      const canRun = decision.kind !== "blocked";
      const settling = isBacktestSettling(decision, plans);
      return {
        apply,
        plans,
        canRun,
        chain: useApplyProposalThenBacktest(apply, state, {
          canRun,
          settling,
          run,
        }),
      };
    },
    {
      initialProps: { state: initialDocumentState("yaml", BASE) },
      wrapper: wrapper(),
    },
  );
  act(() => hook.result.current.apply.onEditorReady(editor.handle));
  let current = initialDocumentState("yaml", BASE);
  editor.subscribe((text) => {
    current = documentReducer(current, { type: "edit", source: text });
    hook.rerender({ state: current });
  });
  /** parse·compile이 끝나 팩터가 둘인 스펙이 나온 상태로 민다. 계획 조회는 이 뒤에 시작된다. */
  const compileProposal = () => {
    const parsedState = documentReducer(current, {
      type: "parsed",
      version: current.sourceVersion,
      result: parseSource(current.source, "yaml"),
    });
    current = documentReducer(parsedState, {
      type: "compiled",
      version: parsedState.sourceVersion,
      outcome: {
        spec: SPEC,
        canonicalJson: JSON.stringify(SPEC),
        specHash: "s".repeat(64),
        schemaVersion: "1.2",
        sourceHash: "x".repeat(64),
        diagnostics: [],
      },
    });
    hook.rerender({ state: current });
  };
  return { run, hook, compileProposal };
};

describe("적용 후 백테스트와 팩터 계획 조회", () => {
  afterEach(() => {
    explainFails = false;
  });

  it("팩터 계획 조회가 끝날 때까지 기다렸다가 실행을 잇는다", async () => {
    const { run, hook, compileProposal } = mountChain();
    act(() =>
      hook.result.current.chain.applyThenBacktest({
        source: PROPOSED,
        baseSource: BASE,
      }),
    );
    expect(hook.result.current.chain.waiting).toBe(true);

    // 인풋 2·3: compile은 끝났지만 새 스펙의 팩터 계획은 아직 조회 중이라 게이트가 닫혀 있다.
    compileProposal();
    expect(hook.result.current.plans.status).toBe("loading");
    expect(hook.result.current.canRun).toBe(false);
    expect(hook.result.current.chain.waiting).toBe(true);
    expect(hook.result.current.chain.notStarted).toBe(false);

    // 인풋 4: 계획이 도착해 게이트가 열리면 그때 실행한다.
    await waitFor(() => expect(hook.result.current.plans.status).toBe("ready"));
    expect(hook.result.current.canRun).toBe(true);
    expect(run).toHaveBeenCalledTimes(1);
    expect(hook.result.current.chain.waiting).toBe(false);
    expect(hook.result.current.chain.notStarted).toBe(false);
  });

  it("팩터 계획 조회가 실패하면 대기를 풀고 실행하지 않는다", async () => {
    explainFails = true;
    const { run, hook, compileProposal } = mountChain();
    act(() =>
      hook.result.current.chain.applyThenBacktest({
        source: PROPOSED,
        baseSource: BASE,
      }),
    );
    compileProposal();
    expect(hook.result.current.chain.waiting).toBe(true);

    // 계획 오류는 끝난 판정이다 — 문서를 고치지 않는 한 게이트가 열리지 않는다.
    await waitFor(() => expect(hook.result.current.plans.status).toBe("error"));
    expect(hook.result.current.chain.waiting).toBe(false);
    expect(hook.result.current.chain.notStarted).toBe(true);
    expect(run).not.toHaveBeenCalled();
  });
});
