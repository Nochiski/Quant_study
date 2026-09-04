import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type {
  FactorCatalog,
  FieldContract,
  ResearchCatalog,
} from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  projectContractInspector,
  type ContractInspectorSource,
} from "../model/contract-inspector";
import type { JsonSchema } from "../model/schema-navigator";
import { ContractInspector } from "../ui/contract-inspector";

afterEach(cleanup);

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

const CONTRACT: FieldContract[] = [
  {
    pointer: "/title",
    type: "string",
    required: true,
    example: null,
  },
  {
    pointer: "/risk/max_name_weight",
    type: "number",
    required: false,
    has_default: true,
    default: 0.1,
    unit: "ratio",
    display_unit: "%",
    applied_stage: "risk",
    description_key: "strategy.contract.risk.max_name_weight",
    example: 0.05,
    minimum: 0,
    exclusive_minimum: true,
    maximum: 1,
  },
  {
    pointer: "/factors/factors/*/graph/nodes/*/field_id",
    branch: "field",
    type: "string",
    required: true,
    catalog: "equity-field",
  },
  {
    pointer: "/factors/factors/*/graph/nodes/*/factor_id",
    branch: "saved_factor",
    type: "string",
    required: true,
    catalog: "factor",
  },
];

const EQUITY_CATALOG = {
  facets: { dataset_ids: ["prices"], frequencies: ["daily"], units: ["KRW"] },
  fields: [
    {
      available_date_basis: "session close",
      coverage: {
        ends_on: "2026-08-31",
        estimated_coverage_pct: 99.5,
        point_in_time: true,
        requires_confirmation: false,
        starts_on: "2000-01-04",
        supported_cell_kinds: ["observed", "missing"],
        venues: ["KOSPI", "KOSDAQ"],
      },
      dataset_id: "prices",
      description: "수정주가 종가",
      disclosure_basis: "exchange session",
      evidence: "KRX daily close",
      field_id: "close",
      frequency: "daily",
      label: "종가",
      recommended_lag_sessions: 0,
      unit: "KRW",
      value_type: "price",
    },
  ],
  page: 1,
  page_count: 1,
  page_size: 100,
  snapshot: {
    built_at: "2026-09-04T00:00:00Z",
    dataset_revisions: [
      { as_of: "2026-09-04", dataset_id: "prices", revision: "r12" },
    ],
    point_in_time: true,
    schema_version: "1",
    snapshot_id: "dataset-snapshot-v1",
    source: "equity-adapter",
  },
  total: 1,
} satisfies ResearchCatalog;

const FACTOR_CATALOG = {
  facets: {
    availability: ["implemented"],
    categories: ["price"],
    output_units: ["score"],
  },
  factors: [
    {
      availability: "implemented",
      category: "price",
      default_graph: null,
      description: "12개월 모멘텀",
      factor_id: "momentum_12m",
      label: "12M Momentum",
      minimum_history_sessions: 252,
      missing_policy: "drop",
      output_unit: "score",
      preference: "high",
      required_field_ids: ["close"],
      tags: ["momentum"],
    },
  ],
  page: 1,
  page_count: 1,
  page_size: 100,
  registry_version: "factor-registry-v1",
  total: 1,
} satisfies FactorCatalog;

const source = (
  overrides: Partial<ContractInspectorSource> = {},
): ContractInspectorSource => ({
  schema: {
    schema: SCHEMA,
    schema_hash: "schema-hash-v1",
    schema_version: "1.0",
  },
  contract: {
    contract: {
      contract_hash: "contract-hash-v1",
      dataset_snapshot_id: "dataset-snapshot-v1",
      factor_registry_version: "factor-registry-v1",
      fields: CONTRACT,
      schema_hash: "schema-hash-v1",
      schema_version: "1.0",
    },
    equity_catalog_url: "/api/v1/equity/catalog",
    factor_catalog_url: "/api/v1/factors/catalog",
  },
  equityCatalog: EQUITY_CATALOG,
  factorCatalog: FACTOR_CATALOG,
  state: {
    schema: "ready",
    contract: "ready",
    equityCatalog: "ready",
    factorCatalog: "ready",
  },
  ...overrides,
});

const TREE = {
  schema_version: "1.0",
  title: "테스트 전략",
  risk: { max_name_weight: 0.05 },
  factors: {
    factors: [
      {
        factor_id: "alpha",
        graph: {
          nodes: [
            { node_id: "px", kind: "field", field_id: "close" },
            {
              node_id: "saved",
              kind: "saved_factor",
              factor_id: "momentum_12m",
            },
          ],
          output_node_id: "px",
        },
      },
    ],
  },
};

