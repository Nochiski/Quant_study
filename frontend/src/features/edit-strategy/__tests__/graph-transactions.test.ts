import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  addNode,
  graphNodeIds,
  nodeKinds,
  nodePointerOf,
  removeNode,
  rewireInput,
  setMissingPolicy,
  setNodeField,
  setOutput,
  suggestNodeId,
} from "../model/graph-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import {
  planSourceOperation,
  type SourceOperation,
} from "../model/source-transactions";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const VERBOSE = readBackendFixture("strategy_documents/quality_momentum.yaml");

const treeOf = (source: string): unknown => {
  const parsed = parseSource(source, "yaml");
  if (parsed.status !== "ok") throw new Error("fixture must parse");
  return parsed.tree;
};

/** 연산을 실제 planner에 태워 결과 텍스트를 돌려준다(연산의 모양뿐 아니라 계획 가능성을 검증). */
const applyPlan = (source: string, op: SourceOperation): string => {
  const planned = planSourceOperation(source, "yaml", op);
  if (planned.status !== "ok") throw new Error(planned.reason);
  return planned.edit.nextSource;
};

const F0 = "/factors/0";
// 같은 `node_id`(`close`)를 쓰는 둘째 팩터: 노드 id는 그래프 스코프라 합법이다.
const TWO_FACTORS = VERBOSE.replace(
  "portfolio:\n",
  "  - factor_id: volume\n    direction: high\n    graph:\n      nodes:\n        - kind: field\n          node_id: close\n          field_id: price.volume\n      output_node_id: close\nportfolio:\n",
);
// P4-03 "항목 추가"가 만드는 빈 팩터(`nodes: []`).
const EMPTY_FACTOR = VERBOSE.replace(
  "portfolio:\n",
  "  - factor_id: blank\n    direction: high\n    graph:\n      nodes: []\n      output_node_id: \"\"\nportfolio:\n",
);

