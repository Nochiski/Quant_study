import type {
  StrategyDebuggerContext,
  StrategyDebuggerUnavailableReason,
} from "../../../features/debug-strategy";
import {
  currentCompile,
  decideBacktestSource,
  factorGraphPointer,
  gateBacktestSourceWithFactorPlans,
  nodePointerById,
  type DocumentState,
  type ExecutionPlansState,
} from "../../../features/edit-strategy";

export type StrategyDebuggerAvailability =
  | { context: StrategyDebuggerContext; reason: null }
  | {
      context: null;
      reason: StrategyDebuggerUnavailableReason;
    };

const unavailableReason = (
  plans: ExecutionPlansState,
): StrategyDebuggerUnavailableReason => {
  if (plans.status === "empty") return "no-factors";
  if (plans.status === "metadata-loading" || plans.status === "loading")
    return "preparing";
  return "execution-plan";
};

/**
 * Composition boundary between editor and debugger features. Dirty/current/source ownership stays
 * in edit-strategy; plan order and fingerprints stay in backend responses. This function only
 * packages those already-authoritative values for the debug feature.
 */
export const buildStrategyDebuggerAvailability = (
  document: DocumentState,
  plans: ExecutionPlansState,
): StrategyDebuggerAvailability => {
  const compiled = currentCompile(document);
  if (compiled === null) return { context: null, reason: "document" };
  if (compiled.spec.factors.factors.length === 0)
    return { context: null, reason: "no-factors" };
  if (plans.status !== "ready")
    return { context: null, reason: unavailableReason(plans) };

  const source = gateBacktestSourceWithFactorPlans(
    decideBacktestSource(document),
    plans,
  );
  if (source.kind === "blocked")
    return {
      context: null,
      reason:
        source.reason === "factor-plan" ? "execution-plan" : "document",
    };

  const factors = plans.factors.flatMap((factor) => {
    const plan = factor.explanation.plan;
    if (!factor.explanation.validation.valid || plan === null) return [];
    const nodes = plan.steps.flatMap((step) => {
      const pointer = nodePointerById(factor, step.node_id);
      return pointer === null
        ? []
        : [
            {
              nodeId: step.node_id,
              operation: step.operation,
              pointer,
            },
          ];
    });
    if (
      !nodes.some((node) => node.nodeId === factor.request.graph.output_node_id)
    )
      return [];
    return [
      {
        factorId: factor.factorId,
        label: factor.label,
        pointer: factorGraphPointer(factor.factorIndex),
        outputNodeId: factor.request.graph.output_node_id,
        expectedPlanHash: plan.plan_hash,
        nodes,
      },
    ];
  });
  if (factors.length !== compiled.spec.factors.factors.length)
    return { context: null, reason: "execution-plan" };

  return {
    reason: null,
    context: {
      documentEpoch: document.documentEpoch,
      sourceVersion: document.sourceVersion,
      strategySource:
        source.kind === "saved_revision" ? source.reference : source.draft,
      specHash: compiled.specHash,
      expectedSnapshotId: plans.expectedDataSnapshotId,
      expectedRegistryVersion: plans.expectedRegistryVersion,
      start: compiled.spec.data.start,
      end: compiled.spec.data.end,
      factors,
    },
  };
};
