import { describe, expect, it } from "vitest";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { canvasProjection, copyCanvasNode } from "../model/canvas-projection";
import { planSourceOperation } from "../model/source-transactions";

const schema = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
);
const source = readBackendFixture("strategy_documents/quality_momentum.yaml");
const treeOf = (text: string) => {
  const parsed = parseSource(text, "yaml");
  if (parsed.status !== "ok") throw new Error("fixture parse");
  return parsed.tree;
};
const names = { catalog: () => null, reference: () => null };

describe("canvas projection", () => {
  it("projects authored ports from schema and resolves edges only inside one factor", () => {
    const tree = treeOf(source);
    const projected = canvasProjection(tree, schema, 0, names);
    expect(projected.nodes.map((node) => node.nodeId)).toEqual([
      "close",
      "mom_252",
    ]);
    expect(projected.nodes[1].inputs.map((port) => port.key)).toEqual([
      "input_node_id",
    ]);
    expect(projected.edges).toEqual([
      {
        from: projected.nodes[0].key,
        to: projected.nodes[1].key,
        input: "input_node_id",
      },
    ]);
    expect(
      projected.nodes.every((node) => !node.label.includes(node.nodeId!)),
    ).toBe(true);
  });

  it("keeps malformed nodes selectable by pointer without resolving an ambiguous wire", () => {
    const tree = treeOf(source.replace("node_id: mom_252", "node_id: close"));
    const projected = canvasProjection(tree, schema, 0, names);
    expect(new Set(projected.nodes.map((node) => node.key)).size).toBe(2);
    expect(projected.nodes.every((node) => !node.connectable)).toBe(true);
    expect(projected.edges).toEqual([]);
    expect(projected.nodes[1].pointer).toBe("/factors/0/graph/nodes/1");
  });

  it("copies one authored node with a new ID and preserves input references through the source planner", () => {
    const op = copyCanvasNode(
      treeOf(source),
      "/factors/0",
      "/factors/0/graph/nodes/1",
    );
    expect(op).not.toBeNull();
    const planned = planSourceOperation(source, "yaml", op!);
    expect(planned.status).toBe("ok");
    if (planned.status !== "ok") throw new Error("copy plan");
    const projected = canvasProjection(
      treeOf(planned.edit.nextSource),
      schema,
      0,
      names,
    );
    expect(projected.nodes.map((node) => node.nodeId)).toEqual([
      "close",
      "mom_252",
      "mom_252_2",
    ]);
    expect(projected.nodes[2].inputs[0].sourceId).toBe("close");
    expect(
      copyCanvasNode(treeOf(source), "/factors/1", "/factors/0/graph/nodes/1"),
    ).toBeNull();
  });
});
