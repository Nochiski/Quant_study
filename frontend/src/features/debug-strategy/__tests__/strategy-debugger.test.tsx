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
  end: "2026-08-31",
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
  asOf: "2026-08-31",
  security: "sec-a, sec-b",
  selectedPointer: "/factors/factors/0/graph/nodes/2",
  onSearchSelection: vi.fn(),
  onSelectPointer: vi.fn(),
  executionPlan: <div>backend execution plan</div>,
});

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

  it("renders a real empty TargetTape state without treating it as an error", async () => {
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, () =>
        HttpResponse.json({ ...traceResponse(), target: null }),
      ),
    );
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(
      await screen.findByText("선택한 기준일에는 TargetTape frame이 없습니다."),
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

  it("exposes an explicit cancellable request state", async () => {
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, async () => {
        await delay(500);
        return HttpResponse.json(traceResponse());
      }),
    );
    const user = userEvent.setup();
    renderDebugger(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("tab", { name: "실행 계획" }));
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await user.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.getByText("추적 요청을 취소했습니다.")).toBeInTheDocument();
    expect(screen.getByText("backend execution plan")).toBeInTheDocument();
    expect(screen.queryByText("3.50%")).not.toBeInTheDocument();
  });
});
