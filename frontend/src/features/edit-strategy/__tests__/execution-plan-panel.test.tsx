import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  FactorExplanation,
  FactorGraphRequest,
} from "../../../shared/api";
import type {
  ExecutionPlansState,
  PlannedFactor,
} from "../model/use-execution-plans";
import { ExecutionPlanPanel } from "../ui/execution-plan-panel";

afterEach(cleanup);

const graph = (fieldId: string): FactorGraphRequest["graph"] => ({
  nodes: [
    { node_id: "close", field_id: fieldId, kind: "field" },
    {
      node_id: "momentum",
      operator: "momentum",
      input_node_id: "close",
      window: 252,
      lag: 0,
      kind: "time_series",
    },
  ],
  output_node_id: "momentum",
  missing_policy: "drop",
});

const explanation = (
  value: FactorGraphRequest["graph"],
  suffix: string,
): FactorExplanation => ({
  registry_version: "factor-registry-v1",
  data_snapshot_id: "dataset-v1",
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
        node_id: "momentum",
        value_type: "numeric_series",
        unit: "ratio",
        minimum_history_sessions: 252,
      },
    ],
    minimum_history_sessions: 252,
    required_field_ids: [
      value.nodes[0]?.kind === "field" ? value.nodes[0].field_id : "",
    ],
  },
  plan: {
    graph_hash: suffix.repeat(64),
    plan_hash: suffix.toUpperCase().repeat(64),
    registry_version: "factor-registry-v1",
    output_node_id: "momentum",
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
        node_id: "momentum",
        operation: "time_series.momentum",
        input_node_ids: ["close"],
        output_type: "numeric_series",
        output_unit: "ratio",
        minimum_history_sessions: 252,
      },
    ],
    required_field_ids: [
      value.nodes[0]?.kind === "field" ? value.nodes[0].field_id : "",
    ],
    referenced_factor_ids: [],
    referenced_subgraph_ids: [],
    minimum_history_sessions: 252,
    missing_policy: "drop",
    as_of_policy: "available_date_lte_as_of",
  },
  narrative: [],
});

const factor = (
  factorIndex: number,
  factorId: string,
  label: string,
  fieldId: string,
  suffix: string,
): PlannedFactor => {
  const request = { graph: graph(fieldId), parameter_ids: [], factor_ids: [] };
  return {
    factorIndex,
    factorId,
    label,
    request,
    explanation: explanation(request.graph, suffix),
  };
};

const readyState = (): ExecutionPlansState => ({
  status: "ready",
  expectedRegistryVersion: "factor-registry-v1",
  expectedDataSnapshotId: "dataset-v1",
  factors: [
    factor(0, "momentum", "모멘텀", "price.close", "a"),
    factor(1, "quality", "퀄리티", "financial.book_equity", "b"),
  ],
});

describe("ExecutionPlanPanel", () => {
  it("projects backend order, contracts, history and fingerprints without recalculation", () => {
    render(
      <ExecutionPlanPanel
        state={readyState()}
        selectedPointer="/factors/factors/0/graph/nodes/1/operator"
        onSelectPointer={vi.fn()}
      />,
    );

    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent(
      /1.*close.*field.*numeric_series.*KRW.*1/s,
    );
    expect(rows[2]).toHaveTextContent(
      /2.*momentum.*time_series\.momentum.*close.*numeric_series.*ratio.*252/s,
    );
    expect(rows[2]).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByText("252 세션")).toHaveLength(2);
    expect(screen.getByText("dataset-v1")).toBeInTheDocument();
    expect(screen.getByTitle("a".repeat(64))).toHaveTextContent(
      `${"a".repeat(12)}…`,
    );
    expect(screen.getByTitle("A".repeat(64))).toHaveTextContent(
      `${"A".repeat(12)}…`,
    );
    expect(screen.getByText("price.close")).toBeInTheDocument();
  });

  it("maps output and input node actions and factor selection to exact source pointers", async () => {
    const user = userEvent.setup();
    const onSelectPointer = vi.fn();
    render(
      <ExecutionPlanPanel
        state={readyState()}
        onSelectPointer={onSelectPointer}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: /momentum 소스 열기/ }),
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/factors/0/graph/nodes/1",
    );
    await user.click(
      screen.getByRole("button", { name: /close.*numeric_series.*KRW/ }),
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/factors/0/graph/nodes/0",
    );

    await user.selectOptions(
      screen.getByRole("combobox", { name: "팩터 그래프" }),
      "1",
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith(
      "/factors/factors/1/graph",
    );
    expect(screen.getByTitle("b".repeat(64))).toBeInTheDocument();
  });

  it("uses a routed factor pointer as the factor selection owner", () => {
    render(
      <ExecutionPlanPanel
        state={readyState()}
        selectedPointer="/factors/factors/1/graph/nodes/0/field_id"
        onSelectPointer={vi.fn()}
      />,
    );

    expect(screen.getByRole("combobox", { name: "팩터 그래프" })).toHaveValue(
      "1",
    );
    expect(screen.getByTitle("b".repeat(64))).toBeInTheDocument();
    expect(screen.getAllByRole("row")[1]).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it.each([
    [{ status: "blocked", reason: "empty" } as const, "문서가 비어"],
    [{ status: "blocked", reason: "invalid" } as const, "검증 오류"],
    [{ status: "blocked", reason: "pending" } as const, "기다리는 중"],
    [{ status: "blocked", reason: "stale" } as const, "이전 실행 계획"],
    [{ status: "metadata-loading" } as const, "계약과 카탈로그"],
    [{ status: "metadata-unavailable" } as const, "불러오지 못해"],
    [{ status: "loading" } as const, "컴파일하는 중"],
    [{ status: "empty" } as const, "정의된 팩터가 없습니다"],
    [
      {
        status: "incompatible",
        resource: "dataset",
        expected: "dataset-v1",
        actual: "dataset-v2",
      } as const,
      "expected dataset-v1 · actual dataset-v2",
    ],
    [{ status: "error", message: "network down" } as const, "network down"],
  ])("renders the %o state without stale plan data", (state, message) => {
    render(
      <ExecutionPlanPanel
        state={state}
        selectedPointer="/factors/factors/0"
        onSelectPointer={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(message);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows backend validation issues when a typed plan is blocked", () => {
    const state = readyState();
    if (state.status !== "ready") throw new Error("fixture must be ready");
    const first = state.factors[0];
    const invalid: ExecutionPlansState = {
      ...state,
      factors: [
        {
          ...first,
          explanation: {
            ...first.explanation,
            validation: {
              ...first.explanation.validation,
              valid: false,
              issues: [
                {
                  code: "factor.graph.input_type",
                  node_id: "momentum",
                  path: "nodes.1",
                  message: "numeric input required",
                  severity: "error",
                },
              ],
            },
            plan: null,
          },
        },
      ],
    };

    render(<ExecutionPlanPanel state={invalid} onSelectPointer={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent(
      /factor\.graph\.input_type.*numeric input required/,
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
