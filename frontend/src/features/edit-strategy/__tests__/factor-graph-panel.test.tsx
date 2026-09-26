import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  FactorExplanation,
  FactorGraphRequest,
} from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { projectFactorGraphs } from "../model/factor-graph-projection";
import type { JsonSchema } from "../model/schema-navigator";
import type { SourceTransactions } from "../model/use-source-transactions";
import type {
  ExecutionPlansState,
  PlannedFactor,
} from "../model/use-execution-plans";
import { FactorGraphPanel } from "../ui/factor-graph-panel";

afterEach(cleanup);

const graph: FactorGraphRequest["graph"] = {
  // Authored order intentionally differs from backend execution order.
  nodes: [
    {
      node_id: "signal",
      kind: "conditional",
      predicate_node_id: "positive",
      true_node_id: "neutralized_value",
      false_node_id: "neutralized_value",
    },
    { node_id: "close", kind: "field", field_id: "price.close" },
    {
      node_id: "neutralized_value",
      kind: "saved_subgraph",
      subgraph_id: "sector-neutral-v2",
    },
    {
      node_id: "positive",
      kind: "comparison",
      operator: "gt",
      left_node_id: "close",
      right_node_id: "zero",
    },
    { node_id: "zero", kind: "constant", value: 0 },
  ],
  output_node_id: "signal",
  missing_policy: "drop",
};

const explanation = (): FactorExplanation => ({
  registry_version: "factor-registry-v7",
  data_snapshot_id: "krx-pit-2026-09-01",
  narrative: [],
  validation: {
    valid: true,
    issues: [
      {
        code: "factor.graph.reference_notice",
        message: "saved subgraph is pinned",
        node_id: "neutralized_value",
        path: "nodes.2",
        severity: "warning",
      },
    ],
    node_contracts: graph.nodes.map((node) => ({
      node_id: node.node_id,
      value_type:
        node.node_id === "positive" ? "boolean_series" : "numeric_series",
      unit: node.node_id === "positive" ? "boolean" : "ratio",
      minimum_history_sessions: node.node_id === "close" ? 1 : 252,
    })),
    minimum_history_sessions: 252,
    required_field_ids: ["price.close"],
  },
  plan: {
    registry_version: "factor-registry-v7",
    graph_hash: "g".repeat(64),
    plan_hash: "p".repeat(64),
    output_node_id: "signal",
    steps: [
      [1, "close", "field", [], "numeric_series", "KRW", 1],
      [2, "zero", "constant", [], "scalar", "unitless", 0],
      [
        3,
        "positive",
        "comparison.gt",
        ["close", "zero"],
        "boolean_series",
        "boolean",
        1,
      ],
      [
        4,
        "neutralized_value",
        "saved_subgraph",
        [],
        "numeric_series",
        "ratio",
        252,
      ],
      [
        5,
        "signal",
        "conditional",
        ["positive", "neutralized_value", "neutralized_value"],
        "numeric_series",
        "ratio",
        252,
      ],
    ].map(
      ([
        sequence,
        node_id,
        operation,
        input_node_ids,
        output_type,
        output_unit,
        minimum_history_sessions,
      ]) => ({
        sequence: sequence as number,
        node_id: node_id as string,
        operation: operation as string,
        input_node_ids: input_node_ids as string[],
        output_type: output_type as string,
        output_unit: output_unit as string,
        minimum_history_sessions: minimum_history_sessions as number,
      }),
    ),
    required_field_ids: ["price.close"],
    referenced_factor_ids: [],
    referenced_subgraph_ids: ["sector-neutral-v2"],
    minimum_history_sessions: 252,
    missing_policy: "drop",
    as_of_policy: "available_date_lte_as_of",
  },
});

const plannedFactor = (
  graphValue: FactorGraphRequest["graph"] = graph,
  graphExplanation: FactorExplanation = explanation(),
): PlannedFactor => ({
  factorIndex: 0,
  factorId: "conditional-value",
  label: "조건부 가치",
  request: {
    graph: graphValue,
    parameter_ids: [],
    factor_ids: ["conditional-value"],
    subgraph_ids: ["sector-neutral-v2"],
  },
  explanation: graphExplanation,
});

const readyState = (): ExecutionPlansState => ({
  status: "ready",
  expectedRegistryVersion: "factor-registry-v7",
  expectedDataSnapshotId: "krx-pit-2026-09-01",
  factors: [plannedFactor()],
});

