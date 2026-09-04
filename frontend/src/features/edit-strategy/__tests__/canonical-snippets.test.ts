import { describe, expect, it } from "vitest";

import type { FactorDefinition } from "../../../shared/api";
import {
  buildCanonicalSnippetCatalog,
  planSnippetEdit,
  type CanonicalSnippet,
} from "../model/canonical-snippets";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA: JsonSchema = {
  type: "object",
  properties: {
    data: { $ref: "#/$defs/DataStep" },
    factors: { $ref: "#/$defs/FactorStep" },
    signal: { $ref: "#/$defs/SignalStep" },
    risk: { $ref: "#/$defs/RiskStep" },
    execution: { $ref: "#/$defs/ExecutionStep" },
  },
  $defs: {
    DataStep: {
      type: "object",
      properties: {
        market: { type: "string", enum: ["TEST"] },
        start: { type: "string", format: "date" },
        end: { type: "string", format: "date" },
        universe_id: { type: "string", "x-catalog": "universe" },
        frequency: { type: "string", default: "weekly" },
      },
      required: ["market", "start", "end", "universe_id"],
    },
    FactorStep: {
      type: "object",
      properties: {
        factors: { type: "array", items: { $ref: "#/$defs/FactorSignal" } },
      },
      required: ["factors"],
    },
    FactorSignal: {
      type: "object",
      properties: {
        factor_id: { type: "string" },
        label: { type: "string" },
        direction: { type: "string", enum: ["low", "high"] },
        weight: { type: "number" },
        graph: {
          type: "object",
          properties: {
            nodes: { type: "array", items: { type: "object" } },
            output_node_id: { type: "string" },
          },
          required: ["nodes", "output_node_id"],
        },
      },
      required: ["factor_id", "label", "direction", "weight", "graph"],
    },
    SignalStep: {
      type: "object",
      properties: {
        method: { type: "string", default: "rank_threshold" },
        entry_percentile: { type: "number", default: 0.23 },
      },
    },
    RiskStep: {
      type: "object",
      properties: {
        max_name_weight: { type: "number", default: 0.27 },
        sector_neutral: { type: "boolean", default: true },
      },
    },
    ExecutionStep: {
      type: "object",
      properties: {
        timing: { type: "string", default: "test_open" },
        fee_bps: { type: "number", default: 7.5 },
      },
    },
  },
};

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

describe("canonical StrategySpec snippets", () => {
  it("projects all five areas, defaults and factor graphs only from backend contracts", () => {
    const snippets = buildCanonicalSnippetCatalog(SCHEMA, [FACTOR]);

    expect(new Set(snippets.map((snippet) => snippet.category))).toEqual(
      new Set(["data", "factor", "signal", "risk", "execution"]),
    );
    expect(findSnippet(snippets, "section:data").value).toEqual({
      market: "TEST",
      start: "",
      end: "",
      universe_id: "",
      frequency: "weekly",
    });
    expect(findSnippet(snippets, "section:risk").value).toEqual({
      max_name_weight: 0.27,
      sector_neutral: true,
    });
    expect(findSnippet(snippets, "section:execution").value).toEqual({
      timing: "test_open",
      fee_bps: 7.5,
    });
    expect(findSnippet(snippets, "factor:server.momentum").value).toEqual({
      factor_id: "server.momentum",
      label: "Server momentum",
      direction: "high",
      weight: 1,
      graph: FACTOR.default_graph,
    });
  });

  it("replaces a partial root key and validates the complete next YAML document", () => {
    const snippet = findSnippet(
      buildCanonicalSnippetCatalog(SCHEMA, [FACTOR]),
      "section:signal",
    );
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
      'schema_version: "1.0"\nsignal:\n  method: rank_threshold\n  entry_percentile: 0.23',
    );
    expect(result.edit).toMatchObject({
      from: source.length - 3,
      to: source.length,
    });
  });

  it("inserts a catalog factor as an indented array item at the cursor", () => {
    const snippet = findSnippet(
      buildCanonicalSnippetCatalog(SCHEMA, [FACTOR]),
      "factor:server.momentum",
    );
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
    const snippet = findSnippet(
      buildCanonicalSnippetCatalog(SCHEMA, [FACTOR]),
      "section:data",
    );
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
