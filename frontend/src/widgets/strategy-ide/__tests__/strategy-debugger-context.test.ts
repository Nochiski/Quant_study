import { describe, expect, it } from "vitest";

import {
  initialDocumentState,
  type DocumentState,
  type ExecutionPlansState,
} from "../../../features/edit-strategy";
import type { StrategySpec } from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { buildStrategyDebuggerAvailability } from "../model/strategy-debugger-context";

const FIXTURE = JSON.parse(
  readBackendFixture("strategy_documents/quality_momentum.legacy.json"),
) as StrategySpec;
const SPEC: StrategySpec = {
  ...FIXTURE,
  factors: [FIXTURE.factors![0]!],
};
const FACTOR = SPEC.factors![0]!;

const documentState = (): DocumentState => ({
  ...initialDocumentState("yaml", "current source"),
  documentEpoch: 3,
  sourceVersion: 7,
  parsedVersion: 7,
  compiledVersion: 7,
  dirty: true,
  phase: "semantically-valid",
  compiled: {
    spec: SPEC,
    canonicalJson: JSON.stringify(SPEC),
    specHash: "spec-hash",
    schemaVersion: "1.1",
    sourceHash: "source-hash",
    diagnostics: [],
  },
});

const plans = (): ExecutionPlansState => ({
  status: "ready",
  expectedRegistryVersion: "registry-v1",
  expectedDataSnapshotId: "snapshot-v1",
  factors: [
    {
      factorIndex: 0,
      factorId: FACTOR.factor_id,
      label: FACTOR.label,
      request: {
        graph: FACTOR.graph,
        parameter_ids: [],
      },
      document: null,
      explanation: {
        registry_version: "registry-v1",
        data_snapshot_id: "snapshot-v1",
        validation: {
          valid: true,
          issues: [],
          node_contracts: [],
          minimum_history_sessions: 252,
          required_field_ids: ["price.close"],
        },
        plan: {
          missing_policy: "drop",
          graph_hash: "graph-hash",
          plan_hash: "plan-hash",
          registry_version: "registry-v1",
          output_node_id: "mom_252",
          steps: [
            {
              sequence: 1,
              node_id: "close",
              operation: "field",
              input_node_ids: [],
              output_type: "numeric_series",
              output_unit: "KRW",
              minimum_history_sessions: 1,
            },
            {
              sequence: 2,
              node_id: "mom_252",
              operation: "time_series.momentum",
              input_node_ids: ["close"],
              output_type: "numeric_series",
              output_unit: "ratio",
              minimum_history_sessions: 252,
            },
          ],
          required_field_ids: ["price.close"],
          minimum_history_sessions: 252,
          as_of_policy: "available_date_lte_as_of",
        },
        narrative: [],
        synthesized_nodes: [],
      },
    },
  ],
});

