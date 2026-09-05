import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
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

describe("StrategyDebugger", () => {
  it("uses the generated trace contract and prioritizes exact TargetTape fields", async () => {
    const user = userEvent.setup();
    render(<StrategyDebugger {...props()} />);

    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(await screen.findAllByText("0.42")).toHaveLength(2);
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      as_of: "2026-08-31",
      security_ids: ["sec-a", "sec-b"],
      factor_id: "momentum",
      node_ids: ["ranked"],
      include_raw: false,
      limit: 2,
      strategy_source: { kind: "inline_draft", source_hash: "source-hash" },
    });
    expect(screen.getByText("3.50%")).toBeInTheDocument();
    expect(screen.getByText("missing_factor")).toBeInTheDocument();
    expect(screen.getByText("snapshot-v1")).toBeInTheDocument();
    expect(screen.getByTitle("plan-hash")).toHaveTextContent("plan-hash");

    await user.click(screen.getByRole("tab", { name: "선택 노드" }));
    expect(screen.getAllByText("cross_sectional.rank")).not.toHaveLength(0);
    expect(screen.getByText("missing_input")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "실행 계획" }));
    expect(screen.getByText("backend execution plan")).toBeInTheDocument();
  });

  it("never requests an invalid or stale document", () => {
    render(<StrategyDebugger {...props(null)} />);
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
    render(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(
      await screen.findByText(/fingerprint가 다른 응답을 폐기/),
    ).toBeInTheDocument();
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
    render(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("security id is unknown");
  });

  it("renders a real empty TargetTape state without treating it as an error", async () => {
    server.use(
      http.post(`${API}/api/v1/strategies/debug/trace`, () =>
        HttpResponse.json({ ...traceResponse(), target: null }),
      ),
    );
    const user = userEvent.setup();
    render(<StrategyDebugger {...props()} />);
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
    render(
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

  it("drops a late response after sourceVersion changes even if transport ignores abort", async () => {
    let resolve: ((value: StrategyTraceResponse) => void) | undefined;
    vi.spyOn(strategyWorkbenchApi, "traceStrategy").mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const user = userEvent.setup();
    const view = render(<StrategyDebugger {...props(context(3))} />);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    expect(screen.getByText(/실제 TargetTape 계산 경로/)).toBeInTheDocument();

    view.rerender(<StrategyDebugger {...props(context(4))} />);
    await act(async () => resolve?.(traceResponse()));
    await waitFor(() =>
      expect(
        screen.getByText(/범위를 선택한 뒤 추적을 실행/),
      ).toBeInTheDocument(),
    );
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
    render(<StrategyDebugger {...props()} />);
    await user.click(screen.getByRole("button", { name: "추적 실행" }));
    await user.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.getByText("추적 요청을 취소했습니다.")).toBeInTheDocument();
    expect(screen.queryByText("3.50%")).not.toBeInTheDocument();
  });
});
