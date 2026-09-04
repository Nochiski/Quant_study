import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import type { PropsWithChildren } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type {
  DatasetFieldProfile,
  FactorExplanation,
  FactorGraphRequest,
  FactorCatalog,
  ResearchCatalog,
  StrategySpec,
} from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { ContractInspectorSource } from "../model/contract-inspector";
import {
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import {
  factorIndexAtPointer,
  factorNodePointer,
  nodePointerById,
  pointerSelectsNode,
  prepareExecutionPlans,
  useExecutionPlans,
} from "../model/use-execution-plans";

const API = "http://localhost:8000";

const FIXTURE_SPEC = JSON.parse(
  readBackendFixture("strategy_documents/quality_momentum.legacy.json"),
) as StrategySpec;
const MOMENTUM_FACTOR = FIXTURE_SPEC.factors.factors[0]!;
const SPEC: StrategySpec = {
  ...FIXTURE_SPEC,
  title: "멀티 팩터",
  factors: {
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
          missing_policy: "keep",
        },
      },
    ],
  },
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
    schema_version: "1.0",
  },
  contract: {
    contract: {
      contract_hash: "contract-hash",
      dataset_snapshot_id: "dataset-v1",
      factor_registry_version: "registry-v1",
      fields: [],
      schema_hash: "schema-hash",
      schema_version: "1.0",
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

const currentState = (): DocumentState => ({
  ...initialDocumentState("yaml", "valid source"),
  sourceVersion: 2,
  parsedVersion: 2,
  compiledVersion: 2,
  compiled: {
    spec: SPEC,
    canonicalJson: JSON.stringify(SPEC),
    specHash: "s".repeat(64),
    schemaVersion: "1.0",
    sourceHash: "x".repeat(64),
    diagnostics: [],
  },
  phase: "semantically-valid",
});

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
      missing_policy: graph.missing_policy ?? "drop",
      as_of_policy: "available_date_lte_as_of",
    },
    narrative: [],
  };
};

