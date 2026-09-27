/**
 * compile 이 붙인 boolean 출력 승격 노드(P2-07)를 실행 계획·그래프 화면이 문서 밖 노드로 보이지 않는다
 * (BACKLOG-014). 문서(아이디어 3, `gt` 출력)에는 노드가 넷이고, compile 된 spec 그래프에는 끝에
 * `__promote_<factor>` 조건 노드와 상수 둘이 더 붙는다. 화면은 backend 가 실행 계획 설명에 싣는
 * `synthesized_nodes` 표식으로 둘을 가른다(Phase 2 감사 #13). 접두사는 이 fixture 에만 있다.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  FactorExplanation,
  FactorGraph,
  StrategySpec,
} from "../../../shared/api";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { ContractInspectorSource } from "../model/contract-inspector";
import {
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import {
  compiledNodeOrigin,
  documentOutputNodeId,
  nodePointerById,
  prepareExecutionPlans,
  type ExecutionPlansState,
  type FactorPlanRequest,
  type PlannedFactor,
} from "../model/use-execution-plans";
import { ExecutionPlanPanel } from "../ui/execution-plan-panel";
import { FactorGraphPanel } from "../ui/factor-graph-panel";

afterEach(cleanup);

const SOURCE = readBackendFixture("strategy_documents/ideas/ma20_breakout.yaml");
const PARSED = parseSource(SOURCE, "yaml");
if (PARSED.status !== "ok") throw new Error("idea fixture must parse");
const AUTHORED = (PARSED.tree as { factors: { graph: FactorGraph }[] })
  .factors[0]!.graph;

/** backend `_promotion.py` 가 hydrate 에서 붙이는 모양(문서에는 없다). */
const PROMOTED: FactorGraph = {
  nodes: [
    ...AUTHORED.nodes,
    { kind: "constant", node_id: "__promote_ma20_breakout_one", value: 1 },
    { kind: "constant", node_id: "__promote_ma20_breakout_zero", value: 0 },
    {
      kind: "conditional",
      node_id: "__promote_ma20_breakout",
      predicate_node_id: "gt",
      true_node_id: "__promote_ma20_breakout_one",
      false_node_id: "__promote_ma20_breakout_zero",
    },
  ],
  output_node_id: "__promote_ma20_breakout",
};

const SPEC = {
  identity: { strategy_id: "draft", revision: 0, schema_version: "1.2" },
  title: "20일 이평 돌파",
  factors: [
    {
      factor_id: "ma20_breakout",
      label: "20일 이평 돌파",
      direction: "high",
      weight: 1,
      graph: PROMOTED,
    },
  ],
} as unknown as StrategySpec;

const METADATA = {
  schema: { schema: {}, schema_hash: "h", schema_version: "1.2" },
  contract: {
    contract: {
      contract_hash: "c",
      dataset_snapshot_id: "dataset-v1",
      factor_registry_version: "registry-v1",
      fields: [],
      schema_hash: "h",
      schema_version: "1.2",
    },
    equity_catalog_url: "/api/v1/equity/catalog",
    factor_catalog_url: "/api/v1/factors/catalog",
  },
  equityCatalog: { snapshot: { snapshot_id: "dataset-v1" }, fields: [] },
  factorCatalog: { registry_version: "registry-v1", factors: [] },
  state: {
    schema: "ready",
    contract: "ready",
    equityCatalog: "ready",
    factorCatalog: "ready",
  },
} as unknown as ContractInspectorSource;

const currentState = (): DocumentState => ({
  ...initialDocumentState("yaml", SOURCE),
  sourceVersion: 2,
  parse: PARSED,
  parsedVersion: 2,
  compiledVersion: 2,
  compiled: {
    spec: SPEC,
    canonicalJson: JSON.stringify(SPEC),
    specHash: "s".repeat(64),
    schemaVersion: "1.2",
    sourceHash: "x".repeat(64),
    diagnostics: [],
  },
  phase: "semantically-valid",
});

const inputsOf = (node: FactorGraph["nodes"][number]): string[] => {
  switch (node.kind) {
    case "time_series":
      return [node.input_node_id];
    case "comparison":
      return [node.left_node_id, node.right_node_id];
    case "conditional":
      return [node.predicate_node_id, node.true_node_id, node.false_node_id];
    default:
      return [];
  }
};

/** backend plan 순서는 위상 순서다. 승격 상수는 조건 노드보다 먼저 온다. */
const EXPLANATION: FactorExplanation = {
  registry_version: "registry-v1",
  data_snapshot_id: "dataset-v1",
  validation: {
    valid: true,
    issues: [],
    node_contracts: PROMOTED.nodes.map((node) => ({
      node_id: node.node_id,
      value_type: node.kind === "comparison" ? "boolean_series" : "numeric_series",
      unit: node.kind === "field" || node.kind === "time_series" ? "KRW" : "1",
      minimum_history_sessions: node.kind === "time_series" ? 20 : 1,
    })),
    minimum_history_sessions: 20,
    required_field_ids: ["price.close"],
  },
  plan: {
    missing_policy: "drop",
    graph_hash: "g".repeat(64),
    plan_hash: "p".repeat(64),
    registry_version: "registry-v1",
    output_node_id: PROMOTED.output_node_id,
    steps: PROMOTED.nodes.map((node, index) => ({
      sequence: index + 1,
      node_id: node.node_id,
      operation: "operator" in node ? `${node.kind}.${node.operator}` : node.kind,
      input_node_ids: inputsOf(node),
      output_type:
        node.kind === "comparison" ? "boolean_series" : "numeric_series",
      output_unit: "KRW",
      minimum_history_sessions: node.kind === "time_series" ? 20 : 1,
    })),
    required_field_ids: ["price.close"],
    minimum_history_sessions: 20,
    as_of_policy: "available_date_lte_as_of",
  },
  narrative: [],
  synthesized_nodes: [
    {
      node_id: "__promote_ma20_breakout_one",
      origin: "promotion",
      role: "promotion_constant",
    },
    {
      node_id: "__promote_ma20_breakout_zero",
      origin: "promotion",
      role: "promotion_constant",
    },
    {
      node_id: "__promote_ma20_breakout",
      origin: "promotion",
      role: "promoted_output",
    },
  ],
};

