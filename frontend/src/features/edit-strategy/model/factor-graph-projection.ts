import type { FactorGraph, FactorValidationIssue } from "../../../shared/api";
import {
  nodePointerById,
  type ExecutionPlansState,
  type PlannedFactor,
} from "./use-execution-plans";

type FactorNode = FactorGraph["nodes"][number];

export type GraphNodeDetail = {
  label: string;
  value: string;
};

export type GraphInputProjection = {
  nodeId: string;
  role: string;
  pointer: string | null;
  outputType: string | null;
  outputUnit: string | null;
};

export type GraphNodeProjection = {
  sequence: number | null;
  planned: boolean;
  nodeId: string;
  kind: FactorNode["kind"] | "unknown";
  operation: string;
  pointer: string | null;
  inputs: GraphInputProjection[];
  outputType: string | null;
  outputUnit: string | null;
  minimumHistorySessions: number | null;
  isOutput: boolean;
  details: GraphNodeDetail[];
  issues: FactorValidationIssue[];
};

export type GraphFactorProjection = {
  factorIndex: number;
  factorId: string;
  label: string;
  valid: boolean;
  source: "execution-plan" | "validation-only";
  nodes: GraphNodeProjection[];
  issues: FactorValidationIssue[];
  minimumHistorySessions: number;
  graphHash: string | null;
  planHash: string | null;
};

export type FactorGraphProjection =
  | Exclude<ExecutionPlansState, { status: "ready" }>
  | {
      status: "ready";
      expectedRegistryVersion: string;
      expectedDataSnapshotId: string;
      factors: GraphFactorProjection[];
    };

const authoredInputs = (
  node: FactorNode,
): Array<{ nodeId: string; role: string }> => {
  switch (node.kind) {
    case "unary":
    case "time_series":
    case "cross_sectional":
    case "group":
      return [{ nodeId: node.input_node_id, role: "input" }];
    case "binary":
    case "comparison":
      return [
        { nodeId: node.left_node_id, role: "left" },
        { nodeId: node.right_node_id, role: "right" },
      ];
    case "conditional":
      return [
        { nodeId: node.predicate_node_id, role: "predicate" },
        { nodeId: node.true_node_id, role: "true" },
        { nodeId: node.false_node_id, role: "false" },
      ];
    default:
      return [];
  }
};

const nodeDetails = (node: FactorNode | undefined): GraphNodeDetail[] => {
  if (node === undefined) return [];
  switch (node.kind) {
    case "field":
      return [{ label: "field_id", value: node.field_id }];
    case "constant":
      return [{ label: "value", value: String(node.value) }];
    case "parameter":
      return [{ label: "parameter_id", value: node.parameter_id }];
    case "unary":
      return node.periods == null
        ? []
        : [{ label: "periods", value: String(node.periods) }];
    case "time_series":
      return [
        { label: "window", value: String(node.window) },
        ...(node.lag === undefined
          ? []
          : [{ label: "lag", value: String(node.lag) }]),
      ];
    case "cross_sectional":
      return [
        ...(node.lower_quantile === undefined
          ? []
          : [
              {
                label: "lower_quantile",
                value: String(node.lower_quantile),
              },
            ]),
        ...(node.upper_quantile === undefined
          ? []
          : [
              {
                label: "upper_quantile",
                value: String(node.upper_quantile),
              },
            ]),
      ];
    case "group":
      return [{ label: "group_field_id", value: node.group_field_id }];
    case "saved_factor":
      return [{ label: "factor_id", value: node.factor_id }];
    case "saved_subgraph":
      return [{ label: "subgraph_id", value: node.subgraph_id }];
    default:
      return [];
  }
};

const authoredOperation = (node: FactorNode): string =>
  "operator" in node ? `${node.kind}.${node.operator}` : node.kind;

