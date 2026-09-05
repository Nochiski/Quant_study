import type { FactorGraph } from "../../../entities/factor";

export const quickTransforms = [
  "lag",
  "rank",
  "zscore",
  "winsorize",
  "neutralize",
] as const;

export type QuickTransform = (typeof quickTransforms)[number];

const isQuickTransform = (operator: string): operator is QuickTransform =>
  quickTransforms.some((transform) => transform === operator);

type ExpressionNode = FactorGraph["nodes"][number];

const uniqueNodeId = (
  graph: FactorGraph,
  transform: QuickTransform,
): string => {
  const used = new Set(graph.nodes.map((node) => node.node_id));
  let suffix = graph.nodes.length + 1;
  while (used.has(`${transform}_${suffix}`)) suffix += 1;
  return `${transform}_${suffix}`;
};

export const appendFactorTransform = (
  graph: FactorGraph,
  transform: QuickTransform,
): FactorGraph => {
  const nodeId = uniqueNodeId(graph, transform);
  const node: ExpressionNode =
    transform === "lag"
      ? {
          kind: "unary",
          node_id: nodeId,
          operator: "lag",
          input_node_id: graph.output_node_id,
          periods: 1,
        }
      : transform === "neutralize"
        ? {
            kind: "unary",
            node_id: nodeId,
            operator: "neutralize",
            input_node_id: graph.output_node_id,
          }
        : {
            kind: "cross_sectional",
            node_id: nodeId,
            operator: transform,
            input_node_id: graph.output_node_id,
            ...(transform === "winsorize"
              ? { lower_quantile: 0.01, upper_quantile: 0.99 }
              : {}),
          };
  return {
    ...graph,
    nodes: [...graph.nodes, node],
    output_node_id: nodeId,
  };
};

const inputOfTransform = (node: ExpressionNode): string | null => {
  if (node.kind === "unary" && isQuickTransform(node.operator)) {
    return node.input_node_id;
  }
  if (node.kind === "cross_sectional") return node.input_node_id;
  return null;
};

export const removeLastFactorTransform = (graph: FactorGraph): FactorGraph => {
  const output = graph.nodes.find(
    (node) => node.node_id === graph.output_node_id,
  );
  if (output === undefined) return graph;
  const previous = inputOfTransform(output);
  if (previous === null) return graph;
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => node.node_id !== output.node_id),
    output_node_id: previous,
  };
};

export const factorTransformChain = (graph: FactorGraph): QuickTransform[] => {
  const nodes = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const chain: QuickTransform[] = [];
  let current = nodes.get(graph.output_node_id);
  while (current !== undefined) {
    if (current.kind === "unary" && isQuickTransform(current.operator)) {
      chain.unshift(current.operator);
      current = nodes.get(current.input_node_id);
    } else if (current.kind === "cross_sectional") {
      chain.unshift(current.operator);
      current = nodes.get(current.input_node_id);
    } else {
      break;
    }
  }
  return chain;
};

export const nodeInputIds = (node: ExpressionNode): string[] => {
  if (
    node.kind === "unary" ||
    node.kind === "time_series" ||
    node.kind === "cross_sectional" ||
    node.kind === "group"
  ) {
    return [node.input_node_id];
  }
  if (node.kind === "binary" || node.kind === "comparison") {
    return [node.left_node_id, node.right_node_id];
  }
  if (node.kind === "conditional") {
    return [node.predicate_node_id, node.true_node_id, node.false_node_id];
  }
  return [];
};