describe("Strategy IDE debugger composition", () => {
  it("packages the editor-owned inline source with backend-owned plan identities", () => {
    expect(buildStrategyDebuggerAvailability(documentState(), plans())).toEqual(
      {
        reason: null,
        context: {
          documentEpoch: 3,
          sourceVersion: 7,
          strategySource: {
            kind: "inline_draft",
            spec: SPEC,
            source_hash: "source-hash",
          },
          specHash: "spec-hash",
          expectedSnapshotId: "snapshot-v1",
          expectedRegistryVersion: "registry-v1",
          start: null,
          end: null,
          factors: [
            {
              factorId: "momentum",
              label: FACTOR.label,
              pointer: "/factors/0/graph",
              outputNodeId: "mom_252",
              expectedPlanHash: "plan-hash",
              nodes: [
                {
                  nodeId: "close",
                  operation: "field",
                  pointer: "/factors/0/graph/nodes/0",
                },
                {
                  nodeId: "mom_252",
                  operation: "time_series.momentum",
                  pointer: "/factors/0/graph/nodes/1",
                },
              ],
            },
          ],
        },
      },
    );
  });

  it("uses an immutable saved reference only for the clean hash-matching base", () => {
    const state = {
      ...documentState(),
      dirty: false,
      strategyId: "strategy-1",
      baseRevision: 5,
      baseSpecHash: "spec-hash",
    };
    expect(
      buildStrategyDebuggerAvailability(state, plans()).context?.strategySource,
    ).toEqual({
      kind: "saved_revision",
      strategy_id: "strategy-1",
      revision: 5,
      expected_spec_hash: "spec-hash",
    });
  });

  it("traces only the authored nodes when compile appended boolean promotion nodes (BACKLOG-014)", () => {
    // compile 이 그래프 끝에 붙인 승격 노드는 문서에 줄이 없다. 디버거는 사용자가 적은 노드만 싣고,
    // 출력 노드는 사용자가 적은 출력이다(붙인 조건 노드가 아니다).
    const promoted = plans();
    if (promoted.status !== "ready") throw new Error("test setup");
    const factor = promoted.factors[0]!;
    factor.request = {
      ...factor.request,
      graph: {
        nodes: [
          ...factor.request.graph.nodes,
          { kind: "constant", node_id: "__promote_momentum_one", value: 1 },
          { kind: "constant", node_id: "__promote_momentum_zero", value: 0 },
          {
            kind: "conditional",
            node_id: "__promote_momentum",
            predicate_node_id: "mom_252",
            true_node_id: "__promote_momentum_one",
            false_node_id: "__promote_momentum_zero",
          },
        ],
        output_node_id: "__promote_momentum",
      },
    };
    factor.document = {
      nodeIds: factor.request.graph.nodes.slice(0, 2).map((node) => node.node_id),
    };
    // backend 가 실행 계획 설명에 싣는 붙인 노드 표식(Phase 2 감사 #13).
    factor.explanation.synthesized_nodes = [
      { node_id: "__promote_momentum_one", origin: "promotion", role: "promotion_constant" },
      { node_id: "__promote_momentum_zero", origin: "promotion", role: "promotion_constant" },
      { node_id: "__promote_momentum", origin: "promotion", role: "promoted_output" },
    ];
    const steps = factor.explanation.plan!.steps;
    factor.explanation.plan!.steps = [
      ...steps,
      ...["__promote_momentum_one", "__promote_momentum_zero"].map(
        (nodeId, index) => ({
          ...steps[0]!,
          sequence: steps.length + index + 1,
          node_id: nodeId,
          operation: "constant",
        }),
      ),
      {
        ...steps[1]!,
        sequence: steps.length + 3,
        node_id: "__promote_momentum",
        operation: "conditional",
        input_node_ids: [
          "mom_252",
          "__promote_momentum_one",
          "__promote_momentum_zero",
        ],
      },
    ];

    const traced =
      buildStrategyDebuggerAvailability(documentState(), promoted).context
        ?.factors[0];
    expect(traced?.outputNodeId).toBe("mom_252");
    expect(traced?.nodes.map((node) => [node.nodeId, node.pointer])).toEqual([
      ["close", "/factors/0/graph/nodes/0"],
      ["mom_252", "/factors/0/graph/nodes/1"],
    ]);
  });

  it("fails closed for a stale document or an unpinned execution plan", () => {
    expect(
      buildStrategyDebuggerAvailability(
        { ...documentState(), sourceVersion: 8 },
        plans(),
      ),
    ).toEqual({ context: null, reason: "document" });
    expect(
      buildStrategyDebuggerAvailability(documentState(), { status: "loading" }),
    ).toEqual({ context: null, reason: "preparing" });
    const invalidPlans = plans();
    if (invalidPlans.status !== "ready") throw new Error("test setup");
    invalidPlans.factors[0]!.explanation.plan = null;
    expect(
      buildStrategyDebuggerAvailability(documentState(), invalidPlans),
    ).toEqual({ context: null, reason: "execution-plan" });

    const incompletePlans = plans();
    if (incompletePlans.status !== "ready") throw new Error("test setup");
    incompletePlans.factors[0]!.explanation.plan!.steps = [
      {
        ...incompletePlans.factors[0]!.explanation.plan!.steps[0]!,
        node_id: "node-that-is-not-in-the-graph",
      },
      incompletePlans.factors[0]!.explanation.plan!.steps[1]!,
    ];
    expect(
      buildStrategyDebuggerAvailability(documentState(), incompletePlans),
    ).toEqual({ context: null, reason: "execution-plan" });
  });
});