const requests: FactorGraphRequest[] = [];
const server = setupServer(
  http.post(`${API}/api/v1/factors/explain`, async ({ request }) => {
    const body = (await request.json()) as FactorGraphRequest;
    requests.push(body);
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

describe("execution plan orchestration", () => {
  it("requests every current factor with contract-pinned field metadata", async () => {
    const { result } = renderHook(
      () => useExecutionPlans(currentState(), METADATA),
      { wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(requests).toHaveLength(2);
    expect(requests[0]).toMatchObject({
      parameter_ids: [],
      factor_ids: ["momentum", "quality"],
    });
    expect(requests[0]).not.toHaveProperty("fields");
    expect(requests.map((request) => request.graph.output_node_id)).toEqual([
      "mom_252",
      "book",
    ]);
    if (result.current.status === "ready") {
      expect(result.current.factors.map((factor) => factor.factorId)).toEqual([
        "momentum",
        "quality",
      ]);
    }
  });

  it("blocks stale specs and every input metadata version drift", () => {
    const stale = {
      ...currentState(),
      sourceVersion: 3,
      phase: "parsing",
    } as const;
    expect(prepareExecutionPlans(stale, METADATA)).toEqual({
      status: "blocked",
      reason: "stale",
    });
    expect(
      prepareExecutionPlans(currentState(), {
        ...METADATA,
        factorCatalog: { ...FACTOR_CATALOG, registry_version: "registry-v2" },
      }),
    ).toMatchObject({
      status: "incompatible",
      resource: "factor-registry",
      expected: "registry-v1",
      actual: "registry-v2",
    });
    expect(
      prepareExecutionPlans(currentState(), {
        ...METADATA,
        schema: { ...METADATA.schema!, schema_hash: "new-schema" },
      }),
    ).toMatchObject({ status: "incompatible", resource: "schema-contract" });
    expect(
      prepareExecutionPlans(currentState(), {
        ...METADATA,
        equityCatalog: {
          ...EQUITY_CATALOG,
          snapshot: {
            ...EQUITY_CATALOG.snapshot,
            snapshot_id: "dataset-v2",
          },
        },
      }),
    ).toMatchObject({ status: "incompatible", resource: "dataset" });
    const nextRuntime: ContractInspectorSource = {
      ...METADATA,
      schema: {
        ...METADATA.schema!,
        schema_hash: "schema-v2",
        schema_version: "2.0",
      },
      contract: {
        ...METADATA.contract!,
        contract: {
          ...METADATA.contract!.contract,
          schema_hash: "schema-v2",
          schema_version: "2.0",
        },
      },
    };
    expect(prepareExecutionPlans(currentState(), nextRuntime)).toMatchObject({
      status: "incompatible",
      resource: "schema-contract",
      expected: "2.0",
      actual: "1.0:1.0",
    });
    const { result } = renderHook(
      () => useExecutionPlans(currentState(), nextRuntime),
      { wrapper: wrapper() },
    );
    expect(result.current).toMatchObject({
      status: "incompatible",
      resource: "schema-contract",
    });
    expect(requests).toHaveLength(0);
  });

  it("checks response provenance even when no plan was produced", async () => {
    server.use(
      http.post(`${API}/api/v1/factors/explain`, async ({ request }) => {
        const body = (await request.json()) as FactorGraphRequest;
        const payload = explanation(body.graph);
        return HttpResponse.json({
          ...payload,
          registry_version: "registry-v2",
          plan: null,
        });
      }),
    );
    const { result } = renderHook(
      () => useExecutionPlans(currentState(), METADATA),
      { wrapper: wrapper() },
    );

    await waitFor(() =>
      expect(result.current).toMatchObject({
        status: "incompatible",
        resource: "factor-registry",
        expected: "registry-v1",
        actual: "registry-v2",
      }),
    );
  });

  it("fails closed when backend field metadata came from another dataset", async () => {
    server.use(
      http.post(`${API}/api/v1/factors/explain`, async ({ request }) => {
        const body = (await request.json()) as FactorGraphRequest;
        return HttpResponse.json({
          ...explanation(body.graph),
          data_snapshot_id: "dataset-v2",
        });
      }),
    );
    const { result } = renderHook(
      () => useExecutionPlans(currentState(), METADATA),
      { wrapper: wrapper() },
    );

    await waitFor(() =>
      expect(result.current).toMatchObject({
        status: "incompatible",
        resource: "dataset",
        expected: "dataset-v1",
        actual: "dataset-v2",
      }),
    );
  });

  it("does not start a network request for a current invalid document", () => {
    const state = {
      ...currentState(),
      compiled: {
        ...currentState().compiled!,
        spec: null,
        specHash: null,
        diagnostics: [
          {
            code: "strategy.invalid",
            kind: "semantic" as const,
            severity: "error" as const,
            pointer: "/factors",
            message: "invalid",
            range: null,
          },
        ],
      },
      phase: "semantic-invalid" as const,
    };
    const { result } = renderHook(() => useExecutionPlans(state, METADATA), {
      wrapper: wrapper(),
    });

    expect(result.current).toEqual({ status: "blocked", reason: "invalid" });
    expect(requests).toHaveLength(0);
  });

  it("cancels in-flight explain requests when the current spec becomes stale", async () => {
    const signals: AbortSignal[] = [];
    server.use(
      http.post(`${API}/api/v1/factors/explain`, ({ request }) => {
        signals.push(request.signal);
        return new Promise<Response>((resolve) => {
          request.signal.addEventListener(
            "abort",
            () => resolve(new HttpResponse(null, { status: 499 })),
            { once: true },
          );
        });
      }),
    );
    const initial = currentState();
    const { result, rerender } = renderHook(
      ({ state }: { state: DocumentState }) =>
        useExecutionPlans(state, METADATA),
      { initialProps: { state: initial }, wrapper: wrapper() },
    );
    await waitFor(() => expect(signals).toHaveLength(2));

    rerender({ state: { ...initial, sourceVersion: 3, phase: "parsing" } });

    await waitFor(() =>
      expect(signals.every((signal) => signal.aborted)).toBe(true),
    );
    expect(result.current).toEqual({ status: "blocked", reason: "stale" });
  });

  it("maps factor and node selections to exact RFC 6901 pointers", () => {
    const prepared = prepareExecutionPlans(currentState(), METADATA);
    expect(prepared.status).toBe("prepared");
    if (prepared.status !== "prepared") return;

    expect(factorNodePointer(1, 0)).toBe("/factors/factors/1/graph/nodes/0");
    expect(nodePointerById(prepared.requests[0], "mom_252")).toBe(
      "/factors/factors/0/graph/nodes/1",
    );
    expect(nodePointerById(prepared.requests[0], "missing")).toBeNull();
    expect(
      factorIndexAtPointer("/factors/factors/1/graph/nodes/0/field_id"),
    ).toBe(1);
    expect(factorIndexAtPointer("/risk/max_name_weight")).toBeNull();
    expect(factorIndexAtPointer("/factors/factors/01/graph")).toBeNull();
    expect(
      pointerSelectsNode(
        "/factors/factors/1/graph/nodes/0/field_id",
        "/factors/factors/1/graph/nodes/0",
      ),
    ).toBe(true);
  });
});
