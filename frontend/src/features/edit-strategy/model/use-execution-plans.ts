import { useQueries } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  strategyWorkbenchApi,
  type FactorExplanation,
  type FactorGraphRequest,
  type StrategySpec,
} from "../../../shared/api";
import { valueAtPointer } from "../../../shared/lib/yaml12";
import {
  isSchemaContractCompatible,
  type ContractInspectorSource,
} from "./contract-inspector";
import { currentSpec, isSpecStale, type DocumentState } from "./document-state";

export type FactorPlanRequest = {
  factorIndex: number;
  factorId: string;
  label: string;
  request: FactorGraphRequest;
  /**
   * 사용자가 문서에 적은 이 팩터 그래프의 노드 id(문서 순서). "소스 열기" pointer 의 노드 index 를
   * 문서에서 찾는 데 쓴다. 문서를 읽지 못하면 null 이고, 그때는 컴파일된 그래프 순서를 쓴다.
   */
  document: { nodeIds: readonly string[] } | null;
};

export type PlannedFactor = FactorPlanRequest & {
  explanation: FactorExplanation;
};

export type ExecutionPlansState =
  | {
      status: "blocked";
      reason: "empty" | "invalid" | "pending" | "stale";
    }
  | { status: "empty" }
  | { status: "metadata-loading" }
  | { status: "metadata-unavailable" }
  | {
      status: "incompatible";
      resource: "schema-contract" | "dataset" | "factor-registry";
      expected: string;
      actual: string | null;
    }
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "ready";
      expectedRegistryVersion: string;
      expectedDataSnapshotId: string;
      factors: PlannedFactor[];
    };

type PreparedPlans =
  | Exclude<ExecutionPlansState, { status: "loading" | "error" | "ready" }>
  | {
      status: "prepared";
      expectedRegistryVersion: string;
      expectedDataSnapshotId: string;
      requests: FactorPlanRequest[];
    };

const blockedReason = (
  state: DocumentState,
): Extract<ExecutionPlansState, { status: "blocked" }>["reason"] => {
  if (state.source.trim() === "") return "empty";
  if (isSpecStale(state)) return "stale";
  if (
    state.composing ||
    state.parsedVersion !== state.sourceVersion ||
    state.compiledVersion !== state.sourceVersion
  )
    return "pending";
  return "invalid";
};

/**
 * Turns one current backend-compiled StrategySpec into factor explain requests. Catalogs pin
 * the expected dataset and registry generations; the backend resolves field metadata and owns
 * plan order, contracts, history and hashes.
 */
export const prepareExecutionPlans = (
  state: DocumentState,
  source: ContractInspectorSource,
): PreparedPlans => {
  const spec = currentSpec(state);
  if (spec === null) return { status: "blocked", reason: blockedReason(state) };
  // schema 1.2에서 `factors`는 생략 가능하다(최상위 필수 키는 두 개뿐).
  const factors = spec.factors ?? [];
  if (factors.length === 0) return { status: "empty" };
  if (source.schema === null || source.contract === null) {
    return source.state.schema === "loading" ||
      source.state.contract === "loading"
      ? { status: "metadata-loading" }
      : { status: "metadata-unavailable" };
  }
  if (!isSchemaContractCompatible(source.schema, source.contract)) {
    return {
      status: "incompatible",
      resource: "schema-contract",
      expected: `${source.schema.schema_version}:${source.schema.schema_hash}`,
      actual: `${source.contract.contract.schema_version}:${source.contract.contract.schema_hash}`,
    };
  }
  const runtimeSchemaVersion = source.schema.schema_version;
  const compiledSchemaVersion = state.compiled?.schemaVersion ?? null;
  if (
    compiledSchemaVersion !== runtimeSchemaVersion ||
    spec.identity.schema_version !== runtimeSchemaVersion
  ) {
    return {
      status: "incompatible",
      resource: "schema-contract",
      expected: runtimeSchemaVersion,
      actual: `${compiledSchemaVersion ?? "missing"}:${spec.identity.schema_version}`,
    };
  }
  if (source.equityCatalog === null || source.factorCatalog === null) {
    return source.state.equityCatalog === "loading" ||
      source.state.factorCatalog === "loading"
      ? { status: "metadata-loading" }
      : { status: "metadata-unavailable" };
  }
  const contract = source.contract.contract;
  const datasetVersion = source.equityCatalog.snapshot.snapshot_id;
  if (datasetVersion !== contract.dataset_snapshot_id) {
    return {
      status: "incompatible",
      resource: "dataset",
      expected: contract.dataset_snapshot_id,
      actual: datasetVersion,
    };
  }
  const registryVersion = source.factorCatalog.registry_version;
  if (registryVersion !== contract.factor_registry_version) {
    return {
      status: "incompatible",
      resource: "factor-registry",
      expected: contract.factor_registry_version,
      actual: registryVersion,
    };
  }
  return {
    status: "prepared",
    expectedRegistryVersion: registryVersion,
    expectedDataSnapshotId: datasetVersion,
    requests: buildFactorPlanRequests(spec, documentTree(state)),
  };
};