const disconnectedReadyState = (): ExecutionPlansState => {
  const graphWithOrphan: FactorGraphRequest["graph"] = {
    ...graph,
    nodes: [
      ...graph.nodes,
      { node_id: "orphan", kind: "field", field_id: "price.open" },
    ],
  };
  const graphExplanation = explanation();
  graphExplanation.validation.node_contracts.push({
    node_id: "orphan",
    value_type: "numeric_series",
    unit: "KRW",
    minimum_history_sessions: 1,
  });
  return {
    status: "ready",
    expectedRegistryVersion: "factor-registry-v7",
    expectedDataSnapshotId: "krx-pit-2026-09-01",
    factors: [plannedFactor(graphWithOrphan, graphExplanation)],
  };
};

describe("FactorGraph projection", () => {
  it("keeps backend plan order, contracts and branch roles while mapping authored pointers", () => {
    const projection = projectFactorGraphs(readyState());
    if (projection.status !== "ready") throw new Error("fixture must be ready");

    const factor = projection.factors[0];
    expect(factor.nodes.map((node) => node.nodeId)).toEqual([
      "close",
      "zero",
      "positive",
      "neutralized_value",
      "signal",
    ]);
    expect(factor.nodes[0]).toMatchObject({
      planned: true,
      pointer: "/factors/0/graph/nodes/1",
      outputType: "numeric_series",
      outputUnit: "KRW",
      minimumHistorySessions: 1,
    });
    expect(factor.nodes[4].inputs).toMatchObject([
      { nodeId: "positive", role: "predicate" },
      { nodeId: "neutralized_value", role: "true" },
      { nodeId: "neutralized_value", role: "false" },
    ]);
    expect(factor.nodes[3].details).toEqual([
      { label: "subgraph_id", value: "sector-neutral-v2" },
    ]);
    expect(factor.nodes[4].isOutput).toBe(true);
  });

  it("keeps disconnected authored nodes visible outside the backend execution plan", () => {
    const projection = projectFactorGraphs(disconnectedReadyState());
    if (projection.status !== "ready") throw new Error("fixture must be ready");

    const factor = projection.factors[0];
    expect(factor.nodes.map((node) => node.nodeId)).toEqual([
      "close",
      "zero",
      "positive",
      "neutralized_value",
      "signal",
      "orphan",
    ]);
    expect(factor.nodes[5]).toMatchObject({
      nodeId: "orphan",
      planned: false,
      sequence: null,
      pointer: "/factors/0/graph/nodes/5",
      outputType: "numeric_series",
      outputUnit: "KRW",
      minimumHistorySessions: 1,
    });
  });

  it("keeps plan-less graphs in authored order and uses only backend validation contracts", () => {
    const invalidExplanation = explanation();
    invalidExplanation.plan = null;
    invalidExplanation.validation.valid = false;
    invalidExplanation.validation.issues = [
      {
        code: "factor.graph.cycle",
        message: "cycle detected",
        node_id: "signal",
        path: "nodes.0",
        severity: "error",
      },
    ];
    const state: ExecutionPlansState = {
      status: "ready",
      expectedRegistryVersion: "factor-registry-v7",
      expectedDataSnapshotId: "krx-pit-2026-09-01",
      factors: [plannedFactor(graph, invalidExplanation)],
    };
    const projection = projectFactorGraphs(state);
    if (projection.status !== "ready") throw new Error("fixture must be ready");

    expect(projection.factors[0].source).toBe("validation-only");
    expect(projection.factors[0].valid).toBe(false);
    expect(projection.factors[0].nodes.map((node) => node.nodeId)).toEqual(
      graph.nodes.map((node) => node.node_id),
    );
    expect(projection.factors[0].nodes.every((node) => !node.planned)).toBe(
      true,
    );
    expect(projection.factors[0].nodes[0].issues[0].code).toBe(
      "factor.graph.cycle",
    );
  });
});

