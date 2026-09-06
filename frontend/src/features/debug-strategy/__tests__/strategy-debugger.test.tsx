import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
import type { ReactElement, ReactNode } from "react";

import {
  strategyWorkbenchApi,
  type StrategySpec,
  type StrategyTraceRequest,
  type StrategyTraceResponse,
} from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { StrategyDebuggerContext } from "../model/strategy-trace";
import { StrategyDebugger } from "../ui/strategy-debugger";

const API = "http://localhost:8000";
const SPEC = JSON.parse(
  readBackendFixture("strategy_documents/quality_momentum.legacy.json"),
) as StrategySpec;

const context = (sourceVersion = 3): StrategyDebuggerContext => ({
  documentEpoch: 2,
  sourceVersion,
  strategySource: {
    kind: "inline_draft",
    spec: SPEC,
    source_hash: "source-hash",
  },
  specHash: "spec-hash",
  expectedSnapshotId: "snapshot-v1",
  expectedRegistryVersion: "registry-v1",
  start: "2025-01-01",
  end: "2026-09-01",
  factors: [
    {
      factorId: "momentum",
      label: "Momentum",
      pointer: "/factors/factors/0/graph",
      outputNodeId: "ranked",
      expectedPlanHash: "plan-hash",
      nodes: [
        {
          nodeId: "ranked",
          operation: "cross_sectional.rank",
          pointer: "/factors/factors/0/graph/nodes/2",
        },
      ],
    },
  ],
});

const savedContext = (): StrategyDebuggerContext => ({
  ...context(),
  strategySource: {
    kind: "saved_revision",
    strategy_id: "strategy-1",
    revision: 7,
    expected_spec_hash: "spec-hash",
  },
});

const traceResponse = (): StrategyTraceResponse => ({
  spec_hash: "spec-hash",
  snapshot_id: "snapshot-v1",
  registry_version: "registry-v1",
  plan_hash: "plan-hash",
  factor_id: "momentum",
  as_of: "2026-08-31",
  provenance: {
    kind: "inline_draft",
    schema_version: "1.0",
    spec_hash: "spec-hash",
    source_hash: "source-hash",
    strategy_id: null,
    revision: null,
  },
  raw: [],
  raw_truncated: false,
  warnings: [],
  trace: {
    rows: [
      {
        node_id: "ranked",
        operation: "cross_sectional.rank",
        as_of: "2026-08-31",
        security_id: "sec-a",
        value: 0.42,
        status: "ok",
        inputs: [{ node_id: "winsorized", value: 0.4 }],
      },
      {
        node_id: "ranked",
        operation: "cross_sectional.rank",
        as_of: "2026-08-31",
        security_id: "sec-b",
        value: null,
        status: "missing_input",
        inputs: [{ node_id: "winsorized", value: null }],
      },
    ],
    offset: 0,
    limit: 2,
    returned: 2,
    has_more: false,
  },
  target: {
    signal_as_of: "2026-08-31",
    execution_on: "2026-09-01",
    candidates: [
      {
        as_of: "2026-08-31",
        security_id: "sec-a",
        sector_id: "sector-a",
        eligible: true,
        composite_score: 0.42,
        rank: 1,
        selected: true,
        side: "long",
        exclusion_reasons: [],
        target_weight: 0.035,
      },
      {
        as_of: "2026-08-31",
        security_id: "sec-b",
        sector_id: null,
        eligible: false,
        composite_score: null,
        rank: null,
        selected: false,
        side: null,
        exclusion_reasons: ["missing_factor"],
        target_weight: 0,
      },
    ],
    targets: [
      {
        security_id: "sec-a",
        side: "long",
        weight: 0.035,
        composite_score: 0.42,
        rank: 1,
      },
    ],
    construction: [
      {
        as_of: "2026-08-31",
        security_id: "sec-a",
        factor_contributions: [
          {
            factor_id: "momentum",
            value: 0.42,
            configured_weight: 1,
            direction: "high",
            weighted_value: 0.42,
            normalized_contribution: 0.42,
            status: "ok",
          },
        ],
        composite_score: 0.42,
        rank: 1,
        eligible: true,
        selected: true,
        side: "long",
        unconstrained_target_weight: 0.05,
        constrained_target_weight: 0.035,
        previous_weight: null,
        estimated_order_delta: null,
        constraint_effect: "adjusted",
        exclusion_reasons: [],
      },
      {
        as_of: "2026-08-31",
        security_id: "sec-b",
        factor_contributions: [
          {
            factor_id: "momentum",
            value: null,
            configured_weight: 1,
            direction: "high",
            weighted_value: null,
            normalized_contribution: null,
            status: "missing",
          },
        ],
        composite_score: null,
        rank: null,
        eligible: false,
        selected: false,
        side: null,
        unconstrained_target_weight: null,
        constrained_target_weight: 0,
        previous_weight: null,
        estimated_order_delta: null,
        constraint_effect: "not_selected",
        exclusion_reasons: ["missing_factor"],
      },
    ],
  },
});

