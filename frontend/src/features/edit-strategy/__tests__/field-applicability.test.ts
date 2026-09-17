import { describe, expect, it } from "vitest";

import type { ApplicableWhen, FieldContract } from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  applicabilityByPointer,
  isApplicableWhen,
  projectApplicability,
} from "../model/field-applicability";
import type { JsonSchema } from "../model/schema-navigator";

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

/** backend가 발행한 조건 행을 그대로 읽는다(frontend 복제본 없음). */
const whenOf = (definition: string, property: string): ApplicableWhen => {
  const defs = SCHEMA.$defs as Record<string, JsonSchema>;
  const properties = defs[definition]!.properties as Record<string, JsonSchema>;
  const value = properties[property]!["x-applicable-when"];
  if (!isApplicableWhen(value))
    throw new Error(`no x-applicable-when: ${property}`);
  return value;
};

describe("field applicability projection", () => {
  it("reads the AND conditions from the backend row and judges them on the current tree", () => {
    const when = whenOf("PortfolioStep", "short_selection_count");
    expect(when.all_of.map((c) => c.pointer)).toEqual([
      "/portfolio/side",
      "/portfolio/selection_method",
    ]);

    const longShortTopN = projectApplicability(when, {
      portfolio: { side: "long_short", selection_method: "top_n" },
    });
    expect(longShortTopN.applicable).toBe(true);
    expect(longShortTopN.conditions.map((c) => c.path)).toEqual([
      "portfolio.side",
      "portfolio.selection_method",
    ]);

    const percentile = projectApplicability(when, {
      portfolio: { side: "long_short", selection_method: "percentile" },
    });
    expect(percentile.applicable).toBe(false);
    expect(percentile.conditions.map((c) => c.holds)).toEqual([true, false]);
  });

  it("never fills in backend defaults: an unwritten condition field is undecided, a false one wins", () => {
    const when = whenOf("PortfolioStep", "short_selection_count");
    expect(projectApplicability(when, { portfolio: {} }).applicable).toBeNull();
    expect(projectApplicability(when, undefined).applicable).toBeNull();
    expect(
      projectApplicability(when, { portfolio: { side: "long_only" } })
        .applicable,
    ).toBe(false);
  });

  it("judges not_null rows by presence and carries the owning error code", () => {
    const when = whenOf("PortfolioStep", "minimum_liquidity");
    expect(when.owned_by_error).toBe("strategy.portfolio.liquidity_field");
    expect(
      projectApplicability(when, { portfolio: { liquidity_field_id: null } })
        .applicable,
    ).toBe(false);
    expect(
      projectApplicability(when, {
        portfolio: { liquidity_field_id: "price.trading_value" },
      }).applicable,
    ).toBe(true);
    expect(projectApplicability(when, { portfolio: {} }).applicable).toBe(
      false,
    );
  });

  it("rejects malformed schema rows at the boundary", () => {
    expect(isApplicableWhen({ all_of: [], description_key: "k" })).toBe(false);
    expect(
      isApplicableWhen({
        all_of: [{ pointer: "/a", equals: 1, not_null: false }],
        description_key: "k",
        owned_by_error: null,
      }),
    ).toBe(false);
  });

  it("maps every contract row with a condition by pointer and skips branch rows", () => {
    const contract: FieldContract[] = [
      {
        pointer: "/portfolio/selection_percentile",
        type: "number",
        required: false,
        example: null,
        applicable_when: whenOf("PortfolioStep", "selection_percentile"),
      },
      {
        pointer: "/factors/*/graph/nodes/*/field_id",
        branch: "field",
        type: "string",
        required: true,
        example: null,
        applicable_when: whenOf("PortfolioStep", "selection_percentile"),
      },
      { pointer: "/title", type: "string", required: true, example: null },
    ];
    const map = applicabilityByPointer(contract, {
      portfolio: { selection_method: "top_n" },
    });
    expect([...map.keys()]).toEqual(["/portfolio/selection_percentile"]);
    expect(map.get("/portfolio/selection_percentile")?.applicable).toBe(false);
  });
});
