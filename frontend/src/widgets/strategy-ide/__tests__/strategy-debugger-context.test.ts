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
  factors: { factors: [FIXTURE.factors.factors[0]!] },
};
const FACTOR = SPEC.factors.factors[0]!;

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
    schemaVersion: "1.0",
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
        factor_ids: [FACTOR.factor_id],
        parameter_ids: [],
        subgraph_ids: [],
      },
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
          referenced_factor_ids: [],
          referenced_subgraph_ids: [],
          minimum_history_sessions: 252,
          missing_policy: "drop",
          as_of_policy: "available_date_lte_as_of",
        },
        narrative: [],
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
          start: SPEC.data.start,
          end: SPEC.data.end,
          factors: [
            {
              factorId: "momentum",
              label: FACTOR.label,
              pointer: "/factors/factors/0/graph",
              outputNodeId: "mom_252",
              expectedPlanHash: "plan-hash",
              nodes: [
                {
                  nodeId: "close",
                  operation: "field",
                  pointer: "/factors/factors/0/graph/nodes/0",
                },
                {
                  nodeId: "mom_252",
                  operation: "time_series.momentum",
                  pointer: "/factors/factors/0/graph/nodes/1",
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
  });
});
