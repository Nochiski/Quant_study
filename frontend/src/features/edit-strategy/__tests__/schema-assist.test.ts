import { describe, expect, it } from "vitest";

import type {
  DatasetFieldProfile,
  FactorDefinition,
  FieldContract,
} from "../../../shared/api";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  documentReducer,
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import {
  buildCompletionSource,
  buildHoverSource,
  describePointer,
  type AssistDeps,
} from "../model/schema-assist";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

const CONTRACT: FieldContract[] = [
  {
    pointer: "/risk/max_name_weight",
    type: "number",
    required: false,
    has_default: true,
    default: 0.1,
    unit: "ratio",
    display_unit: "%",
    applied_stage: "risk",
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
];

const FIELDS = [
  {
    field_id: "close",
    label: "종가",
    description: "수정 종가",
    unit: "KRW",
    frequency: "daily",
  },
  {
    field_id: "volume",
    label: "거래량",
    description: "일 거래량",
    unit: "shares",
    frequency: "daily",
  },
] as DatasetFieldProfile[];
const FACTORS = [
  { factor_id: "quality_roe", label: "ROE", description: "자기자본이익률" },
] as FactorDefinition[];

const YAML = [
  'schema_version: "1.0"',
  "title: 테스트",
  "risk:",
  "  gross_exposure: 1",
  "factors:",
  "  factors:",
  "    - factor_id: momentum",
  "      graph:",
  "        nodes:",
  "          - node_id: px",
  "            kind: field",
  "            field_id: close",
  "          - node_id: mom",
  "            kind: time_series",
  "            input_node_id: ",
  "          - node_id: unknown",
  "            ",
  "        output_node_id: ",
  "parameters:",
  "  - parameter_id: lookback",
  "    kind: integer",
  "",
].join("\n");

const stateFor = (
  text: string,
  extra: Partial<DocumentState> = {},
): DocumentState => {
  let state = documentReducer(initialDocumentState("yaml", ""), {
    type: "load",
    format: "yaml",
    source: text,
    strategyId: null,
    baseRevision: null,
    baseSpecHash: null,
  });
  state = documentReducer(state, {
    type: "parsed",
    version: state.sourceVersion,
    result: parseSource(text, "yaml"),
  });
  return { ...state, ...extra };
};

const deps = (
  state: DocumentState,
  schema: JsonSchema | null = SCHEMA,
): AssistDeps => ({
  schema,
  contract: CONTRACT,
  catalogs: { equityFields: FIELDS, factors: FACTORS },
  getState: () => state,
});

const offsetOf = (text: string, needle: string, after = 0): number => {
  const index = text.indexOf(needle);
  if (index < 0) throw new Error(`needle not found: ${needle}`);
  return index + needle.length + after;
};

describe("schema-driven completion", () => {
  it("completes keys from the schema, minus the keys already present", async () => {
    const text = `${YAML.replace("  gross_exposure: 1", "  gross_exposure: 1\n  max")}`;
    const source = buildCompletionSource(deps(stateFor(text)));
    const result = await source({
      text,
      offset: offsetOf(text, "  max"),
      explicit: false,
    });
    expect(result?.from).toBe(offsetOf(text, "  max") - 3);
    const labels = result!.options.map((o) => o.label);
    expect(labels).toContain("max_name_weight");
    expect(labels).not.toContain("gross_exposure");
    const option = result!.options.find((o) => o.label === "max_name_weight")!;
    expect(option.apply).toBe("max_name_weight: ");
    expect(option.detail).toContain("number");
  });

  it("re-selects the allowed keys from the node's kind, and offers every branch when unknown", async () => {
    const source = buildCompletionSource(deps(stateFor(YAML)));
    const inNode = await source({
      text: YAML,
      offset:
        offsetOf(YAML, "            input_node_id: ") -
        "input_node_id: ".length,
      explicit: true,
    });
    const timeSeriesKeys = inNode!.options.map((o) => o.label);
    expect(timeSeriesKeys).not.toContain("field_id");
    expect(timeSeriesKeys).not.toContain("node_id"); // already present
    const unknownKind = await source({
      text: YAML,
      offset: offsetOf(YAML, "          - node_id: unknown\n            "),
      explicit: true,
    });
    const labels = unknownKind!.options.map((o) => o.label);
    expect(labels).toContain("kind");
    expect(labels).toContain("field_id");
    expect(
      unknownKind!.options.find((o) => o.label === "field_id")?.detail,
    ).toContain("종류=field");
  });

  it("completes identifier values from the catalog or namespace the schema names", async () => {
    const source = buildCompletionSource(deps(stateFor(YAML)));
    const field = await source({
      text: YAML,
      offset: offsetOf(YAML, "            field_id: "),
      explicit: true,
    });
    expect(field!.options.map((o) => o.label)).toEqual(["close", "volume"]);
    expect(field!.options[0].detail).toBe("종가");
    const node = await source({
      text: YAML,
      offset: offsetOf(YAML, "            input_node_id: "),
      explicit: true,
    });
    expect(node!.options.map((o) => o.label)).toEqual(["px", "unknown"]);
    const output = await source({
      text: YAML,
      offset: offsetOf(YAML, "        output_node_id: "),
      explicit: true,
    });
    expect(output!.options.map((o) => o.label)).toEqual([
      "px",
      "mom",
      "unknown",
    ]);
    const kind = await source({
      text: YAML,
      offset: offsetOf(YAML, "            kind: ") - 0,
      explicit: true,
    });
    expect(kind!.options.map((o) => o.label)).toEqual(
      expect.arrayContaining(["field", "time_series", "saved_factor"]),
    );
    const parameterKind = await source({
      text: YAML,
      offset: offsetOf(YAML, "    kind: integer") - "integer".length,
      explicit: true,
    });
    expect(parameterKind!.options.map((o) => o.label)).toEqual(
      expect.arrayContaining(["float", "integer", "choice"]),
    );
  });

  it("stays silent during IME composition, without a schema, and in JSON", async () => {
    const offset = offsetOf(YAML, "            field_id: ");
    const context = { text: YAML, offset, explicit: true };
    expect(
      await buildCompletionSource(deps(stateFor(YAML, { composing: true })))(
        context,
      ),
    ).toBeNull();
    expect(
      await buildCompletionSource(deps(stateFor(YAML), null))(context),
    ).toBeNull();
    expect(
      await buildCompletionSource(deps(stateFor(YAML, { format: "json" })))(
        context,
      ),
    ).toBeNull();
  });
});

describe("schema-driven hover", () => {
  it("describes the field under the cursor from the contract row and the schema", () => {
    const text = YAML.replace(
      "  gross_exposure: 1",
      "  gross_exposure: 1\n  max_name_weight: 0.05",
    );
    const state = stateFor(text);
    const hover = buildHoverSource(deps(state))(
      offsetOf(text, "  max_name") - 2,
    );
    expect(hover).not.toBeNull();
    expect(text.slice(hover!.from, hover!.to)).toBe("max_name_weight");
    expect(hover!.lines[0]).toBe("/risk/max_name_weight");
    expect(hover!.lines).toContain("단위: ratio (표시 %)");
    expect(hover!.lines).toContain("기본값: 0.1");
    expect(hover!.lines).toContain("적용 시점: risk");
    expect(hover!.lines.some((line) => line.startsWith("타입: number"))).toBe(
      true,
    );
  });

  it("uses the branch row for union members and names the value source", () => {
    const lines = describePointer(
      deps(stateFor(YAML)),
      "/factors/factors/0/graph/nodes/0/field_id",
      stateFor(YAML).parse!.tree,
    );
    expect(lines).toContain("필수");
    expect(lines).toContain("값 출처: 데이터 필드 카탈로그");
  });

  it("returns nothing while the text is unparsed", () => {
    const state = stateFor(YAML);
    const stale = { ...state, sourceVersion: state.sourceVersion + 1 };
    expect(buildHoverSource(deps(stale))(offsetOf(YAML, "  gross"))).toBeNull();
  });
});