const projectFactor = (factor: PlannedFactor): GraphFactorProjection => {
  const graph = factor.request.graph;
  const authoredById = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const contractById = new Map(
    factor.explanation.validation.node_contracts.map((contract) => [
      contract.node_id,
      contract,
    ]),
  );
  const issuesById = new Map<string, FactorValidationIssue[]>();
  for (const issue of factor.explanation.validation.issues) {
    if (issue.node_id === null) continue;
    const current = issuesById.get(issue.node_id) ?? [];
    current.push(issue);
    issuesById.set(issue.node_id, current);
  }
  const plan = factor.explanation.plan;
  const planStepById = new Map(
    (plan?.steps ?? []).map((step) => [step.node_id, step]),
  );

  const projectPlannedNode = (
    step: NonNullable<typeof plan>["steps"][number],
  ): GraphNodeProjection => {
    const authored = authoredById.get(step.node_id);
    const authoredInputPorts =
      authored === undefined ? [] : authoredInputs(authored);
    const inputs = step.input_node_ids.map((nodeId, index) => {
      const inputStep = planStepById.get(nodeId);
      const inputContract = contractById.get(nodeId);
      const positionalPort = authoredInputPorts[index];
      const uniqueMatchingPorts = authoredInputPorts.filter(
        (port) => port.nodeId === nodeId,
      );
      return {
        nodeId,
        role:
          positionalPort?.nodeId === nodeId
            ? positionalPort.role
            : uniqueMatchingPorts.length === 1
              ? uniqueMatchingPorts[0].role
              : `input ${index + 1}`,
        pointer: nodePointerById(factor, nodeId),
        outputType: inputStep?.output_type ?? inputContract?.value_type ?? null,
        outputUnit: inputStep?.output_unit ?? inputContract?.unit ?? null,
      };
    });
    return {
      sequence: step.sequence,
      planned: true,
      nodeId: step.node_id,
      kind: authored?.kind ?? "unknown",
      operation: step.operation,
      pointer: nodePointerById(factor, step.node_id),
      inputs,
      outputType: step.output_type,
      outputUnit: step.output_unit,
      minimumHistorySessions: step.minimum_history_sessions,
      isOutput: step.node_id === graph.output_node_id,
      details: nodeDetails(authored),
      issues: issuesById.get(step.node_id) ?? [],
    };
  };

  const plannedNodes = (plan?.steps ?? []).map(projectPlannedNode);
  const plannedNodeIds = new Set(plannedNodes.map((node) => node.nodeId));
  const unplannedNodes = graph.nodes
    .filter((node) => !plannedNodeIds.has(node.node_id))
    .map((node): GraphNodeProjection => {
      const contract = contractById.get(node.node_id);
      return {
        sequence: null,
        planned: false,
        nodeId: node.node_id,
        kind: node.kind,
        operation: authoredOperation(node),
        pointer: nodePointerById(factor, node.node_id),
        inputs: authoredInputs(node).map((input) => {
          const inputContract = contractById.get(input.nodeId);
          return {
            nodeId: input.nodeId,
            role: input.role,
            pointer: nodePointerById(factor, input.nodeId),
            outputType: inputContract?.value_type ?? null,
            outputUnit: inputContract?.unit ?? null,
          };
        }),
        outputType: contract?.value_type ?? null,
        outputUnit: contract?.unit ?? null,
        minimumHistorySessions: contract?.minimum_history_sessions ?? null,
        isOutput: node.node_id === graph.output_node_id,
        details: nodeDetails(node),
        issues: issuesById.get(node.node_id) ?? [],
      };
    });
  const nodes = [...plannedNodes, ...unplannedNodes];

  return {
    factorIndex: factor.factorIndex,
    factorId: factor.factorId,
    label: factor.label,
    valid: factor.explanation.validation.valid && plan !== null,
    source: plan === null ? "validation-only" : "execution-plan",
    nodes,
    issues: factor.explanation.validation.issues,
    minimumHistorySessions:
      plan?.minimum_history_sessions ??
      factor.explanation.validation.minimum_history_sessions,
    graphHash: plan?.graph_hash ?? null,
    planHash: plan?.plan_hash ?? null,
  };
};

/**
 * Pure read-only projection. It never validates, sorts or infers contracts: executable nodes
 * preserve backend plan order/contracts. Authored nodes outside that plan remain visible in
 * authored order and use only backend validation contracts/issues.
 */
export const projectFactorGraphs = (
  state: ExecutionPlansState,
): FactorGraphProjection => {
  if (state.status !== "ready") return state;
  return {
    status: "ready",
    expectedRegistryVersion: state.expectedRegistryVersion,
    expectedDataSnapshotId: state.expectedDataSnapshotId,
    factors: state.factors.map(projectFactor),
  };
};