/** 컴파일된 spec 과 같은 버전의 parse tree. 다른 버전이면 문서 노드 index 를 쓰지 않는다(null). */
const documentTree = (state: DocumentState): unknown =>
  state.parse?.status === "ok" && state.parsedVersion === state.sourceVersion
    ? state.parse.tree
    : null;

const documentGraph = (
  tree: unknown,
  factorIndex: number,
): FactorPlanRequest["document"] => {
  if (tree === null) return null;
  const pointer = factorGraphPointer(factorIndex);
  const nodes = valueAtPointer(tree, `${pointer}/nodes`);
  if (!nodes.present || !Array.isArray(nodes.value)) return null;
  const nodeIds = nodes.value.map((node: unknown) =>
    typeof node === "object" &&
    node !== null &&
    typeof (node as Record<string, unknown>).node_id === "string"
      ? ((node as Record<string, unknown>).node_id as string)
      : "",
  );
  return { nodeIds };
};

const buildFactorPlanRequests = (
  spec: StrategySpec,
  tree: unknown,
): FactorPlanRequest[] => {
  const parameterIds = (spec.parameters ?? []).map(
    (parameter) => parameter.parameter_id,
  );
  const factors = spec.factors ?? [];
  return factors.map((factor, factorIndex) => ({
    factorIndex,
    factorId: factor.factor_id,
    label: factor.label,
    request: {
      graph: factor.graph,
      parameter_ids: parameterIds,
    },
    document: documentGraph(tree, factorIndex),
  }));
};

/** schema 1.1 부터: `factors`가 루트 시퀀스이므로 팩터 그래프는 `/factors/{i}/graph`에 있다. */
export const factorGraphPointer = (factorIndex: number): string =>
  `/factors/${factorIndex}/graph`;

export const factorNodePointer = (
  factorIndex: number,
  nodeIndex: number,
): string => `${factorGraphPointer(factorIndex)}/nodes/${nodeIndex}`;

export const factorIndexAtPointer = (
  pointer: string | undefined,
): number | null => {
  const match = /^\/factors\/(0|[1-9]\d*)(?:\/|$)/.exec(pointer ?? "");
  if (match === null) return null;
  const index = Number(match[1]);
  return Number.isSafeInteger(index) ? index : null;
};

/**
 * 컴파일된 그래프 노드의 출처(BACKLOG-014). `document`는 사용자가 적은 노드다. compile 이 붙인
 * 노드는 backend 가 실행 계획 설명의 `synthesized_nodes` 표식으로 알려 준다 — 지금은 boolean 출력
 * 승격(P2-07)뿐이고, 그래프 출력이 된 조건 노드가 `boolean-score`(참/거짓을 1/0 점수로), 거기 딸린
 * 상수가 `support`다. 승격 노드 이름 규칙(접두사)의 owner 는 backend 라 frontend 에 적지 않는다
 * (Phase 2 감사 #13). 표식이 없는 id(끊긴 참조 포함)는 문서 쪽 노드로 그대로 보인다.
 */
export type CompiledNodeOrigin = "document" | "boolean-score" | "support";

export const compiledNodeOrigin = (
  factor: PlannedFactor,
  nodeId: string,
): CompiledNodeOrigin => {
  const marker = factor.explanation.synthesized_nodes.find(
    (node) => node.node_id === nodeId,
  );
  if (marker === undefined) return "document";
  return marker.role === "promoted_output" ? "boolean-score" : "support";
};

