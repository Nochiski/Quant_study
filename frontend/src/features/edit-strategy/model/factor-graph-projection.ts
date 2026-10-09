import type { FactorGraph, FactorValidationIssue } from "../../../shared/api";
import { t, tName } from "../../../shared/config";
import {
  nodeSlotsByKind,
  settingShown,
  type NodeSlot,
} from "./graph-transactions";
import type { JsonSchema } from "./schema-navigator";
import {
  compiledNodeOrigin,
  nodePointerById,
  type ExecutionPlansState,
  type PlannedFactor,
} from "./use-execution-plans";

type FactorNode = FactorGraph["nodes"][number];

/** 노드 설정 칸 하나. `label` 은 스키마 설명 키의 이름이다. */
export type GraphNodeDetail = {
  label: string;
  value: string;
};

export type GraphInputProjection = {
  nodeId: string;
  /** 입력 칸의 화면 이름(스키마 설명 키). 문서 칸을 짚을 수 없으면 "N번째 입력"이다. */
  role: string;
  pointer: string | null;
  outputType: string | null;
  outputUnit: string | null;
};

export type GraphNodeProjection = {
  sequence: number | null;
  planned: boolean;
  /**
   * `boolean-score`는 문서에 없고 compile 이 붙인 출력 노드다(참/거짓을 1/0 점수로, P2-07). 화면은
   * node_id 대신 사람 말로 부르고 `pointer`는 원래 출력 노드를 가리킨다. 거기 딸린 상수(`support`)는
   * 투영에 넣지 않는다(BACKLOG-014).
   */
  origin: "document" | "boolean-score";
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

type SlotLabel = { key: string; label: string; defaultValue: unknown };
type SlotLabels = ReadonlyMap<
  string,
  { inputs: SlotLabel[]; settings: SlotLabel[] }
>;

/**
 * 노드 kind → 입력 칸과 설정 칸의 이름(#354). 칸은 `nodeSlotsByKind`, 칸 이름은 `x-description-key` 다. 스키마가
 * 모르는 kind 는 칸이 없다 — 그 노드는 backend 검증 진단이 먼저 알린다.
 */
const slotLabels = (schema: JsonSchema): SlotLabels => {
  const labelled = (slots: readonly NodeSlot[]): SlotLabel[] =>
    slots.map(({ key, facts }) => ({
      key,
      label: tName(facts.descriptionKey) ?? key,
      defaultValue: facts.defaultValue,
    }));
  return new Map(
    [...nodeSlotsByKind(schema)].map(([kind, { inputs, settings }]) => [
      kind,
      { inputs: labelled(inputs), settings: labelled(settings) },
    ]),
  );
};

const valueOf = (node: FactorNode, key: string): unknown =>
  (node as unknown as Record<string, unknown>)[key];

/** 문서 노드의 입력 칸(칸 순서)과 그 칸이 가리키는 노드. */
const authoredInputs = (
  labels: SlotLabels,
  node: FactorNode,
): Array<{ nodeId: string; role: string }> =>
  (labels.get(node.kind)?.inputs ?? []).map(({ key, label }) => {
    const nodeId = valueOf(node, key);
    return { nodeId: typeof nodeId === "string" ? nodeId : "", role: label };
  });

/** 노드의 설정 칸 중 보일 값(`settingShown`: 값이 있고 스키마 기본값과 다른 것). */
const nodeDetails = (
  labels: SlotLabels,
  node: FactorNode | undefined,
): GraphNodeDetail[] =>
  node === undefined
    ? []
    : (labels.get(node.kind)?.settings ?? []).flatMap(
        ({ key, label, defaultValue }) => {
          const value = valueOf(node, key);
          return settingShown(value, defaultValue)
            ? [{ label, value: String(value) }]
            : [];
        },
      );

const authoredOperation = (node: FactorNode): string =>
  "operator" in node ? `${node.kind}.${node.operator}` : node.kind;

const projectFactor = (
  factor: PlannedFactor,
  labels: SlotLabels,
): GraphFactorProjection => {
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

  const shown = (nodeId: string): boolean =>
    compiledNodeOrigin(factor, nodeId) !== "support";
  const shownInputs = (node: FactorNode) =>
    authoredInputs(labels, node).filter((input) => shown(input.nodeId));

  const projectPlannedNode = (
    step: NonNullable<typeof plan>["steps"][number],
  ): GraphNodeProjection => {
    const authored = authoredById.get(step.node_id);
    const authoredInputPorts =
      authored === undefined ? [] : authoredInputs(labels, authored);
    const origin = compiledNodeOrigin(factor, step.node_id);
    const inputs = step.input_node_ids.flatMap((nodeId, index) => {
      if (!shown(nodeId)) return [];
      const inputStep = planStepById.get(nodeId);
      const inputContract = contractById.get(nodeId);
      const positionalPort = authoredInputPorts[index];
      const uniqueMatchingPorts = authoredInputPorts.filter(
        (port) => port.nodeId === nodeId,
      );
      return [
        {
          nodeId,
          role:
            positionalPort?.nodeId === nodeId
              ? positionalPort.role
              : uniqueMatchingPorts.length === 1
                ? uniqueMatchingPorts[0].role
                : t("graph.inputOrdinal").replace("{index}", String(index + 1)),
          pointer: nodePointerById(factor, nodeId),
          outputType:
            inputStep?.output_type ?? inputContract?.value_type ?? null,
          outputUnit: inputStep?.output_unit ?? inputContract?.unit ?? null,
        },
      ];
    });
    return {
      sequence: step.sequence,
      planned: true,
      origin: origin === "boolean-score" ? "boolean-score" : "document",
      nodeId: step.node_id,
      kind: authored?.kind ?? "unknown",
      operation: step.operation,
      pointer: nodePointerById(factor, step.node_id),
      inputs,
      outputType: step.output_type,
      outputUnit: step.output_unit,
      minimumHistorySessions: step.minimum_history_sessions,
      isOutput: step.node_id === graph.output_node_id,
      // 붙인 조건 노드의 파라미터는 사용자가 쓴 값이 아니다(참 1 / 거짓 0 고정).
      details: origin === "boolean-score" ? [] : nodeDetails(labels, authored),
      issues: issuesById.get(step.node_id) ?? [],
    };
  };

  const plannedSteps = (plan?.steps ?? []).filter((step) =>
    shown(step.node_id),
  );
  const plannedNodes = plannedSteps.map(projectPlannedNode);
  const plannedNodeIds = new Set(
    (plan?.steps ?? []).map((step) => step.node_id),
  );
  const unplannedNodes = graph.nodes
    .filter(
      (node) => !plannedNodeIds.has(node.node_id) && shown(node.node_id),
    )
    .map((node): GraphNodeProjection => {
      const contract = contractById.get(node.node_id);
      const origin = compiledNodeOrigin(factor, node.node_id);
      return {
        sequence: null,
        planned: false,
        origin: origin === "boolean-score" ? "boolean-score" : "document",
        nodeId: node.node_id,
        kind: node.kind,
        operation: authoredOperation(node),
        pointer: nodePointerById(factor, node.node_id),
        inputs: shownInputs(node).map((input) => {
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
        details: nodeDetails(labels, node),
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
 * authored order and use only backend validation contracts/issues. 입력·설정 칸과 그 이름은 runtime
 * schema 에서 읽는다(#354). 실행 계획은 스키마가 있어야 준비되므로(`prepareExecutionPlans`) 스키마가 아직
 * 없으면 메타데이터를 기다린다.
 */
export const projectFactorGraphs = (
  state: ExecutionPlansState,
  schema: JsonSchema | null,
): FactorGraphProjection => {
  if (state.status !== "ready") return state;
  if (schema === null) return { status: "metadata-loading" };
  const labels = slotLabels(schema);
  return {
    status: "ready",
    expectedRegistryVersion: state.expectedRegistryVersion,
    expectedDataSnapshotId: state.expectedDataSnapshotId,
    factors: state.factors.map((factor) => projectFactor(factor, labels)),
  };
};
