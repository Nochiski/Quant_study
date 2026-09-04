import { useQueries } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  strategyWorkbenchApi,
  type FactorExplanation,
  type FactorGraphRequest,
  type StrategySpec,
} from "../../../shared/api";
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
      status: "metadata-incomplete";
      resource: "equity-catalog";
      missingIds: string[];
    }
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
      factors: PlannedFactor[];
    };

type PreparedPlans =
  | Exclude<ExecutionPlansState, { status: "loading" | "error" | "ready" }>
  | {
      status: "prepared";
      expectedRegistryVersion: string;
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
 * Turns one current backend-compiled StrategySpec into factor explain requests. Catalog values
 * only provide backend-owned field metadata; plan order, contracts, history and hashes still
 * come exclusively from the factor explain endpoint.
 */
export const prepareExecutionPlans = (
  state: DocumentState,
  source: ContractInspectorSource,
): PreparedPlans => {
  const spec = currentSpec(state);
  if (spec === null) return { status: "blocked", reason: blockedReason(state) };
  if (spec.factors.factors.length === 0) return { status: "empty" };
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
  const availableFieldIds = new Set(
    source.equityCatalog.fields.map((field) => field.field_id),
  );
  const referencedFieldIds = new Set(
    spec.factors.factors.flatMap((factor) =>
      factor.graph.nodes.flatMap((node) => {
        if (node.kind === "field") return [node.field_id];
        if (node.kind === "group") return [node.group_field_id];
        return [];
      }),
    ),
  );
  const missingIds = [...referencedFieldIds]
    .filter((fieldId) => !availableFieldIds.has(fieldId))
    .sort();
  if (missingIds.length > 0) {
    return {
      status: "metadata-incomplete",
      resource: "equity-catalog",
      missingIds,
    };
  }
  return {
    status: "prepared",
    expectedRegistryVersion: registryVersion,
    requests: buildFactorPlanRequests(spec, source),
  };
};

const buildFactorPlanRequests = (
  spec: StrategySpec,
  source: ContractInspectorSource,
): FactorPlanRequest[] => {
  const fields =
    source.equityCatalog?.fields.map((field) => ({
      field_id: field.field_id,
      unit: field.unit,
    })) ?? [];
  const parameterIds = (spec.parameters ?? []).map(
    (parameter) => parameter.parameter_id,
  );
  const factorIds = spec.factors.factors.map((factor) => factor.factor_id);
  return spec.factors.factors.map((factor, factorIndex) => ({
    factorIndex,
    factorId: factor.factor_id,
    label: factor.label,
    request: {
      graph: factor.graph,
      fields,
      parameter_ids: parameterIds,
      factor_ids: factorIds,
      subgraph_ids: [],
    },
  }));
};

export const factorNodePointer = (
  factorIndex: number,
  nodeIndex: number,
): string => `/factors/factors/${factorIndex}/graph/nodes/${nodeIndex}`;

export const factorIndexAtPointer = (
  pointer: string | undefined,
): number | null => {
  const match = /^\/factors\/factors\/(0|[1-9]\d*)(?:\/|$)/.exec(pointer ?? "");
  if (match === null) return null;
  const index = Number(match[1]);
  return Number.isSafeInteger(index) ? index : null;
};

export const nodePointerById = (
  factor: FactorPlanRequest,
  nodeId: string,
): string | null => {
  const index = factor.request.graph.nodes.findIndex(
    (node) => node.node_id === nodeId,
  );
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
  const queries = useQueries({
    queries: requests.map((item) => ({
      queryKey: [
        "factor",
        "explanation",
        expectedRegistryVersion,
        item.request,
      ] as const,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        strategyWorkbenchApi.explainFactorGraph(item.request, signal),
      staleTime: 30_000,
    })),
  });

  return useMemo(() => {
    if (prepared.status !== "prepared") return prepared;
    if (queries.some((query) => query.isPending)) return { status: "loading" };
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
    if (queries.some((query) => query.data === undefined))
      return { status: "loading" };
    const factors = prepared.requests.map((request, index) => ({
      ...request,
      explanation: queries[index].data!,
    }));
    const drifted = factors.find(
      (factor) =>
        factor.explanation.plan !== null &&
        factor.explanation.plan.registry_version !==
          prepared.expectedRegistryVersion,
    );
    if (drifted !== undefined && drifted.explanation.plan !== null) {
      return {
        status: "incompatible",
        resource: "factor-registry",
        expected: prepared.expectedRegistryVersion,
        actual: drifted.explanation.plan.registry_version,
      };
    }
    return {
      status: "ready",
      expectedRegistryVersion: prepared.expectedRegistryVersion,
      factors,
    };
  }, [prepared, queries]);
};
