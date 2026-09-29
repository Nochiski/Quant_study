import type { FactorGraph, FactorValidationIssue } from "../../../shared/api";
import { t, tName } from "../../../shared/config";
import { nodeKinds, nodeReferenceKeys } from "./graph-transactions";
import { resolveRef, schemaFacts, type JsonSchema } from "./schema-navigator";
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

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** 노드 이름 칸. 노드 네임스페이스(`x-defines: node`)의 `<namespace>_id` 규칙(`referenceCandidates`)이다. */
const NODE_NAME_KEY = "node_id";

type NodeSlot = { key: string; label: string };

/**
 * 노드 kind → 입력 칸과 설정 칸(#354). kind·칸 표를 손으로 적지 않고 runtime schema 의 노드 분기를 읽는다(정본
 * 대장 "runtime schema 노드의 필드 표시 사실"). 입력 칸은 `x-reference: node`(`nodeReferenceKeys`), 설정 칸은
 * 종류(`const`)·이름·연산(`x-operator`)·입력을 뺀 나머지이고, 칸 이름은 `x-description-key` 다. 스키마가 모르는
 * kind 는 칸이 없다 — 그 노드는 backend 검증 진단이 먼저 알린다.
 */
const nodeSlots = (
  schema: JsonSchema,
  factorPointer: string,
): ReadonlyMap<string, { inputs: NodeSlot[]; settings: NodeSlot[] }> =>
  new Map(
    nodeKinds(schema, undefined, factorPointer).map(([kind, branch]) => {
      const inputKeys = new Set(nodeReferenceKeys(schema, branch));
      const properties = isRecord(branch.properties) ? branch.properties : {};
      const inputs: NodeSlot[] = [];
      const settings: NodeSlot[] = [];
      for (const [key, candidate] of Object.entries(properties)) {
        const property = isRecord(candidate)
          ? resolveRef(schema, candidate)
          : null;
        if (property === null) continue;
        const facts = schemaFacts(property);
        const slot = { key, label: tName(facts.descriptionKey) ?? key };
        if (inputKeys.has(key)) inputs.push(slot);
        else if (
          !facts.hasConst &&
          facts.operatorKeys === null &&
          key !== NODE_NAME_KEY
        )
          settings.push(slot);
      }
      return [kind, { inputs, settings }] as const;
    }),
  );

const authoredOperation = (node: FactorNode): string =>
  "operator" in node ? `${node.kind}.${node.operator}` : node.kind;

const projectFactor = (
  factor: PlannedFactor,
  schema: JsonSchema,
): GraphFactorProjection => {
  const slotsByKind = nodeSlots(schema, `/factors/${factor.factorIndex}`);
  const valueOf = (node: FactorNode, key: string): unknown =>
    (node as unknown as Record<string, unknown>)[key];
  const authoredInputs = (
    node: FactorNode,
  ): Array<{ nodeId: string; role: string }> =>
    (slotsByKind.get(node.kind)?.inputs ?? []).map(({ key, label }) => {
      const nodeId = valueOf(node, key);
      return { nodeId: typeof nodeId === "string" ? nodeId : "", role: label };
    });
  // 값을 적지 않은 선택 칸(`periods: null` 등)은 보이지 않는다.
  const nodeDetails = (node: FactorNode | undefined): GraphNodeDetail[] =>
    node === undefined
      ? []
      : (slotsByKind.get(node.kind)?.settings ?? []).flatMap(
          ({ key, label }) => {
            const value = valueOf(node, key);
            return value === null || value === undefined
              ? []
              : [{ label, value: String(value) }];
          },
        );
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
    authoredInputs(node).filter((input) => shown(input.nodeId));

  const projectPlannedNode = (
    step: NonNullable<typeof plan>["steps"][number],
  ): GraphNodeProjection => {
    const authored = authoredById.get(step.node_id);
    const authoredInputPorts =
      authored === undefined ? [] : authoredInputs(authored);
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
      details: origin === "boolean-score" ? [] : nodeDetails(authored),
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
  return {
    status: "ready",
    expectedRegistryVersion: state.expectedRegistryVersion,
    expectedDataSnapshotId: state.expectedDataSnapshotId,
    factors: state.factors.map((factor) => projectFactor(factor, schema)),
  };
};
