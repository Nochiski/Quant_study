import { describe, expect, it } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { projectForm, type FormField } from "../model/form-projection";
import {
  fieldOperation,
  parseDraft,
  resetOperation,
  unsetOperation,
  type ObjectSection,
} from "../model/form-transactions";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const MINIMAL = readBackendFixture(
  "strategy_documents/quality_momentum.minimal.yaml",
);

const objectSection = (key: string): ObjectSection => {
  const { sections } = projectForm(SCHEMA, parseSource(MINIMAL, "yaml"), []);
  const found = sections.find((s) => s.key === key);
  if (found === undefined || found.kind !== "object")
    throw new Error(`object section ${key}`);
  return found;
};

const fieldOf = (section: ObjectSection, key: string): FormField => {
  const found = section.fields.find((f) => f.key === key);
  if (found === undefined) throw new Error(`field ${key}`);
  return found;
};

describe("parseDraft", () => {
  it("reads numbers with the schema's integer flag and bounds", () => {
    const risk = objectSection("risk");
    const weight = fieldOf(risk, "max_name_weight").control; // number (0, 1]
    expect(parseDraft(weight, "0.1")).toEqual({ status: "ok", value: 0.1 });
    expect(parseDraft(weight, "0")).toEqual({
      status: "invalid",
      reason: "range",
    });
    expect(parseDraft(weight, "1")).toEqual({ status: "ok", value: 1 });
    expect(parseDraft(weight, "abc")).toEqual({
      status: "invalid",
      reason: "number",
    });
    expect(parseDraft(weight, "")).toEqual({
      status: "invalid",
      reason: "number",
    });
    const count = fieldOf(
      objectSection("portfolio"),
      "selection_count",
    ).control;
    expect(parseDraft(count, "2.5")).toEqual({
      status: "invalid",
      reason: "integer",
    });
    expect(parseDraft(count, " 30 ")).toEqual({ status: "ok", value: 30 });
  });

  it("reads dates and passes text through", () => {
    const data = objectSection("data");
    expect(parseDraft(fieldOf(data, "start").control, "2024-01-02")).toEqual({
      status: "ok",
      value: "2024-01-02",
    });
    expect(parseDraft(fieldOf(data, "start").control, "2024/01/02")).toEqual({
      status: "invalid",
      reason: "date",
    });
    expect(
      parseDraft(fieldOf(objectSection(""), "title").control, "제목"),
    ).toEqual({
      status: "ok",
      value: "제목",
    });
  });
});

describe("fieldOperation", () => {
  it("replaces written scalars, inserts omitted keys, and creates an omitted section in one operation", () => {
    const risk = objectSection("risk");
    expect(fieldOperation(risk, fieldOf(risk, "max_name_weight"), 0.1)).toEqual(
      {
        kind: "replace-scalar",
        pointer: "/risk/max_name_weight",
        value: 0.1,
      },
    );
    expect(fieldOperation(risk, fieldOf(risk, "gross_exposure"), 1.5)).toEqual({
      kind: "insert-key",
      parentPointer: "/risk",
      key: "gross_exposure",
      value: 1.5,
    });
    const signal = objectSection("signal");
    expect(signal.written).toBe(false);
    expect(
      fieldOperation(signal, fieldOf(signal, "score_threshold"), 0.2),
    ).toEqual({
      kind: "insert-key",
      parentPointer: "",
      key: "signal",
      value: { score_threshold: 0.2 },
    });
    const root = objectSection("");
    expect(fieldOperation(root, fieldOf(root, "description"), "설명")).toEqual({
      kind: "insert-key",
      parentPointer: "",
      key: "description",
      value: "설명",
    });
  });

  it("resets with remove and unsets nullable fields with null, skipping no-ops", () => {
    const risk = objectSection("risk");
    expect(resetOperation(fieldOf(risk, "max_name_weight"))).toEqual({
      kind: "remove",
      pointer: "/risk/max_name_weight",
    });
    const signal = objectSection("signal");
    // nullable이고 미작성이며 기본값이 null → 이미 그 상태.
    expect(
      unsetOperation(signal, fieldOf(signal, "regime_field_id")),
    ).toBeNull();
    expect(unsetOperation(risk, fieldOf(risk, "max_name_weight"))).toBeNull();
    const written = projectForm(
      SCHEMA,
      parseSource(
        `${MINIMAL}signal:\n  regime_field_id: liquidity.adv\n`,
        "yaml",
      ),
      [],
    ).sections.find((s) => s.key === "signal");
    if (written === undefined || written.kind !== "object")
      throw new Error("signal");
    expect(
      unsetOperation(written, fieldOf(written, "regime_field_id")),
    ).toEqual({
      kind: "replace-scalar",
      pointer: "/signal/regime_field_id",
      value: null,
    });
  });
});
