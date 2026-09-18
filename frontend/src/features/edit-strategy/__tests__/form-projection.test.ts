import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { DocumentDiagnostic } from "../model/document-state";
import {
  projectForm,
  type FormField,
  type FormSection,
} from "../model/form-projection";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const VERBOSE = readBackendFixture("strategy_documents/quality_momentum.yaml");
const MINIMAL = readBackendFixture(
  "strategy_documents/quality_momentum.minimal.yaml",
);

const parsed = (source: string) => parseSource(source, "yaml");

const section = (sections: FormSection[], key: string): FormSection => {
  const found = sections.find((candidate) => candidate.key === key);
  if (found === undefined) throw new Error(`section ${key}`);
  return found;
};

const objectFields = (sections: FormSection[], key: string): FormField[] => {
  const found = section(sections, key);
  if (found.kind !== "object") throw new Error(`${key} is not an object`);
  return found.fields;
};

const field = (fields: FormField[], key: string): FormField => {
  const found = fields.find((candidate) => candidate.key === key);
  if (found === undefined) throw new Error(`field ${key}`);
  return found;
};

describe("projectForm", () => {
  it("lists sections in schema order: root scalars first, then every root property", () => {
    const { sections } = projectForm(SCHEMA, parsed(VERBOSE), []);
    const rootKeys = Object.keys(SCHEMA.properties as object);
    const scalarKeys = objectFields(sections, "").map((f) => f.key);
    expect(sections[0]).toMatchObject({ kind: "object", pointer: "", key: "" });
    expect([...scalarKeys, ...sections.slice(1).map((s) => s.key)]).toEqual(
      rootKeys,
    );
    expect(sections.slice(1).map((s) => s.kind)).toEqual(
      rootKeys
        .filter((key) => !scalarKeys.includes(key))
        .map((key) =>
          (SCHEMA.properties as Record<string, JsonSchema>)[key]!.type ===
          "array"
            ? "list"
            : "object",
        ),
    );
  });

  it("decides controls from the schema only", () => {
    const { sections } = projectForm(SCHEMA, parsed(VERBOSE), []);
    const data = objectFields(sections, "data");
    expect(field(data, "start").control).toEqual({ kind: "date" });
    expect(field(data, "universe_id").control).toEqual({
      kind: "catalog",
      catalog: "universe",
    });
    expect(field(data, "market").control).toMatchObject({ kind: "enum" });
    const portfolio = objectFields(sections, "portfolio");
    expect(field(portfolio, "selection_count").control).toMatchObject({
      kind: "number",
      integer: true,
    });
    expect(field(portfolio, "side").control).toEqual({
      kind: "enum",
      values: ["long_only", "long_short"],
    });
    const risk = objectFields(sections, "risk");
    expect(field(risk, "sector_neutral").control).toEqual({ kind: "boolean" });
    expect(field(risk, "max_name_weight").control).toMatchObject({
      kind: "number",
      integer: false,
      min: { value: 0, inclusive: false },
      max: { value: 1, inclusive: true },
    });
    expect(field(risk, "max_name_weight").unit).toBe("ratio");
    expect(field(risk, "risk_field_id")).toMatchObject({
      control: { kind: "catalog", catalog: "equity-field" },
      nullable: true,
    });
    expect(field(objectFields(sections, ""), "title").control).toEqual({
      kind: "text",
    });
  });

  it("marks written fields from the tree and shows defaults for omitted ones", () => {
    const verbose = projectForm(SCHEMA, parsed(VERBOSE), []);
    const minimal = projectForm(SCHEMA, parsed(MINIMAL), []);
    const verboseData = objectFields(verbose.sections, "data");
    const minimalData = objectFields(minimal.sections, "data");
    expect(field(verboseData, "market")).toMatchObject({
      written: true,
      value: "KRX",
    });
    expect(field(minimalData, "market")).toMatchObject({
      written: false,
      value: "KRX",
      hasDefault: true,
      defaultValue: "KRX",
    });
    expect(field(minimalData, "start")).toMatchObject({
      written: true,
      required: true,
      value: "2021-01-01",
    });
    expect(section(minimal.sections, "signal")).toMatchObject({
      kind: "object",
      written: false,
    });
    expect(section(verbose.sections, "signal")).toMatchObject({
      written: false,
    });
    expect(section(verbose.sections, "risk")).toMatchObject({ written: true });
    const total = (sections: FormSection[]) =>
      sections.flatMap((s) =>
        s.kind === "object" ? s.fields : s.items.flatMap((i) => i.fields),
      ).length;
    // 같은 스키마·같은 factors 수 → 필드 수는 작성 여부와 무관하게 같다.
    expect(total(minimal.sections)).toBe(total(verbose.sections));
  });

  it("projects list sections with item fields, summaries and the graph link", () => {
    const { sections } = projectForm(SCHEMA, parsed(VERBOSE), []);
    const factors = section(sections, "factors");
    if (factors.kind !== "list") throw new Error("factors must be a list");
    expect(factors.itemSchemaPointer).toBe("/$defs/FactorSignal");
    expect(factors.items).toHaveLength(1);
    const [momentum] = factors.items;
    expect(momentum).toMatchObject({
      pointer: "/factors/0",
      summary: "momentum",
      branches: null,
    });
    expect(field(momentum!.fields, "direction").control).toEqual({
      kind: "enum",
      values: ["high", "low"],
    });
    expect(field(momentum!.fields, "graph").control).toEqual({
      kind: "graph-link",
    });
    expect(field(momentum!.fields, "weight")).toMatchObject({
      written: true,
      value: 0.6,
    });
    const eligibility = objectFields(sections, "eligibility");
    expect(field(eligibility, "rules").control).toEqual({ kind: "list-link" });
    expect(section(sections, "parameters")).toMatchObject({
      kind: "list",
      items: [],
    });
  });

  it("reports unresolved union items with their kind candidates and reference candidates", () => {
    const source = `${VERBOSE}\nparameters:\n  - name: lookback\n`.replace(
      "parameters: []\n",
      "",
    );
    const { sections } = projectForm(SCHEMA, parsed(source), []);
    const parameters = section(sections, "parameters");
    if (parameters.kind !== "list") throw new Error("parameters is a list");
    expect(parameters.items[0]).toMatchObject({
      pointer: "/parameters/0",
      summary: "lookback",
      fields: [],
    });
    expect(parameters.items[0]!.branches).toEqual(
      expect.arrayContaining(["float", "integer", "choice"]),
    );
  });

  it("judges applicability from the schema table and maps diagnostics by pointer", () => {
    const diagnostics: DocumentDiagnostic[] = [
      {
        code: "strategy.field.inapplicable",
        kind: "semantic",
        severity: "warning",
        pointer: "/portfolio/selection_percentile",
        message: "읽히지 않음",
        range: null,
      },
      {
        code: "strategy.graph.cycle",
        kind: "semantic",
        severity: "error",
        pointer: "/factors/0/graph/nodes/1",
        message: "순환",
        range: null,
      },
    ];
    const withPercentile = VERBOSE.replace(
      "portfolio:\n  selection_count: 20\n",
      "portfolio:\n  selection_count: 20\n  selection_percentile: 0.2\n",
    );
    const { sections } = projectForm(
      SCHEMA,
      parsed(withPercentile),
      diagnostics,
    );
    const portfolio = objectFields(sections, "portfolio");
    // selection_method는 문서에 없고 발행 기본값(top_n)으로 판정한다.
    expect(field(portfolio, "selection_count").applicable).toBe(true);
    expect(field(portfolio, "selection_percentile")).toMatchObject({
      applicable: false,
      written: true,
      diagnostics: [
        expect.objectContaining({ pointer: "/portfolio/selection_percentile" }),
      ],
    });
    expect(field(portfolio, "weighting").applicable).toBeNull();
    const factors = section(sections, "factors");
    if (factors.kind !== "list") throw new Error("factors is a list");
    expect(field(factors.items[0]!.fields, "graph").diagnostics).toHaveLength(
      1,
    );
    expect(field(factors.items[0]!.fields, "factor_id").diagnostics).toEqual(
      [],
    );
  });

  it("names union items by their identifier, skipping const-fixed keys like kind (DEFECT-121-01)", () => {
    const source = VERBOSE.replace(
      "parameters: []\n",
      "parameters:\n  - kind: float\n    parameter_id: lookback\n    minimum: 1.0\n    maximum: 2.0\n    default: 1.5\n  - kind: float\n    parameter_id: window\n    minimum: 1\n    maximum: 5\n    default: 3\n",
    );
    const parameters = section(
      projectForm(SCHEMA, parsed(source), []).sections,
      "parameters",
    );
    if (parameters.kind !== "list") throw new Error("parameters is a list");
    expect(parameters.items.map((item) => item.summary)).toEqual([
      "lookback",
      "window",
    ]);
    expect(field(parameters.items[0]!.fields, "kind").control).toEqual({
      kind: "const",
      value: "float",
    });
  });

  it("exposes const values, x-default-from and the applicability presence flag", () => {
    const { sections } = projectForm(SCHEMA, parsed(MINIMAL), []);
    expect(field(objectFields(sections, ""), "schema_version").control).toEqual(
      {
        kind: "const",
        value: "1.1",
      },
    );
    const factors = section(sections, "factors");
    if (factors.kind !== "list") throw new Error("factors is a list");
    expect(field(factors.items[0]!.fields, "label")).toMatchObject({
      defaultFrom: "factor_id",
    });
    const portfolio = objectFields(sections, "portfolio");
    expect(field(portfolio, "selection_count")).toMatchObject({
      hasApplicability: true,
    });
    expect(field(portfolio, "weighting")).toMatchObject({
      hasApplicability: false,
      applicable: null,
    });
  });

  it("routes every diagnostic to a field, an item, a section or the root section (DEFECT-121-02)", () => {
    const source = VERBOSE.replace(
      "parameters: []\n",
      "parameters:\n  - kind: float\n    parameter_id: lookback\n    minimum: 2.0\n    maximum: 1.0\n    default: 1.5\n",
    ).replace(
      "eligibility:\n  rules: []\n",
      "eligibility:\n  rules:\n    - field_id: liquidity.adv\n      operator: gte\n      value: 1\n",
    );
    const pointers = [
      "",
      "/factors",
      "/factors/0",
      "/factors/0/weight",
      "/factors/0/graph/nodes/1",
      "/parameters",
      "/parameters/0",
      "/parameters/0/minimum",
      "/eligibility",
      "/eligibility/rules",
      "/eligibility/rules/0",
      "/eligibility/rules/0/field_id",
      "/portfolio/selection_count",
      "/nope/unknown",
    ];
    const diagnostics: DocumentDiagnostic[] = pointers.map((pointer) => ({
      code: "strategy.probe",
      kind: "semantic",
      severity: "error",
      pointer,
      message: pointer,
      range: null,
    }));
    const { sections } = projectForm(SCHEMA, parsed(source), diagnostics);
    const landed: Record<string, string> = {};
    const claim = (owner: string, list: DocumentDiagnostic[]) => {
      for (const d of list) {
        expect(landed[d.pointer]).toBeUndefined();
        landed[d.pointer] = owner;
      }
    };
    for (const s of sections) {
      claim(`section ${s.pointer}`, s.diagnostics);
      if (s.kind === "object")
        for (const f of s.fields) claim(`field ${f.pointer}`, f.diagnostics);
      else
        for (const item of s.items) {
          claim(`item ${item.pointer}`, item.diagnostics);
          for (const f of item.fields)
            claim(`field ${f.pointer}`, f.diagnostics);
        }
    }
    expect(Object.keys(landed).sort()).toEqual([...pointers].sort());
    expect(landed[""]).toBe("section ");
    expect(landed["/nope/unknown"]).toBe("section ");
    expect(landed["/factors"]).toBe("section /factors");
    expect(landed["/factors/0"]).toBe("item /factors/0");
    expect(landed["/factors/0/weight"]).toBe("field /factors/0/weight");
    expect(landed["/factors/0/graph/nodes/1"]).toBe("field /factors/0/graph");
    expect(landed["/parameters/0"]).toBe("item /parameters/0");
    expect(landed["/parameters/0/minimum"]).toBe("field /parameters/0/minimum");
    expect(landed["/eligibility"]).toBe("section /eligibility");
    expect(landed["/eligibility/rules/0/field_id"]).toBe(
      "field /eligibility/rules",
    );
  });

  it("projects the schema with every field unwritten when there is no parse", () => {
    const { sections } = projectForm(SCHEMA, null, []);
    expect(sections.length).toBeGreaterThan(1);
    for (const candidate of sections) {
      if (candidate.kind === "object") {
        expect(candidate.fields.every((f) => !f.written)).toBe(true);
      } else {
        expect(candidate.items).toEqual([]);
      }
    }
  });
});
