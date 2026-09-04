import { describe, expect, it } from "vitest";

import type { FactorDefinition } from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  buildCanonicalSnippetCatalog,
  planSnippetEdit,
  type CanonicalSnippet,
} from "../model/canonical-snippets";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

const FACTOR: FactorDefinition = {
  availability: "implemented",
  category: "price",
  default_graph: {
    nodes: [{ kind: "field", node_id: "px", field_id: "price.close" }],
    output_node_id: "px",
    missing_policy: "drop",
  },
  description: "Server factor",
  factor_id: "server.momentum",
  label: "Server momentum",
  minimum_history_sessions: 1,
  missing_policy: "drop",
  output_unit: "score",
  preference: "high",
  required_field_ids: ["price.close"],
};

const findSnippet = (
  snippets: readonly CanonicalSnippet[],
  id: string,
): CanonicalSnippet => {
  const snippet = snippets.find((candidate) => candidate.id === id);
  if (!snippet) throw new Error(`missing test snippet: ${id}`);
  return snippet;
};

const catalog = (
  schema: JsonSchema = SCHEMA,
  factors: readonly FactorDefinition[] = [FACTOR],
  status: "loading" | "ready" | "unavailable" = "ready",
): CanonicalSnippet[] =>
  buildCanonicalSnippetCatalog({ schema, factors, status });

describe("canonical StrategySpec snippets", () => {
  it("projects all five areas, defaults and factor graphs only from backend contracts", () => {
    const snippets = catalog();

    expect(new Set(snippets.map((snippet) => snippet.category))).toEqual(
      new Set(["data", "factor", "signal", "risk", "execution"]),
    );
    expect(findSnippet(snippets, "section:data").value).toEqual({
      market: "KRX",
      start: "",
      end: "",
      universe_id: "",
      frequency: "daily",
    });
    expect(findSnippet(snippets, "section:risk").value).toMatchObject({
      max_name_weight: 0.1,
      sector_neutral: false,
    });
    expect(findSnippet(snippets, "section:execution").value).toMatchObject({
      timing: "next_open",
      fee_bps: 15,
    });
    expect(findSnippet(snippets, "factor:server.momentum").value).toEqual({
      factor_id: "server.momentum",
      label: "Server momentum",
      direction: "high",
      weight: 1,
      graph: FACTOR.default_graph,
    });
  });

  it("fails closed for catalog-only, missing graph and incomplete authoring metadata", () => {
    const unavailable = [
      { ...FACTOR, availability: "catalog_only" as const },
      { ...FACTOR, factor_id: "missing-graph", default_graph: null },
    ];
    expect(
      catalog(SCHEMA, unavailable).filter(
        (snippet) => snippet.kind === "factor",
      ),
    ).toEqual([]);

    const broken = structuredClone(SCHEMA);
    const defs = broken.$defs as Record<string, JsonSchema>;
    const properties = defs.FactorSignal.properties as Record<
      string,
      JsonSchema
    >;
    delete properties.weight["x-authoring-default"];
    expect(catalog(broken).some((snippet) => snippet.kind === "factor")).toBe(
      false,
    );
  });

  it("skips a recursive schema branch instead of crashing catalog projection", () => {
    const recursive: JsonSchema = {
      type: "object",
      properties: { risk: { $ref: "#/$defs/Loop" } },
      $defs: {
        Loop: {
          type: "object",
          properties: { self: { $ref: "#/$defs/Loop" } },
          required: ["self"],
        },
      },
    };
    expect(catalog(recursive)).toEqual([]);
    expect(catalog(SCHEMA, [FACTOR], "loading")).toEqual([]);
  });

  it("replaces a partial root key and validates the complete next YAML document", () => {
    const snippet = findSnippet(catalog(), "section:signal");
    const source = 'schema_version: "1.0"\nsig';
    const result = planSnippetEdit(
      source,
      "yaml",
      { from: source.length, to: source.length },
      snippet,
    );

    expect(result.status).toBe("ok");
    if (result.status !== "ok") return;
    expect(result.edit.nextSource).toBe(
      'schema_version: "1.0"\nsignal:\n  method: weighted_sum\n  entry_percentile: 0.1\n  score_threshold: null\n  regime_field_id: null\n  regime_minimum: null',
    );
    expect(result.edit).toMatchObject({
      from: source.length - 3,
      to: source.length,
    });
  });

  it("rejects a duplicate catalog factor without becoming a semantic validator", () => {
    const snippet = findSnippet(catalog(), "factor:server.momentum");
    const source =
      "factors:\n  factors:\n    - factor_id: server.momentum\n    ";
    expect(
      planSnippetEdit(
        source,
        "yaml",
        { from: source.length, to: source.length },
        snippet,
      ),
    ).toEqual({ status: "error", reason: "duplicate" });
  });

  it.each([
    ['schema_version: "1.0"\r\nsig\r\nrisk: {}\r\n', "\r\nrisk: {}\r\n"],
    ['schema_version: "1.0"\r\nsig', ""],
    ['schema_version: "1.0"\r\nsig\r\n\r\nrisk: {}', "\r\n\r\nrisk: {}"],
  ])("preserves CRLF at a partial-key boundary", (source, suffix) => {
    const snippet = findSnippet(catalog(), "section:signal");
    const cursor = source.indexOf("sig") + 3;
    const result = planSnippetEdit(
      source,
      "yaml",
      { from: cursor, to: cursor },
      snippet,
    );
    expect(result.status).toBe("ok");
    if (result.status !== "ok") return;
    expect(result.edit.nextSource.endsWith(suffix)).toBe(true);
    expect(result.edit.nextSource.replaceAll("\r\n", "")).not.toContain("\n");
  });

  it("inserts a catalog factor as an indented array item at the cursor", () => {
    const snippet = findSnippet(catalog(), "factor:server.momentum");
    const source = 'schema_version: "1.0"\nfactors:\n  factors:\n    ';
    const result = planSnippetEdit(
      source,
      "yaml",
      { from: source.length, to: source.length },
      snippet,
    );

    expect(result.status).toBe("ok");
    if (result.status !== "ok") return;
    expect(result.edit.insert).toContain("- factor_id: server.momentum");
    expect(result.edit.insert).toContain(
      "      graph:\n        nodes:\n          - kind: field",
    );
    expect(result.edit.nextSource).toContain(
      "  factors:\n    - factor_id: server.momentum",
    );
  });

  it("fails without mutation for duplicate, selected, JSON and unsafe cursor contexts", () => {
    const snippet = findSnippet(catalog(), "section:data");
    const duplicate = "data:\n  market: TEST\n";
    expect(
      planSnippetEdit(
        duplicate,
        "yaml",
        { from: duplicate.length, to: duplicate.length },
        snippet,
      ),
    ).toEqual({ status: "error", reason: "duplicate" });
    expect(planSnippetEdit("", "yaml", { from: 0, to: 1 }, snippet)).toEqual({
      status: "error",
      reason: "selection",
    });
    expect(planSnippetEdit("", "yaml", { from: 1, to: 1 }, snippet)).toEqual({
      status: "error",
      reason: "selection",
    });
    expect(planSnippetEdit("", "yaml", { from: -1, to: -1 }, snippet)).toEqual({
      status: "error",
      reason: "selection",
    });
    expect(planSnippetEdit("{}", "json", { from: 0, to: 0 }, snippet)).toEqual({
      status: "error",
      reason: "yaml-only",
    });
    const value = "title: here";
    expect(
      planSnippetEdit(
        value,
        "yaml",
        { from: value.length, to: value.length },
        snippet,
      ),
    ).toEqual({ status: "error", reason: "cursor-context" });
  });
});
