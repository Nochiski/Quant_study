import { describe, expect, it } from "vitest";

import type { StrategySpec, StrategyTraceResponse } from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  parseSecurityIds,
  prepareStrategyTrace,
  responseMatchesStrategyTrace,
  type StrategyDebuggerContext,
} from "../model/strategy-trace";
import { projectTargetTapeRows } from "../model/target-tape";

const SPEC = JSON.parse(
  readBackendFixture("strategy_documents/quality_momentum.legacy.json"),
) as StrategySpec;

const context = (): StrategyDebuggerContext => ({
  documentEpoch: 4,
  sourceVersion: 9,
  strategySource: {
    kind: "inline_draft",
    spec: SPEC,
    source_hash: "source-hash",
  },
  specHash: "spec-hash",
  expectedSnapshotId: "snapshot-v1",
  expectedRegistryVersion: "registry-v1",
  start: SPEC.data.start,
  end: "2026-08-31",
  factors: [
    {
      factorId: "momentum",
      label: "Momentum",
      pointer: "/factors/factors/0/graph",
      outputNodeId: "ranked",
      expectedPlanHash: "plan-hash",
      nodes: [
        {
          nodeId: "ranked",
          operation: "cross_sectional.rank",
          pointer: "/factors/factors/0/graph/nodes/2",
        },
      ],
    },
  ],
});

const response = (): StrategyTraceResponse => ({
  spec_hash: "spec-hash",
  snapshot_id: "snapshot-v1",
  registry_version: "registry-v1",
  plan_hash: "plan-hash",
  factor_id: "momentum",
  as_of: "2026-08-31",
  provenance: {
    kind: "inline_draft",
    schema_version: "1.0",
    spec_hash: "spec-hash",
    source_hash: "source-hash",
    strategy_id: null,
    revision: null,
  },
  raw: [],
  raw_truncated: false,
  warnings: [],
  trace: {
    rows: [
      {
        node_id: "ranked",
        operation: "cross_sectional.rank",
        as_of: "2026-08-31",
        security_id: "sec-a",
        value: 0,
        status: "ok",
        inputs: [],
      },
    ],
    offset: 0,
    limit: 1,
    returned: 1,
    has_more: false,
  },
  target: {
    signal_as_of: "2026-08-31",
    execution_on: "2026-09-01",
    candidates: [
      {
        as_of: "2026-08-31",
        security_id: "sec-a",
        sector_id: null,
        eligible: true,
        composite_score: 0,
        rank: 1,
        selected: true,
        side: "long",
        exclusion_reasons: [],
        target_weight: 0,
      },
    ],
    targets: [],
  },
});

describe("strategy trace request contract", () => {
  it("normalizes security IDs without changing first-seen order and builds one bounded node request", () => {
    expect(parseSecurityIds(" sec-b,sec-a  sec-b\nsec-c ")).toEqual([
      "sec-b",
      "sec-a",
      "sec-c",
    ]);
    const prepared = prepareStrategyTrace(context(), {
      asOf: "2026-08-31",
      security: "sec-b, sec-a, sec-b",
      factorId: "momentum",
      nodeId: "ranked",
    });
    expect(prepared).toMatchObject({
      kind: "ready",
      request: {
        as_of: "2026-08-31",
        security_ids: ["sec-b", "sec-a"],
        factor_id: "momentum",
        node_ids: ["ranked"],
        include_raw: false,
        offset: 0,
        limit: 2,
      },
    });
  });

  it.each([
    [
      "date",
      {
        asOf: "2026-02-31",
        security: "sec-a",
        factorId: "momentum",
        nodeId: "ranked",
      },
    ],
    [
      "security",
      {
        asOf: "2026-08-31",
        security: "",
        factorId: "momentum",
        nodeId: "ranked",
      },
    ],
    [
      "factor",
      {
        asOf: "2026-08-31",
        security: "sec-a",
        factorId: "unknown",
        nodeId: "ranked",
      },
    ],
    [
      "node",
      {
        asOf: "2026-08-31",
        security: "sec-a",
        factorId: "momentum",
        nodeId: "unknown",
      },
    ],
  ] as const)(
    "blocks an invalid %s selection before HTTP",
    (reason, selection) => {
      expect(prepareStrategyTrace(context(), selection)).toEqual({
        kind: "blocked",
        reason,
      });
    },
  );

  it("accepts only the exact source, request and four backend fingerprints", () => {
    const prepared = prepareStrategyTrace(context(), {
      asOf: "2026-08-31",
      security: "sec-a",
      factorId: "momentum",
      nodeId: "ranked",
    });
    expect(prepared.kind).toBe("ready");
    if (prepared.kind !== "ready") return;
    expect(responseMatchesStrategyTrace(prepared, response())).toBe(true);
    for (const mismatch of [
      { spec_hash: "other" },
      { snapshot_id: "other" },
      { registry_version: "other" },
      { plan_hash: "other" },
      { factor_id: "other" },
      { as_of: "2026-08-30" },
      { trace: { ...response().trace, offset: 1 } },
      { trace: { ...response().trace, returned: 2 } },
      {
        provenance: { ...response().provenance, source_hash: "other" },
      },
      {
        target: {
          ...response().target!,
          candidates: [
            { ...response().target!.candidates[0]!, as_of: "2026-08-30" },
          ],
        },
      },
    ]) {
      expect(
        responseMatchesStrategyTrace(prepared, { ...response(), ...mismatch }),
      ).toBe(false);
    }
  });

  it("keeps numeric zero distinct from unavailable values in the TargetTape projection", () => {
    expect(
      projectTargetTapeRows(response(), ["sec-a", "sec-missing"], "ranked"),
    ).toEqual([
      {
        securityId: "sec-a",
        score: 0,
        rank: 1,
        selected: true,
        exclusionReasons: [],
        targetWeight: 0,
        nodeValue: 0,
        nodeStatus: "ok",
      },
      {
        securityId: "sec-missing",
        score: null,
        rank: null,
        selected: null,
        exclusionReasons: [],
        targetWeight: null,
        nodeValue: null,
        nodeStatus: null,
      },
    ]);
  });
});