describe("graph transactions (P5-01)", () => {
  it("reads node ids, pointers and the union kinds from the schema", () => {
    const tree = treeOf(VERBOSE);
    expect(graphNodeIds(tree, F0)).toEqual(["close", "mom_252"]);
    expect(nodePointerOf(tree, F0, "mom_252")).toBe("/factors/0/graph/nodes/1");
    expect(nodePointerOf(tree, F0, "nope")).toBeNull();
    const kinds = nodeKinds(SCHEMA, tree, F0).map(([kind]) => kind);
    expect(kinds).toContain("field");
    expect(kinds).toContain("time_series");
    expect(kinds).toContain("conditional");
    expect(kinds.length).toBeGreaterThanOrEqual(12);
  });

  it("suggests unique node ids by suffixing a counter", () => {
    const tree = treeOf(VERBOSE);
    expect(suggestNodeId(tree, F0, "close")).toBe("close_2");
    expect(suggestNodeId(tree, F0, "rank")).toBe("rank");
    const withClose2 = treeOf(
      VERBOSE.replace(
        "      output_node_id: mom_252\n",
        "        - kind: field\n          node_id: close_2\n          field_id: price.volume\n      output_node_id: mom_252\n",
      ),
    );
    expect(suggestNodeId(withClose2, F0, "close")).toBe("close_3");
  });

  it("adds a materialized node whose reference fields point at the last node, and the planner accepts it", () => {
    const tree = treeOf(VERBOSE);
    const added = addNode(tree, F0, "unary", SCHEMA);
    if ("error" in added) throw new Error(added.error);
    expect(added.nodeId).toBe("unary");
    expect(added.op).toMatchObject({
      kind: "insert-item",
      parentPointer: "/factors/0/graph/nodes",
      value: { kind: "unary", node_id: "unary", input_node_id: "mom_252" },
    });
    const next = treeOf(applyPlan(VERBOSE, added.op));
    expect(graphNodeIds(next, F0)).toEqual(["close", "mom_252", "unary"]);
    expect(addNode(tree, F0, "nope", SCHEMA)).toEqual({ error: "unknown-kind" });
  });

  it("adds the first node to an empty factor graph (P4-03 output) with `insert-item` on `nodes: []`", () => {
    const tree = treeOf(EMPTY_FACTOR);
    const added = addNode(tree, "/factors/1", "field", SCHEMA);
    if ("error" in added) throw new Error(added.error);
    expect(added.op).toMatchObject({
      kind: "insert-item",
      parentPointer: "/factors/1/graph/nodes",
      value: { kind: "field", node_id: "field" },
    });
    const source = applyPlan(EMPTY_FACTOR, added.op);
    expect(graphNodeIds(treeOf(source), "/factors/1")).toEqual(["field"]);
    // 그 다음 출력 지정도 계획된다(빈 문자열 → replace-scalar).
    const output = setOutput(treeOf(source), "/factors/1", "field");
    if ("error" in output) throw new Error(output.error);
    expect(output).toEqual({
      kind: "replace-scalar",
      pointer: "/factors/1/graph/output_node_id",
      value: "field",
    });
    expect(applyPlan(source, output)).toContain('output_node_id: field');
  });

  it("opens `graph` or `nodes` when they are missing", () => {
    const noNodes = treeOf(
      VERBOSE.replace(
        "portfolio:\n",
        "  - factor_id: bare\n    direction: high\n    graph:\n      output_node_id: \"\"\nportfolio:\n",
      ),
    );
    const viaNodes = addNode(noNodes, "/factors/1", "field", SCHEMA);
    if ("error" in viaNodes) throw new Error(viaNodes.error);
    expect(viaNodes.op).toMatchObject({
      kind: "insert-key",
      parentPointer: "/factors/1/graph",
      key: "nodes",
    });
    const noGraph = treeOf(
      VERBOSE.replace(
        "portfolio:\n",
        "  - factor_id: bare\n    direction: high\nportfolio:\n",
      ),
    );
    const viaGraph = addNode(noGraph, "/factors/1", "field", SCHEMA);
    if ("error" in viaGraph) throw new Error(viaGraph.error);
    expect(viaGraph.op).toMatchObject({
      kind: "insert-key",
      parentPointer: "/factors/1",
      key: "graph",
      value: { nodes: [{ kind: "field", node_id: "field" }], output_node_id: "field" },
    });
  });

  it("sets node fields, rewires inputs within the graph, and sets output and missing policy", () => {
    const tree = treeOf(VERBOSE);
    const node = "/factors/0/graph/nodes/1";
    expect(setNodeField(tree, node, "window", 126)).toEqual({
      kind: "replace-scalar",
      pointer: `${node}/window`,
      value: 126,
    });
    expect(setNodeField(tree, node, "lag", 1)).toEqual({
      kind: "insert-key",
      parentPointer: node,
      key: "lag",
      value: 1,
    });
    expect(rewireInput(tree, node, "input_node_id", "mom_252")).toEqual({
      error: "self",
    });
    expect(rewireInput(tree, node, "input_node_id", "ghost")).toEqual({
      error: "not-found",
    });
    expect(rewireInput(tree, node, "input_node_id", "close")).toEqual({
      kind: "replace-scalar",
      pointer: `${node}/input_node_id`,
      value: "close",
    });
    expect(setOutput(tree, F0, "close")).toEqual({
      kind: "replace-scalar",
      pointer: "/factors/0/graph/output_node_id",
      value: "close",
    });
    expect(setOutput(tree, F0, "ghost")).toEqual({ error: "not-found" });
    expect(setMissingPolicy(tree, F0, "zero")).toMatchObject({
      pointer: "/factors/0/graph/missing_policy",
      value: "zero",
    });
  });

  it("refuses to remove a referenced node, scoped to its own graph, and removes an unreferenced one with its leading comment", () => {
    const tree = treeOf(TWO_FACTORS);
    // `close`는 같은 그래프의 `mom_252`가 입력으로 참조한다. 다른 팩터의 같은 id는 참조가 아니다(감사 DEFECT-P4X-002).
    expect(removeNode(tree, F0, "close")).toEqual({
      error: "referenced",
      by: ["/factors/0/graph/nodes/1/input_node_id"],
    });
    expect(removeNode(tree, F0, "ghost")).toEqual({ error: "not-found" });
    // 출력 노드는 `output_node_id`가 참조한다.
    expect(removeNode(tree, F0, "mom_252")).toEqual({
      error: "referenced",
      by: ["/factors/0/graph/output_node_id"],
    });
    // 둘째 팩터의 `close`는 자기 출력이 참조한다; 출력을 바꾸면 지울 수 있고 선행 주석도 함께 사라진다(감사 R3).
    const commented = TWO_FACTORS.replace(
      "        - kind: field\n          node_id: close\n          field_id: price.volume\n",
      "        # 거래량 노드\n        - kind: field\n          node_id: close\n          field_id: price.volume\n        - kind: field\n          node_id: px\n          field_id: price.close\n",
    ).replace("      output_node_id: close\nportfolio:", "      output_node_id: px\nportfolio:");
    const removal = removeNode(treeOf(commented), "/factors/1", "close");
    if ("error" in removal) throw new Error(removal.error);
    expect(removal).toEqual({ kind: "remove", pointer: "/factors/1/graph/nodes/0" });
    const next = applyPlan(commented, removal);
    expect(next).not.toContain("거래량 노드");
    expect(graphNodeIds(treeOf(next), "/factors/1")).toEqual(["px"]);
    expect(graphNodeIds(treeOf(next), F0)).toEqual(["close", "mom_252"]);
  });
});