const request = (): FactorPlanRequest => {
  const prepared = prepareExecutionPlans(currentState(), METADATA);
  if (prepared.status !== "prepared") throw new Error(prepared.status);
  return prepared.requests[0]!;
};

const readyState = (): ExecutionPlansState => ({
  status: "ready",
  expectedRegistryVersion: "registry-v1",
  expectedDataSnapshotId: "dataset-v1",
  factors: [planned()],
});

const planned = (
  explanation: FactorExplanation = EXPLANATION,
): PlannedFactor => ({ ...request(), explanation });

describe("compile 이 붙인 승격 노드 (BACKLOG-014)", () => {
  it("문서에 적힌 노드와 compile 이 붙인 노드를 backend 표식으로 가른다", () => {
    const factor = planned();

    expect(factor.document).toEqual({
      nodeIds: ["adj_close", "mean", "adj_close_2", "gt"],
    });
    expect(
      factor.request.graph.nodes.map((node) => [
        node.node_id,
        compiledNodeOrigin(factor, node.node_id),
      ]),
    ).toEqual([
      ["adj_close", "document"],
      ["mean", "document"],
      ["adj_close_2", "document"],
      ["gt", "document"],
      ["__promote_ma20_breakout_one", "support"],
      ["__promote_ma20_breakout_zero", "support"],
      ["__promote_ma20_breakout", "boolean-score"],
    ]);
    expect(documentOutputNodeId(factor)).toBe("gt");
    // 붙인 출력은 원래 출력 줄을, 붙인 상수는 아무 줄도 가리키지 않는다(문서 밖 index 가 아니다).
    expect(nodePointerById(factor, "__promote_ma20_breakout")).toBe(
      "/factors/0/graph/nodes/3",
    );
    expect(nodePointerById(factor, "__promote_ma20_breakout_one")).toBeNull();
    expect(nodePointerById(factor, "adj_close_2")).toBe("/factors/0/graph/nodes/2");
    // 끊긴 참조는 붙인 노드가 아니다: 문서 쪽 결함으로 남아 포인터가 없다.
    expect(compiledNodeOrigin(factor, "typo")).toBe("document");
    expect(nodePointerById(factor, "typo")).toBeNull();
  });

  it("표식이 없으면 이름이 승격 노드처럼 보여도 문서 노드로 둔다(접두사를 읽지 않는다)", () => {
    const factor = planned({ ...EXPLANATION, synthesized_nodes: [] });

    expect(
      factor.request.graph.nodes.map((node) =>
        compiledNodeOrigin(factor, node.node_id),
      ),
    ).toEqual(Array(7).fill("document"));
    expect(documentOutputNodeId(factor)).toBe("__promote_ma20_breakout");
  });

  it("문서를 읽지 못해도 표식으로 원래 출력을 찾는다", () => {
    const factor = { ...planned(), document: null };

    expect(compiledNodeOrigin(factor, "__promote_ma20_breakout")).toBe(
      "boolean-score",
    );
    expect(documentOutputNodeId(factor)).toBe("gt");
    expect(nodePointerById(factor, "__promote_ma20_breakout")).toBe(
      "/factors/0/graph/nodes/3",
    );
    expect(nodePointerById(factor, "__promote_ma20_breakout_zero")).toBeNull();
  });

  it("실행 계획 표가 붙인 출력을 사람 말로 부르고 원래 출력 줄로 보낸다", async () => {
    const user = userEvent.setup();
    const onSelectPointer = vi.fn();
    render(
      <ExecutionPlanPanel state={readyState()} onSelectPointer={onSelectPointer} />,
    );

    const table = screen.getByRole("table");
    expect(table.textContent).not.toMatch(/__promote_/);
    // 머리글 + 문서 노드 넷 + 붙인 출력 하나. 상수 둘은 보이지 않는다.
    expect(within(table).getAllByRole("row")).toHaveLength(6);
    await user.click(
      within(table).getByRole("button", {
        name: "참/거짓을 1/0으로 소스 열기: /factors/0/graph/nodes/3",
      }),
    );
    expect(onSelectPointer).toHaveBeenLastCalledWith("/factors/0/graph/nodes/3");
  });

  it("그래프 화면도 붙인 출력 카드를 사람 말로 보이고 소스 열기가 원래 출력으로 간다", async () => {
    const user = userEvent.setup();
    const onOpenSource = vi.fn();
    render(
      <FactorGraphPanel
        state={readyState()}
        diagnostics={[]}
        onSelectPointer={vi.fn()}
        onOpenSource={onOpenSource}
      />,
    );

    const list = screen.getAllByRole("listitem");
    expect(
      list.map((item) => item.textContent ?? "").join("\n"),
    ).not.toMatch(/__promote_/);
    const card = screen
      .getByRole("button", { name: "그래프 노드 선택: 참/거짓을 1/0으로" })
      .closest("li") as HTMLElement;
    expect(within(card).getByText("OUTPUT")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "소스에서 열기" }));
    expect(onOpenSource).toHaveBeenLastCalledWith("/factors/0/graph/nodes/3");
  });
});
