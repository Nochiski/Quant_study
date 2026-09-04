import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  FactorExplanation,
  FactorGraphRequest,
} from "../../../shared/api";
import { projectFactorGraphs } from "../model/factor-graph-projection";
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
      pointer: "/factors/factors/0/graph/nodes/1",
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
    expect(projection.factors[0].nodes[0].issues[0].code).toBe(
      "factor.graph.cycle",
    );
  });
});

describe("FactorGraphPanel", () => {
  it("renders conditional branches, saved references, provenance and exact selection actions", async () => {
    const user = userEvent.setup();
    const onSelectPointer = vi.fn();
    const onOpenSource = vi.fn();
    render(
      <FactorGraphPanel
        state={readyState()}
        diagnostics={[]}
        selectedPointer="/factors/factors/0/graph/nodes/0/true_node_id"
        onSelectPointer={onSelectPointer}
        onOpenSource={onOpenSource}
      />,
    );

    expect(screen.getByText("factor-registry-v7")).toBeInTheDocument();
    expect(screen.getByText("krx-pit-2026-09-01")).toBeInTheDocument();
    expect(screen.getByTitle("g".repeat(64))).toHaveTextContent(
      `${"g".repeat(12)}…`,
    );
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
      "/factors/factors/0/graph/nodes/0",
    );
    await user.click(
      screen.getByRole("button", {
        name: "true 입력 노드 선택: neutralized_value",
      }),
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/factors/0/graph/nodes/2",
    );
    await user.click(within(signal as HTMLElement).getByText("소스에서 열기"));
    expect(onOpenSource).toHaveBeenLastCalledWith(
      "/factors/factors/0/graph/nodes/0",
    );
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
            pointer: "/factors/factors/0/graph/nodes/2/input_node_id",
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
      "/factors/factors/0/graph/nodes/2/input_node_id",
    );
  });
});