const selectedNodeSentinel = (securityId: string): number =>
  499_000 + Number(securityId.slice("sec-".length)) + 0.123456;

const pagedTraceResponse = (
  request: StrategyTraceRequest,
): StrategyTraceResponse => {
  const asOf = request.as_of ?? "2026-08-31";
  const offset = request.offset ?? 0;
  const limit = request.limit ?? 200;
  const allRows = (request.node_ids ?? []).flatMap((nodeId) =>
    request.security_ids.map((securityId) => ({
      node_id: nodeId,
      operation: `operation.${nodeId}`,
      as_of: asOf,
      security_id: securityId,
      // A selected-node sentinel proves that virtualization displays the exact server value;
      // it must not derive one from the node or security identity on the client.
      value: nodeId === "n499" ? selectedNodeSentinel(securityId) : 0.25,
      status: "ok" as const,
      inputs: [],
    })),
  );
  const rows = allRows.slice(offset, offset + limit);
  const explicitHoldings = request.starting_holdings != null;
  const source = request.strategy_source;
  return {
    spec_hash: "spec-hash",
    snapshot_id: "snapshot-v1",
    registry_version: "registry-v1",
    plan_hash: "plan-hash",
    factor_id: request.factor_id,
    as_of: asOf,
    provenance:
      source.kind === "saved_revision"
        ? {
            kind: "saved_revision",
            schema_version: "1.0",
            spec_hash: "spec-hash",
            source_hash: null,
            strategy_id: source.strategy_id,
            revision: source.revision,
          }
        : {
            kind: "inline_draft",
            schema_version: "1.0",
            spec_hash: "spec-hash",
            source_hash: source.source_hash ?? null,
            strategy_id: null,
            revision: null,
          },
    raw: [],
    raw_truncated: false,
    warnings: [],
    trace: {
      rows,
      offset,
      limit,
      returned: rows.length,
      has_more: offset + limit < allRows.length,
    },
    target: {
      signal_as_of: asOf,
      execution_on: "2026-09-01",
      candidates: request.security_ids.map((securityId, index) => ({
        as_of: asOf,
        security_id: securityId,
        sector_id: null,
        eligible: true,
        composite_score: 0.25,
        rank: index + 1,
        selected: true,
        side: "long",
        exclusion_reasons: [],
        target_weight: 0,
      })),
      targets: [],
      construction: request.security_ids.map((securityId, index) => ({
        as_of: asOf,
        security_id: securityId,
        factor_contributions: [
          {
            factor_id: request.factor_id,
            value: 0.25,
            configured_weight: 1,
            direction: "high",
            weighted_value: 0.25,
            normalized_contribution: 0.25,
            status: "ok",
          },
        ],
        composite_score: 0.25,
        rank: index + 1,
        eligible: true,
        selected: true,
        side: "long",
        unconstrained_target_weight: 0,
        constrained_target_weight: 0,
        previous_weight: explicitHoldings ? 0 : null,
        estimated_order_delta: explicitHoldings ? 0 : null,
        constraint_effect: "unchanged",
        exclusion_reasons: [],
      })),
    },
  };
};

