import type {
  StrategyDebuggerContext,
  StrategyDebuggerUnavailableReason,
} from "../../../features/debug-strategy";
import type { RunEnvironment } from "../../../shared/api";
import {
  compiledNodeOrigin,
  currentCompile,
  decideBacktestSource,
  documentOutputNodeId,
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
  environment: RunEnvironment | null,
): StrategyDebuggerAvailability => {
  const compiled = currentCompile(document);
  if (compiled === null) return { context: null, reason: "document" };
  if ((compiled.spec.factors ?? []).length === 0)
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
      reason: source.reason === "factor-plan" ? "execution-plan" : "document",
    };

  const factors = plans.factors.flatMap((factor) => {
    const plan = factor.explanation.plan;
    if (!factor.explanation.validation.valid || plan === null) return [];
    // 디버거는 사용자가 적은 노드만 추적한다. compile 이 붙인 승격 노드(BACKLOG-014)는 문서에 줄이
    // 없어 선택·"소스 열기"가 원래 출력과 겹치므로 숨기고, 출력 노드는 사용자가 적은 출력이다.
    const authoredSteps = plan.steps.filter(
      (step) => compiledNodeOrigin(factor, step.node_id) === "document",
    );
    const outputNodeId = documentOutputNodeId(factor);
    const nodes = authoredSteps.flatMap((step) => {
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
      nodes.length !== authoredSteps.length ||
      !nodes.some((node) => node.nodeId === outputNodeId)
    )
      return [];
    return [
      {
        factorId: factor.factorId,
        label: factor.label,
        pointer: factorGraphPointer(factor.factorIndex),
        outputNodeId,
        expectedPlanHash: plan.plan_hash,
        nodes,
      },
    ];
  });
  if (factors.length !== (compiled.spec.factors ?? []).length)
    return { context: null, reason: "execution-plan" };
  // 실행 설정(기간·유니버스)은 실행 설정 패널이 owner 다(P3-02). 정해지기 전에는 추적하지 않는다.
  if (environment === null) return { context: null, reason: "environment" };

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
      environment,
      start: environment.start,
      end: environment.end,
      factors,
    },
  };
};