/** 순환·중복 진단이 노드를 집어 오는 상태(P1-05). 계획은 없고 검증 진단만 있다. */
const cyclicReadyState = (): ExecutionPlansState => {
  const graphExplanation = explanation();
  graphExplanation.plan = null;
  graphExplanation.validation.valid = false;
  graphExplanation.validation.issues = [
    {
      code: "factor.graph.cycle",
      message:
        "이 노드가 순환 참조에 묶여 있어 값을 계산할 수 없습니다. 고리 중 한 곳의 입력을 끊어 주세요 — cycle=close → positive → close",
      node_id: "close",
      path: "nodes.1",
      severity: "error",
    },
    {
      code: "factor.graph.cycle",
      message:
        "이 노드가 순환 참조에 묶여 있어 값을 계산할 수 없습니다. 고리 중 한 곳의 입력을 끊어 주세요 — cycle=close → positive → close",
      node_id: "positive",
      path: "nodes.3",
      severity: "error",
    },
  ];
  return {
    status: "ready",
    expectedRegistryVersion: "factor-registry-v7",
    expectedDataSnapshotId: "krx-pit-2026-09-01",
    factors: [plannedFactor(graph, graphExplanation)],
  };
};

describe("FactorGraphPanel", () => {
  it("puts a cycle diagnostic on every node card in the loop, not in the graph-level list", () => {
    // P1-05: `node_id`가 없던 시절에는 "순환 참조가 있습니다" 한 줄이 그래프 머리에만 떠서,
    // 어느 노드를 고쳐야 하는지 사용자가 목록을 눈으로 훑어야 했다.
    const projection = projectFactorGraphs(cyclicReadyState());
    if (projection.status !== "ready") throw new Error("fixture must be ready");

    const byNodeId = new Map(
      projection.factors[0].nodes.map((node) => [node.nodeId, node]),
    );
    expect(byNodeId.get("close")?.issues.map((issue) => issue.code)).toEqual([
      "factor.graph.cycle",
    ]);
    expect(byNodeId.get("positive")?.issues.map((issue) => issue.code)).toEqual(
      ["factor.graph.cycle"],
    );
    expect(byNodeId.get("zero")?.issues).toEqual([]);
    expect(
      projection.factors[0].issues.filter((issue) => issue.node_id === null),
    ).toEqual([]);

    render(
      <FactorGraphPanel
        state={cyclicReadyState()}
        diagnostics={[]}
        onSelectPointer={vi.fn()}
        onOpenSource={vi.fn()}
      />,
    );

    // 고리에 묶인 노드 카드 둘에만 배지가 붙고, 문장이 고리 경로를 말한다.
    const badges = screen.getAllByText("factor.graph.cycle");
    expect(badges).toHaveLength(2);
    for (const badge of badges) {
      const row = badge.closest("li");
      expect(
        within(row as HTMLElement).getByText(/cycle=close → positive → close/),
      ).toBeInTheDocument();
    }
  });

  it("keeps the last plan projection with a recomputing badge while the plan reloads (OBS-132-05)", () => {
    const schema = JSON.parse(
      readBackendFixture("strategy_documents/runtime-schema.json"),
    ) as JsonSchema;
    const transactions: SourceTransactions = {
      apply: vi.fn(() => true),
      run: vi.fn(() => true),
      feedback: { status: "idle" },
      feedbackFor: () => ({ status: "idle" }),
      onEditorReady: vi.fn(),
      enabled: true,
      disabled: null,
      settling: false,
    };
    const editing = {
      tree: { factors: [{ factor_id: "f", direction: "high", graph }] },
      schema,
      transactions,
      catalogs: { equityFields: null, factors: null },
    };
    const view = (state: ExecutionPlansState, documentKey = 1) => (
      <FactorGraphPanel
        state={state}
        diagnostics={[]}
        onSelectPointer={vi.fn()}
        onOpenSource={vi.fn()}
        editing={{ ...editing, documentKey }}
      />
    );
    const { rerender } = render(view(readyState()));
    expect(screen.queryByText("재계산 중")).toBeNull();
    // 편집 확정 뒤 실제 경로: 이전 compile의 spec이 남아 blocked(stale) → blocked(pending) → loading → ready.
    // 그동안 직전 투영이 남는다(reducer 실측: stale 판정이 pending보다 먼저다 — 4차 리뷰).
    rerender(view({ status: "blocked", reason: "stale" }));
    expect(screen.getByText("재계산 중")).toBeInTheDocument();
    rerender(view({ status: "blocked", reason: "pending" }));
    expect(screen.getByText("재계산 중")).toBeInTheDocument();
    rerender(view({ status: "loading" }));
    expect(screen.getByText("재계산 중")).toBeInTheDocument();
    expect(document.querySelector('[data-node-id="signal"]')).not.toBeNull();
    expect(
      screen.getByRole("button", { name: "데이터 필드 노드 추가" }),
    ).toBeInTheDocument();
    // 다른 문서로 가면(문서 키 변경) 직전 투영을 쓰지 않는다.
    rerender(view({ status: "loading" }, 2));
    expect(screen.queryByText("재계산 중")).toBeNull();
    expect(document.querySelector('[data-node-id="signal"]')).toBeNull();
  });

  it("renders conditional branches, saved references, provenance and exact selection actions", async () => {
    const user = userEvent.setup();
    const onSelectPointer = vi.fn();
    const onOpenSource = vi.fn();
    render(
      <FactorGraphPanel
        state={readyState()}
        diagnostics={[]}
        selectedPointer="/factors/0/graph/nodes/0/true_node_id"
        onSelectPointer={onSelectPointer}
        onOpenSource={onOpenSource}
      />,
    );

    expect(screen.getByText("factor-registry-v7")).toBeInTheDocument();
    expect(screen.getByText("krx-pit-2026-09-01")).toBeInTheDocument();
    // fingerprint는 `title`에 숨지 않고 본문으로 전부 보인다(P1-04).
    expect(screen.getByText("g".repeat(64))).toBeInTheDocument();
    const signal = document.querySelector('[data-node-id="signal"]');
    expect(signal).toHaveAttribute("aria-current", "true");
    expect(
      within(signal as HTMLElement).getByText("OUTPUT"),
    ).toBeInTheDocument();
    expect(
      within(signal as HTMLElement).getByText("predicate"),
    ).toBeInTheDocument();
    expect(within(signal as HTMLElement).getByText("true")).toBeInTheDocument();
    expect(
      within(signal as HTMLElement).getByText("false"),
    ).toBeInTheDocument();
    expect(screen.getByText("sector-neutral-v2")).toBeInTheDocument();
    expect(screen.getByText("saved subgraph is pinned")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "그래프 노드 선택: signal" }),
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/0/graph/nodes/0",
    );
    await user.click(
      screen.getByRole("button", {
        name: "true 입력 노드 선택: neutralized_value",
      }),
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/0/graph/nodes/2",
    );
    await user.click(within(signal as HTMLElement).getByText("소스에서 열기"));
    expect(onOpenSource).toHaveBeenLastCalledWith("/factors/0/graph/nodes/0");
  });

  it("hides stale graph data and exposes current backend graph diagnostics", async () => {
    const user = userEvent.setup();
    const onOpenSource = vi.fn();
    render(
      <FactorGraphPanel
        state={{ status: "blocked", reason: "invalid" }}
        diagnostics={[
          {
            code: "factor.graph.missing_input",
            kind: "semantic",
            severity: "error",
            pointer: "/factors/0/graph/nodes/2/input_node_id",
            nodeId: "momentum",
            message: "input node does not exist",
            range: null,
          },
        ]}
        onSelectPointer={vi.fn()}
        onOpenSource={onOpenSource}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("검증 오류");
    expect(screen.getByText("factor.graph.missing_input")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("백엔드 계획 순서의 팩터 노드와 입력 연결"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "소스에서 열기" }));
    expect(onOpenSource).toHaveBeenCalledWith(
      "/factors/0/graph/nodes/2/input_node_id",
    );
  });

  it("separates disconnected definitions and preserves their exact source pointer", async () => {
    const user = userEvent.setup();
    const onSelectPointer = vi.fn();
    const onOpenSource = vi.fn();
    render(
      <FactorGraphPanel
        state={disconnectedReadyState()}
        diagnostics={[]}
        onSelectPointer={onSelectPointer}
        onOpenSource={onOpenSource}
      />,
    );

    const unplanned = screen.getByRole("region", {
      name: "실행 계획에 포함되지 않은 정의",
    });
    const orphan = within(unplanned).getByRole("listitem");
    expect(orphan).toHaveAttribute("data-node-id", "orphan");
    expect(within(orphan).getByText("미실행")).toBeInTheDocument();
    expect(within(orphan).getByText("price.open")).toBeInTheDocument();

    await user.click(
      within(orphan).getByRole("button", {
        name: "그래프 노드 선택: orphan",
      }),
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/0/graph/nodes/5",
    );
    await user.click(within(orphan).getByText("소스에서 열기"));
    expect(onOpenSource).toHaveBeenLastCalledWith("/factors/0/graph/nodes/5");
  });
});