let requests: StrategyTraceRequest[] = [];
const server = setupServer(
  http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
    requests.push((await request.json()) as StrategyTraceRequest);
    return HttpResponse.json(traceResponse());
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  requests = [];
  server.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

const props = (debugContext: StrategyDebuggerContext | null = context()) => ({
  context: debugContext,
  unavailableReason: debugContext === null ? ("document" as const) : null,
  publicationOwnerKey: "route-generation-1",
  asOf: "2026-08-31",
  security: "sec-a, sec-b",
  selectedPointer: "/factors/factors/0/graph/nodes/2",
  onSearchSelection: vi.fn(),
  onSelectPointer: vi.fn(),
  executionPlan: <div>backend execution plan</div>,
});

const contextWithNodes = (count: number): StrategyDebuggerContext => {
  const expanded = context();
  expanded.factors[0]!.nodes = Array.from({ length: count }, (_, index) => ({
    nodeId: `n${index}`,
    operation: `operation.n${index}`,
    pointer: `/factors/factors/0/graph/nodes/${index}`,
  }));
  expanded.factors[0]!.outputNodeId = `n${count - 1}`;
  return expanded;
};

const renderDebugger = (ui: ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
};

describe("StrategyDebugger", () => {
  it("uses the generated trace contract and prioritizes exact TargetTape fields", async () => {
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} />);

    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(await screen.findAllByText("0.42")).toHaveLength(3);
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      as_of: "2026-08-31",
      security_ids: ["sec-a", "sec-b"],
      factor_id: "momentum",
      node_ids: ["ranked"],
      include_raw: true,
      limit: 2,
      strategy_source: { kind: "inline_draft", source_hash: "source-hash" },
    });
    expect(screen.getByText("3.50%")).toBeInTheDocument();
    expect(screen.getByText("missing_factor")).toBeInTheDocument();
    expect(screen.getAllByText("1 원시 데이터")).toHaveLength(2);
    expect(screen.getAllByText("7 위험 제약 후")).toHaveLength(2);
    expect(screen.queryByText("8 주문 차이 추정")).not.toBeInTheDocument();
    expect(screen.queryByText(/실제 주문이 아닙니다/)).not.toBeInTheDocument();
    expect(screen.getByText("snapshot-v1")).toBeInTheDocument();
    expect(screen.getByTitle("plan-hash")).toHaveTextContent("plan-hash");

    await user.click(screen.getByRole("tab", { name: "선택 노드" }));
    expect(screen.getAllByText("cross_sectional.rank")).not.toHaveLength(0);
    expect(screen.getByText("missing_input")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "실행 계획" }));
    expect(screen.getByText("backend execution plan")).toBeInTheDocument();
  });

  it("keeps the selected-node views pinned while the linked request includes every node", async () => {
    const expandedContext = context();
    expandedContext.factors[0]!.nodes.unshift({
      nodeId: "close",
      operation: "field",
      pointer: "/factors/factors/0/graph/nodes/0",
    });
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, () => {
        const payload = traceResponse();
        payload.trace = {
          ...payload.trace,
          rows: [
            {
              node_id: "close",
              operation: "field",
              as_of: "2026-08-31",
              security_id: "sec-a",
              value: 123,
              status: "ok",
              inputs: [],
            },
            {
              node_id: "close",
              operation: "field",
              as_of: "2026-08-31",
              security_id: "sec-b",
              value: 456,
              status: "ok",
              inputs: [],
            },
            ...payload.trace.rows,
          ],
          limit: 4,
          returned: 4,
        };
        return HttpResponse.json(payload);
      }),
    );
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props(expandedContext)} />);

    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await screen.findByText("123");
    await user.click(screen.getByRole("tab", { name: "TargetTape" }));
    const targetPanel = screen.getByRole("tabpanel", { name: "TargetTape" });
    expect(within(targetPanel).queryByText("123")).not.toBeInTheDocument();
    expect(within(targetPanel).getAllByText("0.42")).toHaveLength(2);

    await user.click(screen.getByRole("tab", { name: "선택 노드" }));
    const nodePanel = screen.getByRole("tabpanel", { name: "선택 노드" });
    expect(within(nodePanel).queryByText("field")).not.toBeInTheDocument();
    expect(within(nodePanel).getAllByText("cross_sectional.rank")).toHaveLength(
      2,
    );
  });

  it("pages 6 nodes by 100 securities and keeps every selected output row", async () => {
    const expanded = contextWithNodes(6);
    const securities = Array.from(
      { length: 100 },
      (_, index) => `sec-${index}`,
    );
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const received = (await request.json()) as StrategyTraceRequest;
        requests.push(received);
        return HttpResponse.json(pagedTraceResponse(received));
      }),
    );
    const user = userEvent.setup();
    renderDebugger(
      <StrategyDebugger
        {...props(expanded)}
        security={securities.join(",")}
        selectedPointer="/factors/factors/0/graph/nodes/5"
      />,
    );

    await user.click(screen.getByRole("tab", { name: "선택 노드" }));
    await user.click(screen.getByRole("button", { name: "추적 실행" }));

    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests.map((request) => request.offset)).toEqual([0, 400]);
    expect(requests.map((request) => request.limit)).toEqual([400, 200]);
    expect(requests.every((request) => request.node_ids?.length === 6)).toBe(
      true,
    );
    const panel = screen.getByRole("tabpanel", { name: "선택 노드" });
    const table = within(panel).getByRole("table");
    const viewport = within(panel).getByRole("region", {
      name: "선택한 FactorGraph 노드의 실제 계산 결과",
    });
    expect(viewport).toContainElement(table);
    expect(table).toHaveAttribute("aria-rowcount", "101");
    expect(viewport).toHaveAttribute("data-virtualized", "true");
    expect(viewport).toHaveAttribute("tabindex", "0");
    expect(within(panel).getAllByRole("row").length).toBeLessThan(30);
    expect(within(panel).getAllByText("operation.n5").length).toBeLessThan(30);
    Object.defineProperty(viewport, "clientHeight", {
      configurable: true,
      value: 384,
    });
    for (
      let attempt = 0;
      attempt < 30 && document.activeElement !== viewport;
      attempt += 1
    )
      await user.tab();
    expect(viewport).toHaveFocus();
    await user.keyboard("{End}");
    expect(await within(panel).findByText("sec-99")).toBeInTheDocument();
    await user.keyboard("{Home}");
    expect(await within(panel).findByText("sec-0")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("chunks 101 reachable nodes without exceeding the backend node cap", async () => {
    const expanded = contextWithNodes(101);
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const received = (await request.json()) as StrategyTraceRequest;
        requests.push(received);
        return HttpResponse.json(pagedTraceResponse(received));
      }),
    );
    const user = userEvent.setup();
    renderDebugger(
      <StrategyDebugger
        {...props(expanded)}
        security="sec-a"
        selectedPointer="/factors/factors/0/graph/nodes/100"
      />,
    );

    await user.click(screen.getByRole("tab", { name: "선택 노드" }));
    await user.click(screen.getByRole("button", { name: "추적 실행" }));

    expect(
      await within(
        screen.getByRole("tabpanel", { name: "선택 노드" }),
      ).findByText("operation.n100"),
    ).toBeInTheDocument();
    expect(requests.map((request) => request.node_ids?.length)).toEqual([
      64, 37,
    ]);
    expect(requests.every((request) => request.offset === 0)).toBe(true);
  });

  it("caps the aggregate trace and fetches a truncated selected node separately", async () => {
    const expanded = contextWithNodes(81);
    const securities = Array.from(
      { length: 100 },
      (_, index) => `sec-${index}`,
    );
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const received = (await request.json()) as StrategyTraceRequest;
        requests.push(received);
        return HttpResponse.json(pagedTraceResponse(received));
      }),
    );
    const user = userEvent.setup();
    renderDebugger(
      <StrategyDebugger
        {...props(expanded)}
        security={securities.join(",")}
        selectedPointer="/factors/factors/0/graph/nodes/80"
      />,
    );

    await user.click(screen.getByRole("tab", { name: "선택 노드" }));
    await user.click(screen.getByRole("button", { name: "추적 실행" }));

    expect(
      await screen.findByText(/8,000행 예산에서 중단/),
    ).toBeInTheDocument();
    expect(requests).toHaveLength(21);
    expect(requests.at(-1)).toMatchObject({
      node_ids: ["n80"],
      offset: 0,
      limit: 100,
      include_raw: false,
    });
    const panel = screen.getByRole("tabpanel", { name: "선택 노드" });
    expect(within(panel).getByRole("table")).toHaveAttribute(
      "aria-rowcount",
      "101",
    );
    expect(within(panel).getAllByRole("row").length).toBeLessThan(30);
  }, 15_000);

  it("bounds linked securities and a 500-node chain in the DOM", async () => {
    const expanded = contextWithNodes(500);
    const securities = Array.from(
      { length: 100 },
      (_, index) => `sec-${index}`,
    );
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const received = (await request.json()) as StrategyTraceRequest;
        requests.push(received);
        return HttpResponse.json(pagedTraceResponse(received));
      }),
    );
    const user = userEvent.setup();
    renderDebugger(
      <StrategyDebugger
        {...props(expanded)}
        security={securities.join(",")}
        selectedPointer="/factors/factors/0/graph/nodes/499"
      />,
    );

    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(
      await screen.findByText(/8,000행 예산에서 중단/),
    ).toBeInTheDocument();
    const securityList = screen.getByRole("list", { name: "연결 추적" });
    expect(securityList).toHaveAttribute("data-total-rows", "100");
    expect(securityList).toHaveAttribute("data-virtualized", "true");
    expect(securityList).toHaveAttribute("tabindex", "0");
    expect(
      Number(securityList.getAttribute("data-rendered-rows")),
    ).toBeLessThan(10);

    const nodeLists = within(securityList).getAllByRole("list", {
      name: "2 FactorGraph 노드",
    });
    expect(nodeLists.length).toBeLessThan(10);
    for (const list of nodeLists) {
      expect(list).toHaveAttribute("data-virtualized", "true");
      expect(list).toHaveAttribute("tabindex", "0");
      expect(Number(list.getAttribute("data-rendered-rows"))).toBeLessThan(20);
    }
    expect(within(securityList).getAllByText("operation.n499").length).toBe(
      nodeLists.length,
    );

    Object.defineProperty(securityList, "clientHeight", {
      configurable: true,
      value: 648,
    });
    for (
      let attempt = 0;
      attempt < 30 && document.activeElement !== securityList;
      attempt += 1
    )
      await user.tab();
    expect(securityList).toHaveFocus();
    await user.keyboard("{End}");
    expect(await within(securityList).findByText("sec-99")).toBeInTheDocument();
    await user.keyboard("{Home}");
    expect(await within(securityList).findByText("sec-0")).toBeInTheDocument();
    await user.keyboard("{End}");
    expect(await within(securityList).findByText("sec-99")).toBeInTheDocument();

    const visibleNodeList = within(securityList).getAllByRole("list", {
      name: "2 FactorGraph 노드",
    })[0]!;
    const visiblePipeline =
      visibleNodeList.closest<HTMLElement>('[role="listitem"]')!;
    const visibleSecurity = within(visiblePipeline).getByRole("heading", {
      level: 3,
    }).textContent!;
    Object.defineProperty(visibleNodeList, "clientHeight", {
      configurable: true,
      value: 174,
    });
    await user.tab();
    expect(visibleNodeList).toHaveFocus();
    const outerScrollTop = securityList.scrollTop;
    await user.keyboard("{Home}");
    expect(securityList.scrollTop).toBe(outerScrollTop);
    expect(
      await within(visibleNodeList).findByText("operation.n0"),
    ).toBeInTheDocument();
    await user.keyboard("{End}");
    expect(securityList.scrollTop).toBe(outerScrollTop);
    expect(
      await within(visibleNodeList).findByText("operation.n499"),
    ).toBeInTheDocument();
    expect(
      within(visibleNodeList).getByText(
        selectedNodeSentinel(visibleSecurity).toLocaleString("en-US", {
          maximumFractionDigits: 8,
        }),
      ),
    ).toBeInTheDocument();
  }, 20_000);

  it("discards a fingerprint drift on a later linked-trace page", async () => {
    const expanded = contextWithNodes(6);
    const securities = Array.from(
      { length: 100 },
      (_, index) => `sec-${index}`,
    );
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const received = (await request.json()) as StrategyTraceRequest;
        requests.push(received);
        const payload = pagedTraceResponse(received);
        return HttpResponse.json(
          received.offset === 400
            ? { ...payload, plan_hash: "different-plan" }
            : payload,
        );
      }),
    );
    const user = userEvent.setup();
    renderDebugger(
      <StrategyDebugger
        {...props(expanded)}
        security={securities.join(",")}
        selectedPointer="/factors/factors/0/graph/nodes/5"
      />,
    );

    await user.click(screen.getByRole("button", { name: "추적 실행" }));

    expect(
      await screen.findByText(/fingerprint가 다른 응답을 폐기/),
    ).toBeInTheDocument();
    expect(requests).toHaveLength(2);
    expect(screen.queryByText("25.00%")).not.toBeInTheDocument();
  });

  it("leaves an absent URL date to the backend schedule and shows the resolved date", async () => {
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} asOf={undefined} />);

    expect(screen.getByLabelText("기준일")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "추적 실행" }));

    expect(await screen.findByText("2026-08-31")).toBeInTheDocument();
    expect(requests).toHaveLength(1);
    expect(requests[0]).not.toHaveProperty("as_of");
    expect(screen.getByText(/서버가 실제 TargetTape/)).toBeInTheDocument();
  });

  it("commits a typed security scope and runs the trace in one submit", async () => {
    const user = userEvent.setup();
    const onSearchSelection = vi.fn();
    renderDebugger(
      <StrategyDebugger
        {...props()}
        security={undefined}
        onSearchSelection={onSearchSelection}
      />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "종목 ID" }),
      "sec-a, sec-b",
    );
    await user.click(screen.getByRole("button", { name: "추적 실행" }));

    expect(await screen.findByText("3.50%")).toBeInTheDocument();
    expect(onSearchSelection).toHaveBeenCalledTimes(1);
    expect(onSearchSelection).toHaveBeenCalledWith({
      asOf: "2026-08-31",
      security: "sec-a, sec-b",
    });
    expect(requests).toHaveLength(1);
    expect(requests[0]?.security_ids).toEqual(["sec-a", "sec-b"]);
  });

  it("does not let a resolved request restore a superseded URL scope", async () => {
    let resolveTrace: ((value: StrategyTraceResponse) => void) | undefined;
    vi.spyOn(strategyWorkbenchApi, "traceStrategy").mockReturnValue(
      new Promise((resolve) => {
        resolveTrace = resolve;
      }),
    );
    const onSearchSelection = vi.fn();
    const initial = props();
    const view = renderDebugger(
      <StrategyDebugger {...initial} onSearchSelection={onSearchSelection} />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await waitFor(() =>
      expect(strategyWorkbenchApi.traceStrategy).toHaveBeenCalledTimes(1),
    );

    view.rerender(
      <StrategyDebugger
        {...initial}
        asOf="2026-08-28"
        security="sec-new"
        onSearchSelection={onSearchSelection}
      />,
    );
    await act(async () => resolveTrace?.(traceResponse()));

    await waitFor(() =>
      expect(screen.getByLabelText("기준일")).toHaveValue("2026-08-28"),
    );
    expect(onSearchSelection).not.toHaveBeenCalled();
  });

  it("does not publish into a newer opaque route generation with the same trace owner", async () => {
    let resolveTrace: ((value: StrategyTraceResponse) => void) | undefined;
    vi.spyOn(strategyWorkbenchApi, "traceStrategy").mockReturnValue(
      new Promise((resolve) => {
        resolveTrace = resolve;
      }),
    );
    const onSearchSelection = vi.fn();
    const initial = props();
    const view = renderDebugger(
      <StrategyDebugger {...initial} onSearchSelection={onSearchSelection} />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await waitFor(() =>
      expect(strategyWorkbenchApi.traceStrategy).toHaveBeenCalledTimes(1),
    );

    view.rerender(
      <StrategyDebugger
        {...initial}
        publicationOwnerKey="route-generation-2"
        onSearchSelection={onSearchSelection}
      />,
    );
    await act(async () => resolveTrace?.(traceResponse()));

    expect(onSearchSelection).not.toHaveBeenCalled();
  });

  it("does not let a rejected request restore a previous document route", async () => {
    let rejectTrace: ((reason?: unknown) => void) | undefined;
    vi.spyOn(strategyWorkbenchApi, "traceStrategy").mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectTrace = reject;
      }),
    );
    const onSearchSelection = vi.fn();
    const initial = props(context());
    const view = renderDebugger(
      <StrategyDebugger {...initial} onSearchSelection={onSearchSelection} />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await waitFor(() =>
      expect(strategyWorkbenchApi.traceStrategy).toHaveBeenCalledTimes(1),
    );

    view.rerender(
      <StrategyDebugger
        {...props({ ...savedContext(), documentEpoch: 9 })}
        onSearchSelection={onSearchSelection}
      />,
    );
    await act(async () => rejectTrace?.(new Error("old route failed")));

    await waitFor(() =>
      expect(screen.getByText("저장 리비전")).toBeInTheDocument(),
    );
    expect(onSearchSelection).not.toHaveBeenCalled();
  });

  it("does not publish a deep link after unmount aborts the request", async () => {
    let requestSignal: AbortSignal | undefined;
    vi.spyOn(strategyWorkbenchApi, "traceStrategy").mockImplementation(
      (_request, signal) => {
        requestSignal = signal;
        return new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        });
      },
    );
    const onSearchSelection = vi.fn();
    const view = renderDebugger(
      <StrategyDebugger {...props()} onSearchSelection={onSearchSelection} />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await waitFor(() => expect(requestSignal).toBeDefined());

    view.unmount();

    await waitFor(() => expect(requestSignal?.aborted).toBe(true));
    expect(onSearchSelection).not.toHaveBeenCalled();
  });

  it("shows raw cell semantics and only requests order deltas for an explicit opening book", async () => {
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const received = (await request.json()) as StrategyTraceRequest;
        requests.push(received);
        const payload = traceResponse();
        payload.raw = [
          {
            as_of: "2026-08-31",
            security_id: "sec-a",
            field_id: "flow.foreign_net_buy",
            value: 0,
            available_date: "2026-08-31",
            kind: "source_omitted_zero",
          },
        ];
        payload.target!.construction = payload.target!.construction.map(
          (row) => ({
            ...row,
            previous_weight: row.security_id === "sec-a" ? 0.1 : 0,
            estimated_order_delta: row.security_id === "sec-a" ? -0.065 : 0,
          }),
        );
        return HttpResponse.json(payload);
      }),
    );
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} />);

    await user.type(
      screen.getByRole("textbox", { name: /시작 보유 비중/ }),
      "sec-a=0.1 sec-b=0",
    );
    await user.click(screen.getByRole("button", { name: "추적 실행" }));

    expect(await screen.findByText("source_omitted_zero")).toBeInTheDocument();
    expect(
      screen.getByText(
        (_content, element) =>
          element?.classList.contains("strategy-debugger__stage-primary") ===
            true && element.textContent?.includes("-6.50%") === true,
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText("8 주문 차이 추정")).toHaveLength(2);
    expect(screen.getAllByText(/실제 주문이 아닙니다/)).toHaveLength(2);
    expect(requests[0]?.starting_holdings).toEqual([
      { security_id: "sec-a", weight: 0.1 },
      { security_id: "sec-b", weight: 0 },
    ]);
    await user.click(screen.getByRole("tab", { name: "원시 데이터" }));
    expect(screen.getByText("flow.foreign_net_buy")).toBeInTheDocument();
  });

  it("refetches the same exact owner through the query cache", async () => {
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} />);

    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(await screen.findByText("3.50%")).toBeInTheDocument();
    expect(requests).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(await screen.findByText("3.50%")).toBeInTheDocument();
  });

  it("hides an inline success as soon as the same document becomes a saved source", async () => {
    const user = userEvent.setup();
    const view = renderDebugger(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(await screen.findByText("3.50%")).toBeInTheDocument();

    view.rerender(<StrategyDebugger {...props(savedContext())} />);

    expect(screen.getByText("저장 리비전")).toBeInTheDocument();
    expect(screen.queryByText("3.50%")).not.toBeInTheDocument();
    expect(
      screen.getByText(/범위를 선택한 뒤 추적을 실행/),
    ).toBeInTheDocument();
  });

  it("never requests an invalid or stale document", () => {
    renderDebugger(<StrategyDebugger {...props(null)} />);
    expect(screen.getByRole("button", { name: "추적 실행" })).toBeDisabled();
    expect(
      screen.getByText(/현재 문서가 아직 실행 가능한 StrategySpec이 아닙니다/),
    ).toBeInTheDocument();
    expect(requests).toHaveLength(0);
  });

  it("discards a response when any backend fingerprint differs", async () => {
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, () =>
        HttpResponse.json({ ...traceResponse(), plan_hash: "wrong-plan" }),
      ),
    );
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("tab", { name: "실행 계획" }));
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(
      await screen.findByText(/fingerprint가 다른 응답을 폐기/),
    ).toBeInTheDocument();
    expect(screen.getByText("backend execution plan")).toBeInTheDocument();
    expect(screen.queryByText("3.50%")).not.toBeInTheDocument();
  });

  it("surfaces a structured backend diagnostic without inventing a client error", async () => {
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "trace.request.invalid",
              message: "security id is unknown",
            },
          },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("tab", { name: "실행 계획" }));
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("security id is unknown");
    expect(screen.getByText("backend execution plan")).toBeInTheDocument();
  });

  it("keeps raw and node stages visible when an explicit date has no TargetTape frame", async () => {
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, () =>
        HttpResponse.json({ ...traceResponse(), target: null }),
      ),
    );
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(
      await screen.findAllByText(/원시 데이터와 노드 계산은 표시/),
    ).toHaveLength(2);
    expect(screen.getAllByText("2 FactorGraph 노드")).toHaveLength(2);
    expect(screen.getByText("0.42")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "TargetTape" }));
    expect(
      screen.getByText("선택한 기준일에는 TargetTape frame이 없습니다."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("maps factor and node choices to their canonical source pointers", async () => {
    const user = userEvent.setup();
    const onSelectPointer = vi.fn();
    const expanded = context();
    expanded.factors[0]!.nodes.unshift({
      nodeId: "close",
      operation: "field",
      pointer: "/factors/factors/0/graph/nodes/0",
    });
    expanded.factors.push({
      factorId: "quality",
      label: "Quality",
      pointer: "/factors/factors/1/graph",
      outputNodeId: "quality-score",
      expectedPlanHash: "quality-plan",
      nodes: [
        {
          nodeId: "quality-score",
          operation: "field",
          pointer: "/factors/factors/1/graph/nodes/0",
        },
      ],
    });
    renderDebugger(
      <StrategyDebugger
        {...props(expanded)}
        onSelectPointer={onSelectPointer}
      />,
    );

    await user.selectOptions(
      screen.getByRole("combobox", { name: "노드" }),
      "close",
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/factors/0/graph/nodes/0",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "팩터" }),
      "quality",
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/factors/1/graph/nodes/0",
    );
  });

  it("drops a late inline response after the same document becomes a saved source", async () => {
    let resolve: ((value: StrategyTraceResponse) => void) | undefined;
    vi.spyOn(strategyWorkbenchApi, "traceStrategy").mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const user = userEvent.setup();
    const view = renderDebugger(<StrategyDebugger {...props(context())} />);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(screen.getByText(/실제 TargetTape 계산 경로/)).toBeInTheDocument();

    view.rerender(<StrategyDebugger {...props(savedContext())} />);
    await act(async () => resolve?.(traceResponse()));
    await waitFor(() =>
      expect(
        screen.getByText(/범위를 선택한 뒤 추적을 실행/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("저장 리비전")).toBeInTheDocument();
    expect(screen.queryByText("3.50%")).not.toBeInTheDocument();
  });

  it("cancels a later page without publishing a partial aggregate", async () => {
    const expanded = contextWithNodes(6);
    const securities = Array.from(
      { length: 100 },
      (_, index) => `sec-${index}`,
    );
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, async ({ request }) => {
        const received = (await request.json()) as StrategyTraceRequest;
        requests.push(received);
        if (received.offset === 400) await delay(500);
        return HttpResponse.json(pagedTraceResponse(received));
      }),
    );
    const user = userEvent.setup();
    renderDebugger(
      <StrategyDebugger
        {...props(expanded)}
        security={securities.join(",")}
        selectedPointer="/factors/factors/0/graph/nodes/5"
      />,
    );
    await user.click(screen.getByRole("tab", { name: "실행 계획" }));
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.getByText("추적 요청을 취소했습니다.")).toBeInTheDocument();
    expect(screen.getByText("backend execution plan")).toBeInTheDocument();
    expect(screen.queryByText("25.00%")).not.toBeInTheDocument();
  });
});
