import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  addNode,
  graphNodeIds,
  nodeKinds,
  nodePointerOf,
  nodeReferenceKeys,
  removeNodeAt,
  setNodeField,
  suggestNodeId,
} from "../model/graph-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import {
  planSourceOperation,
  planSourceOperations,
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
    expect(added.ops[0]!).toMatchObject({
      kind: "insert-item",
      parentPointer: "/factors/0/graph/nodes",
      value: { kind: "unary", node_id: "unary", input_node_id: "mom_252" },
    });
    const next = treeOf(applyPlan(VERBOSE, added.ops[0]!));
    expect(graphNodeIds(next, F0)).toEqual(["close", "mom_252", "unary"]);
    expect(addNode(tree, F0, "nope", SCHEMA)).toEqual({ error: "unknown-kind" });
  });

  it("lists node reference keys from schemaFacts and leaves multi-slot kinds unfilled (review P1-1·P2-3)", () => {
    const tree = treeOf(VERBOSE);
    const kinds = new Map(nodeKinds(SCHEMA, tree, F0));
    expect(nodeReferenceKeys(SCHEMA, kinds.get("time_series")!)).toEqual(["input_node_id"]);
    expect(nodeReferenceKeys(SCHEMA, kinds.get("binary")!)).toEqual(["left_node_id", "right_node_id"]);
    expect(nodeReferenceKeys(SCHEMA, kinds.get("conditional")!)).toEqual([
      "predicate_node_id",
      "true_node_id",
      "false_node_id",
    ]);
    expect(nodeReferenceKeys(SCHEMA, kinds.get("field")!)).toEqual([]);
    // 참조 슬롯이 둘 이상이면 같은 노드를 여러 슬롯에 넣지 않는다(빈 문자열로 두고 사용자가 고른다).
    const binary = addNode(tree, F0, "binary", SCHEMA);
    if ("error" in binary) throw new Error(binary.error);
    expect(binary.ops[0]).toMatchObject({
      value: { kind: "binary", left_node_id: "", right_node_id: "" },
    });
  });

  it("adds the first node to an empty factor graph (P4-03 output) and makes it the output in one transaction (backlog 13)", () => {
    const tree = treeOf(EMPTY_FACTOR);
    const added = addNode(tree, "/factors/1", "field", SCHEMA);
    if ("error" in added) throw new Error(added.error);
    expect(added.ops).toEqual([
      expect.objectContaining({
        kind: "insert-item",
        parentPointer: "/factors/1/graph/nodes",
        value: expect.objectContaining({ kind: "field", node_id: "field" }),
      }),
      {
        kind: "replace-scalar",
        pointer: "/factors/1/graph/output_node_id",
        value: "field",
      },
    ]);
    const planned = planSourceOperations(EMPTY_FACTOR, "yaml", added.ops);
    if (planned.status !== "ok") throw new Error(planned.reason);
    const source = planned.edit.nextSource;
    expect(graphNodeIds(treeOf(source), "/factors/1")).toEqual(["field"]);
    expect(source).toContain("output_node_id: field");
    // 출력이 이미 정해진 빈 그래프는 그대로 두고, 노드가 있는 그래프의 추가는 연산 하나다.
    const preset = treeOf(
      EMPTY_FACTOR.replace('output_node_id: ""', "output_node_id: later"),
    );
    const kept = addNode(preset, "/factors/1", "field", SCHEMA);
    if ("error" in kept) throw new Error(kept.error);
    expect(kept.ops).toHaveLength(1);
    const more = addNode(treeOf(source), "/factors/1", "unary", SCHEMA);
    if ("error" in more) throw new Error(more.error);
    expect(more.ops).toHaveLength(1);
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
    expect(viaNodes.ops[0]!).toMatchObject({
      kind: "insert-key",
      parentPointer: "/factors/1/graph",
      key: "nodes",
    });
    // `nodes` 키가 없어도 첫 노드라 출력도 함께 정한다(빈 문자열 → replace-scalar, 리뷰 P2-5).
    expect(viaNodes.ops[1]).toEqual({
      kind: "replace-scalar",
      pointer: "/factors/1/graph/output_node_id",
      value: "field",
    });
    const noGraph = treeOf(
      VERBOSE.replace(
        "portfolio:\n",
        "  - factor_id: bare\n    direction: high\nportfolio:\n",
      ),
    );
    const viaGraph = addNode(noGraph, "/factors/1", "field", SCHEMA);
    if ("error" in viaGraph) throw new Error(viaGraph.error);
    expect(viaGraph.ops[0]!).toMatchObject({
      kind: "insert-key",
      parentPointer: "/factors/1",
      key: "graph",
      value: { nodes: [{ kind: "field", node_id: "field" }], output_node_id: "field" },
    });
  });

  it("sets node fields: replace-scalar when the key is written, insert-key otherwise", () => {
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
  });

  it("removes by pointer when node ids are duplicated or missing (review DEFECT-132-01)", () => {
    const duplicated = treeOf(
      VERBOSE.replace(
        "      output_node_id: mom_252\n",
        "        - kind: field\n          node_id: spare\n          field_id: price.volume\n        - kind: field\n          node_id: spare\n          field_id: price.open\n        - kind: field\n          field_id: price.high\n      output_node_id: mom_252\n",
      ),
    );
    // id 기반이면 첫 일치 노드(nodes/2)가 지워졌을 자리 — pointer 기반은 누른 노드를 지운다.
    expect(nodePointerOf(duplicated, F0, "spare")).toBe("/factors/0/graph/nodes/2");
    expect(removeNodeAt(duplicated, F0, "/factors/0/graph/nodes/3")).toEqual({
      kind: "remove",
      pointer: "/factors/0/graph/nodes/3",
    });
    // 참조 검사는 같다.
    expect(removeNodeAt(duplicated, F0, "/factors/0/graph/nodes/0")).toMatchObject({
      error: "referenced",
      by: ["/factors/0/graph/nodes/1/input_node_id"],
    });
    // node_id가 없는 노드도 pointer로 지운다; 없는 pointer는 not-found.
    expect(removeNodeAt(duplicated, F0, "/factors/0/graph/nodes/4")).toEqual({
      kind: "remove",
      pointer: "/factors/0/graph/nodes/4",
    });
    expect(removeNodeAt(duplicated, F0, "/factors/0/graph/nodes/9")).toEqual({
      error: "not-found",
    });
  });

  it("refuses to remove a referenced node, scoped to its own graph, and removes an unreferenced one with its leading comment", () => {
    const tree = treeOf(TWO_FACTORS);
    // `close`는 같은 그래프의 `mom_252`가 입력으로 참조한다. 다른 팩터의 같은 id는 참조가 아니다(감사 DEFECT-P4X-002).
    expect(removeNodeAt(tree, F0, nodePointerOf(tree, F0, "close")!)).toEqual({
      error: "referenced",
      by: ["/factors/0/graph/nodes/1/input_node_id"],
    });
    expect(nodePointerOf(tree, F0, "ghost")).toBeNull();
    // 출력 노드는 `output_node_id`가 참조한다.
    expect(removeNodeAt(tree, F0, nodePointerOf(tree, F0, "mom_252")!)).toEqual({
      error: "referenced",
      by: ["/factors/0/graph/output_node_id"],
    });
    // 둘째 팩터의 `close`는 자기 출력이 참조한다; 출력을 바꾸면 지울 수 있고 선행 주석도 함께 사라진다(감사 R3).
    const commented = TWO_FACTORS.replace(
      "        - kind: field\n          node_id: close\n          field_id: price.volume\n",
      "        # 거래량 노드\n        - kind: field\n          node_id: close\n          field_id: price.volume\n        - kind: field\n          node_id: px\n          field_id: price.close\n",
    ).replace("      output_node_id: close\nportfolio:", "      output_node_id: px\nportfolio:");
    const removal = removeNodeAt(treeOf(commented), "/factors/1", "/factors/1/graph/nodes/0");
    if ("error" in removal) throw new Error(removal.error);
    expect(removal).toEqual({ kind: "remove", pointer: "/factors/1/graph/nodes/0" });
    const next = applyPlan(commented, removal);
    expect(next).not.toContain("거래량 노드");
    expect(graphNodeIds(treeOf(next), "/factors/1")).toEqual(["px"]);
    expect(graphNodeIds(treeOf(next), F0)).toEqual(["close", "mom_252"]);
  });
});
