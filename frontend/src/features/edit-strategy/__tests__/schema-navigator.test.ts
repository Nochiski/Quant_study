import { describe, expect, it } from "vitest";

import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  propertyOptions,
  schemaAt,
  typeLabel,
  valueOptions,
  type JsonSchema,
} from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const DOCUMENT = JSON.parse(
  readBackendFixture("strategy_documents/quality_momentum.json"),
) as Record<string, unknown>;

describe("schema navigator", () => {
  it("lists the root document properties with their required flags", () => {
    const root = schemaAt(SCHEMA, "", DOCUMENT);
    expect(root).not.toBeNull();
    const options = propertyOptions(SCHEMA, root!);
    expect(options.map((o) => o.name)).toEqual(
      Object.keys(SCHEMA.properties as object),
    );
    expect(options.find((o) => o.name === "risk")?.required).toBe(true);
    expect(options.find((o) => o.name === "parameters")?.required).toBe(false);
  });

  it("follows $ref, arrays and nullable anyOf down to a scalar with its metadata", () => {
    const weight = schemaAt(SCHEMA, "/risk/max_name_weight", DOCUMENT);
    expect(weight?.node["x-unit"]).toBe("ratio");
    expect(typeLabel(weight!.node)).toBe("number >0 ≤1");
    const regime = schemaAt(SCHEMA, "/signal/regime_field_id", DOCUMENT);
    expect(regime).toMatchObject({ nullable: true });
    expect(regime?.node["x-catalog"]).toBe("equity-field");
    expect(schemaAt(SCHEMA, "/risk/nope", DOCUMENT)).toBeNull();
    expect(schemaAt(SCHEMA, "/risk/max_name_weight/x", DOCUMENT)).toBeNull();
  });

  it("selects the union branch from the document's kind and re-selects when it changes", () => {
    const nodes = "/factors/factors/0/graph/nodes";
    const field = schemaAt(SCHEMA, `${nodes}/0`, DOCUMENT);
    expect(field?.branches).toBeNull();
    expect(propertyOptions(SCHEMA, field!).map((o) => o.name)).toEqual([
      "node_id",
      "field_id",
      "kind",
    ]);
    const changed = structuredClone(DOCUMENT) as {
      factors: { factors: { graph: { nodes: { kind: string }[] } }[] };
    };
    changed.factors.factors[0].graph.nodes[0].kind = "time_series";
    const timeSeries = schemaAt(SCHEMA, `${nodes}/0`, changed);
    expect(propertyOptions(SCHEMA, timeSeries!).map((o) => o.name)).toContain(
      "input_node_id",
    );
    expect(
      propertyOptions(SCHEMA, timeSeries!).map((o) => o.name),
    ).not.toContain("field_id");
  });

  it("offers every branch's properties, labelled, while the kind is still unknown", () => {
    const unresolved = schemaAt(
      SCHEMA,
      "/factors/factors/0/graph/nodes/9",
      DOCUMENT,
    );
    expect(unresolved?.branches?.length).toBeGreaterThan(5);
    const options = propertyOptions(SCHEMA, unresolved!);
    expect(options.find((o) => o.name === "kind")?.branch).toBeNull();
    expect(options.find((o) => o.name === "field_id")?.branch).toBe("field");
    expect(options.find((o) => o.name === "node_id")?.branch).toBeNull();
    expect(valueOptions(unresolved!)).toContain("field");
    const kind = schemaAt(
      SCHEMA,
      "/factors/factors/0/graph/nodes/0/kind",
      DOCUMENT,
    );
    expect(valueOptions(kind!)).toEqual(["field"]);
  });

  it.each([undefined, "not_a_node_kind"])(
    "marks branch-only and conflicting property schemas unresolved when kind is %s",
    (kind) => {
      const changed = structuredClone(DOCUMENT) as {
        factors: { factors: { graph: { nodes: Record<string, unknown>[] } }[] };
      };
      changed.factors.factors[0].graph.nodes[0] = {
        node_id: "draft",
        ...(kind === undefined ? {} : { kind }),
        operator: "momentum",
        field_id: "close",
      };
      const base = "/factors/factors/0/graph/nodes/0";
      const nodeId = schemaAt(SCHEMA, `${base}/node_id`, changed);
      expect(nodeId?.propertyVariants).toBeNull();
      expect(nodeId?.propertyRequired).toBe(true);
      const kindField = schemaAt(SCHEMA, `${base}/kind`, changed);
      expect(kindField?.propertyVariants).not.toBeNull();
      expect(kindField?.propertyRequired).toBe(true);
      const operator = schemaAt(SCHEMA, `${base}/operator`, changed);
      expect(
        operator?.propertyVariants?.map((variant) => variant.branch),
      ).toEqual(expect.arrayContaining(["unary", "time_series"]));
      expect(valueOptions(operator!)).toEqual([]);
      const variantEnums = operator?.propertyVariants?.map(
        (variant) => variant.schema.enum,
      );
      expect(
        new Set(variantEnums?.map((values) => JSON.stringify(values))).size,
      ).toBeGreaterThan(1);

      const fieldId = schemaAt(SCHEMA, `${base}/field_id`, changed);
      expect(
        fieldId?.propertyVariants?.map((variant) => variant.branch),
      ).toEqual(["field"]);
      expect(valueOptions(fieldId!)).toEqual([]);
    },
  );

  it("keeps differing requiredness unresolved even when branch schemas match", () => {
    const schema: JsonSchema = {
      type: "object",
      properties: {
        items: {
          type: "array",
          items: {
            discriminator: { propertyName: "kind" },
            oneOf: [
              {
                type: "object",
                properties: {
                  kind: { type: "string", const: "a" },
                  value: { type: "string" },
                },
                required: ["kind", "value"],
              },
              {
                type: "object",
                properties: {
                  kind: { type: "string", const: "b" },
                  value: { type: "string" },
                },
                required: ["kind"],
              },
            ],
          },
        },
      },
    };
    const value = schemaAt(schema, "/items/0/value", {
      items: [{ value: "draft" }],
    });
    expect(value?.propertyVariants?.map((variant) => variant.branch)).toEqual([
      "a",
      "b",
    ]);
    expect(value?.propertyRequired).toBeNull();
  });

  it("enumerates enum, const and boolean values from the schema alone", () => {
    const operator = schemaAt(
      SCHEMA,
      "/eligibility/rules/0/operator",
      DOCUMENT,
    );
    expect(valueOptions(operator!)).toEqual(["gt", "gte", "lt", "lte", "eq"]);
    const version = schemaAt(SCHEMA, "/schema_version", DOCUMENT);
    expect(valueOptions(version!)).toEqual(["1.0"]);
    expect(typeLabel(operator!.node)).toBe("enum(gt|gte|lt|lte|eq)");
  });
});
