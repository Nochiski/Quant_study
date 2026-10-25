import { describe, expect, it } from "vitest";
import { canvasMetadata } from "../model/canvas-metadata";
import type { CanvasProjection } from "../model/canvas-projection";
import type {
  GraphFactorProjection,
  GraphNodeProjection,
} from "../model/factor-graph-projection";
const node: GraphNodeProjection = {
  sequence: 1,
  planned: true,
  origin: "document",
  nodeId: "a",
  kind: "field",
  operation: "field",
  pointer: "/factors/0/graph/nodes/0",
  inputs: [],
  outputType: "boolean_series",
  outputUnit: "boolean",
  minimumHistorySessions: 2,
  isOutput: false,
  details: [],
  issues: [],
};
const canvas: CanvasProjection = {
  nodes: [
    {
      key: "id:a",
      nodeId: "a",
      pointer: node.pointer!,
      connectable: true,
      label: "1. 조건",
      inputs: [],
    },
  ],
  edges: [],
};
const factor: GraphFactorProjection = {
  factorIndex: 0,
  factorId: "factor",
  label: "팩터",
  valid: true,
  source: "execution-plan",
  nodes: [node],
  issues: [],
  minimumHistorySessions: 2,
  graphHash: "graph",
  planHash: "plan",
};
describe("canvas metadata ownership", () => {
  it("requires matching pointer and a unique source ID and hides absent/stale projections", () => {
    expect(canvasMetadata(canvas, factor).get("id:a")?.node).toBe(node);
    expect(canvasMetadata(canvas, undefined).size).toBe(0);
    expect(
      canvasMetadata(canvas, {
        ...factor,
        nodes: [{ ...node, pointer: "/factors/0/graph/nodes/1" }],
      }).size,
    ).toBe(0);
    expect(
      canvasMetadata(
        { ...canvas, nodes: [{ ...canvas.nodes[0], connectable: false }] },
        factor,
      ).size,
    ).toBe(0);
    expect(
      canvasMetadata(canvas, { ...factor, nodes: [node, node] }).size,
    ).toBe(0);
  });
  it("keeps boolean contracts separate from the read-only promotion and uses backend planned status only", () => {
    const promotion = {
      ...node,
      nodeId: "synthesized",
      origin: "boolean-score" as const,
      outputType: "numeric_series",
    };
    const metadata = canvasMetadata(canvas, {
      ...factor,
      nodes: [node, promotion],
    }).get("id:a")!;
    expect(metadata.node.outputType).toBe("boolean_series");
    expect(metadata.promotion).toBe(promotion);
    expect(metadata.notExecuted).toBe(false);
    expect(
      canvasMetadata(canvas, {
        ...factor,
        nodes: [{ ...node, planned: false }],
      }).get("id:a")?.notExecuted,
    ).toBe(true);
    expect(
      canvasMetadata(canvas, {
        ...factor,
        source: "validation-only",
        nodes: [{ ...node, planned: false }],
      }).get("id:a")?.notExecuted,
    ).toBe(false);
  });
});