/** 사용자가 적은 출력 노드 id. 승격된 그래프면 원래 출력(붙인 조건 노드의 predicate)이다. */
export const documentOutputNodeId = (factor: PlannedFactor): string => {
  const graph = factor.request.graph;
  if (compiledNodeOrigin(factor, graph.output_node_id) !== "boolean-score")
    return graph.output_node_id;
  const output = graph.nodes.find(
    (node) => node.node_id === graph.output_node_id,
  );
  return output?.kind === "conditional"
    ? output.predicate_node_id
    : graph.output_node_id;
};

/**
 * 노드가 가리키는 문서 위치. 문서 노드는 문서 안 순서로 찾는다. `boolean-score`는 원래 출력 노드를
 * 짚고("소스 열기"가 사용자가 쓴 줄로 간다), `support`는 문서에 대응하는 줄이 없어 null 이다.
 */
export const nodePointerById = (
  factor: PlannedFactor,
  nodeId: string,
): string | null => {
  const origin = compiledNodeOrigin(factor, nodeId);
  if (origin === "support") return null;
  const target =
    origin === "boolean-score" ? documentOutputNodeId(factor) : nodeId;
  const index =
    factor.document === null
      ? factor.request.graph.nodes.findIndex((node) => node.node_id === target)
      : factor.document.nodeIds.indexOf(target);
  return index < 0 ? null : factorNodePointer(factor.factorIndex, index);
};

export const pointerSelectsNode = (
  selectedPointer: string | undefined,
  nodePointer: string,
): boolean =>
  selectedPointer === nodePointer ||
  selectedPointer?.startsWith(`${nodePointer}/`) === true;

/** Current text only: a stale last-valid spec is never sent to the explain API. */
export const useExecutionPlans = (
  state: DocumentState,
  source: ContractInspectorSource,
): ExecutionPlansState => {
  const prepared = useMemo(
    () => prepareExecutionPlans(state, source),
    [source, state],
  );
  const requests = prepared.status === "prepared" ? prepared.requests : [];
  const expectedRegistryVersion =
    prepared.status === "prepared" ? prepared.expectedRegistryVersion : null;
  const expectedDataSnapshotId =
    prepared.status === "prepared" ? prepared.expectedDataSnapshotId : null;
  const queries = useQueries({
    queries: requests.map((item) => ({
      queryKey: [
        "factor",
        "explanation",
        expectedRegistryVersion,
        expectedDataSnapshotId,
        item.request,
      ] as const,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        strategyWorkbenchApi.explainFactorGraph(item.request, signal),
      staleTime: 30_000,
    })),
  });

  return useMemo(() => {
    if (prepared.status !== "prepared") return prepared;
    const failed = queries.find((query) => query.isError);
    if (failed !== undefined) {
      return {
        status: "error",
        message:
          failed.error instanceof Error
            ? failed.error.message
            : "factor explain request failed",
      };
    }
    if (queries.some((query) => query.isPending)) return { status: "loading" };
    if (queries.some((query) => query.data === undefined))
      return { status: "loading" };
    const factors = prepared.requests.map((request, index) => ({
      ...request,
      explanation: queries[index].data!,
    }));
    const drifted = factors.find(
      (factor) =>
        factor.explanation.registry_version !==
          prepared.expectedRegistryVersion ||
        (factor.explanation.plan !== null &&
          factor.explanation.plan.registry_version !==
            prepared.expectedRegistryVersion),
    );
    if (drifted !== undefined) {
      const actualRegistryVersion =
        drifted.explanation.registry_version !==
        prepared.expectedRegistryVersion
          ? drifted.explanation.registry_version
          : (drifted.explanation.plan?.registry_version ?? null);
      return {
        status: "incompatible",
        resource: "factor-registry",
        expected: prepared.expectedRegistryVersion,
        actual: actualRegistryVersion,
      };
    }
    const datasetDrifted = factors.find(
      (factor) =>
        factor.explanation.data_snapshot_id !== prepared.expectedDataSnapshotId,
    );
    if (datasetDrifted !== undefined) {
      return {
        status: "incompatible",
        resource: "dataset",
        expected: prepared.expectedDataSnapshotId,
        actual: datasetDrifted.explanation.data_snapshot_id,
      };
    }
    return {
      status: "ready",
      expectedRegistryVersion: prepared.expectedRegistryVersion,
      expectedDataSnapshotId: prepared.expectedDataSnapshotId,
      factors,
    };
  }, [prepared, queries]);
};