describe("contract projection", () => {
  it("projects raw and display values, bounds, defaults and stage from one contract row", () => {
    const result = projectContractInspector(
      source(),
      "/risk/max_name_weight",
      TREE,
      false,
    );
    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.field.value).toMatchObject({
      present: true,
      raw: 0.05,
      formatted: "0.05",
      display: "5%",
    });
    expect(result.field.defaultValue).toBe(0.1);
    expect(result.field.minimum).toEqual({ value: 0, inclusive: false });
    expect(result.field.maximum).toEqual({ value: 1, inclusive: true });
    expect(result.field.appliedStage).toBe("risk");
    expect(result.field.descriptionKey).toBe(
      "strategy.contract.risk.max_name_weight",
    );
  });

  it("treats the wire contract's null example as absent", () => {
    const result = projectContractInspector(source(), "/title", TREE, false);
    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.field.hasExample).toBe(false);
    expect(result.field.example).toBeUndefined();
  });

  it.each([undefined, "not_a_node_kind"])(
    "keeps an unresolved discriminator variant-based when document kind is %s",
    (kind) => {
      const pendingNode =
        kind === undefined
          ? { node_id: "pending" }
          : { node_id: "pending", kind };
      const pendingTree = {
        ...TREE,
        factors: {
          factors: [
            {
              ...TREE.factors.factors[0],
              graph: {
                nodes: [pendingNode],
                output_node_id: "pending",
              },
            },
          ],
        },
      };
      const result = projectContractInspector(
        source(),
        "/factors/factors/0/graph/nodes/0/kind",
        pendingTree,
        false,
      );
      expect(result.status).toBe("ready");
      if (result.status !== "ready") return;
      expect(result.field.discriminator?.selected).toBeNull();
      expect(result.field.discriminator?.variants).toContain("field");
      expect(result.field.enumValues).toEqual(
        result.field.discriminator?.variants,
      );
      expect(result.field.hasConst).toBe(false);
      expect(result.field.constValue).toBeUndefined();
    },
  );

  it("joins a field only to the contract-pinned snapshot and exposes PIT metadata", () => {
    const result = projectContractInspector(
      source(),
      "/factors/factors/0/graph/nodes/0/field_id",
      TREE,
      false,
    );
    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.field.discriminator).toMatchObject({
      propertyName: "kind",
      selected: "field",
    });
    expect(result.field.discriminator?.variants).toContain("time_series");
    expect(result.catalog).toMatchObject({
      kind: "equity-field",
      status: "ready",
      id: "close",
      actualVersion: "dataset-snapshot-v1",
    });
    if (
      result.catalog?.kind !== "equity-field" ||
      result.catalog.status !== "ready"
    )
      return;
    expect(result.catalog.field.coverage.point_in_time).toBe(true);
    expect(result.catalog.snapshot.dataset_revisions[0].revision).toBe("r12");
  });

  it("joins saved factor ids to the contract-pinned registry", () => {
    const result = projectContractInspector(
      source(),
      "/factors/factors/0/graph/nodes/1/factor_id",
      TREE,
      false,
    );
    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.catalog).toMatchObject({
      kind: "factor",
      status: "ready",
      id: "momentum_12m",
      actualVersion: "factor-registry-v1",
    });
  });

  it("fails closed for schema-contract drift and does not join catalog version drift", () => {
    const incompatible = projectContractInspector(
      source({
        schema: {
          schema: SCHEMA,
          schema_hash: "new-schema",
          schema_version: "2.0",
        },
      }),
      "/risk/max_name_weight",
      TREE,
      false,
    );
    expect(incompatible.status).toBe("incompatible");

    const mismatchedCatalog = {
      ...EQUITY_CATALOG,
      snapshot: { ...EQUITY_CATALOG.snapshot, snapshot_id: "new-snapshot" },
    };
    const field = projectContractInspector(
      source({ equityCatalog: mismatchedCatalog }),
      "/factors/factors/0/graph/nodes/0/field_id",
      TREE,
      false,
    );
    expect(field.status).toBe("ready");
    if (field.status === "ready")
      expect(field.catalog).toMatchObject({ status: "mismatch" });
  });

  it("distinguishes loading, root, object, array and unknown paths", () => {
    expect(
      projectContractInspector(
        source({
          schema: null,
          state: { ...source().state, schema: "loading" },
        }),
        undefined,
        undefined,
        false,
      ).status,
    ).toBe("loading");
    const cases = [
      [undefined, "root"],
      ["/risk", "object"],
      ["/factors/factors", "array"],
    ] as const;
    for (const [pointer, shape] of cases) {
      const result = projectContractInspector(source(), pointer, TREE, false);
      expect(result.status).toBe("ready");
      if (result.status === "ready") expect(result.field.shape).toBe(shape);
    }
    expect(
      projectContractInspector(source(), "/risk/not_a_field", TREE, false)
        .status,
    ).toBe("unknown");
  });
});

describe("ContractInspector UI", () => {
  it("does not render a null wire example", () => {
    render(
      <ContractInspector
        source={source()}
        selectedPointer="/title"
        tree={TREE}
        stale={false}
      />,
    );
    expect(screen.queryByText("예시")).not.toBeInTheDocument();
  });

  it("renders localized backend description and raw/display values without sample metadata", () => {
    render(
      <ContractInspector
        source={source()}
        selectedPointer="/risk/max_name_weight"
        tree={TREE}
        stale={false}
      />,
    );
    const rows = screen.getByText("저장 값").closest("dl");
    expect(rows).not.toBeNull();
    expect(within(rows!).getAllByText("0.05").length).toBe(2);
    expect(within(rows!).getByText("5%")).toBeInTheDocument();
    expect(screen.getByText("종목별 최대 목표 비중 한도")).toBeInTheDocument();
    expect(screen.getByText("> 0 · ≤ 1")).toBeInTheDocument();
  });

  it("renders catalog-owned PIT evidence and exact snapshot provenance", () => {
    render(
      <ContractInspector
        source={source()}
        selectedPointer="/factors/factors/0/graph/nodes/0/field_id"
        tree={TREE}
        stale={false}
      />,
    );
    expect(screen.getByText("데이터 필드 · PIT")).toBeInTheDocument();
    expect(screen.getByText("KRX daily close")).toBeInTheDocument();
    expect(screen.getAllByText("dataset-snapshot-v1").length).toBeGreaterThan(
      0,
    );
    expect(screen.getAllByText("field").length).toBeGreaterThanOrEqual(2);
  });
});
