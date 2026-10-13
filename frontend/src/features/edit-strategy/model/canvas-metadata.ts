/** 편집 원문과 backend 투영의 신원이 일치하는 노드만 읽기 전용 정보를 붙인다. */
import type { CanvasProjection } from "./canvas-projection";
import type {
  GraphFactorProjection,
  GraphNodeProjection,
} from "./factor-graph-projection";
export type CanvasMetadata = {
  node: GraphNodeProjection;
  promotion?: GraphNodeProjection;
  notExecuted: boolean;
};
export const canvasMetadata = (
  canvas: CanvasProjection,
  factor: GraphFactorProjection | undefined,
): ReadonlyMap<string, CanvasMetadata> => {
  const result = new Map<string, CanvasMetadata>();
  if (factor === undefined) return result;
  for (const source of canvas.nodes) {
    if (!source.connectable) continue;
    const candidates = factor.nodes.filter(
      (node) => node.origin === "document" && node.nodeId === source.nodeId,
    );
    if (candidates.length !== 1 || candidates[0].pointer !== source.pointer)
      continue;
    const node = candidates[0];
    result.set(source.key, {
      node,
      promotion: factor.nodes.find(
        (candidate) =>
          candidate.origin === "boolean-score" &&
          candidate.pointer === source.pointer,
      ),
      notExecuted: factor.source === "execution-plan" && !node.planned,
    });
  }
  return result;
};
