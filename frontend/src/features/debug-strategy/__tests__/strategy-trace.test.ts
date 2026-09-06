import { describe, expect, it } from "vitest";

import type { StrategySpec, StrategyTraceResponse } from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  parseSecurityIds,
  parseStartingHoldings,
  prepareStrategyTrace,
  responseMatchesStrategyTrace,
  type StrategyDebuggerContext,
} from "../model/strategy-trace";
import { projectLinkedTraceRows } from "../model/linked-trace";
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
  end: "2026-09-01",
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
    construction: [
      {
        as_of: "2026-08-31",
        security_id: "sec-a",
        factor_contributions: [
          {
            factor_id: "momentum",
            value: 0,
            configured_weight: 1,
            direction: "high",
            weighted_value: 0,
            normalized_contribution: 0,
            status: "ok",
          },
        ],
        composite_score: 0,
        rank: 1,
        eligible: true,
        selected: true,
        side: "long",
        unconstrained_target_weight: 0,
        constrained_target_weight: 0,
        previous_weight: null,
        estimated_order_delta: null,
        constraint_effect: "unchanged",
        exclusion_reasons: [],
      },
    ],
  },
});

describe("strategy trace request contract", () => {
  it("normalizes security IDs and builds bounded linked plus selected-node requests", () => {
    expect(parseSecurityIds(" sec-b,sec-a  sec-b\nsec-c ")).toEqual([
      "sec-b",
      "sec-a",
      "sec-c",
    ]);
    const expanded = context();
    expanded.factors[0]!.nodes.unshift({
      nodeId: "close",
      operation: "field",
      pointer: "/factors/factors/0/graph/nodes/0",
    });
    const prepared = prepareStrategyTrace(expanded, {
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
        node_ids: ["close", "ranked"],
        include_raw: true,
        offset: 0,
        limit: 4,
      },
      selectedRequest: {
        node_ids: ["ranked"],
        include_raw: false,
        limit: 2,
      },
    });
  });

  it("chunks more than 100 reachable nodes below the server cap", () => {
    const expanded = context();
    expanded.factors[0]!.nodes = Array.from({ length: 101 }, (_, index) => ({
      nodeId: `n${index}`,
      operation: `op.${index}`,
      pointer: `/factors/factors/0/graph/nodes/${index}`,
    }));
    expanded.factors[0]!.outputNodeId = "n100";

    const prepared = prepareStrategyTrace(expanded, {
      asOf: "2026-08-31",
      security: "sec-a",
      factorId: "momentum",
      nodeId: "n100",
    });

    expect(prepared.kind).toBe("ready");
    if (prepared.kind !== "ready") return;
    expect(prepared.linkedRequests.map((item) => item.node_ids?.length)).toEqual([
      64, 37,
    ]);
    expect(prepared.linkedRequests.map((item) => item.limit)).toEqual([64, 37]);
    expect(prepared.selectedRequest.node_ids).toEqual(["n100"]);
  });

  it("omits an empty as-of so the backend schedule owns the default", () => {
    const prepared = prepareStrategyTrace(context(), {
      asOf: "",
      security: "sec-a",
      factorId: "momentum",
      nodeId: "ranked",
    });
    expect(prepared.kind).toBe("ready");
    if (prepared.kind !== "ready") return;
    expect(prepared.request).not.toHaveProperty("as_of");
    expect(responseMatchesStrategyTrace(prepared, response())).toBe(true);
    expect(
      responseMatchesStrategyTrace(prepared, { ...response(), target: null }),
    ).toBe(false);
  });

  it("distinguishes source-owned, flat and explicit opening books", () => {
    expect(parseStartingHoldings(" ")).toEqual({ kind: "source" });
    expect(parseStartingHoldings("flat")).toEqual({
      kind: "explicit",
      holdings: [],
    });
    expect(parseStartingHoldings("sec-a=0.2, sec-b=-1e-1")).toEqual({
      kind: "explicit",
      holdings: [
        { security_id: "sec-a", weight: 0.2 },
        { security_id: "sec-b", weight: -0.1 },
      ],
    });
    expect(parseStartingHoldings("sec-a=0.2 sec-a=0.3")).toEqual({
      kind: "invalid",
    });
    const flat = prepareStrategyTrace(context(), {
      asOf: "2026-08-31",
      security: "sec-a",
      factorId: "momentum",
      nodeId: "ranked",
      startingHoldings: "flat",
    });
    expect(flat).toMatchObject({
      kind: "ready",
      request: { starting_holdings: [] },
    });
  });

  it("uses execution-source identity even when document and fingerprint identity stay unchanged", () => {
    const selection = {
      asOf: "2026-08-31",
      security: "sec-a",
      factorId: "momentum",
      nodeId: "ranked",
    };
    const inline = prepareStrategyTrace(context(), selection);
    const savedContext = context();
    savedContext.strategySource = {
      kind: "saved_revision",
      strategy_id: "strategy-1",
      revision: 3,
      expected_spec_hash: savedContext.specHash,
    };
    const saved = prepareStrategyTrace(savedContext, selection);

    expect(inline.kind).toBe("ready");
    expect(saved.kind).toBe("ready");
    if (inline.kind !== "ready" || saved.kind !== "ready") return;
    expect(saved.ownerKey).not.toBe(inline.ownerKey);
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
      {
        target: {
          ...response().target!,
          execution_on: "2026-09-02",
        },
      },
      {
        raw: [
          {
            as_of: "2026-08-31",
            security_id: "other",
            field_id: "price.close",
            value: 1,
            available_date: "2026-08-31",
            kind: "observed" as const,
          },
        ],
      },
      {
        target: {
          ...response().target!,
          construction: [
            {
              ...response().target!.construction[0]!,
              security_id: "other",
            },
          ],
        },
      },
      {
        target: {
          ...response().target!,
          construction: [
            {
              ...response().target!.construction[0]!,
              composite_score: 0.5,
            },
          ],
        },
      },
      {
        target: {
          ...response().target!,
          candidates: [],
          construction: [],
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

  it("joins the linked trace by server identities without recalculating values", () => {
    const payload = response();
    payload.raw = [
      {
        as_of: "2026-08-31",
        security_id: "sec-a",
        field_id: "flow.foreign_net_buy",
        value: 0,
        available_date: "2026-08-31",
        kind: "source_omitted_zero",
      },
    ];

    expect(projectLinkedTraceRows(payload, ["sec-a"])).toEqual([
      {
        securityId: "sec-a",
        raw: payload.raw,
        nodes: payload.trace.rows,
        construction: payload.target!.construction[0],
      },
    ]);
  });
});
